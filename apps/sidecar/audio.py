"""Subprocess wrapper for the Swift `audio-capture` binary."""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
from pathlib import Path


def find_audio_capture_binary() -> Path:
    """Locate the audio-capture executable.

    Resolution order:
    1. $QUIETNOTES_AUDIO_CAPTURE
    2. ../audio-capture/.build/release/audio-capture
    3. ../audio-capture/.build/debug/audio-capture
    4. audio-capture on $PATH
    """
    if env := os.environ.get("QUIETNOTES_AUDIO_CAPTURE"):
        p = Path(env).expanduser()
        if p.is_file():
            return p
        raise FileNotFoundError(f"QUIETNOTES_AUDIO_CAPTURE={env} not found")

    here = Path(__file__).resolve().parent
    for candidate in (
        here.parent / "audio-capture" / ".build" / "release" / "audio-capture",
        here.parent / "audio-capture" / ".build" / "debug" / "audio-capture",
    ):
        if candidate.is_file():
            return candidate.resolve()

    if path_bin := shutil.which("audio-capture"):
        return Path(path_bin)

    raise FileNotFoundError(
        "audio-capture binary not found. Build it first:\n"
        "  cd apps/audio-capture && swift build"
    )


def record(output_wav: Path, duration_seconds: float | None = None) -> Path:
    """Record stereo audio (L=system, R=mic) to `output_wav`.

    If `duration_seconds` is None, record until Ctrl+C; the SIGINT is
    forwarded to the spawned `audio-capture` process so it can finalize
    the WAV cleanly.
    """
    binary = find_audio_capture_binary()
    output_wav = output_wav.expanduser().resolve()
    output_wav.parent.mkdir(parents=True, exist_ok=True)

    cmd = [str(binary), "--output", str(output_wav)]
    if duration_seconds is not None:
        cmd += ["--duration", str(duration_seconds)]

    label = f"{duration_seconds:.0f}s" if duration_seconds else "until Ctrl+C"
    print(f"[audio] recording → {output_wav.name} ({label})", file=sys.stderr)

    proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL)
    try:
        proc.wait()
    except KeyboardInterrupt:
        proc.send_signal(signal.SIGINT)
        proc.wait()

    if proc.returncode != 0:
        raise RuntimeError(f"audio-capture exited with code {proc.returncode}")
    if not output_wav.is_file():
        raise RuntimeError(f"audio-capture did not write {output_wav}")

    size_kb = output_wav.stat().st_size / 1024
    print(f"[audio] wrote {output_wav} ({size_kb:.1f} KB)", file=sys.stderr)
    return output_wav


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Smoke test for audio.py")
    ap.add_argument("--output", required=True, type=Path)
    ap.add_argument("--duration", type=float, default=5.0)
    args = ap.parse_args()
    record(args.output, args.duration)
