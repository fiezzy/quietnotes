"""Pluggable summarizer. Takes a transcript, returns structured sections.

Backends (providers):
- ``ollama``  — local Ollama model (default, keeps the local-only privacy story).
- ``claude``  — Claude Code CLI (``claude -p``), uses the user's existing auth.
- ``codex``   — Codex CLI (``codex exec``), uses the user's existing auth.
- ``custom``  — any user-supplied command: stdin = prompt+transcript, stdout = answer.

All providers target the same JSON schema and run through one parser, so the
note layout is identical regardless of which backend produced it.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import ollama

DEFAULT_PROVIDER = "ollama"
DEFAULT_MODEL = "gemma3:4b"  # ollama default
DEFAULT_TIMEOUT = 180.0  # seconds — CLI agents can take a while on long transcripts

SYSTEM_PROMPT = """You extract structure from a meeting or voice-memo transcript. Return STRICT JSON with four fields: tldr, key_points, decisions, tasks.

CRITICAL — language preservation:
- The transcript may be in any language (English, Russian, Spanish, German, French, Italian, etc).
- Always answer in the SAME language as the transcript. Do NOT translate.
- If the transcript is in Russian, every field must be in Russian. Same for any other language.

Example input (English):
"Reviewed the new marketing page design. Anya will ship a prototype by Friday. We also agreed to keep the current color palette and revisit the tool budget next sprint."

Example output (English):
{"tldr": "Discussed the marketing page design and follow-up actions.", "key_points": ["Reviewed the new marketing page design.", "Agreed to keep the current color palette."], "decisions": ["Keep the current color palette."], "tasks": [{"title": "Ship the marketing page prototype", "owner": "Anya", "due": "Friday"}, {"title": "Revisit the tool budget", "owner": null, "due": "next sprint"}]}

Rules:
- tldr: 1-2 sentences. ALWAYS non-empty.
- key_points: array of 2-7 takeaways. ALWAYS at least 2 entries for a coherent transcript.
- decisions: array of concrete decisions/agreements reached. Empty array if none.
- tasks: array of objects {title, owner, due}. `title` is the action. `owner` is the responsible person if named, else null. `due` is the deadline if named, else null. Empty array if no tasks.
- Distribute tasks to people: when a transcript names who does what, set `owner` accordingly.
- Only JSON. No markdown fences, no surrounding prose, no explanations.
- Even if the transcript is fragmentary, extract something concrete (named topics, objects, intent).
- Preserve proper nouns and named entities exactly as spoken.
"""


class SummarizerError(Exception):
    """Base summarizer failure. ``code`` maps to a daemon error code."""

    code = "summarizer_failed"

    def __init__(self, message: str, *, recoverable: bool = True) -> None:
        super().__init__(message)
        self.recoverable = recoverable


class SummarizerNotFound(SummarizerError):
    """The chosen backend's executable could not be located. Needs user action."""

    code = "summarizer_not_found"

    def __init__(self, message: str) -> None:
        super().__init__(message, recoverable=False)


class SummarizerTimeout(SummarizerError):
    code = "summarizer_timeout"


class SummarizerBadOutput(SummarizerError):
    code = "summarizer_bad_output"


def summarize(
    transcript: str,
    *,
    provider: str = DEFAULT_PROVIDER,
    model: str = "",
    command: str = "",
    host: str | None = None,
    system_prompt: str | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    """Summarize ``transcript`` via the chosen ``provider``.

    Returns a dict with keys: ``tldr`` (str), ``key_points`` (list[str]),
    ``decisions`` (list[str]), ``tasks`` (list[{title, owner, due}]).
    """
    if not transcript.strip():
        return {"tldr": "Empty transcript.", "key_points": [], "decisions": [], "tasks": []}

    sys_prompt = (system_prompt or "").strip() or SYSTEM_PROMPT

    print(f"[summarize] provider={provider}, transcript={len(transcript)} chars...", file=sys.stderr)
    started = time.time()

    if provider == "ollama":
        raw = _ollama(transcript, model or DEFAULT_MODEL, host, sys_prompt)
    elif provider == "claude":
        raw = _claude_cli(transcript, model, command, sys_prompt, timeout)
    elif provider == "codex":
        raw = _codex_cli(transcript, model, command, sys_prompt, timeout)
    elif provider == "custom":
        raw = _custom_cmd(transcript, command, sys_prompt, timeout)
    else:
        raise SummarizerError(f"unknown summarizer provider: {provider!r}", recoverable=False)

    data = _parse_summary_json(raw)
    result = _normalize(data)

    elapsed = time.time() - started
    print(
        f"[summarize] done in {elapsed:.1f}s — tldr={len(result['tldr'])} chars, "
        f"{len(result['key_points'])} key points, {len(result['decisions'])} decisions, "
        f"{len(result['tasks'])} tasks",
        file=sys.stderr,
    )
    if not (result["tldr"] or result["key_points"] or result["decisions"] or result["tasks"]):
        print(f"[summarize] WARNING: all fields empty. Raw output:\n  {raw[:500]}", file=sys.stderr)
    return result


# --------------------------------------------------------------------------- #
# Providers
# --------------------------------------------------------------------------- #


def _ollama(transcript: str, model: str, host: str | None, system_prompt: str) -> str:
    client = ollama.Client(host=host) if host else ollama
    try:
        response = client.chat(
            model=model,
            format="json",
            options={"temperature": 0.2},
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": transcript},
            ],
        )
    except Exception as e:  # noqa: BLE001 — ollama raises a variety of connection/response errors
        raise SummarizerError(f"ollama error: {type(e).__name__}: {e}") from e
    return response["message"]["content"]


