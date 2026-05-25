# Design: Local Meeting Notetaker for Obsidian

**Status:** DRAFT
**Mode:** Builder (office-hours session)
**Date:** 2026-05-11
**Author:** Maksim Zhers
**Working repo name (tentative):** `obsidian-local-notetaker` (or `quietnotes`, `vault-notetaker` — to be finalised when the repo is created)

---

## Problem Statement

A few hours every week on calls across different platforms (Meet / Zoom / Telemost). Notes are either skipped, taken manually, or handed off to someone else's cloud tools (Granola, Otter, Krisp) — which all share three downsides at once: (1) audio goes to the cloud, (2) zero Obsidian integration, (3) paid. The goal: after a call, open your vault and see a ready-made page — summary, key points, tasks, transcript — and be able to prove that the audio never left your laptop.

## What Makes This Cool

The whole idea rests on two screenshots for the README:

1. **Hands-off Obsidian magic.** Close the Zoom lid → open the vault → there's a ready-made page with TL;DR, key points, a task checklist, frontmatter (date, duration, model), and the full collapsed transcript. No clicks, no manual export.
2. **Provable privacy.** During the demo you open Activity Monitor (or Little Snitch) and show zero network activity during processing. There are no open-source competitors in the "Obsidian-native + local-only" niche. Hyprnote is the closest, but it's a standalone app, not Obsidian-first.

## Constraints

- macOS-only at launch (Apple Silicon, macOS 15+ — required for `SCStreamConfiguration.captureMicrophone`)
- Core flow works without an internet connection
- No virtual audio devices in the install (BlackHole / Loopback / SoundFlower — no)
- The user installs Ollama and the Whisper model themselves (BYOM approach)
- Stack: TypeScript for the Obsidian plugin + Python for the sidecar
- One monorepo with two `apps/*`
- Open-source, MIT

## Premises (locked 2026-05-11)

1. **macOS-only at launch — fine.** The target audience (Obsidian power users + AI tooling crowd) is ~80% on Mac. Cross-platform is a v0.5+ topic, not a blocker for community traction or daily use.
2. **Audio capture via ScreenCaptureKit, no virtual audio devices.** macOS 15+ natively delivers system audio and microphone in a single `SCStream` (`SCStreamConfiguration.captureMicrophone`, available from macOS 15.0). No "go install BlackHole". The minimum was raised from macOS 13 to 15 during the M1 spike — without this API, mic capture would need a separate CoreAudio path, which complicates the implementation.
3. **The LLM stays outside the project — BYOM via Ollama.** Don't ship models in the repo. The user runs `brew install ollama` themselves, and the plugin shows a dropdown of locally-installed models. The privacy story gets stronger ("here's my Ollama"), and we don't haul gigabytes around.
4. **Whisper: `mlx-whisper`.** ~2–3× faster than faster-whisper on M-series (native Metal). Russian works out of the box.
5. **Plugin↔sidecar IPC — stdin/stdout JSON-RPC, not an HTTP server.** The plugin spawns a Python subprocess and talks over a pipe. No open ports, no security headaches.
6. **One monorepo.** `apps/plugin` (TS) + `apps/sidecar` (Python). One commit history, one set of stars.
7. **v0.1 distribution — BRAT, not Community Plugins immediately.** Submission to the community registry waits for ~50 stars and feedback; otherwise the review cycle delays the release by weeks.
8. **The README sells with two screenshots:** a GIF "after the call → ready-made page" + a screenshot of Activity Monitor / Little Snitch showing no network activity.

## Approaches Considered

### Approach A — Honest MVP (CHOSEN)

The plugin spawns a Python subprocess per recording. JSON-RPC over stdin/stdout. The sidecar is a single-process pipeline: `record() → transcribe() → summarize() → write_md()`. State lives in memory. When the recording is done, the process exits.

- Effort: S (2–3 weekends to a working build)
- Reuses: `mlx-whisper`, `ollama-python`, the Obsidian Plugin API, ScreenCaptureKit via a Swift CLI helper

### Approach B — Ideal architecture (rejected for v0.1)

Sidecar as a standalone FastAPI daemon, launchd plist, stage-based pipeline with pause/resume, SQLite audit log. Effort L (1–2 months). Too far from the first working `.md` — high risk of burning out. A good target for a v0.3 refactor.

### Approach C — Standalone menubar app (rejected)

