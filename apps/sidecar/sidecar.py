"""quietnotes sidecar — record → transcribe → summarize → write .md."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import traceback
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import audio
import output
import summarize
import transcribe


def _emit(obj: dict) -> None:
    """Write one NDJSON line to stdout (used by daemon mode)."""
    print(json.dumps(obj, ensure_ascii=False), flush=True)


def _emit_error(msg_id: str, code: str, message: str, *, recoverable: bool = False) -> None:
    _emit({"id": msg_id, "error": {"code": code, "message": message, "recoverable": recoverable}})


def _emit_event(event: str, **fields: Any) -> None:
    _emit({"event": event, **fields})


def run_daemon(args: argparse.Namespace) -> int:
    """NDJSON-RPC loop on stdio. See docs/plans/2026-05-25-obsidian-plugin-design.md §4."""
    state: dict[str, Any] = {
        "recording_id": None,
        "wav_path": None,
        "recorded_at": None,
        "audio_proc": None,
    }

    for raw_line in sys.stdin:
        raw_line = raw_line.strip()
        if not raw_line:
            continue
        try:
            msg = json.loads(raw_line)
        except json.JSONDecodeError as e:
            _emit_error("?", "bad_json", f"could not parse line: {e}")
            continue

        method = msg.get("method")
        msg_id = msg.get("id", "?")
        params = msg.get("params") or {}

        try:
            if method == "start_recording":
                _daemon_start_recording(args, state, msg_id, params)
            elif method == "stop_and_process":
                _daemon_stop_and_process(args, state, msg_id, params)
            elif method == "cancel":
                _daemon_cancel(args, state, msg_id, params)
            elif method == "probe_summarizer":
                _daemon_probe_summarizer(args, msg_id, params)
            elif method == "test_summarizer":
                _daemon_test_summarizer(args, msg_id, params)
            else:
                _emit_error(msg_id, "unknown_method", f"unknown method: {method!r}")
        except Exception as e:  # noqa: BLE001 — daemon must not crash on a single bad request
            tb = traceback.format_exc()
            print(f"[sidecar] internal_error in {method!r}:\n{tb}", file=sys.stderr, flush=True)
            _emit_error(msg_id, "internal_error", f"{type(e).__name__}: {e}")

    # On EOF (plugin closed our stdin) — cleanly cancel any in-flight recording.
    if state["recording_id"] is not None:
        _daemon_cancel(args, state, "shutdown", {})
    return 0


def _daemon_start_recording(args: argparse.Namespace, state: dict, msg_id: str, params: dict) -> None:
    if state["recording_id"] is not None:
        _emit_error(msg_id, "already_recording", "a recording is already in progress")
        return

    recorded_at = datetime.now()
    rid = uuid.uuid4().hex[:12]
    wav_path = Path(tempfile.gettempdir()) / f"quietnotes-{recorded_at:%Y%m%d-%H%M%S}.wav"

    binary = audio.find_audio_capture_binary()
    cmd = [str(binary), "--output", str(wav_path)]
    print(f"[sidecar] spawning audio-capture: {' '.join(cmd)}", file=sys.stderr, flush=True)
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    state["recording_id"] = rid
    state["wav_path"] = wav_path
    state["recorded_at"] = recorded_at
    state["audio_proc"] = proc

    _emit({
        "id": msg_id,
        "result": {
            "recording_id": rid,
            "started_at": recorded_at.isoformat(),
            "wav_path": str(wav_path),
        },
    })


def _daemon_cancel(args: argparse.Namespace, state: dict, msg_id: str, params: dict) -> None:
    import signal as _sig

    proc = state.get("audio_proc")
    if proc is not None and proc.poll() is None:
        proc.send_signal(_sig.SIGINT)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=2)

    wav = state.get("wav_path")
    if wav and Path(wav).exists():
        try:
            Path(wav).unlink()
        except OSError:
            pass

    state["recording_id"] = None
    state["wav_path"] = None
    state["recorded_at"] = None
    state["audio_proc"] = None
    _emit({"id": msg_id, "result": {"cancelled": True}})


def _daemon_stop_and_process(args: argparse.Namespace, state: dict, msg_id: str, params: dict) -> None:
    import signal as _sig

    if state["recording_id"] is None:
        _emit_error(msg_id, "not_recording", "no recording in progress")
        return

    proc = state["audio_proc"]
    proc.send_signal(_sig.SIGINT)
    try:
        out, err = proc.communicate(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        out, err = proc.communicate(timeout=2)
        print(f"[sidecar] audio-capture HUNG. stdout:\n{out}\nstderr:\n{err}", file=sys.stderr, flush=True)
        _emit_error(msg_id, "audio_capture_hung", "audio-capture did not exit after SIGINT")
        _reset_state(state)
        return

    print(f"[sidecar] audio-capture exit={proc.returncode}\nstdout:\n{out}\nstderr:\n{err}", file=sys.stderr, flush=True)

    if proc.returncode != 0:
        _emit_error(msg_id, "audio_capture_failed", f"audio-capture exit code {proc.returncode}: {err[:200]}")
        _reset_state(state)
        return

    wav_path = state["wav_path"]
    recorded_at = state["recorded_at"]

    if not Path(wav_path).is_file() or Path(wav_path).stat().st_size < 100:
        _emit_error(
            msg_id,
            "no_audio_data",
            f"audio-capture exited cleanly but produced no/empty wav at {wav_path}",
        )
        _reset_state(state)
        return

    _emit_event("stage", stage="transcribing")
    tr = transcribe.transcribe(wav_path, model=args.whisper_model, language=args.language)

    _emit_event("stage", stage="summarizing")
    try:
        sm = summarize.summarize(tr["text"], **_summarizer_kwargs(args))
    except summarize.SummarizerError as e:
        _emit_error(msg_id, e.code, str(e), recoverable=e.recoverable)
        _reset_state(state)
        return

    _emit_event("stage", stage="writing")
    result = {
        "wav_path": str(wav_path),
        "language": tr.get("language"),
        "transcript": tr.get("text", ""),
        "tldr": sm.get("tldr", ""),
        "key_points": sm.get("key_points", []),
        "decisions": sm.get("decisions", []),
        "tasks": sm.get("tasks", []),
        "duration_seconds": output.wav_duration(wav_path),
    }

    md_path = None
    if args.vault:
        result_for_md = dict(result)
        result_for_md["wav_path"] = None  # daemon mode treats wav as ephemeral by default
        md_path = output.write_markdown(
            result_for_md,
            vault=args.vault,
            subfolder=args.subfolder,
            recorded_at=recorded_at,
            whisper_model=args.whisper_model,
            summarizer=args.summarizer,
            summarizer_model=_summarizer_model_label(args),
        )

    # Cleanup temp wav.
    try:
        Path(wav_path).unlink(missing_ok=True)
    except OSError:
        pass

    _reset_state(state)
    _emit({
        "id": msg_id,
        "result": {
            "md_path": str(md_path) if md_path else None,
            "duration_seconds": result["duration_seconds"],
            "tldr": result["tldr"],
            "key_points": result["key_points"],
            "decisions": result["decisions"],
            "tasks": result["tasks"],
        },
    })


# Canned transcript used by the settings "Test summarizer" button.
TEST_TRANSCRIPT = (
    "Quick sync. Alex will fix the login bug by Tuesday. "
    "We decided to ship the beta next week, and Maria takes the release notes."
)


def _summarizer_kwargs(args: argparse.Namespace) -> dict[str, Any]:
    """Build kwargs for summarize.summarize() from CLI args."""
    system_prompt = None
    if getattr(args, "system_prompt_file", None):
        try:
            system_prompt = Path(args.system_prompt_file).read_text(encoding="utf-8")
        except OSError as e:
            print(f"[sidecar] could not read --system-prompt-file: {e}", file=sys.stderr)
    if args.summarizer == "ollama":
        return {
            "provider": "ollama",
            "model": args.ollama_model,
            "host": args.ollama_url,
            "system_prompt": system_prompt,
        }
    return {
        "provider": args.summarizer,
        "model": args.summarizer_model,
        "command": args.summarizer_command,
        "system_prompt": system_prompt,
    }


def _summarizer_model_label(args: argparse.Namespace) -> str | None:
    """The model string to record in frontmatter for the active provider."""
    if args.summarizer == "ollama":
        return args.ollama_model
    return args.summarizer_model or None


def _daemon_probe_summarizer(args: argparse.Namespace, msg_id: str, params: dict) -> None:
    provider = params.get("provider") or args.summarizer
    command = params.get("command", args.summarizer_command)
    _emit({"id": msg_id, "result": summarize.probe(provider, command)})


def _daemon_test_summarizer(args: argparse.Namespace, msg_id: str, params: dict) -> None:
    # Allow the UI to override provider/model/command without restarting the daemon.
    overrides = argparse.Namespace(**vars(args))
    for key in ("summarizer", "summarizer_model", "summarizer_command", "ollama_model", "ollama_url"):
        if key in params and params[key] is not None:
            setattr(overrides, key, params[key])
    try:
        sm = summarize.summarize(TEST_TRANSCRIPT, **_summarizer_kwargs(overrides))
    except summarize.SummarizerError as e:
        _emit_error(msg_id, e.code, str(e), recoverable=e.recoverable)
        return
    _emit({"id": msg_id, "result": sm})


def _reset_state(state: dict) -> None:
    state["recording_id"] = None
    state["wav_path"] = None
    state["recorded_at"] = None
    state["audio_proc"] = None

LOCAL_CONFIG_FILE = Path(__file__).resolve().parent / "dev.config.json"


def load_local_config() -> dict[str, Any]:
    """Load `dev.config.json` next to sidecar.py if it exists. Gitignored.

    Used in development as defaults for CLI args. In production the Obsidian
    plugin spawns sidecar.py with explicit `--vault` / `--subfolder` flags.
    """
    if not LOCAL_CONFIG_FILE.is_file():
        return {}
    try:
        return json.loads(LOCAL_CONFIG_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        print(f"[sidecar] warning: failed to load {LOCAL_CONFIG_FILE.name}: {e}", file=sys.stderr)
        return {}


def run(
    *,
    wav_path: Path,
    whisper_model: str,
    language: str | None,
    summarizer_kwargs: dict[str, Any],
) -> dict[str, Any]:
    tr = transcribe.transcribe(wav_path, model=whisper_model, language=language)
    sm = summarize.summarize(tr["text"], **summarizer_kwargs)
    return {
        "wav_path": str(wav_path),
        "language": tr.get("language"),
        "transcript": tr.get("text", ""),
        "segments": tr.get("segments", []),
        "tldr": sm.get("tldr", ""),
        "key_points": sm.get("key_points", []),
        "decisions": sm.get("decisions", []),
        "tasks": sm.get("tasks", []),
    }


def _print_pretty(result: dict[str, Any]) -> None:
    sep = "─" * 70
    print()
    print(sep)
    print(f"WAV   : {result['wav_path']}")
    print(f"LANG  : {result['language']}")
    print(sep)
    print("TL;DR")
    print(f"  {result['tldr']}")
    print(sep)
    print("KEY POINTS")
    if result["key_points"]:
        for p in result["key_points"]:
            print(f"  • {p}")
    else:
        print("  (none)")
    print(sep)
    print("TASKS")
    if result["tasks"]:
        for t in result["tasks"]:
            if isinstance(t, dict):
                line = t.get("title", "")
                if t.get("owner"):
                    line += f" — {t['owner']}"
                if t.get("due"):
                    line += f" ({t['due']})"
            else:
                line = str(t)
            print(f"  [ ] {line}")
    else:
        print("  (none)")
    print(sep)
    print("DECISIONS")
    if result.get("decisions"):
        for d in result["decisions"]:
            print(f"  • {d}")
    else:
        print("  (none)")
    print(sep)
    print("FULL TRANSCRIPT")
    print(f"  {result['transcript']}")
    print(sep)


def main() -> int:
    cfg = load_local_config()

    ap = argparse.ArgumentParser(
        description="quietnotes sidecar: record → transcribe → summarize"
    )
    src = ap.add_mutually_exclusive_group(required=False)
    src.add_argument("--record", type=float, metavar="SECONDS",
                     help="Record N seconds via audio-capture, then process")
    src.add_argument("--from-wav", type=Path,
                     help="Skip recording, process an existing .wav file")
    src.add_argument("--daemon", action="store_true",
                     help="Read NDJSON-RPC over stdin/stdout (used by the Obsidian plugin)")

    ap.add_argument("--output-wav", type=Path, default=None,
                    help="Where to save the recording (default: /tmp/quietnotes-<ts>.wav)")
    ap.add_argument("--whisper-model", default=cfg.get("whisper_model", transcribe.DEFAULT_MODEL))
    ap.add_argument("--summarizer", default=cfg.get("summarizer", summarize.DEFAULT_PROVIDER),
                    choices=["ollama", "claude", "codex", "custom"],
                    help="Summarizer backend. Default: ollama (local).")
    ap.add_argument("--summarizer-model", default=cfg.get("summarizer_model", ""),
                    help="Model for claude/codex (empty = the CLI's default).")
    ap.add_argument("--summarizer-command", default=cfg.get("summarizer_command", ""),
                    help="Executable path (claude/codex) or full command (custom).")
    ap.add_argument("--system-prompt-file", default=cfg.get("system_prompt_file"),
                    help="Path to a file whose contents override the built-in system prompt.")
    ap.add_argument("--ollama-model", default=cfg.get("ollama_model", summarize.DEFAULT_MODEL))
    ap.add_argument("--ollama-url", default=cfg.get("ollama_url"),
                    help="Ollama host URL, e.g. http://localhost:11434.")
    ap.add_argument("--language", default=cfg.get("language"),
                    help="Force language code (e.g. ru). Omit for auto-detect.")
    ap.add_argument("--vault", type=Path,
                    default=Path(cfg["vault"]) if cfg.get("vault") else None,
                    help="Write a .md note under <vault>/<subfolder>/. "
                         "Defaults to `vault` in dev.config.json.")
    ap.add_argument("--subfolder", default=cfg.get("subfolder", "Meetings"),
                    help="Folder inside the vault. Defaults to `subfolder` in dev.config.json or 'Meetings'.")
    ap.add_argument("--keep-wav", action="store_true",
                    help="Don't delete the temp .wav after processing. Default: delete (privacy).")
    ap.add_argument("--json", action="store_true",
                    help="Print result as JSON instead of human-readable text")
    args = ap.parse_args()

    if not (args.record or args.from_wav or args.daemon):
        ap.error("one of --record, --from-wav, or --daemon is required")

    if args.daemon:
        return run_daemon(args)

    if args.from_wav:
        wav_path = args.from_wav.expanduser().resolve()
        if not wav_path.is_file():
            print(f"file not found: {wav_path}", file=sys.stderr)
            return 1
        recorded_at = datetime.fromtimestamp(wav_path.stat().st_mtime)
    else:
        recorded_at = datetime.now()
        wav_path = (
            args.output_wav
            or Path(tempfile.gettempdir()) / f"quietnotes-{recorded_at:%Y%m%d-%H%M%S}.wav"
        )
        audio.record(wav_path, args.record)

    result = run(
        wav_path=wav_path,
        whisper_model=args.whisper_model,
        language=args.language,
        summarizer_kwargs=_summarizer_kwargs(args),
    )

    # Decide whether the wav is ephemeral (we made it, no one asked to keep it).
    wav_is_ephemeral = (
        not args.keep_wav
        and not args.from_wav
        and not args.output_wav
    )

    # Capture duration *before* a possible delete so the frontmatter survives.
    result["duration_seconds"] = output.wav_duration(wav_path)

    # If we're about to delete the wav, don't reference it in the .md.
    result_for_md = dict(result)
    if wav_is_ephemeral:
        result_for_md["wav_path"] = None

    if args.vault:
        md_path = output.write_markdown(
            result_for_md,
            vault=args.vault,
            subfolder=args.subfolder,
            recorded_at=recorded_at,
            whisper_model=args.whisper_model,
            summarizer=args.summarizer,
            summarizer_model=_summarizer_model_label(args),
        )
        result["md_path"] = str(md_path)

    if wav_is_ephemeral:
        try:
            wav_path.unlink(missing_ok=True)
            print(f"[audio] removed temp recording {wav_path}", file=sys.stderr)
        except OSError as e:
            print(f"[audio] failed to remove {wav_path}: {e}", file=sys.stderr)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        _print_pretty(result)
        if md := result.get("md_path"):
            print(f"\nMarkdown saved: {md}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
