"""Assembles the meeting markdown file in an Obsidian vault."""

from __future__ import annotations

import sys
import wave
from datetime import datetime
from pathlib import Path
from typing import Any


def write_markdown(
    result: dict[str, Any],
    vault: Path,
    *,
    subfolder: str = "Meetings",
    recorded_at: datetime | None = None,
    whisper_model: str | None = None,
    summarizer: str | None = None,
    summarizer_model: str | None = None,
) -> Path:
    """Write a structured meeting note under `<vault>/<subfolder>/`.

    Filename is `YYYY-MM-DD-HHMM.md`. Returns the written path.
    """
    vault = vault.expanduser().resolve()
    target_dir = vault / subfolder
    target_dir.mkdir(parents=True, exist_ok=True)

    when = recorded_at or datetime.now()
    target = target_dir / f"{when:%Y-%m-%d-%H%M}.md"

    duration = result.get("duration_seconds")
    if duration is None and result.get("wav_path"):
        duration = wav_duration(Path(result["wav_path"]))

    fm_lines = [
        "---",
        f"date: {when:%Y-%m-%d}",
        f"time: {when:%H:%M}",
        f"language: {result.get('language', '')}",
    ]
    if duration is not None:
        fm_lines.append(f"duration_seconds: {duration:.1f}")
    if whisper_model:
        fm_lines.append(f"whisper_model: {whisper_model}")
    if summarizer:
        fm_lines.append(f"summarizer: {summarizer}")
        # Backward-compat: existing Dataview queries key off `ollama_model`.
        if summarizer == "ollama" and summarizer_model:
            fm_lines.append(f"ollama_model: {summarizer_model}")
    if summarizer_model:
        fm_lines.append(f"summarizer_model: {summarizer_model}")
    if wav := result.get("wav_path"):
        fm_lines.append(f"source_audio: {wav}")
    fm_lines.append("tags: [meeting, quietnotes]")
    fm_lines.append("---")
    fm = "\n".join(fm_lines)

    tldr = (result.get("tldr") or "").strip() or "_(no summary)_"

    key_points = result.get("key_points", [])
    kp_block = "\n".join(f"- {p}" for p in key_points) if key_points else "_(none)_"

    tasks = result.get("tasks", [])
    tasks_block = "\n".join(_render_task(t) for t in tasks) if tasks else "_(none)_"

    decisions = result.get("decisions", [])
    decisions_section = ""
    if decisions:
        decisions_block = "\n".join(f"- {d}" for d in decisions)
        decisions_section = f"\n## Decisions\n\n{decisions_block}\n"

    transcript = (result.get("transcript") or "").strip() or "_(empty)_"

    md = f"""{fm}

# Meeting — {when:%Y-%m-%d %H:%M}

## TL;DR

{tldr}

## Key points

{kp_block}

## Tasks

{tasks_block}
{decisions_section}
## Transcript

<details>
<summary>Full transcript</summary>

{transcript}

</details>
"""

    target.write_text(md, encoding="utf-8")
    print(f"[output] wrote {target}", file=sys.stderr)
    return target


def _render_task(task: Any) -> str:
    """Render one task as a checklist line. Accepts a {title, owner, due} dict
    or a plain string (legacy / defensive)."""
    if isinstance(task, str):
        return f"- [ ] {task}"
    title = str(task.get("title", "")).strip()
    line = f"- [ ] {title}"
    owner = task.get("owner")
    if owner:
        line += f" — **{owner}**"
    due = task.get("due")
    if due:
        line += f" _({due})_"
    return line


def wav_duration(path: Path) -> float | None:
    """Best-effort duration in seconds. Tries `wave` first, then falls back to
    size-based estimation for our 48 kHz stereo float32 format."""
    try:
        with wave.open(str(path), "rb") as wf:
            return wf.getnframes() / wf.getframerate()
    except Exception:
        pass
    try:
        # 48 kHz × 2 ch × 4 bytes = 384 000 bytes/sec
        return path.stat().st_size / 384_000
    except Exception:
        return None
