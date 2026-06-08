"""Unit tests for the pluggable summarizer: parsing, normalization, providers."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import summarize  # noqa: E402

# _parse_summary_json

def test_parse_clean_json() -> None:
    raw = '{"tldr": "hi", "key_points": ["a"], "tasks": []}'
    assert summarize._parse_summary_json(raw) == {"tldr": "hi", "key_points": ["a"], "tasks": []}


def test_parse_fenced_json() -> None:
    raw = '```json\n{"tldr": "hi", "key_points": [], "tasks": []}\n```'
    assert summarize._parse_summary_json(raw)["tldr"] == "hi"


def test_parse_json_with_surrounding_prose() -> None:
    raw = 'Sure! Here you go:\n{"tldr": "x", "key_points": [], "tasks": []}\nHope that helps.'
    assert summarize._parse_summary_json(raw)["tldr"] == "x"


def test_parse_nested_braces() -> None:
    raw = '{"tldr": "x", "key_points": [], "tasks": [{"title": "t", "owner": "A"}]}'
    out = summarize._parse_summary_json(raw)
    assert out["tasks"][0]["owner"] == "A"


def test_parse_braces_inside_strings() -> None:
    # A closing brace inside a string must not end the object early.
    raw = '{"tldr": "use {curly} braces", "key_points": [], "tasks": []}'
    assert summarize._parse_summary_json(raw)["tldr"] == "use {curly} braces"


def test_parse_empty_raises() -> None:
    with pytest.raises(summarize.SummarizerBadOutput):
        summarize._parse_summary_json("   ")


def test_parse_no_json_raises() -> None:
    with pytest.raises(summarize.SummarizerBadOutput):
        summarize._parse_summary_json("there is no json here at all")


def test_parse_broken_json_raises() -> None:
    with pytest.raises(summarize.SummarizerBadOutput):
        summarize._parse_summary_json('{"tldr": "x", "key_points": [,]}')

# normalization

def test_normalize_task_legacy_string() -> None:
    assert summarize._normalize_task("do x") == {"title": "do x", "owner": None, "due": None}


def test_normalize_task_object() -> None:
    t = {"title": "do y", "owner": "Anya", "due": "Fri", "extra": "ignored"}
    assert summarize._normalize_task(t) == {"title": "do y", "owner": "Anya", "due": "Fri"}


def test_normalize_task_deadline_alias() -> None:
    assert summarize._normalize_task({"title": "z", "deadline": "Mon"})["due"] == "Mon"


def test_normalize_task_empty_dropped() -> None:
    assert summarize._normalize_task("") is None
    assert summarize._normalize_task({"title": ""}) is None
    assert summarize._normalize_task(123) is None


def test_normalize_full() -> None:
    data = {
        "tldr": " hi ",
        "key_points": ["a", "", "b"],
        "decisions": ["d1"],
        "tasks": ["s1", {"title": "t2", "owner": "Bo"}],
    }
    out = summarize._normalize(data)
    assert out["tldr"] == "hi"
    assert out["key_points"] == ["a", "b"]
    assert out["decisions"] == ["d1"]
    assert out["tasks"] == [
        {"title": "s1", "owner": None, "due": None},
        {"title": "t2", "owner": "Bo", "due": None},
    ]


def test_normalize_missing_fields_default_empty() -> None:
    out = summarize._normalize({"tldr": "x"})
    assert out == {"tldr": "x", "key_points": [], "decisions": [], "tasks": []}

# providers (mocked subprocess)

def _fake_run(stdout: str = "", returncode: int = 0, stderr: str = ""):
    def run(argv, **kwargs):  # noqa: ANN001
        return subprocess.CompletedProcess(argv, returncode, stdout=stdout, stderr=stderr)

    return run


def test_empty_transcript_short_circuits() -> None:
    out = summarize.summarize("   ", provider="claude")
    assert out["tldr"] == "Empty transcript."
    assert out["tasks"] == []


def test_unknown_provider_raises() -> None:
    with pytest.raises(summarize.SummarizerError):
        summarize.summarize("hello", provider="nope")


def test_claude_envelope_parsing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(summarize, "_resolve_bin", lambda c, d: "/bin/claude")
    envelope = (
        '{"type":"result","is_error":false,'
        '"result":"{\\"tldr\\":\\"ok\\",\\"key_points\\":[\\"k\\"],'
        '\\"tasks\\":[{\\"title\\":\\"do\\",\\"owner\\":\\"A\\"}]}"}'
    )
    monkeypatch.setattr(subprocess, "run", _fake_run(stdout=envelope))
    out = summarize.summarize("some meeting text", provider="claude")
    assert out["tldr"] == "ok"
    assert out["tasks"][0] == {"title": "do", "owner": "A", "due": None}


def test_claude_is_error_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(summarize, "_resolve_bin", lambda c, d: "/bin/claude")
    envelope = '{"type":"result","is_error":true,"result":"rate limited"}'
    monkeypatch.setattr(subprocess, "run", _fake_run(stdout=envelope))
    with pytest.raises(summarize.SummarizerError):
        summarize.summarize("text", provider="claude")


def test_custom_requires_command() -> None:
    with pytest.raises(summarize.SummarizerNotFound):
        summarize.summarize("text", provider="custom", command="")


def test_custom_parses_stdout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(summarize, "_resolve_bin", lambda c, d: "/bin/echo")
    stdout = 'prefix {"tldr":"c","key_points":[],"tasks":[]} suffix'
    monkeypatch.setattr(subprocess, "run", _fake_run(stdout=stdout))
    out = summarize.summarize("text", provider="custom", command="fake-llm")
    assert out["tldr"] == "c"


def test_nonzero_exit_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(summarize, "_resolve_bin", lambda c, d: "/bin/claude")
    monkeypatch.setattr(subprocess, "run", _fake_run(returncode=1, stderr="boom"))
    with pytest.raises(summarize.SummarizerError) as ei:
        summarize.summarize("text", provider="claude")
    assert "boom" in str(ei.value)


def test_timeout_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(summarize, "_resolve_bin", lambda c, d: "/bin/claude")

    def boom(argv, **kwargs):  # noqa: ANN001
        raise subprocess.TimeoutExpired(argv, 1)

    monkeypatch.setattr(subprocess, "run", boom)
    with pytest.raises(summarize.SummarizerTimeout):
        summarize.summarize("text", provider="claude", timeout=1)


def test_resolve_bin_missing_raises() -> None:
    with pytest.raises(summarize.SummarizerNotFound):
        summarize._resolve_bin("/nonexistent/path/to/claude", "claude")


def test_probe_unreachable_command() -> None:
    r = summarize.probe("claude", "/nonexistent/claude")
    assert r["available"] is False
