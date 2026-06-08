# quietnotes

Local-first meeting notetaker for Obsidian. Captures audio from your Mac, transcribes with `mlx-whisper`, and turns it into a structured note — TL;DR, key points, decisions, and tasks with owners.

> **Status:** v0.1 in development. macOS-only (Apple Silicon, macOS 15+ required for unified system+mic capture via ScreenCaptureKit).

## Privacy modes

quietnotes captures and transcribes audio entirely on your machine. The **summarizer** stage is pluggable, so you choose the privacy/quality trade-off:

| Backend | What it is | Privacy |
| --- | --- | --- |
| **Ollama** (default) | A local LLM via [Ollama](https://ollama.com). | 🔒 Local-only — nothing leaves the laptop. |
| **Claude Code** | Shells out to the `claude` CLI using your existing auth. | ☁️ Transcript sent to Anthropic. |
| **Codex** | Shells out to the `codex` CLI using your existing auth. | ☁️ Transcript sent to OpenAI. |
| **Custom** | Any command you provide (stdin = prompt+transcript, stdout = answer). | Depends on your command. |

The CLI-agent backends need **no extra install** if you already have `claude` or `codex` — and no Ollama download. They produce noticeably better summaries and task assignment. Pick the backend in **Settings → Summarizer**; the backend used is recorded in each note's `summarizer:` frontmatter. Non-Ollama backends show a privacy warning in settings.

See [`docs/design-idea.md`](docs/design-idea.md) for the full design and [`docs/plans/2026-06-08-pluggable-summarizer-design.md`](docs/plans/2026-06-08-pluggable-summarizer-design.md) for the summarizer design.

## License

[MIT](LICENSE)
