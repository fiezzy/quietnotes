# Design: Local Meeting Notetaker для Obsidian

**Status:** DRAFT
**Mode:** Builder (office-hours session)
**Date:** 2026-05-11
**Author:** Maksim Zhers
**Working repo name (tentative):** `obsidian-local-notetaker` (или `quietnotes`, `vault-notetaker` — финализировать в момент создания репо)

---

## Problem Statement

Каждую неделю несколько часов в звонках на разных платформах (Meet / Zoom / Telemost). Заметки либо не делаются, либо ведутся вручную, либо отдаются в чужие облачные тулзы (Granola, Otter, Krisp), у которых три минуса разом: (1) аудио уходит в облако, (2) ноль интеграции с Obsidian, (3) платные. Цель: после колла открыть свой vault и увидеть готовую страницу — summary, key points, tasks, transcript — и при этом доказуемо знать что аудио не покидало ноутбук.

## What Makes This Cool

Вся идея стоит на двух кадрах для README:

1. **Hands-off Obsidian magic.** Закрыл крышку Zoom — открыл vault — там лежит готовая страница с TL;DR, ключевыми мыслями, чек-листом задач, фронтматтером (дата, длительность, модель), и свёрнутым полным transcript. Никаких кликов, никакого ручного export.
2. **Доказуемая приватность.** На демо открываешь Activity Monitor (или Little Snitch) и показываешь нулевую сетевую активность во время обработки. Open-source конкурентов в нише «Obsidian-native + local-only» — нет. Hyprnote ближе всех, но он стандалон-app, не Obsidian-first.

## Constraints

- macOS-only на старте (M1+, macOS 13+)
- Core flow работает без интернета
- Никаких virtual audio devices в инсталляции (BlackHole / Loopback / SoundFlower — нет)
- Юзер сам ставит Ollama и Whisper-модель (BYOM-подход)
- Стек: TypeScript для Obsidian-plugin + Python для sidecar
- Один monorepo с двумя `apps/*`
- Open-source, MIT

## Premises (locked 2026-05-11)

1. **macOS-only на старте — ок.** Целевая аудитория (Obsidian power-users + AI-tooling crowd) на 80% Mac. Cross-platform — v0.5+, не блокер ни для community traction, ни для daily use.
2. **Audio capture через ScreenCaptureKit, без virtual audio devices.** macOS 13+ нативно отдаёт system audio. Никакого "поставь себе BlackHole".
3. **LLM выносится из проекта — BYOM через Ollama.** Не паковать модели в репо. Юзер ставит `brew install ollama` сам, в plugin'е dropdown с локальными моделями. Privacy story усиливается («вот мой Ollama»), MB не таскаем.
4. **Whisper: `mlx-whisper`.** ~2-3x быстрее faster-whisper на M-series (нативный Metal). RU из коробки.
5. **IPC plugin↔sidecar — stdin/stdout JSON-RPC, не HTTP-сервер.** Plugin спавнит Python subprocess, общается через pipe. Без open ports, без security headaches.
6. **Один monorepo.** `apps/plugin` (TS) + `apps/sidecar` (Python). Одна история коммитов, одни звёзды.
7. **Distribution v0.1 — BRAT, не Community Plugins сразу.** Подача в community registry — после ~50 звёзд и фидбека, иначе review-cycle задерживает релиз на недели.
8. **README продаёт двумя кадрами:** GIF «после колла → готовая страница» + скриншот Activity Monitor / Little Snitch без сетевой активности.

## Approaches Considered

### Approach A — Honest MVP (CHOSEN)

Plugin спавнит Python subprocess per recording. JSON-RPC через stdin/stdout. Sidecar — pipeline в одном процессе: `record() → transcribe() → summarize() → write_md()`. Состояние — в памяти процесса. После записи процесс умирает.

- Effort: S (2–3 выходных до working build)
- Reuses: `mlx-whisper`, `ollama-python`, Obsidian Plugin API, ScreenCaptureKit через Swift CLI helper

### Approach B — Ideal architecture (rejected for v0.1)

Sidecar как самостоятельный FastAPI daemon, launchd plist, stage-based pipeline с pause/resume, SQLite audit-log. Effort L (1–2 месяца). Слишком далеко от первого работающего `.md` — высокий риск перегореть. Годится как target рефакторинга в v0.3.

### Approach C — Standalone menubar app (rejected)

Без Obsidian-plugin, sidecar превращается в menubar-app, `.md` пишется в vault folder, Obsidian подхватывает file watcher'ом. Ломает Obsidian-native позиционирование и community discovery. Хорошая идея для другого проекта.

## Recommended Approach

**Approach A.** Главный риск пет-проекта — не дойти до v0.1 пока есть мотивация. A добегает за 2–3 выходных. Архитектурный апгрейд A→B — это рефакторинг, не переписывание, и делать его обоснованно можно только когда есть real usage, который покажет где именно нужен stage-based pipeline и persistence.

## v0.1 Scope (explicit)

**Включено:**

- Audio capture: system-audio через ScreenCaptureKit (Swift CLI helper, вызывается из Python sidecar)
- Transcription: `mlx-whisper`, модель `large-v3` по умолчанию, `medium` как опция для слабого железа
- Summary + extracted tasks: через Ollama (default `qwen2.5:7b`, юзер выбирает любую установленную)
- Output: одна `.md` в `{vault}/Meetings/YYYY-MM-DD-HHMM.md` с фронтматтером
- Plugin UI: ribbon button «Start meeting», status-bar widget с состоянием, settings tab (vault path, model picker, Ollama URL, output folder)
- README с GIF + screenshot нулевой сетевой активности