No Obsidian plugin: the sidecar becomes a menubar app, the `.md` is written to a vault folder, and Obsidian picks it up via a file watcher. Breaks the Obsidian-native positioning and the community discovery story. A good idea for a different project.

## Recommended Approach

**Approach A.** The main risk on a side project is not making it to v0.1 while motivation is still there. A gets there in 2–3 weekends. The A→B architectural upgrade is a refactor, not a rewrite, and can be justified only once there's real usage that shows exactly where a stage-based pipeline and persistence are needed.

## v0.1 Scope (explicit)

**Included:**

- Audio capture: system audio via ScreenCaptureKit (Swift CLI helper, invoked from the Python sidecar)
- Transcription: `mlx-whisper`, model `large-v3` by default, `medium` as an option for weaker hardware
- Summary + extracted tasks: via Ollama (default `qwen2.5:7b`, user picks any installed model)
- Output: a single `.md` at `{vault}/Meetings/YYYY-MM-DD-HHMM.md` with frontmatter
- Plugin UI: ribbon button "Start meeting", status-bar widget with state, settings tab (vault path, model picker, Ollama URL, output folder)
- README with a GIF + screenshot of zero network activity

**Deliberately NOT included:**

- Speaker diarization (v0.2 via `pyannote-audio`)
- Semantic search across recordings (v0.3)
- Real-time hints during the call (rejected in office hours)
- Windows / Linux (v0.5+)
- Our own LLM packaging (intentionally BYOM)
- Auto-detection of the platform (Meet vs Zoom) — we record whatever plays on system audio, full stop

## Architecture

```
┌────────────────────────────────────────┐
│ Obsidian Plugin  (TypeScript)          │
│  - Ribbon button: Start meeting        │
│  - Status bar widget                   │
│  - Settings tab (paths, models)        │
└──────────┬─────────────────────────────┘
           │ spawn + JSON-RPC over pipe
           ↓
┌────────────────────────────────────────┐
│ Python sidecar  (one-shot per meeting) │
│  - audio.py     → Swift helper → .wav  │
│  - transcribe.py → mlx-whisper         │
│  - summarize.py  → Ollama → sections   │
│  - output.py     → assemble + write    │
└────────────────────────────────────────┘
           │
           ↓
   {vault}/Meetings/2026-05-11-1234.md
```

**JSON-RPC contract (minimal):**

| Method             | Args                                     | Returns                                                                             |
| ------------------ | ---------------------------------------- | ----------------------------------------------------------------------------------- |
| `start_recording`  | `{output_dir, model_whisper, model_llm}` | `{recording_id}`                                                                    |
| `stop_and_process` | `{recording_id}`                         | streaming progress events → final `{md_path}`                                       |
| `status`           | `{recording_id}`                         | `{stage: "recording" \| "transcribing" \| "summarizing" \| "done", progress: 0..1}` |

## Open Questions

- **mlx-whisper `large-v3` on M1 16GB:** OK on latency? Needs a benchmark: 30min of audio → ?min of transcribe time. If >2× real-time on M1 base — drop the default to `medium`.
- **Microphone capture:** ScreenCaptureKit delivers system audio as a single track. Your voice (microphone input) is a separate stream that has to be captured in parallel via CoreAudio and mixed in, otherwise the transcript only contains the other person.
- **README privacy demo:** Activity Monitor shows the process, but it's weak. A Little Snitch screenshot with a "deny all outbound" rule during the recording is more convincing.
- **Project name:** `obsidian-local-notetaker` is descriptive but boring. Alternatives: `quietnotes`, `vault-scribe`, `silentmemo`. To be finalised before the first commit.

## Success Criteria

- **Personal:** you use it yourself after every other call (≥3 times a week) for at least 2 weeks straight with no manual edits
- **Public v0.1:** a working install via BRAT + README with the two key screenshots
- **30 days post-release:** ≥20 stars (organic, from Twitter / RU dev channels / r/ObsidianMD)
- **60 days:** ≥1 external contributor with a merged PR (any size)
- **Qualitative:** at least one issue with real feedback from a stranger

## Distribution Plan

| Stage        | Channel                                                                                       |
| ------------ | --------------------------------------------------------------------------------------------- |
| v0.1         | GitHub repo + README + BRAT install instructions                                              |
| v0.1.x       | Pre-built Swift `audio-capture` binary in GitHub Releases (otherwise the user needs Xcode)    |
| v0.2 (~50⭐) | Submit to the Obsidian Community Plugins registry                                             |
| v0.3+        | Optional standalone landing page with a GIF demo                                              |

