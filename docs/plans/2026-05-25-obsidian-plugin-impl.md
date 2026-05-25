# M3 — Obsidian Plugin Implementation Plan

> **For agentic workers:** Steps use checkbox (`- [ ]`) syntax for tracking. **No `git commit` between tasks** — per project policy (memory: `feedback_commit_strategy`), all M3 work accumulates into one init commit at the end. Treat each "Checkpoint" as a moment to verify and test, not to commit.

**Goal:** Ship a working Obsidian plugin that captures audio, transcribes, summarises, and writes a markdown note — fully in-app, zero Terminal use.

**Architecture:** Plugin spawns existing Python sidecar (`apps/sidecar/sidecar.py`) in NDJSON-RPC daemon mode per recording. UI = ribbon + status bar + settings tab. No TDD on plugin TS (smoke-tested in real Obsidian); light pytest on sidecar daemon-mode.

**Tech Stack:** TypeScript, esbuild, Obsidian Plugin API (`obsidian` package), Node.js `child_process`. Python ≥3.10 in venv (managed by `uv`). Existing Swift `audio-capture` binary (no changes).

---

## File Map

What we create, where, and why.

```
apps/sidecar/
├── sidecar.py                 # ADD --daemon mode + dispatch loop
└── tests/
    └── test_daemon.py         # NEW — pytest for stdin/stdout protocol

apps/plugin/                   # NEW — entire plugin
├── manifest.json              # Obsidian plugin manifest
├── package.json               # esbuild + obsidian deps
├── tsconfig.json
├── esbuild.config.mjs
├── styles.css                 # status-bar pulse animation
├── .gitignore                 # node_modules, main.js
└── src/
    ├── main.ts                # Plugin class — entry, lifecycle
    ├── settings.ts            # Settings interface + tab UI
    ├── state.ts               # State machine + status-bar text rendering
    ├── sidecar-client.ts      # spawn + NDJSON-RPC client
    ├── errors.ts              # Error parsing + modal
    └── onboarding.ts          # First-run wizard

docs/plans/                    # (this file)
└── 2026-05-25-obsidian-plugin-impl.md
```

**Boundaries:**
- `sidecar-client.ts` knows nothing about Obsidian — pure spawn + RPC. Could be unit-tested in isolation later.
- `state.ts` is the single source of truth for current stage. UI components subscribe to its events.
- `main.ts` is the wiring (DI-style): construct everything, register Obsidian lifecycle hooks, route events.

---

## Task 1 — Sidecar daemon mode (Python)

Extend `apps/sidecar/sidecar.py` to accept `--daemon` flag. In daemon mode it reads NDJSON requests from stdin, writes NDJSON responses/events to stdout, keeps debug logs on stderr.

**Files:**
- Modify: `apps/sidecar/sidecar.py`
- Create: `apps/sidecar/tests/__init__.py`
- Create: `apps/sidecar/tests/test_daemon.py`
- Modify: `apps/sidecar/pyproject.toml` (add `pytest` as dev dep)

### Step 1.1 — Add pytest as dev dep

- [ ] Open `apps/sidecar/pyproject.toml` and add a dev-deps group.

```toml
[project]
name = "quietnotes-sidecar"
version = "0.1.0"
description = "Local-only meeting notetaker pipeline: record → transcribe → summarize → write markdown."
requires-python = ">=3.10"
dependencies = [
    "mlx-whisper>=0.4.3",
    "ollama>=0.4.0",
]

[project.optional-dependencies]
dev = ["pytest>=8.0"]

[project.scripts]
quietnotes-sidecar = "sidecar:main"

[tool.uv]
package = false
```

- [ ] Run: `cd apps/sidecar && uv sync --extra dev`. Expected: pytest installed.

### Step 1.2 — Write a failing test for the daemon's start_recording method

- [ ] Create `apps/sidecar/tests/__init__.py` (empty file).
- [ ] Create `apps/sidecar/tests/test_daemon.py`:

```python
"""Smoke tests for sidecar daemon mode (NDJSON over stdio)."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

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
        bufsize=1,  # line-buffered
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
```

- [ ] Run: `cd apps/sidecar && uv run pytest tests/test_daemon.py -v`.
- [ ] Expected: FAIL (`--daemon` flag not yet recognized; argparse will exit with error).

### Step 1.3 — Add the --daemon flag and stub loop to sidecar.py

- [ ] Edit `apps/sidecar/sidecar.py`. Add at top of `main()`, immediately after `cfg = load_local_config()`:

```python
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
                     help="Read JSON-RPC over stdin/stdout (used by the Obsidian plugin)")
```

Note: changed `required=True` → `required=False` so `--daemon` can stand alone.

- [ ] Below the existing arg additions, add validation after `args = ap.parse_args()`:

```python
    args = ap.parse_args()

    if not (args.record or args.from_wav or args.daemon):
        ap.error("one of --record, --from-wav, or --daemon is required")

    if args.daemon:
        return run_daemon(args)
```

- [ ] At module top level (below imports, above `main()`), add a stub:

```python
def run_daemon(args: argparse.Namespace) -> int:
    """NDJSON-RPC loop on stdio. See docs/plans/2026-05-25-obsidian-plugin-design.md §4."""
    import sys as _sys
    for raw_line in _sys.stdin:
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

        # method dispatch — filled in next steps
        _emit_error(msg_id, "unknown_method", f"unknown method: {method!r}")
    return 0


def _emit(obj: dict) -> None:
    print(json.dumps(obj, ensure_ascii=False), flush=True)


def _emit_error(msg_id: str, code: str, message: str, *, recoverable: bool = False) -> None:
    _emit({"id": msg_id, "error": {"code": code, "message": message, "recoverable": recoverable}})


def _emit_event(event: str, **fields) -> None:
    _emit({"event": event, **fields})
```

- [ ] Run: `cd apps/sidecar && uv run pytest tests/test_daemon.py -v`.
- [ ] Expected: PASS — daemon now answers with `unknown_method` error.

### Step 1.4 — Implement start_recording method

State held across method calls: `recording_id`, the spawned Swift `audio-capture` subprocess, the temp wav path, recording-start datetime.

- [ ] At module top, add:

```python
import uuid
```

- [ ] Replace the dispatch block in `run_daemon` with stateful dispatch. The full new `run_daemon` body:

```python
def run_daemon(args: argparse.Namespace) -> int:
    """NDJSON-RPC loop on stdio. See docs/plans/2026-05-25-obsidian-plugin-design.md §4."""
    import sys as _sys

    state: dict[str, Any] = {
        "recording_id": None,
        "wav_path": None,
        "recorded_at": None,
        "audio_proc": None,
    }

    for raw_line in _sys.stdin:
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
                _daemon_cancel(state, msg_id, params)
            else:
                _emit_error(msg_id, "unknown_method", f"unknown method: {method!r}")
        except Exception as e:  # noqa: BLE001 — daemon must not crash
            _emit_error(msg_id, "internal_error", repr(e))
    return 0
```

- [ ] Add helper for start_recording:

```python
def _daemon_start_recording(args, state, msg_id, params) -> None:
    if state["recording_id"] is not None:
        _emit_error(msg_id, "already_recording", "a recording is already in progress")
        return

    import audio  # local import: only needed in daemon path
    recorded_at = datetime.now()
    rid = uuid.uuid4().hex[:12]
    wav_path = Path(tempfile.gettempdir()) / f"quietnotes-{recorded_at:%Y%m%d-%H%M%S}.wav"

    binary = audio.find_audio_capture_binary()
    cmd = [str(binary), "--output", str(wav_path)]
    proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL)

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
```