**Намеренно НЕ включено:**

- Speaker diarization (v0.2 через `pyannote-audio`)
- Семантический поиск по записям (v0.3)
- Real-time hints во время колла (отклонено в office-hours)
- Windows / Linux (v0.5+)
- Свой LLM packaging (намеренно BYOM)
- Auto-detect платформы (Meet vs Zoom) — пишем что играет на system audio, точка

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

**JSON-RPC контракт (минимальный):**

| Method             | Args                                     | Returns                                                                             |
| ------------------ | ---------------------------------------- | ----------------------------------------------------------------------------------- |
| `start_recording`  | `{output_dir, model_whisper, model_llm}` | `{recording_id}`                                                                    |
| `stop_and_process` | `{recording_id}`                         | streaming progress events → final `{md_path}`                                       |
| `status`           | `{recording_id}`                         | `{stage: "recording" \| "transcribing" \| "summarizing" \| "done", progress: 0..1}` |

## Open Questions

- **mlx-whisper `large-v3` на M1 16GB:** окей по latency? Нужен бенчмарк: 30min audio → ?min transcribe time. Если >2x real-time на M1 base — даунгрейд default до `medium`.
- **Захват микрофона:** ScreenCaptureKit отдаёт system audio одним треком. Твой голос (microphone input) — это отдельный поток, его надо ловить параллельно через CoreAudio и mix'ить, иначе в transcript будет только собеседник.
- **README privacy demo:** Activity Monitor показывает процесс, но это слабо. Little Snitch screenshot с правилом «deny all outbound» во время записи — нагляднее.
- **Имя проекта:** `obsidian-local-notetaker` точно описательное, но скучно. Варианты: `quietnotes`, `vault-scribe`, `silentmemo`. Финализировать перед первым коммитом.

## Success Criteria

- **Personal:** ты сам юзаешь после каждого второго колла (≥3 раз в неделю) минимум 2 недели подряд без правок руками
- **Public v0.1:** работающая инсталляция через BRAT + README с двумя ключевыми кадрами
- **30 дней после релиза:** ≥20 звёзд (organic из Twitter / RU dev-каналов / r/ObsidianMD)
- **60 дней:** ≥1 внешний contributor с merged PR (любым)
- **Качественный:** хотя бы один issue с обратной связью от незнакомого пользователя

## Distribution Plan

| Stage        | Channel                                                                                    |
| ------------ | ------------------------------------------------------------------------------------------ |
| v0.1         | GitHub repo + README + BRAT install instruction                                            |
| v0.1.x       | Pre-built Swift `audio-capture` binary в GitHub Releases (иначе юзер должен ставить Xcode) |
| v0.2 (~50⭐) | Подача в Obsidian Community Plugins registry                                               |
| v0.3+        | Опциональный отдельный landing с GIF-демо                                                  |

## Build Order (3 weeks to v0.1)

**Week 1 — рискованная часть первой:**

1. Swift CLI `audio-capture --output /path/to.wav` через ScreenCaptureKit. Самая неизвестная часть, нужно проверить что system audio + mic mix реально работает.
2. Python pipeline standalone (без plugin): `python sidecar.py --record 60 --vault /path` → пишет валидный `.md`. End-to-end в CLI.

**Week 2 — обвязка:**

3. Plugin scaffold (Obsidian sample-plugin как стартер). Один ribbon button, спавнит Python через `child_process.spawn`.
4. JSON-RPC: ровно три метода из контракта выше.
5. Settings tab: vault path, model picker для Whisper, Ollama URL, output folder.

**Week 3 — публикация:**

6. README v1 с GIF + privacy-screenshot. Один абзац «Why this exists».
7. BRAT manifest, GitHub Release v0.1.0.
8. Один post в r/ObsidianMD + одно личное demo-видео в твоей сети (LinkedIn / Telegram).

## What I noticed about how you think

- Ты сразу сузил критерий до двух конкретных wow-моментов (готовая страница + privacy), не пытался уместить «всё что AI notetaker умеет». Это узкий wedge с первой итерации — редкость.
- Ты выбрал Obsidian-plugin + thin Python sidecar, отказавшись от Electron из изначального плана. Это означает что ты ставишь скорость до v0.1 выше, чем верность техническому wishlist.
- Ты согласился со всеми восемью premises без сопротивления, включая «v0.1 не в Community Plugins». Многие на этом этапе хотят сразу «правильно» — а правильная последовательность для пет-проекта именно такая: BRAT → community-feedback → registry.

---

## The Assignment

До следующих выходных — **один реальный технический спайк**, не код всего проекта.

Цель: убедиться, что **Swift + ScreenCaptureKit** может одновременно вытащить **system audio** (то что играет в Zoom) и **microphone input** (твой голос), смикшировать их в один `.wav`, и сохранить на диск. **Без plugin'а, без Python — просто `swift run audio-capture --output meeting.wav`**.

Если эта часть работает за выходные — весь остальной дизайн становится понятным проектом на 2–3 недели. Если эта часть упирается в Apple-restrictions или плохое качество — нужно переосмыслить либо premise #2 (capture method), либо v0.1 scope (только system audio, mic отдельно в v0.2). Лучше узнать это сейчас, до того как написана plugin-обвязка.
