# Obsidian plugin design — quietnotes v0.1 (M3)

**Status:** DRAFT (ready to implement)
**Date:** 2026-05-25
**Author:** Maksim Zhers
**Supersedes:** "Recommended Approach / Plugin scaffold" section of [`docs/design-idea.md`](../design-idea.md) — that doc was a wedge-level spec, this one is implementation-level.

---

## Context

Two milestones are already done locally (uncommitted, single init-commit pending per [memory](../../README.md)):

- **M1 — `apps/audio-capture/`** (Swift CLI). `swift run audio-capture --output meeting.wav [--duration N]` produces a stereo WAV (L=system, R=mic, 48 kHz float32) on macOS 15+. ScreenCaptureKit unified-stream path confirmed.
- **M2 — `apps/sidecar/`** (Python). `uv run python sidecar.py --record N [--vault X --subfolder Y]` runs end-to-end: record → mlx-whisper transcribe → gemma3:4b summarize → write `.md` with frontmatter. ~6× real-time on M-series. Privacy: temp wav deleted after success by default.

What's missing to ship v0.1: the **Obsidian-native experience**. Currently users would have to open Terminal and run a Python command — that defeats the "hands-off Obsidian magic" wow-moment from design-idea.md.

This document specs the plugin (M3) so it can be implemented in a small number of well-scoped sub-tasks.

---

## Goals

- One-click recording and processing from inside Obsidian. Zero Terminal use for end-user.
- Reuse `apps/audio-capture` and `apps/sidecar` without forking logic — plugin is the orchestrator, not a re-implementation.
- Zero-friction install: BRAT install → plugin onboarding handles binary download + Python venv setup.
- Privacy story preserved: no auto-detection requiring extra TCC permissions.

## Non-goals (v0.1)

- Auto-detection of meeting apps (Meet/Zoom/Telemost). Deferred to v0.2 or later.
- Per-recording UI toggles ("save this one's audio?"). Settings-level toggle is enough.
- Custom markdown template engine. Hard-coded template; revisit if v0.1 gets traction.
- Streaming partial transcripts during recording. The pipeline is post-recording.
- Inline `@person #tag` task parsing. LLM-output rendered verbatim.
- Side panel with recording history. Native vault search via folder + tags is sufficient.
- Wave-form visualizations or other visual flourishes during recording.

---

## 1 — UI overview

Three Obsidian-native touch points:

**Ribbon button** (left rail):
- Default state icon: 🎙 (idle). Recording state: 🔴 (animated dot pulse).
- Click while idle → start recording. Click while recording → stop + begin processing.
- Click during `processing.*` → no-op, show Notice "Already processing — please wait".

**Status-bar widget** (bottom of Obsidian window):
- `🎙 quietnotes: idle` (default)
- `🔴 quietnotes: recording 02:34` (live timer)
- `⏳ quietnotes: transcribing…`
- `⏳ quietnotes: summarizing…`
- `✓ quietnotes: saved 2026-05-25-1730.md` (auto-fades to idle after 3 s)
- `❌ quietnotes: <short reason>` (sticky until click → opens error modal)
- Click on widget → opens detail modal with last events + current temp wav path (useful for debugging).
- Toggleable in settings (`Show status bar widget`, default on).

**Settings tab** — see section 2.

**Notification toasts** — used for terminal errors that need user attention even if the user isn't looking at the status bar. Click → opens detail modal.

**Hotkeys** — not exposed in our settings. Obsidian provides hotkey assignment for any command. We expose two commands: `quietnotes: Start recording`, `quietnotes: Stop and process`. Users wire them to whatever combo they want via `Settings → Hotkeys`.

---

## 2 — Settings tab

Grouped sections (all in one scrollable settings page):

### Vault output
- `Output subfolder` (text). Default: `Meetings`. Vault-relative.
- `Filename pattern` (text). Default: `{date}-{time}`. Placeholders: `{date}` (`YYYY-MM-DD`), `{time}` (`HHMM`), `{lang}` (transcript language). `.md` appended automatically.