def _claude_cli(transcript: str, model: str, command: str, system_prompt: str, timeout: float) -> str:
    """Claude Code CLI (`claude -p`). Prompt + transcript both on stdin.

    Passing a positional prompt arg *and* piping stdin makes the CLI hang, and
    `--json-schema` produces empty output in current builds — so we put the
    whole instruction on stdin and rely on the shared JSON parser.
    """
    binary = _resolve_bin(command, "claude")
    argv = [binary, "-p", "--output-format", "json"]
    if model.strip():
        argv += ["--model", model.strip()]
    payload = f"{system_prompt}\n\n---\nTRANSCRIPT:\n{transcript}"
    raw = _run_agent(argv, stdin=payload, timeout=timeout, label="claude")

    # `--output-format json` wraps the answer in an envelope: {"result": ..., "is_error": ...}.
    try:
        env = json.loads(raw)
    except json.JSONDecodeError:
        return raw  # fall through to the generic parser
    if isinstance(env, dict) and ("result" in env or "is_error" in env):
        if env.get("is_error"):
            raise SummarizerError(f"claude returned an error: {str(env.get('result') or env)[:300]}")
        result = env.get("result", env)
        return json.dumps(result) if isinstance(result, (dict, list)) else str(result)
    return raw


def _codex_cli(transcript: str, model: str, command: str, system_prompt: str, timeout: float) -> str:
    """Codex CLI (`codex exec`). Prompt read from stdin (`-`), read-only sandbox."""
    binary = _resolve_bin(command, "codex")
    last_msg = _temp_path(suffix=".txt")
    try:
        argv = [
            binary,
            "exec",
            "--skip-git-repo-check",
            "--sandbox", "read-only",
            "--color", "never",
            "--output-last-message", last_msg,
        ]
        if model.strip():
            argv += ["-m", model.strip()]
        argv.append("-")  # read the prompt from stdin (avoids ARG_MAX on long transcripts)
        payload = f"{system_prompt}\n\n---\nTRANSCRIPT:\n{transcript}"
        stdout = _run_agent(argv, stdin=payload, timeout=timeout, label="codex")
        # Prefer the clean last-message file; fall back to stdout if codex didn't write it.
        try:
            content = Path(last_msg).read_text(encoding="utf-8")
            return content if content.strip() else stdout
        except OSError:
            return stdout
    finally:
        _unlink(last_msg)


def _custom_cmd(transcript: str, command: str, system_prompt: str, timeout: float) -> str:
    """User-supplied command. stdin = system_prompt + transcript, stdout = answer."""
    if not command.strip():
        raise SummarizerNotFound("custom summarizer selected but no command is configured")
    argv = shlex.split(command)
    argv[0] = _resolve_bin(argv[0], argv[0])
    payload = f"{system_prompt}\n\n---\nTRANSCRIPT:\n{transcript}"
    return _run_agent(argv, stdin=payload, timeout=timeout, label="custom")


# --------------------------------------------------------------------------- #
# Subprocess + path helpers
# --------------------------------------------------------------------------- #


def _resolve_bin(command: str, default_bin: str) -> str:
    """Resolve a provider executable. ``command`` may be a bare name or a path;
    empty falls back to ``default_bin`` looked up on PATH."""
    cand = (command or "").strip() or default_bin
    if os.path.sep in cand or cand.startswith("~"):
        p = Path(cand).expanduser()
        if p.is_file():
            return str(p)
        raise SummarizerNotFound(f"summarizer executable not found at {cand!r}")
    found = shutil.which(cand)
    if not found:
        raise SummarizerNotFound(
            f"{cand!r} not found on PATH — install it or set its path in quietnotes settings"
        )
    return found