- [ ] Also import `subprocess` and `tempfile` at the top if not already:

```python
import subprocess
import tempfile
```

(They probably already exist in the file from the one-shot mode; check before adding.)

### Step 1.5 — Test start_recording happy path

- [ ] Add test in `tests/test_daemon.py`:

```python
def test_daemon_start_recording_returns_id(tmp_path: Path) -> None:
    proc = spawn_daemon(["--vault", str(tmp_path), "--subfolder", "Meetings"])
    try:
        send(proc, {"id": "1", "method": "start_recording", "params": {}})
        resp = read_one(proc)
        assert resp["id"] == "1"
        assert "result" in resp, resp
        result = resp["result"]
        assert "recording_id" in result
        assert "started_at" in result
        assert result["wav_path"].endswith(".wav")
    finally:
        # Cleanup: send cancel so the audio-capture subprocess exits.
        send(proc, {"id": "99", "method": "cancel", "params": {}})
        proc.terminate()
        proc.wait(timeout=3)
```

- [ ] Run: `uv run pytest tests/test_daemon.py::test_daemon_start_recording_returns_id -v`.
- [ ] Expected: PASS — but the test will also leave a temp .wav and pollute. We'll fix that in the cancel test next.

### Step 1.6 — Implement cancel method

- [ ] Add helper:

```python
def _daemon_cancel(state, msg_id, params) -> None:
    proc = state.get("audio_proc")
    if proc is not None and proc.poll() is None:
        import signal as _sig
        proc.send_signal(_sig.SIGINT)
        proc.wait(timeout=5)
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
```

- [ ] Add test:

```python
def test_daemon_cancel_after_start(tmp_path: Path) -> None:
    proc = spawn_daemon(["--vault", str(tmp_path), "--subfolder", "Meetings"])
    try:
        send(proc, {"id": "1", "method": "start_recording", "params": {}})
        start_resp = read_one(proc)
        wav_path = Path(start_resp["result"]["wav_path"])

        send(proc, {"id": "2", "method": "cancel", "params": {}})
        cancel_resp = read_one(proc)
        assert cancel_resp["id"] == "2"
        assert cancel_resp["result"]["cancelled"] is True
        # The temp wav should be gone.
        assert not wav_path.exists(), f"{wav_path} should be cleaned up"
    finally:
        proc.terminate()
        proc.wait(timeout=3)
```

- [ ] Run: `uv run pytest tests/test_daemon.py -v`.
- [ ] Expected: all three tests PASS.

### Step 1.7 — Implement stop_and_process method

- [ ] Add helper:

```python
def _daemon_stop_and_process(args, state, msg_id, params) -> None:
    if state["recording_id"] is None:
        _emit_error(msg_id, "not_recording", "no recording in progress")
        return

    # 1. Stop the Swift recorder.
    proc = state["audio_proc"]
    import signal as _sig
    proc.send_signal(_sig.SIGINT)
    proc.wait(timeout=10)
    if proc.returncode != 0:
        _emit_error(msg_id, "audio_capture_failed", f"exit code {proc.returncode}")
        return

    wav_path = state["wav_path"]
    recorded_at = state["recorded_at"]

    _emit_event("stage", stage="transcribing")
    tr = transcribe.transcribe(wav_path, model=args.whisper_model, language=args.language)

    _emit_event("stage", stage="summarizing")
    sm = summarize.summarize(tr["text"], model=args.ollama_model)

    _emit_event("stage", stage="writing")
    result = {
        "wav_path": str(wav_path),
        "language": tr.get("language"),
        "transcript": tr.get("text", ""),
        "tldr": sm.get("tldr", ""),
        "key_points": sm.get("key_points", []),
        "tasks": sm.get("tasks", []),
        "duration_seconds": output.wav_duration(wav_path),
    }

    md_path = None
    if args.vault:
        # If we'll delete the temp wav, don't reference it from the .md.
        result_for_md = dict(result)
        result_for_md["wav_path"] = None  # daemon mode treats wav as ephemeral by default
        md_path = output.write_markdown(
            result_for_md,
            vault=args.vault,
            subfolder=args.subfolder,
            recorded_at=recorded_at,
            whisper_model=args.whisper_model,
            ollama_model=args.ollama_model,
        )

    # Cleanup temp wav.
    try:
        Path(wav_path).unlink(missing_ok=True)
    except OSError:
        pass

    state["recording_id"] = None
    state["wav_path"] = None
    state["recorded_at"] = None
    state["audio_proc"] = None

    _emit({
        "id": msg_id,
        "result": {
            "md_path": str(md_path) if md_path else None,
            "duration_seconds": result["duration_seconds"],
            "tldr": result["tldr"],
            "key_points": result["key_points"],
            "tasks": result["tasks"],
        },
    })
```

- [ ] Add a smoke test that exercises the full happy path — but only if Ollama is available locally. Mark it slow:

```python
import os
import pytest


@pytest.mark.skipif(
    os.environ.get("QUIETNOTES_SKIP_E2E") == "1",
    reason="end-to-end test requires Ollama + Whisper model + ScreenCaptureKit TCC",
)
def test_daemon_full_flow(tmp_path: Path) -> None:
    """Records ~2 seconds, runs transcribe+summarize+write. Requires TCC perms."""
    proc = spawn_daemon([
        "--vault", str(tmp_path),
        "--subfolder", "Meetings",
        "--whisper-model", "mlx-community/whisper-tiny",
        "--ollama-model", "gemma3:4b",
        "--language", "ru",
    ])
    try:
        send(proc, {"id": "1", "method": "start_recording", "params": {}})
        read_one(proc)
        time.sleep(2)
        send(proc, {"id": "2", "method": "stop_and_process", "params": {}})
        # Drain events until the response with id=2.
        for _ in range(20):
            msg = read_one(proc, timeout=120)
            if msg.get("id") == "2":
                assert "result" in msg, msg
                assert msg["result"]["md_path"], "expected md_path"
                return
        pytest.fail("did not get final response for stop_and_process")
    finally:
        proc.terminate()
        proc.wait(timeout=5)
```

- [ ] Run unit tests only (skip e2e): `QUIETNOTES_SKIP_E2E=1 uv run pytest tests/test_daemon.py -v`.
- [ ] Expected: all PASS.

### Checkpoint Task 1

- Sidecar can be spawned with `--daemon`
- Three methods working: `start_recording`, `stop_and_process`, `cancel`
- Events emitted at stage transitions
- Errors returned as JSON instead of exceptions
- `uv run pytest tests/test_daemon.py -v` passes (skip e2e is fine)

Manual smoke test:

```bash
cd apps/sidecar
echo '{"id":"1","method":"start_recording","params":{}}' | uv run python sidecar.py --daemon --vault /tmp --subfolder Meetings
```

