"""Smoke tests for sidecar daemon mode (NDJSON over stdio)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

SIDECAR = Path(__file__).resolve().parent.parent / "sidecar.py"


def spawn_daemon(extra_args: list[str] | None = None) -> subprocess.Popen[str]:
    """Spawn sidecar in --daemon mode with stdin/stdout pipes ready."""
    args = [sys.executable, str(SIDECAR), "--daemon"]
    if extra_args:
        args.extend(extra_args)
    return subprocess.Popen(
        args,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )


def send(proc: subprocess.Popen[str], obj: dict) -> None:
    assert proc.stdin is not None
    proc.stdin.write(json.dumps(obj) + "\n")
    proc.stdin.flush()


def read_one(proc: subprocess.Popen[str], timeout: float = 5.0) -> dict:
    """Read one JSON object from stdout (one line)."""
    assert proc.stdout is not None
    deadline = time.time() + timeout
    while time.time() < deadline:
        line = proc.stdout.readline()
        if line:
            return json.loads(line)
        time.sleep(0.05)
    raise TimeoutError("daemon did not respond in time")


def test_daemon_rejects_unknown_method(tmp_path: Path) -> None:
    proc = spawn_daemon(["--vault", str(tmp_path), "--subfolder", "Meetings"])
    try:
        send(proc, {"id": "1", "method": "wat", "params": {}})
        resp = read_one(proc)
        assert resp["id"] == "1"
        assert "error" in resp
        assert resp["error"]["code"] == "unknown_method"
    finally:
        proc.terminate()
        proc.wait(timeout=2)


def test_daemon_start_recording_returns_id(tmp_path: Path) -> None:
    proc = spawn_daemon(["--vault", str(tmp_path), "--subfolder", "Meetings"])
    try:
        send(proc, {"id": "1", "method": "start_recording", "params": {}})
        resp = read_one(proc, timeout=10)
        assert resp["id"] == "1", resp
        assert "result" in resp, resp
        result = resp["result"]
        assert "recording_id" in result
        assert "started_at" in result
        assert result["wav_path"].endswith(".wav")
    finally:
        # Cancel to clean up the audio-capture subprocess and temp wav.
        try:
            send(proc, {"id": "99", "method": "cancel", "params": {}})
            read_one(proc, timeout=10)
        except (BrokenPipeError, TimeoutError):
            pass
        proc.terminate()
        proc.wait(timeout=5)


def test_daemon_cancel_after_start(tmp_path: Path) -> None:
    proc = spawn_daemon(["--vault", str(tmp_path), "--subfolder", "Meetings"])
    try:
        send(proc, {"id": "1", "method": "start_recording", "params": {}})
        start_resp = read_one(proc, timeout=10)
        wav_path = Path(start_resp["result"]["wav_path"])

        send(proc, {"id": "2", "method": "cancel", "params": {}})
        cancel_resp = read_one(proc, timeout=10)
        assert cancel_resp["id"] == "2"
        assert cancel_resp["result"]["cancelled"] is True
        assert not wav_path.exists(), f"{wav_path} should be cleaned up"
    finally:
        proc.terminate()
        proc.wait(timeout=5)
