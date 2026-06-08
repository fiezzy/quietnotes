import { App, PluginSettingTab, Setting, Notice } from "obsidian";
import type QuietnotesPlugin from "./main";

export type SummarizerProvider = "ollama" | "claude" | "codex" | "custom";

export interface QuietnotesSettings {
  outputSubfolder: string;
  filenamePattern: string;

  keepAudio: boolean;
  audioStoragePath: string;

  whisperModel: string;
  language: string; // "" = auto, or ISO code like "ru"

  // Summarizer backend.
  summarizer: SummarizerProvider;
  ollamaUrl: string;
  ollamaModel: string;
  summarizerModel: string; // model for claude/codex ("" = CLI default)
  summarizerCommand: string; // path/command for claude|codex|custom ("" = resolve on PATH)

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
  language: "",
  summarizer: "ollama",
  ollamaUrl: "http://localhost:11434",
  ollamaModel: "gemma3:4b",
  summarizerModel: "",
  summarizerCommand: "",
  customSystemPrompt: "",
  showStatusBar: true,
  autoOpenNote: false,
};

const SUMMARIZER_LABELS: Record<SummarizerProvider, string> = {
  ollama: "Ollama (local)",
  claude: "Claude Code (claude)",
  codex: "Codex (codex)",
  custom: "Custom command",
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

    containerEl.createEl("h2", { text: "Transcription" });

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

    this.renderSummarizerSection(containerEl);

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

  private renderSummarizerSection(containerEl: HTMLElement): void {
    const s = this.plugin.settings;
    containerEl.createEl("h2", { text: "Summarizer" });

    new Setting(containerEl)
      .setName("Backend")
      .setDesc("How transcripts are turned into the summary and tasks.")
      .addDropdown((d) => {
        for (const [value, label] of Object.entries(SUMMARIZER_LABELS)) {
          d.addOption(value, label);
        }
        d.setValue(s.summarizer).onChange(async (v) => {
          s.summarizer = v as SummarizerProvider;
          await this.plugin.saveSettings();
          this.display(); // re-render conditional fields + privacy notice
        });
      });

    if (s.summarizer !== "ollama") {
      const warn = containerEl.createDiv({ cls: "setting-item-description" });
      warn.style.color = "var(--text-error)";
      warn.style.padding = "0 0 0.75em 0";
      warn.setText(
        "⚠️ This backend sends the transcript to an external service. quietnotes' local-only privacy guarantee applies to the Ollama backend only.",
      );
    }

    if (s.summarizer === "ollama") {
      new Setting(containerEl)
        .setName("Ollama URL")
        .setDesc("Default http://localhost:11434.")
        .addText((t) =>
          t.setValue(s.ollamaUrl).onChange(async (v) => {
            s.ollamaUrl = v.trim() || DEFAULT_SETTINGS.ollamaUrl;
            await this.plugin.saveSettings();
          }),
        );

      new Setting(containerEl)
        .setName("Ollama model")
        .setDesc("Click Refresh to pull the installed model list from Ollama.")
        .addText((t) =>
          t.setValue(s.ollamaModel).onChange(async (v) => {
            s.ollamaModel = v.trim() || DEFAULT_SETTINGS.ollamaModel;
            await this.plugin.saveSettings();
          }),
        )
        .addButton((b) =>
          b.setButtonText("Refresh").onClick(async () => {
            const models = await fetchOllamaModels(s.ollamaUrl);
            new Notice(
              models.length === 0 ? "Ollama unreachable or has no models" : `Found: ${models.join(", ")}`,
            );
          }),
        );
    } else if (s.summarizer === "claude" || s.summarizer === "codex") {
      const bin = s.summarizer === "claude" ? "claude" : "codex";
      new Setting(containerEl)
        .setName("Command / path")
        .setDesc(`Path to the ${bin} executable. Leave empty to resolve "${bin}" on PATH.`)
        .addText((t) =>
          t
            .setPlaceholder(bin)
            .setValue(s.summarizerCommand)
            .onChange(async (v) => {
              s.summarizerCommand = v.trim();
              await this.plugin.saveSettings();
            }),
        )
        .addButton((b) =>
          b.setButtonText("Detect").onClick(() => this.runDetect()),
        );

      new Setting(containerEl)
        .setName("Model")
        .setDesc(`Optional. Leave empty to use ${bin}'s default model.`)
        .addText((t) =>
          t.setValue(s.summarizerModel).onChange(async (v) => {
            s.summarizerModel = v.trim();
            await this.plugin.saveSettings();
          }),
        );
    } else if (s.summarizer === "custom") {
      new Setting(containerEl)
        .setName("Command")
        .setDesc(
          "Your command receives the prompt + transcript on stdin and must print the answer (JSON, optionally with surrounding prose) on stdout.",
        )
        .addTextArea((t) => {
          t.setPlaceholder("my-llm --json")
            .setValue(s.summarizerCommand)
            .onChange(async (v) => {
              s.summarizerCommand = v.trim();
              await this.plugin.saveSettings();
            });
          t.inputEl.rows = 2;
          t.inputEl.style.width = "100%";
        });
    }

    new Setting(containerEl)
      .setName("Test summarizer")
      .setDesc("Run a short canned transcript through the selected backend to verify it works.")
      .addButton((b) =>
        b
          .setButtonText("Test")
          .setCta()
          .onClick(() => this.runSummarizerTest()),
      );
  }

  private async runDetect(): Promise<void> {
    const s = this.plugin.settings;
    try {
      const r = await this.plugin.runSidecarRPC<{
        available: boolean;
        path: string;
        version: string;
        error?: string;
      }>("probe_summarizer", { provider: s.summarizer, command: s.summarizerCommand });
      if (r.available) {
        new Notice(`✓ Found ${s.summarizer} at ${r.path}${r.version ? ` (${r.version})` : ""}`);
      } else {
        new Notice(`✗ Not found: ${r.error ?? "unknown"}`);
      }
    } catch (e) {
      new Notice(`Detect failed: ${(e as { message?: string }).message ?? String(e)}`);
    }
  }

  private async runSummarizerTest(): Promise<void> {
    const s = this.plugin.settings;
    new Notice(`Testing ${s.summarizer}…`);
    try {
      const r = await this.plugin.runSidecarRPC<{
        tldr: string;
        key_points: string[];
        decisions: string[];
        tasks: { title: string; owner: string | null; due: string | null }[];
      }>("test_summarizer", {
        summarizer: s.summarizer,
        summarizer_model: s.summarizerModel,
        summarizer_command: s.summarizerCommand,
        ollama_model: s.ollamaModel,
        ollama_url: s.ollamaUrl,
      });
      new Notice(
        `✓ ${s.summarizer} works.\nTL;DR: ${r.tldr}\n${r.key_points.length} key points, ${r.tasks.length} tasks.`,
        10000,
      );
    } catch (e) {
      const err = e as { code?: string; message?: string };
      new Notice(`✗ Test failed (${err.code ?? "error"}): ${err.message ?? String(e)}`, 10000);
    }
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