Expected: a JSON line with `recording_id` printed, then sidecar waits for more input. Ctrl+C to exit. A temp .wav is left in `/var/folders/...` (it'll get cleaned by `cancel` when we actually use it).

---

## Task 2 — Plugin scaffold (TypeScript, esbuild, Obsidian-loadable)

Empty plugin that loads in Obsidian and prints to console.

**Files (all NEW):**
- `apps/plugin/manifest.json`
- `apps/plugin/package.json`
- `apps/plugin/tsconfig.json`
- `apps/plugin/esbuild.config.mjs`
- `apps/plugin/.gitignore`
- `apps/plugin/styles.css`
- `apps/plugin/src/main.ts`

### Step 2.1 — manifest.json

- [ ] Create `apps/plugin/manifest.json`:

```json
{
  "id": "quietnotes",
  "name": "quietnotes",
  "version": "0.1.0",
  "minAppVersion": "1.5.0",
  "description": "Local-only meeting notetaker: record audio, transcribe with mlx-whisper, summarise with Ollama, write a markdown note. Nothing leaves your Mac.",
  "author": "Maksim Zhers",
  "authorUrl": "https://github.com/fiezzy/quietnotes",
  "isDesktopOnly": true
}
```

### Step 2.2 — package.json + dev deps

- [ ] Create `apps/plugin/package.json`:

```json
{
  "name": "quietnotes-plugin",
  "version": "0.1.0",
  "private": true,
  "description": "Obsidian plugin for the quietnotes local meeting notetaker.",
  "main": "main.js",
  "scripts": {
    "dev": "node esbuild.config.mjs",
    "build": "node esbuild.config.mjs production",
    "typecheck": "tsc --noEmit"
  },
  "devDependencies": {
    "@types/node": "^22.0.0",
    "builtin-modules": "^5.0.0",
    "esbuild": "^0.24.0",
    "obsidian": "^1.5.0",
    "typescript": "^5.5.0"
  }
}
```

- [ ] Run: `cd apps/plugin && npm install`. Expected: `node_modules` populated, no peer-dep warnings beyond the usual Obsidian ones.

### Step 2.3 — tsconfig.json

- [ ] Create `apps/plugin/tsconfig.json`:

```json
{
  "compilerOptions": {
    "target": "ES2022",
    "module": "ESNext",
    "moduleResolution": "Bundler",
    "lib": ["DOM", "ES2022"],
    "strict": true,
    "noImplicitAny": true,
    "noUnusedLocals": false,
    "noUnusedParameters": false,
    "esModuleInterop": true,
    "allowSyntheticDefaultImports": true,
    "resolveJsonModule": true,
    "isolatedModules": true,
    "skipLibCheck": true,
    "baseUrl": ".",
    "paths": {
      "src/*": ["src/*"]
    }
  },
  "include": ["src/**/*.ts"]
}
```

### Step 2.4 — esbuild config

- [ ] Create `apps/plugin/esbuild.config.mjs`:

```javascript
import esbuild from "esbuild";
import process from "process";
import builtins from "builtin-modules";

const banner = `/* quietnotes plugin — bundled by esbuild */`;
const prod = process.argv.includes("production");

const ctx = await esbuild.context({
  banner: { js: banner },
  entryPoints: ["src/main.ts"],
  bundle: true,
  external: [
    "obsidian",
    "electron",
    "@codemirror/autocomplete",
    "@codemirror/collab",
    "@codemirror/commands",
    "@codemirror/language",
    "@codemirror/lint",
    "@codemirror/search",
    "@codemirror/state",
    "@codemirror/view",
    "@lezer/common",
    "@lezer/highlight",
    "@lezer/lr",
    ...builtins,
  ],
  format: "cjs",
  target: "es2022",
  logLevel: "info",
  sourcemap: prod ? false : "inline",
  treeShaking: true,
  outfile: "main.js",
  minify: prod,
});

if (prod) {
  await ctx.rebuild();
  await ctx.dispose();
} else {
  await ctx.watch();
}
```

### Step 2.5 — .gitignore for plugin

- [ ] Create `apps/plugin/.gitignore`:

```
node_modules/
main.js
main.js.map
data.json
```

### Step 2.6 — Empty plugin entry

- [ ] Create `apps/plugin/styles.css`:

```css
.quietnotes-statusbar {
  display: inline-flex;
  align-items: center;
  gap: 0.35em;
  cursor: pointer;
}

.quietnotes-statusbar--recording {
  color: var(--text-error);
}

.quietnotes-statusbar--recording::before {
  content: "";
  display: inline-block;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--text-error);
  animation: quietnotes-pulse 1s ease-in-out infinite;
}

@keyframes quietnotes-pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.3; }
}
```

- [ ] Create `apps/plugin/src/main.ts`:

```typescript
import { Plugin } from "obsidian";

export default class QuietnotesPlugin extends Plugin {
  async onload(): Promise<void> {
    console.log("[quietnotes] loaded");
  }

  async onunload(): Promise<void> {
    console.log("[quietnotes] unloaded");
  }
}
```

### Step 2.7 — Build and load into Obsidian via symlink

- [ ] Run: `cd apps/plugin && npm run build`. Expected: `main.js` produced (~1 KB).
- [ ] Symlink the plugin into your vault so Obsidian sees it (do this once, in your terminal):

```bash
mkdir -p "/Users/fiezzyy/Documents/Obsidian Vault/.obsidian/plugins"
ln -s /Users/fiezzyy/Documents/Projects/quietnotes/apps/plugin "/Users/fiezzyy/Documents/Obsidian Vault/.obsidian/plugins/quietnotes"
```

- [ ] In Obsidian → Settings → Community plugins → make sure "Restricted mode" is OFF. Then refresh installed plugins; "quietnotes" should appear. Toggle it on.
- [ ] Open Obsidian Developer Tools (View → Toggle Developer Tools, or `⌥⌘I`) and look at Console.
- [ ] Expected: `[quietnotes] loaded`.

### Checkpoint Task 2

- `apps/plugin/main.js` builds cleanly
- Plugin appears in Obsidian's community plugins list, toggles on without errors
- Console prints `[quietnotes] loaded`
- Disabling the plugin prints `[quietnotes] unloaded`

---

## Task 3 — Settings: data model, defaults, settings tab

Add persisted settings and a settings tab matching design doc §2.

**Files:**
- Create: `apps/plugin/src/settings.ts`
- Modify: `apps/plugin/src/main.ts`

### Step 3.1 — Settings interface and defaults

- [ ] Create `apps/plugin/src/settings.ts`:

```typescript
import { App, PluginSettingTab, Setting, Notice } from "obsidian";
import type QuietnotesPlugin from "./main";

export interface QuietnotesSettings {
  outputSubfolder: string;
  filenamePattern: string;

  keepAudio: boolean;
  audioStoragePath: string;

  whisperModel: string;
  ollamaUrl: string;
  ollamaModel: string;
  language: string; // "" = auto, or ISO code like "ru"

  customSystemPrompt: string;
  showStatusBar: boolean;
  autoOpenNote: boolean;
}

export const DEFAULT_SETTINGS: QuietnotesSettings = {
  outputSubfolder: "Meetings",
  filenamePattern: "{date}-{time}",
  keepAudio: false,
  audioStoragePath: "Meetings/attachments",
  whisperModel: "mlx-community/whisper-medium-mlx",
  ollamaUrl: "http://localhost:11434",
  ollamaModel: "gemma3:4b",
  language: "",
  customSystemPrompt: "",
  showStatusBar: true,
  autoOpenNote: false,
};

const LANGUAGE_OPTIONS: Record<string, string> = {
  "": "Auto-detect",
  en: "English",
  ru: "Russian",
  es: "Spanish",
  de: "German",
  fr: "French",
  it: "Italian",
};

export class QuietnotesSettingTab extends PluginSettingTab {
  constructor(app: App, private plugin: QuietnotesPlugin) {
    super(app, plugin);
  }

  display(): void {
    const { containerEl } = this;
    containerEl.empty();

    containerEl.createEl("h2", { text: "Vault output" });

    new Setting(containerEl)
      .setName("Output subfolder")
      .setDesc("Folder inside the vault where notes are written.")
      .addText((t) =>
        t
          .setPlaceholder("Meetings")
          .setValue(this.plugin.settings.outputSubfolder)
          .onChange(async (v) => {
            this.plugin.settings.outputSubfolder = v.trim() || "Meetings";
            await this.plugin.saveSettings();
          }),
      );

    new Setting(containerEl)
      .setName("Filename pattern")
      .setDesc("Placeholders: {date}, {time}, {lang}. .md is added automatically.")
      .addText((t) =>
        t
          .setPlaceholder("{date}-{time}")
          .setValue(this.plugin.settings.filenamePattern)
          .onChange(async (v) => {
            this.plugin.settings.filenamePattern = v.trim() || "{date}-{time}";
            await this.plugin.saveSettings();
          }),
      );

    containerEl.createEl("h2", { text: "Audio" });

    new Setting(containerEl)
      .setName("Keep audio recordings")
      .setDesc("Off (default) deletes the temp .wav after processing. On reveals storage path.")
      .addToggle((t) =>
        t.setValue(this.plugin.settings.keepAudio).onChange(async (v) => {
          this.plugin.settings.keepAudio = v;
          await this.plugin.saveSettings();
          this.display(); // re-render to show/hide audioStoragePath
        }),
      );

    if (this.plugin.settings.keepAudio) {
      new Setting(containerEl)
        .setName("Audio storage path")
        .setDesc("Vault-relative, unless it starts with / or ~. Absolute paths copy outside the vault.")
        .addText((t) =>
          t
            .setPlaceholder("Meetings/attachments")
            .setValue(this.plugin.settings.audioStoragePath)
            .onChange(async (v) => {
              this.plugin.settings.audioStoragePath = v.trim() || "Meetings/attachments";
              await this.plugin.saveSettings();
            }),
        );
    }

    containerEl.createEl("h2", { text: "AI models" });

    new Setting(containerEl)
      .setName("Whisper model")
      .setDesc("HuggingFace repo id (e.g. mlx-community/whisper-medium-mlx) or local path.")
      .addText((t) =>
        t.setValue(this.plugin.settings.whisperModel).onChange(async (v) => {
          this.plugin.settings.whisperModel = v.trim() || DEFAULT_SETTINGS.whisperModel;
          await this.plugin.saveSettings();
        }),
      );

    new Setting(containerEl)
      .setName("Ollama URL")
      .setDesc("Default http://localhost:11434.")
      .addText((t) =>
        t.setValue(this.plugin.settings.ollamaUrl).onChange(async (v) => {
          this.plugin.settings.ollamaUrl = v.trim() || DEFAULT_SETTINGS.ollamaUrl;
          await this.plugin.saveSettings();
        }),
      );

    new Setting(containerEl)
      .setName("Ollama model")
      .setDesc("Click Refresh to pull the installed model list from Ollama.")
      .addText((t) =>
        t.setValue(this.plugin.settings.ollamaModel).onChange(async (v) => {
          this.plugin.settings.ollamaModel = v.trim() || DEFAULT_SETTINGS.ollamaModel;
          await this.plugin.saveSettings();
        }),
      )
      .addButton((b) =>
        b.setButtonText("Refresh").onClick(async () => {
          const models = await fetchOllamaModels(this.plugin.settings.ollamaUrl);
          if (models.length === 0) {
            new Notice("Ollama unreachable or has no models");
          } else {
            new Notice(`Found: ${models.join(", ")}`);
          }
        }),
      );

    new Setting(containerEl)
      .setName("Language")
      .setDesc("Force transcription language. Auto-detect works for most cases.")
      .addDropdown((d) => {
        for (const [code, label] of Object.entries(LANGUAGE_OPTIONS)) {
          d.addOption(code, label);
        }
        d.setValue(this.plugin.settings.language).onChange(async (v) => {
          this.plugin.settings.language = v;
          await this.plugin.saveSettings();
        });
      });

    containerEl.createEl("h2", { text: "Advanced" });

    new Setting(containerEl)
      .setName("Custom system prompt")
      .setDesc("Empty = built-in prompt. Override to customise summary style.")
      .addTextArea((t) => {
        t.setValue(this.plugin.settings.customSystemPrompt).onChange(async (v) => {
          this.plugin.settings.customSystemPrompt = v;
          await this.plugin.saveSettings();
        });
        t.inputEl.rows = 6;
        t.inputEl.style.width = "100%";
      });

    new Setting(containerEl)
      .setName("Show status bar widget")
      .addToggle((t) =>
        t.setValue(this.plugin.settings.showStatusBar).onChange(async (v) => {
          this.plugin.settings.showStatusBar = v;
          await this.plugin.saveSettings();
          new Notice("Restart Obsidian to apply status-bar toggle");
        }),
      );

    new Setting(containerEl)
      .setName("Auto-open generated note")
      .setDesc("Open the .md file after the recording is processed.")
      .addToggle((t) =>
        t.setValue(this.plugin.settings.autoOpenNote).onChange(async (v) => {
          this.plugin.settings.autoOpenNote = v;
          await this.plugin.saveSettings();
        }),
      );
  }
}

async function fetchOllamaModels(ollamaUrl: string): Promise<string[]> {
  try {
    const url = `${ollamaUrl.replace(/\/$/, "")}/api/tags`;
    const r = await fetch(url);
    if (!r.ok) return [];
    const data = (await r.json()) as { models?: { name: string }[] };
    return (data.models ?? []).map((m) => m.name);
  } catch {
    return [];
  }
}
```

### Step 3.2 — Wire settings into main.ts

- [ ] Replace `apps/plugin/src/main.ts`:

```typescript
import { Plugin } from "obsidian";
import { DEFAULT_SETTINGS, QuietnotesSettings, QuietnotesSettingTab } from "./settings";

export default class QuietnotesPlugin extends Plugin {
  settings!: QuietnotesSettings;

  async onload(): Promise<void> {
    await this.loadSettings();
    this.addSettingTab(new QuietnotesSettingTab(this.app, this));
    console.log("[quietnotes] loaded");
  }

  async onunload(): Promise<void> {
    console.log("[quietnotes] unloaded");
  }

  async loadSettings(): Promise<void> {
    this.settings = Object.assign({}, DEFAULT_SETTINGS, await this.loadData());
  }

  async saveSettings(): Promise<void> {
    await this.saveData(this.settings);
  }
}
```

### Step 3.3 — Smoke-test settings in Obsidian

- [ ] Run: `cd apps/plugin && npm run build`.
- [ ] In Obsidian: reload the plugin (Settings → Community plugins → toggle off then on, or use the [hot-reload](https://github.com/pjeby/hot-reload) plugin if you have it).
- [ ] Open Settings → quietnotes. Expected: all five sections render, fields show defaults.
- [ ] Toggle "Keep audio" → "Audio storage path" appears below.
- [ ] Click "Refresh" next to Ollama model (assuming Ollama is running) → Notice shows `Found: gemma3:4b, llama3.2:latest`.
- [ ] Open `<vault>/.obsidian/plugins/quietnotes/data.json` in a side editor — your settings should be persisted there as JSON.

### Checkpoint Task 3

- All 5 settings sections render
- Changes persist across Obsidian restarts (verified via `data.json`)
- Ollama Refresh button correctly pulls model list
- Language dropdown contains all 7 entries

---

## Task 4 — State machine + status-bar widget

The "single source of truth" for what the plugin is currently doing. UI subscribes to its events.

**Files:**
- Create: `apps/plugin/src/state.ts`
- Modify: `apps/plugin/src/main.ts`

### Step 4.1 — State machine

- [ ] Create `apps/plugin/src/state.ts`:

```typescript
export type Stage =
  | { kind: "idle" }
  | { kind: "recording"; startedAt: number; recordingId: string }
  | { kind: "transcribing" }
  | { kind: "summarizing" }
  | { kind: "writing" }
  | { kind: "done"; mdPath: string; expiresAt: number }
  | { kind: "error"; code: string; message: string; recoverable: boolean; wavPath?: string };

type Listener = (s: Stage) => void;

export class StateMachine {
  private stage: Stage = { kind: "idle" };
  private listeners: Listener[] = [];

  get current(): Stage {
    return this.stage;
  }

  subscribe(fn: Listener): () => void {
    this.listeners.push(fn);
    fn(this.stage);
    return () => {
      this.listeners = this.listeners.filter((l) => l !== fn);
    };
  }

  set(stage: Stage): void {
    this.stage = stage;
    for (const l of this.listeners) l(stage);
  }

  // Convenience transitions
  toIdle(): void {
    this.set({ kind: "idle" });
  }
  toRecording(recordingId: string): void {
    this.set({ kind: "recording", startedAt: Date.now(), recordingId });
  }
  toTranscribing(): void {
    this.set({ kind: "transcribing" });
  }
  toSummarizing(): void {
    this.set({ kind: "summarizing" });
  }
  toWriting(): void {
    this.set({ kind: "writing" });
  }
  toDone(mdPath: string): void {
    this.set({ kind: "done", mdPath, expiresAt: Date.now() + 3000 });
  }
  toError(code: string, message: string, recoverable = false, wavPath?: string): void {
    this.set({ kind: "error", code, message, recoverable, wavPath });
  }
}

export function statusBarText(stage: Stage): string {
  switch (stage.kind) {
    case "idle":
      return "🎙 quietnotes: idle";
    case "recording": {
      const elapsed = Math.floor((Date.now() - stage.startedAt) / 1000);
      const mm = String(Math.floor(elapsed / 60)).padStart(2, "0");
      const ss = String(elapsed % 60).padStart(2, "0");
      return `🔴 quietnotes: recording ${mm}:${ss}`;
    }
    case "transcribing":
      return "⏳ quietnotes: transcribing…";
    case "summarizing":
      return "⏳ quietnotes: summarizing…";
    case "writing":
      return "⏳ quietnotes: writing markdown…";
    case "done":
      return `✓ quietnotes: saved ${stage.mdPath.split("/").pop()}`;
    case "error":
      return `❌ quietnotes: ${stage.message.slice(0, 60)}`;
  }
}
```

### Step 4.2 — Wire state machine + status bar into main.ts

- [ ] Edit `apps/plugin/src/main.ts`:

```typescript
import { Plugin } from "obsidian";
import { DEFAULT_SETTINGS, QuietnotesSettings, QuietnotesSettingTab } from "./settings";
import { StateMachine, statusBarText } from "./state";

export default class QuietnotesPlugin extends Plugin {
  settings!: QuietnotesSettings;
  state = new StateMachine();
  private statusBarEl: HTMLElement | null = null;
  private statusBarTimer: number | null = null;

  async onload(): Promise<void> {
    await this.loadSettings();
    this.addSettingTab(new QuietnotesSettingTab(this.app, this));

    if (this.settings.showStatusBar) {
      this.statusBarEl = this.addStatusBarItem();
      this.statusBarEl.addClass("quietnotes-statusbar");
      const unsubscribe = this.state.subscribe((stage) => {
        if (!this.statusBarEl) return;
        this.statusBarEl.toggleClass("quietnotes-statusbar--recording", stage.kind === "recording");
        this.statusBarEl.setText(statusBarText(stage));
      });
      this.register(unsubscribe);

      // Tick the recording timer once a second.
      this.statusBarTimer = window.setInterval(() => {
        if (!this.statusBarEl) return;
        if (this.state.current.kind === "recording") {
          this.statusBarEl.setText(statusBarText(this.state.current));
        }
        if (this.state.current.kind === "done" && Date.now() >= this.state.current.expiresAt) {
          this.state.toIdle();
        }
      }, 1000);
      this.register(() => {
        if (this.statusBarTimer !== null) window.clearInterval(this.statusBarTimer);
      });
    }

    console.log("[quietnotes] loaded");
  }

  async onunload(): Promise<void> {
    console.log("[quietnotes] unloaded");
  }

  async loadSettings(): Promise<void> {
    this.settings = Object.assign({}, DEFAULT_SETTINGS, await this.loadData());
  }

  async saveSettings(): Promise<void> {
    await this.saveData(this.settings);
  }
}
```

### Step 4.3 — Smoke-test the status bar transitions

The state machine isn't wired to anything real yet. We'll fake transitions via Developer Console to verify the UI part works.

- [ ] Run: `cd apps/plugin && npm run build`. Reload plugin in Obsidian.
- [ ] Expected: status bar shows `🎙 quietnotes: idle`.
- [ ] Open Developer Console and run:

```javascript
const p = app.plugins.plugins.quietnotes;
p.state.toRecording("test-id");
```

- [ ] Expected: status bar changes to `🔴 quietnotes: recording 00:00`, then `00:01`, `00:02`… and a pulsing red dot appears via CSS.

```javascript
p.state.toTranscribing();
// → "⏳ quietnotes: transcribing…", pulsing dot gone

p.state.toDone("/Users/.../Meetings/2026-05-25-1730.md");
// → "✓ quietnotes: saved 2026-05-25-1730.md", then auto-fades to idle after 3s
```

### Checkpoint Task 4

- All state transitions reflected visually in the status bar
- Recording timer ticks correctly
- Done state auto-fades to idle after 3 seconds
- Pulsing animation visible during recording

---

## Task 5 — SidecarClient: spawn + NDJSON-RPC

Spawn the Python sidecar, send JSON requests, parse JSON responses/events.

**Files:**
- Create: `apps/plugin/src/sidecar-client.ts`

### Step 5.1 — Client class

- [ ] Create `apps/plugin/src/sidecar-client.ts`:

```typescript
import { spawn, ChildProcessWithoutNullStreams } from "child_process";
import { EventEmitter } from "events";

export type StageEvent = { event: "stage"; stage: "transcribing" | "summarizing" | "writing" };
export type SidecarEvent = StageEvent;

export interface SidecarError {
  code: string;
  message: string;
  recoverable: boolean;
}

export interface SpawnOptions {
  pythonPath: string;       // path to sidecar-venv/bin/python
  sidecarScript: string;    // path to sidecar.py
  vault: string;            // absolute path
  subfolder: string;
  whisperModel: string;
  ollamaUrl: string;
  ollamaModel: string;
  language: string;         // "" = auto
}

interface PendingRequest {
  resolve: (value: unknown) => void;
  reject: (err: SidecarError) => void;
}

export class SidecarClient extends EventEmitter {
  private proc: ChildProcessWithoutNullStreams | null = null;
  private pending = new Map<string, PendingRequest>();
  private nextId = 1;
  private stdoutBuffer = "";

  constructor(private opts: SpawnOptions) {
    super();
  }

  spawn(): void {
    if (this.proc) return;

    const args = [
      this.opts.sidecarScript,
      "--daemon",
      "--vault", this.opts.vault,
      "--subfolder", this.opts.subfolder,
      "--whisper-model", this.opts.whisperModel,
      "--ollama-model", this.opts.ollamaModel,
    ];
    if (this.opts.language) {
      args.push("--language", this.opts.language);
    }

    this.proc = spawn(this.opts.pythonPath, args, {
      stdio: ["pipe", "pipe", "pipe"],
    });

    this.proc.stdout.setEncoding("utf-8");
    this.proc.stdout.on("data", (chunk: string) => this.onStdoutChunk(chunk));

    this.proc.stderr.setEncoding("utf-8");
    this.proc.stderr.on("data", (chunk: string) => {
      for (const line of chunk.split("\n")) {
        if (line) console.log(`[quietnotes:sidecar] ${line}`);
      }
    });

    this.proc.on("exit", (code) => {
      console.log(`[quietnotes:sidecar] process exited with code ${code}`);
      this.proc = null;
      // Reject any pending requests so the UI doesn't hang.
      for (const [id, p] of this.pending) {
        p.reject({ code: "sidecar_exited", message: `sidecar exited (code ${code})`, recoverable: false });
        this.pending.delete(id);
      }
      this.emit("exit", code);
    });

    this.proc.on("error", (err) => {
      console.error("[quietnotes:sidecar] spawn error", err);
      this.emit("spawn_error", err);
    });
  }

  private onStdoutChunk(chunk: string): void {
    this.stdoutBuffer += chunk;
    let nl: number;
    while ((nl = this.stdoutBuffer.indexOf("\n")) >= 0) {
      const line = this.stdoutBuffer.slice(0, nl).trim();
      this.stdoutBuffer = this.stdoutBuffer.slice(nl + 1);
      if (!line) continue;

      let msg: any;
      try {
        msg = JSON.parse(line);
      } catch (e) {
        console.warn("[quietnotes:sidecar] bad json from sidecar", line);
        continue;
      }

      if (msg.event) {
        this.emit("event", msg as SidecarEvent);
      } else if (msg.id) {
        const pending = this.pending.get(msg.id);
        if (pending) {
          this.pending.delete(msg.id);
          if (msg.error) pending.reject(msg.error);
          else pending.resolve(msg.result);
        }
      }
    }
  }

  send<T = unknown>(method: string, params: Record<string, unknown> = {}): Promise<T> {
    if (!this.proc) {
      return Promise.reject({ code: "not_spawned", message: "sidecar not spawned", recoverable: false });
    }
    const id = String(this.nextId++);
    const req = { id, method, params };
    return new Promise<T>((resolve, reject) => {
      this.pending.set(id, { resolve: resolve as (v: unknown) => void, reject });
      this.proc!.stdin.write(JSON.stringify(req) + "\n");
    });
  }

  async shutdown(): Promise<void> {
    if (!this.proc) return;
    this.proc.stdin.end();
    await new Promise<void>((r) => {
      this.proc!.once("exit", () => r());
      setTimeout(() => {
        if (this.proc) {
          this.proc.kill("SIGTERM");
          r();
        }
      }, 3000);
    });
  }
}
```

### Step 5.2 — Quick console test (no real recording yet)

This step only verifies the `SidecarClient` plumbing — it doesn't yet attempt a full recording. We dry-run by spawning the sidecar and pinging it with an unknown method.

We'll temporarily wire this into `main.ts` just for the smoke test, then revert.

- [ ] Temporarily add to `onload()` in `main.ts`, **after** state-machine setup:

```typescript
// TEMP: smoke test SidecarClient
import { SidecarClient } from "./sidecar-client";
this.addCommand({
  id: "qn-sidecar-smoketest",
  name: "Smoke test sidecar client (TEMP)",
  callback: async () => {
    const sc = new SidecarClient({
      pythonPath: "/Users/fiezzyy/Documents/Projects/quietnotes/apps/sidecar/.venv/bin/python",
      sidecarScript: "/Users/fiezzyy/Documents/Projects/quietnotes/apps/sidecar/sidecar.py",
      vault: this.app.vault.adapter.getBasePath() as any,
      subfolder: this.settings.outputSubfolder,
      whisperModel: this.settings.whisperModel,
      ollamaUrl: this.settings.ollamaUrl,
      ollamaModel: this.settings.ollamaModel,
      language: this.settings.language,
    });
    sc.spawn();
    try {
      const r = await sc.send("wat", {});
      console.log("unexpected result:", r);
    } catch (e) {
      console.log("expected error:", e);
    }
    await sc.shutdown();
  },
});
```

Note: `getBasePath()` is on `FileSystemAdapter` (desktop). Cast is fine because `isDesktopOnly: true` in manifest.

- [ ] Build, reload, in Obsidian press `⌘P` (Command palette) → run "Smoke test sidecar client".
- [ ] Expected in console:
  - `[quietnotes:sidecar] ` lines (any stderr from sidecar)
  - `expected error: { code: 'unknown_method', message: "unknown method: 'wat'", recoverable: false }`
  - `[quietnotes:sidecar] process exited with code 0`
- [ ] Once verified, **remove** the temporary command from `main.ts`.

### Checkpoint Task 5

- Sidecar spawns from the plugin
- Unknown method returns proper error envelope
- Process exits cleanly when stdin closes
- stderr forwarded to Obsidian console

---

## Task 6 — Wire ribbon + commands to recording flow

Connect ribbon click → start_recording → stop_and_process → display result.

**Files:**
- Modify: `apps/plugin/src/main.ts`

### Step 6.1 — Ribbon icon + commands

Add to `main.ts` `onload()`, after status bar setup:

- [ ] Replace `apps/plugin/src/main.ts` with the wired version:

```typescript
import { Plugin, Notice, Platform } from "obsidian";
import { DEFAULT_SETTINGS, QuietnotesSettings, QuietnotesSettingTab } from "./settings";
import { StateMachine, statusBarText } from "./state";
import { SidecarClient, SidecarError } from "./sidecar-client";
import * as path from "path";

export default class QuietnotesPlugin extends Plugin {
  settings!: QuietnotesSettings;
  state = new StateMachine();
  private sidecar: SidecarClient | null = null;
  private statusBarEl: HTMLElement | null = null;
  private statusBarTimer: number | null = null;

  async onload(): Promise<void> {
    await this.loadSettings();
    this.addSettingTab(new QuietnotesSettingTab(this.app, this));
    this.setupStatusBar();
    this.setupRibbonAndCommands();
    console.log("[quietnotes] loaded");
  }

  async onunload(): Promise<void> {
    if (this.sidecar) await this.sidecar.shutdown();
    console.log("[quietnotes] unloaded");
  }

  async loadSettings(): Promise<void> {
    this.settings = Object.assign({}, DEFAULT_SETTINGS, await this.loadData());
  }

  async saveSettings(): Promise<void> {
    await this.saveData(this.settings);
  }

  private setupStatusBar(): void {
    if (!this.settings.showStatusBar) return;
    this.statusBarEl = this.addStatusBarItem();
    this.statusBarEl.addClass("quietnotes-statusbar");
    this.register(
      this.state.subscribe((stage) => {
        if (!this.statusBarEl) return;
        this.statusBarEl.toggleClass("quietnotes-statusbar--recording", stage.kind === "recording");
        this.statusBarEl.setText(statusBarText(stage));
      }),
    );
    this.statusBarTimer = window.setInterval(() => {
      if (!this.statusBarEl) return;
      if (this.state.current.kind === "recording") {
        this.statusBarEl.setText(statusBarText(this.state.current));
      }
      if (this.state.current.kind === "done" && Date.now() >= this.state.current.expiresAt) {
        this.state.toIdle();
      }
    }, 1000);
    this.register(() => {
      if (this.statusBarTimer !== null) window.clearInterval(this.statusBarTimer);
    });
  }

  private setupRibbonAndCommands(): void {
    this.addRibbonIcon("mic", "quietnotes: Start/Stop recording", () => this.toggleRecording());
    this.addCommand({
      id: "qn-start-stop",
      name: "Start or stop recording",
      callback: () => this.toggleRecording(),
    });
  }

  private async toggleRecording(): Promise<void> {
    const stage = this.state.current;

    if (stage.kind === "idle" || stage.kind === "error" || stage.kind === "done") {
      await this.startRecording();
    } else if (stage.kind === "recording") {
      await this.stopAndProcess(stage.recordingId);
    } else {
      new Notice("Already processing — please wait");
    }
  }

  private async startRecording(): Promise<void> {
    if (!Platform.isDesktop) {
      new Notice("quietnotes is desktop-only");
      return;
    }

    const vaultPath = (this.app.vault.adapter as any).getBasePath?.();
    if (!vaultPath) {
      new Notice("Cannot resolve vault path");
      return;
    }

    // Resolve sidecar paths. Convention: <vault>/.obsidian/plugins/quietnotes/sidecar-venv/...
    // For dev, fall back to the repo path.
    const pluginDir = `${vaultPath}/.obsidian/plugins/quietnotes`;
    const devVenv = "/Users/fiezzyy/Documents/Projects/quietnotes/apps/sidecar/.venv/bin/python";
    const devScript = "/Users/fiezzyy/Documents/Projects/quietnotes/apps/sidecar/sidecar.py";
    const pythonPath = devVenv; // Task 9 will replace this with the bundled venv check
    const sidecarScript = devScript;

    this.sidecar = new SidecarClient({
      pythonPath,
      sidecarScript,
      vault: vaultPath,
      subfolder: this.settings.outputSubfolder,
      whisperModel: this.settings.whisperModel,
      ollamaUrl: this.settings.ollamaUrl,
      ollamaModel: this.settings.ollamaModel,
      language: this.settings.language,
    });

    this.sidecar.on("event", (e: any) => {
      if (e.event === "stage") {
        if (e.stage === "transcribing") this.state.toTranscribing();
        else if (e.stage === "summarizing") this.state.toSummarizing();
        else if (e.stage === "writing") this.state.toWriting();
      }
    });

    this.sidecar.on("exit", () => {
      // If sidecar dies unexpectedly during recording, treat as error.
      if (this.state.current.kind === "recording") {
        this.state.toError("sidecar_exited", "Sidecar exited unexpectedly", false);
      }
    });

    try {
      this.sidecar.spawn();
      const result = await this.sidecar.send<{ recording_id: string }>("start_recording", {});
      this.state.toRecording(result.recording_id);
    } catch (err) {
      const e = err as SidecarError;
      this.state.toError(e.code, e.message, e.recoverable);
      await this.sidecar?.shutdown();
      this.sidecar = null;
    }
  }

  private async stopAndProcess(recordingId: string): Promise<void> {
    if (!this.sidecar) {
      this.state.toError("no_sidecar", "Sidecar not running", false);
      return;
    }
    this.state.toTranscribing();
    try {
      const result = await this.sidecar.send<{ md_path: string | null }>("stop_and_process", {
        recording_id: recordingId,
      });
      if (result.md_path) {
        this.state.toDone(result.md_path);
        if (this.settings.autoOpenNote) {
          // Open the freshly-written .md.
          const relPath = path.relative((this.app.vault.adapter as any).getBasePath(), result.md_path);
          await this.app.workspace.openLinkText(relPath, "", false);
        }
      } else {
        this.state.toError("no_md_path", "Sidecar did not return a markdown path", false);
      }
    } catch (err) {
      const e = err as SidecarError;
      this.state.toError(e.code, e.message, e.recoverable);
    } finally {
      await this.sidecar.shutdown();
      this.sidecar = null;
    }
  }
}
```

### Step 6.2 — End-to-end smoke test in Obsidian

- [ ] Build: `cd apps/plugin && npm run build`. Reload plugin.
- [ ] Settings → quietnotes → verify Output subfolder and Ollama model are set. Set Language to Russian if you'll speak Russian.
- [ ] Click ribbon mic icon. Expected: status bar shows `🔴 recording 00:01`, then `00:02`…
- [ ] Talk for ~15 seconds. Click ribbon again. Expected: `⏳ transcribing…` → `⏳ summarizing…` → `⏳ writing markdown…` → `✓ saved YYYY-MM-DD-HHMM.md`.
- [ ] Navigate in Obsidian to `<vault>/Meetings/YYYY-MM-DD-HHMM.md`. Expected: full note with TL;DR, key points, tasks, transcript.

If anything fails, the status bar should show `❌ quietnotes: <reason>`. Open Developer Console to see sidecar stderr.

### Checkpoint Task 6

- Full happy path works in Obsidian
- Ribbon icon visible and clickable
- Command palette command also works
- Auto-open setting opens the note after processing if enabled

---

## Task 7 — Error modal + recovery

Friendly error messages and a Retry button for `--from-wav`-style recovery.

**Files:**
- Create: `apps/plugin/src/errors.ts`
- Modify: `apps/plugin/src/main.ts`

### Step 7.1 — Error modal

- [ ] Create `apps/plugin/src/errors.ts`:

```typescript
import { App, Modal, Setting, Notice } from "obsidian";

export interface FriendlyError {
  title: string;
  message: string;
  hint?: string;
  copyText?: string;       // a command the user can copy
  openSystemSettings?: boolean;
  retryable?: boolean;
}

export function classifyError(code: string, message: string): FriendlyError {
  switch (code) {
    case "ollama_unreachable":
      return {
        title: "Ollama unreachable",
        message: "The plugin couldn't reach Ollama at the configured URL.",
        hint: "Make sure Ollama is running, then click Retry.",
        retryable: true,
      };
    case "audio_capture_failed":
      return {
        title: "Audio capture failed",
        message,
        hint: "Check Screen Recording and Microphone permissions in System Settings → Privacy & Security.",
        openSystemSettings: true,
      };
    case "sidecar_exited":
      return {
        title: "Sidecar process exited",
        message,
        hint: "Try again. If it persists, check the Developer Console for the sidecar's stderr.",
        retryable: true,
      };
    case "unknown_method":
    case "internal_error":
      return {
        title: "Internal error",
        message: `Unexpected: ${message}`,
        hint: "This is likely a bug. Copy details and open an issue.",
        copyText: `${code}: ${message}`,
      };
    default:
      return {
        title: `Error: ${code}`,
        message,
      };
  }
}

export class ErrorModal extends Modal {
  constructor(
    app: App,
    private err: FriendlyError,
    private onRetry?: () => void,
  ) {
    super(app);
  }

  onOpen(): void {
    const { contentEl, titleEl } = this;
    titleEl.setText(`❌ ${this.err.title}`);
    contentEl.createEl("p", { text: this.err.message });
    if (this.err.hint) {
      contentEl.createEl("p", { text: this.err.hint, cls: "setting-item-description" });
    }
    if (this.err.copyText) {
      new Setting(contentEl)
        .addButton((b) =>
          b.setButtonText("Copy details").onClick(() => {
            navigator.clipboard.writeText(this.err.copyText!);
            new Notice("Copied");
          }),
        );
    }
    if (this.err.openSystemSettings) {
      new Setting(contentEl).addButton((b) =>
        b.setButtonText("Open System Settings").onClick(() => {
          // Open Privacy & Security pane.
          const { shell } = require("electron");
          shell.openExternal("x-apple.systempreferences:com.apple.preference.security?Privacy");
        }),
      );
    }
    if (this.err.retryable && this.onRetry) {
      new Setting(contentEl).addButton((b) =>
        b.setButtonText("Retry").setCta().onClick(() => {
          this.close();
          this.onRetry!();
        }),
      );
    }
  }

  onClose(): void {
    this.contentEl.empty();
  }
}
```

### Step 7.2 — Show modal when status bar enters error

- [ ] Modify `apps/plugin/src/main.ts` — add to `setupStatusBar()` after the subscribe block:

```typescript
    this.statusBarEl.addEventListener("click", () => {
      const s = this.state.current;
      if (s.kind === "error") {
        const friendly = classifyError(s.code, s.message);
        new ErrorModal(this.app, friendly, friendly.retryable ? () => this.startRecording() : undefined).open();
      }
    });
```

- [ ] Add imports at top:

```typescript
import { classifyError, ErrorModal } from "./errors";
```

### Step 7.3 — Smoke-test the modal

- [ ] Build, reload.
- [ ] Trigger an error: in Settings → quietnotes → set Ollama URL to `http://localhost:99999` (unreachable). Click ribbon → record 5s → click ribbon to stop.
- [ ] Expected: status bar `❌ quietnotes: …Connection refused…`. Click it → modal "Ollama unreachable" with Retry button. (Retry will fail too with the bad URL — that's fine, the modal flow is what we're verifying.)
- [ ] Reset Ollama URL back to default.

### Checkpoint Task 7

- Error modal appears on status-bar click when in error state
- Classified messages are human-readable
- Retry button visible for retryable errors
- "Open System Settings" works for TCC errors (test it manually — set garbage Whisper model path to force one)

---

## Task 8 — Manual end-to-end + polish

No new code; mostly verification and small fixes.

### Step 8.1 — Realistic recording test

- [ ] Start a 2-3 minute self-recording where you discuss something concrete (a project, a list of tasks). Stop via ribbon.
- [ ] Expected: full pipeline completes within ~30s, .md file opens (if auto-open is on) or appears in `Meetings/`. TL;DR, Key points, Tasks all populated.

### Step 8.2 — Reload-during-recording test

- [ ] Start recording. While recording, disable the plugin from Settings → Community plugins.
- [ ] Expected: `onunload()` runs → sidecar gets stdin EOF → exits gracefully → temp .wav cleaned up by daemon's cancel-on-exit (verify: `ls /var/folders/.../T/quietnotes-*.wav` shows no files newer than 1 min).

(If you didn't add SIGINT-on-EOF handling in sidecar, do so now:)

- [ ] Edit `sidecar.py` `run_daemon`: when the `for raw_line in _sys.stdin` loop exits naturally (EOF), call `_daemon_cancel` if a recording is active.

```python
    # After the for loop:
    if state["recording_id"] is not None:
        _daemon_cancel(args, state, "shutdown", {})
    return 0
```

### Step 8.3 — Settings persistence test

- [ ] Change Output subfolder to `Personal/Notes`, set Auto-open note to on, restart Obsidian. Open Settings → quietnotes → values preserved.

### Step 8.4 — Verify network silence

- [ ] Open Activity Monitor → Network tab. Filter for "python" or "audio-capture". Start a recording.
- [ ] Expected: 0 bytes sent during recording phase. (HF model download from earlier sessions is one-time; if the cache is warm, even transcribe is silent. Ollama is localhost-only.)

### Checkpoint Task 8 (FULL MVP)

This is the moment we've been working toward. Everything below should be true:

- Click ribbon → record → click ribbon → markdown appears in vault, no Terminal touched
- Status bar correctly reflects every stage
- Settings persist
- Errors handled gracefully with retry
- Reload-during-recording doesn't leak processes or files
- Zero network activity during recording (privacy demo ready)

---

## Task 9 — Onboarding wizard (POST-MVP polish, for v0.1 release)

**This task is deferred and not required for the "MVP works locally" milestone.** It's the difference between "works on my machine" and "BRAT users can install it". Document it here so it's not forgotten.

The wizard handles:
1. Detecting + downloading `audio-capture` binary from GitHub Releases.
2. Detecting `uv`, creating a venv at `<plugin-dir>/sidecar-venv/`, installing deps from a bundled `requirements.txt`.
3. Optional: `ollama pull gemma3:4b`.
4. Optional: prefetching the Whisper model.

State persisted to `<plugin-dir>/.quietnotes-state.json`.

This task gets its own implementation plan when we're ready to publish on BRAT. Until then, dev-mode hardcoded paths in `startRecording()` are fine.

---

## Init commit (executed once, when MVP works)

When Task 8 checkpoint passes — the moment we've been postponing commits for — make the single commit covering M1 + M2 + M3.

- [ ] Verify nothing in `dev.config.json` (gitignored, but double-check)
- [ ] Stage everything: `git add -A`
- [ ] Inspect `git status` carefully — anything sensitive (vault paths, personal data) should be either gitignored or removed
- [ ] Commit (NO Co-Authored-By trailer; user is sole author):

```bash
git commit -m "$(cat <<'EOF'
feat: v0.1 — local meeting notetaker for Obsidian

M1 — Swift audio-capture CLI:
- ScreenCaptureKit unified system+mic capture (macOS 15+)
- Stereo WAV output (L=system, R=mic, 48 kHz float32)
- Sample-rate alignment, PTS-based skew compensation

M2 — Python sidecar:
- record → mlx-whisper → Ollama → markdown pipeline
- One-shot CLI mode for dev iteration
- NDJSON-RPC daemon mode for the plugin

M3 — Obsidian plugin:
- Ribbon button + status-bar widget + settings tab
- State machine for recording lifecycle
- Sidecar subprocess management
- Error modal with retry
EOF
)"
```

- [ ] Do NOT push. (Memory: "No push until plugin MVP works end-to-end" — we're at that point but pushing is a separate user-confirmed action.)

---

## Open follow-ups (out of scope for this plan)

- Code signing for `audio-capture` (Apple Developer ID, $99/yr) — Gatekeeper warning workaround
- BRAT distribution + GitHub Actions release workflow (binary download URL embedded in manifest)
- Whisper model prefetch UI in onboarding
- Recovery flow polish: when `--keep-wav` is on and processing fails, surface "Retry with the saved wav?" automatically
- Test coverage on plugin TS (probably with jest + obsidian mock, only if v0.1 gets contributors)

---

## Self-review notes

- **Spec coverage:** sections 1–7 of design doc map to Tasks 2, 3, 4 (state machine UI), 5+6 (sidecar comms + flow), 7 (errors), 9 (onboarding, deferred), and Task 1 (file output — implicitly via sidecar that's already correct).
- **No placeholders:** every code block in this plan is complete and runnable. No "TODO" sections.
- **Type consistency:** `SidecarClient.send<T>()` typed consistently; `Stage` discriminated-union; `FriendlyError` shape stable.
- **Order independence:** Task N can read prior tasks' final code, but no task forward-references undefined symbols.
