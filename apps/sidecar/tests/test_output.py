"""Tests for markdown assembly: task rendering, decisions, frontmatter."""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import output  # noqa: E402


def test_render_task_plain_string() -> None:
    assert output._render_task("do x") == "- [ ] do x"


def test_render_task_object_full() -> None:
    t = {"title": "Ship it", "owner": "Anya", "due": "Friday"}
    assert output._render_task(t) == "- [ ] Ship it — **Anya** _(Friday)_"


def test_render_task_object_owner_only() -> None:
    assert output._render_task({"title": "X", "owner": "Bo", "due": None}) == "- [ ] X — **Bo**"


def test_render_task_object_bare() -> None:
    assert output._render_task({"title": "X", "owner": None, "due": None}) == "- [ ] X"


def test_write_markdown_full(tmp_path: Path) -> None:
    result = {
        "tldr": "Quick sync.",
        "key_points": ["k1", "k2"],
        "decisions": ["Ship next week"],
        "tasks": [{"title": "Fix bug", "owner": "Alex", "due": "Tue"}],
        "language": "en",
        "transcript": "blah",
    }
    md_path = output.write_markdown(
        result,
        vault=tmp_path,
        subfolder="Meetings",
        recorded_at=datetime(2026, 6, 8, 14, 30),
        whisper_model="whisper-medium",
        summarizer="claude",
        summarizer_model="claude-opus",
    )
    text = md_path.read_text(encoding="utf-8")
    assert "## Decisions" in text
    assert "- Ship next week" in text
    assert "- [ ] Fix bug — **Alex** _(Tue)_" in text
    assert "summarizer: claude" in text
    assert "summarizer_model: claude-opus" in text
    # claude is not ollama → no backward-compat ollama_model line
    assert "ollama_model:" not in text


def test_write_markdown_hides_empty_decisions(tmp_path: Path) -> None:
    result = {"tldr": "t", "key_points": ["k"], "decisions": [], "tasks": [], "transcript": "x"}
    md_path = output.write_markdown(result, vault=tmp_path, recorded_at=datetime(2026, 6, 8, 9, 0))
    text = md_path.read_text(encoding="utf-8")
    assert "## Decisions" not in text


def test_write_markdown_ollama_backcompat(tmp_path: Path) -> None:
    result = {"tldr": "t", "key_points": [], "decisions": [], "tasks": [], "transcript": "x"}
    md_path = output.write_markdown(
        result,
        vault=tmp_path,
        recorded_at=datetime(2026, 6, 8, 9, 0),
        summarizer="ollama",
        summarizer_model="gemma3:4b",
    )
    text = md_path.read_text(encoding="utf-8")
    assert "summarizer: ollama" in text
    assert "ollama_model: gemma3:4b" in text  # backward-compat line for Dataview