### Audio
- `Keep audio recordings` (toggle). Default **off** (privacy-first). When on, reveals:
  - `Audio storage path` (text). Default: `Meetings/attachments`. Vault-relative *unless* it starts with `/` or `~` (then treated as absolute, wav copied there but not embedded in markdown — Obsidian can't embed files outside the vault).

### AI models
- `Whisper model` (text). Default: `mlx-community/whisper-medium-mlx`. Help: "HuggingFace repo id or local path".
- `Ollama URL` (text). Default: `http://localhost:11434`.
- `Ollama model` (dropdown). Populated dynamically by GET `{ollama_url}/api/tags`. Fallback to text input + warning if Ollama unreachable.
- `Language` (dropdown). `Auto` / `English` / `Russian` / `Spanish` / `German` / … Default: `Auto`.

### Advanced (collapsed by default)
- `Custom system prompt for summarization` (textarea). Empty = built-in prompt (with ICL example).
- `Show status bar widget` (toggle). Default on.
- `Auto-open generated note` (toggle). Default off. If on, opens `.md` in active pane after `done`.

### System status (read-only diagnostics)
- ✓/✗ Swift audio-capture binary (path + version)
- ✓/✗ Python venv ready
- ✓/✗ Ollama reachable (URL + model count)
- ✓/✗ Whisper model cached locally
- "Run setup" button — re-trigger onboarding wizard (section 6).

---

## 3 — Recording lifecycle (state machine)

```
idle ─click─► recording ─click─► transcribing ─auto─► summarizing ─auto─► writing ─auto─► done ─3s─► idle
                                  │                    │                   │
                                  └────── error ◄──────┴───────────────────┘
```

States and behaviour:

| State                       | Status bar                                | Ribbon click behaviour                |
|-----------------------------|-------------------------------------------|---------------------------------------|
| `idle`                      | `🎙 quietnotes: idle`                     | → `recording`                         |
| `recording`                 | `🔴 quietnotes: recording HH:MM:SS`       | → `processing.transcribing`           |
| `processing.transcribing`   | `⏳ quietnotes: transcribing…`            | no-op (Notice: "Already processing")  |
| `processing.summarizing`    | `⏳ quietnotes: summarizing…`             | no-op                                 |
| `processing.writing`        | `⏳ quietnotes: writing markdown…`        | no-op                                 |
| `done`                      | `✓ quietnotes: saved <filename>`         | → `recording` (3 s after entering this state, auto-transitions to `idle`) |
| `error`                     | `❌ quietnotes: <short reason>` (sticky)  | → opens error modal; doesn't change state |

**Closing Obsidian during `recording`:** confirm dialog "Recording active — abort?". If yes → SIGTERM sidecar (wav lost). If no → cancel close.

**Closing Obsidian during any `processing.*`:** abort processing, transition to `error`. Wav is kept (recovery via `--from-wav` from `error` modal — see section 5).

**Re-entrancy:** plugin tracks `currentRecordingId`. If user somehow triggers `start_recording` twice (e.g. hotkey + ribbon), second invocation is rejected with Notice "Already recording".

---

## 4 — Sidecar communication

**Architecture:** spawn-per-recording (no long-running daemon). Plugin spawns `python sidecar.py --daemon …` when user clicks Start. Process lives for one recording's lifetime, then exits.

Rationale: cold-starting mlx-whisper costs ~10–30 s on first transcribe, but most users record meetings at intervals of hours, so the cold-start cost is acceptable and we save ~500 MB–1 GB RAM in idle.

### Spawn

When user clicks Start (idle → recording), plugin spawns:

```bash
<sidecar-venv>/bin/python <plugin-dir>/sidecar/sidecar.py \
    --daemon \
    --vault "<vault-absolute-path>" \
    --subfolder "<configured-subfolder>" \
    --whisper-model "<configured>" \
    --ollama-model "<configured>" \
    --ollama-url "<configured>" \
    --language "<configured-or-auto>"
```

(In daemon mode the `--record` and `--from-wav` flags are ignored; everything is driven by JSON-RPC over stdio.)

### Protocol — JSON Lines (NDJSON) over stdin/stdout

Each line on either pipe is one JSON object. No JSON-RPC 2.0 envelope — simpler.

**Plugin → sidecar** (one object per line, plugin writes to sidecar's stdin):

```json
{"id": "1", "method": "start_recording", "params": {}}
{"id": "2", "method": "stop_and_process", "params": {"recording_id": "abc"}}
{"id": "3", "method": "cancel", "params": {"recording_id": "abc"}}
```

**Sidecar → plugin** (sidecar writes JSON objects to stdout; logs go to stderr instead):

```json
{"id": "1", "result": {"recording_id": "abc", "started_at": "2026-05-25T17:30:00Z"}}
{"event": "stage", "stage": "transcribing"}
{"event": "stage", "stage": "summarizing"}
{"event": "stage", "stage": "writing"}
{"id": "2", "result": {"md_path": "<abs-path>", "duration_seconds": 312.4, "wav_path": null}}
```

Error envelope:

```json
{"id": "2", "error": {"code": "ollama_unreachable", "message": "Connection refused at http://localhost:11434", "recoverable": true}}
```

Plugin maintains a `Map<id, pendingPromise>` to correlate replies; events without `id` go to the status-bar updater.

### stderr — debug log

`[audio] recording …`, `[transcribe] done in 4.9 s …`, etc. Plugin forwards to Obsidian Developer Console via `console.log("[quietnotes] " + line)` so it's debuggable without leaving Obsidian.

### Sidecar-side changes

Add `--daemon` flag (parallel to the existing `--record`/`--from-wav` one-shot modes). Internally:

```python
if args.daemon:
    daemon_loop(args)  # reads stdin, dispatches methods, writes events
else:
    run_oneshot(args)  # current behaviour
```

`daemon_loop` reuses existing `audio.record()`, `transcribe.transcribe()`, `summarize.summarize()`, `output.write_markdown()` unchanged.

---

## 5 — Error handling

Errors are grouped into four categories with distinct UI treatment.

### A — Setup errors (one-time fix; usually first-run)

Detected at sidecar spawn or first method call. Plugin parses known error patterns from sidecar's stderr / error JSON.

| Error                                | Modal title                          | Action button                                  |
|--------------------------------------|--------------------------------------|------------------------------------------------|
| Python 3.10+ not found               | "Python missing"                     | "Open install guide" (link to project wiki)    |
| `audio-capture` binary missing       | "Helper binary missing"              | "Re-run setup" → triggers section 6 wizard     |
| Sidecar deps missing (`ModuleNotFound`) | "Sidecar dependencies missing"   | "Re-run setup"                                 |
| TCC: Screen Recording denied         | "Screen Recording permission needed" | "Open System Settings → Privacy"               |
| TCC: Microphone denied               | "Microphone permission needed"       | "Open System Settings → Privacy"               |

### B — Runtime errors (transient; retry possible)

| Error                                  | UI                                                                    |
|----------------------------------------|------------------------------------------------------------------------|
| Ollama unreachable                     | Modal "Ollama unreachable" + buttons "Start Ollama / Change URL / Retry" |
| Ollama model not installed             | Modal with `ollama pull <model>` command + "Copy to clipboard"        |
| Whisper model download failed          | Modal "Model download failed (HF rate-limited?)" + "Set HF_TOKEN / Retry" |
| Vault write failed                     | Modal "Cannot write to `<path>`: <reason>"                            |
| Microphone produced no audio           | Notice warning; `.md` is still written with `mic_silence: true` in frontmatter |

### C — Recovery (never lose a wav on processing failure)

When `processing.*` fails, sidecar does **not** delete the wav. Plugin's error modal includes:

> Recording saved at `<temp-wav-path>`. Re-run processing manually? [Retry]

The Retry button respawns sidecar with `--from-wav <path>` (one-shot mode) and re-runs transcribe + summarize + write. If `keep_audio` setting is on, wav is already in the vault and Retry uses that path.

### D — Internal errors (bugs)

JSON parse errors, unknown sidecar methods, protocol violations. Status bar: `❌ quietnotes: internal error`. Modal "Unexpected error" with stack trace + "Copy & open issue" button (deep link to GitHub Issues prefilled).

---

## 6 — Onboarding & distribution

Goal: user never touches the Terminal. Three dependency tiers with different strategies.

### 6.1 Swift `audio-capture` binary — fully automated

- **CI** (GitHub Actions): on each tag, runs on `macos-latest` (arm64). Builds `swift build -c release`, ad-hoc signs (proper Developer ID signing is a v0.1.x stretch), packages as `audio-capture-arm64.tar.gz` with SHA256 sidecar file. Uploaded to GitHub Release.
- **Plugin first-run**: detects binary absent → shows progress notice "Downloading helper binary…" → downloads + verifies SHA256 → extracts to `<vault>/.obsidian/plugins/quietnotes/bin/audio-capture` → `chmod +x`.
- **Failure path**: download fails (no network, GitHub down) → modal with manual instructions + "Retry".

### 6.2 Python sidecar — onboarding wizard

Sidecar deps (`mlx`, `numpy`, `scipy`, `torch`, `ollama`) weigh ~500 MB+. Bundling is overkill; PyInstaller with MLX is painful. Bootstrap at first run instead.

**Wizard flow (modal with progress bar, runs once):**

1. **Check `uv`.** If absent, prompt: "quietnotes uses `uv` to manage Python dependencies. Install it now?" → run `curl -LsSf https://astral.sh/uv/install.sh | sh` with **explicit consent click** (don't silently run a shell installer).
2. **Create venv** at `<plugin-dir>/sidecar-venv/` via `uv venv`.
3. **Copy sidecar source** from plugin .zip (bundled — files are tiny, ~10 KB total) into `<plugin-dir>/sidecar/`.
4. **Install deps:** `uv pip install -r requirements.txt` into the venv. Progress streamed to modal.
5. **Optional: prefetch Whisper model.** Checkbox "Download Whisper model now (~1.5 GB)" — default unchecked. If on, runs a tiny Python snippet that triggers HF download to cache. Skippable; otherwise model downloads on first transcribe.
6. **Optional: check Ollama.** GET `{ollama_url}/api/tags`. If unreachable → instructions modal (see 6.3). If reachable but `gemma3:4b` not in list → prompt "Pull gemma3:4b now? (~3.3 GB)" + Run.

State is persisted to `<plugin-dir>/.quietnotes-state.json` so wizard doesn't re-run.

### 6.3 Ollama — guided, not installed

We don't install Ollama for the user (Homebrew interactions are out of scope). Onboarding step shows:

> **Install Ollama:**
> ```
> brew install ollama
> ollama pull gemma3:4b
> ```
> [Copy commands]

Then "I've installed it" button → re-check.

### Constraints

- **All artifacts under `<vault>/.obsidian/plugins/quietnotes/`** — uninstalling the plugin via Obsidian's UI removes everything (the venv, the binary, the cached state). No system-wide leftovers.
- **`uv` installer requires user consent** — never silently `curl | sh`.
- **Whisper model prefetch is opt-in** — saves first-run time for users who want to start immediately.

---

## 7 — File output

### Vault layout

```
<vault>/
└── <subfolder>/                           # default: Meetings
    ├── 2026-05-25-1730.md
    ├── 2026-05-25-1845.md
    └── attachments/                       # only if keep_audio=true AND path is vault-relative
        ├── 2026-05-25-1730.wav
        └── 2026-05-25-1845.wav
```

If `audio_storage_path` is absolute (outside vault), wav is copied there but no embed link in `.md` (Obsidian only embeds vault-internal files).

### Filename collision

Existing file with same name → append `-N` suffix (`2026-05-25-1730-2.md`). Never overwrite.

### Frontmatter

```yaml
---
date: 2026-05-25
time: 17:30
duration_seconds: 312.4
language: ru
whisper_model: mlx-community/whisper-medium-mlx
ollama_model: gemma3:4b
source_audio: Meetings/attachments/2026-05-25-1730.wav  # optional, only when keep_audio
mic_silence: false                                       # optional, only on warning
tags: [meeting, quietnotes]
---
```

Renders as Obsidian Properties; usable in Dataview queries and global search.

### Body template (hard-coded for v0.1)

```markdown
# Meeting — 2026-05-25 17:30

## TL;DR
<one to two sentences from the LLM>

## Key points
- ...
- ...

## Tasks
- [ ] task 1
- [ ] task 2

## Recording                                  ← only when audio is in vault
![[Meetings/attachments/2026-05-25-1730.wav]]

## Transcript
<details>
<summary>Full transcript</summary>

<full text>

</details>
```

Obsidian renders the audio embed as an inline player.

---

## Open questions

- **Developer-ID code signing for `audio-capture`** — initially ad-hoc signed (Gatekeeper will warn on first run "macOS cannot verify…"). Mitigation: BRAT-install instructions explain right-click → Open the first time. Real signing requires Apple Developer Program ($99/yr) — v0.1.x stretch.
- **Plugin manifest version vs Swift binary version** — when plugin updates, should it re-download a fresh `audio-capture`? Likely yes if minor-version bumps; revisit when first update lands.
- **i18n in the plugin UI** — for now strings hard-coded in English (status bar, modals). Russian-only labels for now would alienate non-RU contributors; English-only is the safe default.

## Implementation notes

- Plugin scaffolding from [Obsidian's sample plugin](https://github.com/obsidianmd/obsidian-sample-plugin). TypeScript + esbuild bundling.
- Plugin sources live in `apps/plugin/`.
- The state machine in section 3 is small enough to write by hand; no XState dependency.
- `child_process.spawn` for sidecar with `{stdio: ['pipe', 'pipe', 'pipe']}` so stdin/stdout/stderr are separately addressable.
- Use Obsidian's `request()` helper (CORS-safe HTTP) for Ollama health checks and `ollama list` discovery in settings.

## References

- M1 spike result: [`docs/design-idea.md` — Implementation notes section](../design-idea.md#implementation-notes--m1-spike-closed-2026-05-18)
- Current sidecar entry point: [`apps/sidecar/sidecar.py`](../../apps/sidecar/sidecar.py)
- Current Swift CLI entry: [`apps/audio-capture/Sources/AudioCapture/AudioCaptureCommand.swift`](../../apps/audio-capture/Sources/AudioCapture/AudioCaptureCommand.swift)
- Obsidian Plugin API: https://docs.obsidian.md/Plugins
- ScreenCaptureKit API used in M1: https://developer.apple.com/documentation/screencapturekit
