"""Ollama-based summarizer. Takes a transcript, returns structured sections."""

from __future__ import annotations

import json
import sys
import time
from typing import Any

import ollama

DEFAULT_MODEL = "gemma3:4b"

SYSTEM_PROMPT = """You extract structure from a meeting or voice-memo transcript. Return STRICT JSON with three fields: tldr, key_points, tasks.

CRITICAL — language preservation:
- The transcript may be in any language (English, Russian, Spanish, German, French, Italian, etc).
- Always answer in the SAME language as the transcript. Do NOT translate.
- If the transcript is in Russian, "tldr", "key_points", and "tasks" must be in Russian. Same for any other language.

Example input (English):
"Reviewed the new marketing page design. Anya will ship a prototype by Friday. We also need to revisit the tool budget next sprint."

Example output (English):
{"tldr": "Discussed the marketing page design and follow-up actions.", "key_points": ["Reviewed the new marketing page design.", "Agreed on a Friday deadline for the prototype.", "Tool budget is open for next sprint."], "tasks": ["Anya delivers the prototype by Friday", "Revisit the tool budget next sprint"]}

Rules:
- tldr: 1-2 sentences. ALWAYS non-empty.
- key_points: array of 2-7 takeaways. ALWAYS at least 2 entries for a coherent transcript.
- tasks: array of concrete action items. Include the responsible person and deadline when named. Empty array if no tasks.
- Only JSON. No markdown fences, no surrounding prose, no explanations.
- Even if the transcript is fragmentary, extract something concrete (named topics, objects, intent).
- Preserve proper nouns and named entities exactly as spoken.
"""


def summarize(
    transcript: str,
    model: str = DEFAULT_MODEL,
    host: str | None = None,
) -> dict[str, Any]:
    """Run the transcript through a local Ollama model and return parsed JSON.

    Returns a dict with keys: `tldr` (str), `key_points` (list[str]), `tasks` (list[str]).
    """
    if not transcript.strip():
        return {"tldr": "Empty transcript.", "key_points": [], "tasks": []}

    client = ollama.Client(host=host) if host else ollama
    print(f"[summarize] model={model}, transcript={len(transcript)} chars...", file=sys.stderr)
    started = time.time()

    response = client.chat(
        model=model,
        format="json",
        options={"temperature": 0.2},
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": transcript},
        ],
    )

    elapsed = time.time() - started
    raw = response["message"]["content"]

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"[summarize] model returned non-JSON: {e}\n--- raw ---\n{raw[:500]}", file=sys.stderr)
        raise

    tldr = str(data.get("tldr", "")).strip()
    key_points = [str(x).strip() for x in data.get("key_points", []) if str(x).strip()]
    tasks = [str(x).strip() for x in data.get("tasks", []) if str(x).strip()]

    print(
        f"[summarize] done in {elapsed:.1f}s — tldr={len(tldr)} chars, "
        f"{len(key_points)} key points, {len(tasks)} tasks",
        file=sys.stderr,
    )
    if not (tldr or key_points or tasks):
        print(f"[summarize] WARNING: all fields empty. Raw model output:\n  {raw}", file=sys.stderr)
    return {"tldr": tldr, "key_points": key_points, "tasks": tasks}


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Smoke test for summarize.py")
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--input", type=str, help="Path to a .txt file with the transcript")
    group.add_argument("--text", type=str, help="Inline transcript text")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--host", default=None, help="Ollama host URL, e.g. http://localhost:11434")
    args = ap.parse_args()

    if args.input:
        from pathlib import Path
        transcript = Path(args.input).expanduser().read_text(encoding="utf-8")
    else:
        transcript = args.text

    result = summarize(transcript, model=args.model, host=args.host)
    print(json.dumps(result, ensure_ascii=False, indent=2))