def _run_agent(argv: list[str], *, stdin: str, timeout: float, label: str) -> str:
    preview = " ".join(shlex.quote(a) for a in argv[:5])
    print(f"[summarize] {label}: {preview} … ({len(stdin)} chars on stdin)", file=sys.stderr)
    try:
        proc = subprocess.run(
            argv,
            input=stdin,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError as e:
        raise SummarizerNotFound(f"{label}: executable not found: {e}") from e
    except subprocess.TimeoutExpired as e:
        raise SummarizerTimeout(f"{label}: no response within {timeout:.0f}s") from e

    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip()[-500:]
        raise SummarizerError(f"{label}: exit code {proc.returncode}: {tail}")
    return proc.stdout


def _temp_path(*, suffix: str) -> str:
    fd, path = tempfile.mkstemp(prefix="quietnotes-", suffix=suffix)
    os.close(fd)
    return path


def _unlink(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


# --------------------------------------------------------------------------- #
# Parsing + normalization (shared across all providers)
# --------------------------------------------------------------------------- #


def _parse_summary_json(raw: str) -> dict[str, Any]:
    """Extract and parse the first balanced JSON object from ``raw``.

    Tolerates code fences and surrounding prose that CLI agents sometimes add.
    Raises ``SummarizerBadOutput`` on empty / unparseable output.
    """
    if not raw or not raw.strip():
        raise SummarizerBadOutput("summarizer returned empty output")

    obj = _extract_json_object(raw)
    if obj is None:
        raise SummarizerBadOutput(f"no JSON object found in output: {raw.strip()[:300]}")
    try:
        data = json.loads(obj)
    except json.JSONDecodeError as e:
        raise SummarizerBadOutput(f"invalid JSON from summarizer: {e}: {obj[:300]}") from e
    if not isinstance(data, dict):
        raise SummarizerBadOutput(f"expected a JSON object, got {type(data).__name__}")
    return data


def _extract_json_object(text: str) -> str | None:
    """Return the substring of the first complete top-level ``{...}`` object,
    respecting string literals and escapes. ``None`` if there isn't one."""
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        c = text[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def _normalize(data: dict[str, Any]) -> dict[str, Any]:
    tldr = str(data.get("tldr", "")).strip()
    key_points = [str(x).strip() for x in (data.get("key_points") or []) if str(x).strip()]
    decisions = [str(x).strip() for x in (data.get("decisions") or []) if str(x).strip()]
    tasks: list[dict[str, Any]] = []
    for t in data.get("tasks") or []:
        norm = _normalize_task(t)
        if norm:
            tasks.append(norm)
    return {"tldr": tldr, "key_points": key_points, "decisions": decisions, "tasks": tasks}


def _normalize_task(t: Any) -> dict[str, Any] | None:
    """Accept both legacy strings and {title, owner, due} objects."""
    if isinstance(t, str):
        title = t.strip()
        return {"title": title, "owner": None, "due": None} if title else None
    if isinstance(t, dict):
        title = str(t.get("title") or t.get("task") or "").strip()
        if not title:
            return None
        owner = t.get("owner")
        due = t.get("due") if t.get("due") is not None else t.get("deadline")
        owner = str(owner).strip() if owner not in (None, "") else None
        due = str(due).strip() if due not in (None, "") else None
        return {"title": title, "owner": owner, "due": due}
    return None


def probe(provider: str, command: str = "") -> dict[str, Any]:
    """Check whether the chosen provider is usable. Returns {available, path, version}.

    Used by the plugin's "Detect" button. Never raises — reports availability.
    """
    if provider == "ollama":
        return {"available": True, "path": "(http)", "version": ""}
    default = {"claude": "claude", "codex": "codex"}.get(provider)
    if provider == "custom":
        if not command.strip():
            return {"available": False, "path": "", "version": "", "error": "no command configured"}
        default = shlex.split(command)[0]
    try:
        binary = _resolve_bin(command if provider != "custom" else shlex.split(command)[0], default or "")
    except SummarizerNotFound as e:
        return {"available": False, "path": "", "version": "", "error": str(e)}
    version = ""
    try:
        out = subprocess.run(
            [binary, "--version"], capture_output=True, text=True, timeout=10
        )
        version = (out.stdout or out.stderr).strip().splitlines()[0] if (out.stdout or out.stderr) else ""
    except (OSError, subprocess.SubprocessError, IndexError):
        pass
    return {"available": True, "path": binary, "version": version}


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Smoke test for summarize.py")
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--input", type=str, help="Path to a .txt file with the transcript")
    group.add_argument("--text", type=str, help="Inline transcript text")
    ap.add_argument("--provider", default=DEFAULT_PROVIDER, choices=["ollama", "claude", "codex", "custom"])
    ap.add_argument("--model", default="")
    ap.add_argument("--command", default="", help="Executable path or full command (custom)")
    ap.add_argument("--host", default=None, help="Ollama host URL, e.g. http://localhost:11434")
    args = ap.parse_args()

    if args.input:
        transcript = Path(args.input).expanduser().read_text(encoding="utf-8")
    else:
        transcript = args.text

    result = summarize(
        transcript,
        provider=args.provider,
        model=args.model,
        command=args.command,
        host=args.host,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
