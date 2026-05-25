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
