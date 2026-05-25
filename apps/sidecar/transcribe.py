"""mlx-whisper wrapper. Takes a WAV, returns transcript + segments."""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any

import mlx_whisper

DEFAULT_MODEL = "mlx-community/whisper-medium-mlx"


def transcribe(
    audio_path: Path,
    model: str = DEFAULT_MODEL,
    language: str | None = None,
) -> dict[str, Any]:
    """Transcribe a .wav file with mlx-whisper.

    Returns a dict with keys: `text`, `segments`, `language`.
    """
    audio_path = audio_path.expanduser().resolve()
    if not audio_path.is_file():
        raise FileNotFoundError(audio_path)

    print(f"[transcribe] {audio_path.name} via {model}...", file=sys.stderr)
    started = time.time()

    result = mlx_whisper.transcribe(
        str(audio_path),
        path_or_hf_repo=model,
        language=language,
        verbose=False,
    )

    elapsed = time.time() - started
    text_len = len(result.get("text", ""))
    segs = len(result.get("segments", []))
    print(
        f"[transcribe] done in {elapsed:.1f}s — lang={result.get('language')}, "
        f"{segs} segments, {text_len} chars",
        file=sys.stderr,
    )
    return result


if __name__ == "__main__":
    import argparse
    import json

    ap = argparse.ArgumentParser(description="Smoke test for transcribe.py")
    ap.add_argument("--input", required=True, type=Path)
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--language", default=None)
    args = ap.parse_args()

    result = transcribe(args.input, model=args.model, language=args.language)
    print(json.dumps(
        {
            "language": result.get("language"),
            "text": result.get("text"),
            "segments_count": len(result.get("segments", [])),
        },
        ensure_ascii=False,
        indent=2,
    ))