## Build Order (3 weeks to v0.1)

**Week 1 — risky part first:**

1. Swift CLI `audio-capture --output /path/to.wav` via ScreenCaptureKit. The biggest unknown — need to confirm that system audio + mic mixing actually works.
2. Python pipeline standalone (no plugin): `python sidecar.py --record 60 --vault /path` → writes a valid `.md`. End-to-end at the CLI.

**Week 2 — plumbing:**

3. Plugin scaffold (Obsidian sample-plugin as a starter). One ribbon button, spawns Python via `child_process.spawn`.
4. JSON-RPC: exactly the three methods from the contract above.
5. Settings tab: vault path, Whisper model picker, Ollama URL, output folder.

**Week 3 — release:**

6. README v1 with a GIF + privacy screenshot. One paragraph of "Why this exists".
7. BRAT manifest, GitHub Release v0.1.0.
8. One post in r/ObsidianMD + one personal demo video to your network (LinkedIn / Telegram).

## What I noticed about how you think

- You narrowed the criterion to two concrete wow-moments (ready-made page + privacy) right away, without trying to cram in "everything an AI notetaker can do". That's a narrow wedge from the first iteration — rare.
- You picked Obsidian-plugin + thin Python sidecar, abandoning Electron from the original plan. That means you're putting speed-to-v0.1 above fidelity to your technical wishlist.
- You agreed with all eight premises without pushback, including "v0.1 not in Community Plugins". Many people at this stage want to do it "properly" right away — but the right sequence for a side project is exactly this: BRAT → community feedback → registry.

---

## The Assignment

By next weekend — **one real technical spike**, not the code for the whole project.

The goal: confirm that **Swift + ScreenCaptureKit** can simultaneously pull **system audio** (what plays in Zoom) and **microphone input** (your voice), mix them into a single `.wav`, and save it to disk. **No plugin, no Python — just `swift run audio-capture --output meeting.wav`**.

If that part works over the weekend, the rest of the design becomes a clear 2–3 week project. If it hits Apple restrictions or poor quality — we need to rethink either premise #2 (capture method) or the v0.1 scope (system audio only, mic separately in v0.2). Better to find out now, before the plugin scaffolding is written.

---

## Implementation notes — M1 spike (closed 2026-05-18)

Locked in by code under `apps/audio-capture/`:

- **Minimum macOS 15.0.** `SCStreamConfiguration.captureMicrophone` and `SCStreamOutputType.microphone` are macOS 15.0+ only. Premise #2 was tightened to reflect that.
- **One SCStream, two outputs:** `.audio` (system) + `.microphone` (mic). No separate CoreAudio path is needed for the microphone.
- **Sample-rate mismatch.** System audio is forced to 48 kHz mono (`SCStreamConfiguration.sampleRate` / `channelCount`). The mic comes back at the device's native rate — 24 kHz for AirPods SCO, for instance. Resampling to 48 kHz via `AVAudioConverter` happens before the merge.
- **Stream start skew ≈200 ms.** The mic output starts after system audio. Handled with zero-padding based on `CMSampleBufferGetPresentationTimeStamp` from the first buffers of each stream.
- **Output format:** stereo WAV (L=system, R=mic), 48 kHz, float32, interleaved. Separate channels simplify downstream work — for v0.2 diarization, channels can be fed to whisper independently.
- **mlx-whisper confirmed.** On `mlx-community/whisper-medium-mlx` (~1.5 GB), 30 seconds of audio transcribe faster than real time on M-series after a one-time model load into memory. The latency open question is resolved positively.
- **TCC permissions.** Screen Recording + Microphone are needed by the host application (Terminal.app in dev, Obsidian.app in production). A SwiftPM executable doesn't need its own Info.plist — TCC attribution goes to the parent process. The production binary will be signed and distributed via GitHub Releases, and the permissions will attach to Obsidian.
- **M1 layout:**
  ```
  apps/audio-capture/
  ├── Package.swift                        // SwiftPM, swift-argument-parser
  └── Sources/AudioCapture/
      ├── AudioCaptureCommand.swift        // @main + CLI args
      ├── CaptureSession.swift             // SCStream config + delegate
      └── StereoWriter.swift               // ring buffer + resample + AVAudioFile
  ```
