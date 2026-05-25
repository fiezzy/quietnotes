import { Plugin, FileSystemAdapter, Notice, Platform } from "obsidian";
import * as path from "path";
import { DEFAULT_SETTINGS, QuietnotesSettings, QuietnotesSettingTab } from "./settings";
import { StateMachine, statusBarText } from "./state";
import { SidecarClient, SidecarError } from "./sidecar-client";
import { classifyError, ErrorModal } from "./errors";

// Dev-mode hardcoded paths. Task 9 (onboarding) replaces these with a bundled venv.
const DEV_PYTHON = "/Users/fiezzyy/Documents/Projects/quietnotes/apps/sidecar/.venv/bin/python";
const DEV_SIDECAR_SCRIPT = "/Users/fiezzyy/Documents/Projects/quietnotes/apps/sidecar/sidecar.py";

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
    if (this.sidecar) {
      await this.sidecar.shutdown();
      this.sidecar = null;
    }
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

    const unsubscribe = this.state.subscribe((stage) => {
      if (!this.statusBarEl) return;
      this.statusBarEl.toggleClass("quietnotes-statusbar--recording", stage.kind === "recording");
      this.statusBarEl.setText(statusBarText(stage));
    });
    this.register(unsubscribe);

    this.statusBarEl.addEventListener("click", () => {
      const s = this.state.current;
      if (s.kind === "error") {
        const friendly = classifyError(s.code, s.message);
        const retry = friendly.retryable ? () => this.startRecording() : undefined;
        new ErrorModal(this.app, friendly, retry).open();
      }
    });

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
      new Notice("quietnotes: already processing — please wait");
    }
  }

  private getVaultPath(): string | null {
    const adapter = this.app.vault.adapter;
    if (!(adapter instanceof FileSystemAdapter)) return null;
    return adapter.getBasePath();
  }

  private async startRecording(): Promise<void> {
    if (!Platform.isDesktop) {
      new Notice("quietnotes is desktop-only");
      return;
    }

    const vaultPath = this.getVaultPath();
    if (!vaultPath) {
      new Notice("Cannot resolve vault path");
      return;
    }

    this.sidecar = new SidecarClient({
      pythonPath: DEV_PYTHON,
      sidecarScript: DEV_SIDECAR_SCRIPT,
      vault: vaultPath,
      subfolder: this.settings.outputSubfolder,
      whisperModel: this.settings.whisperModel,
      ollamaUrl: this.settings.ollamaUrl,
      ollamaModel: this.settings.ollamaModel,
      language: this.settings.language,
    });

    this.sidecar.on("event", (e: { event: string; stage?: string }) => {
      if (e.event === "stage") {
        if (e.stage === "transcribing") this.state.toTranscribing();
        else if (e.stage === "summarizing") this.state.toSummarizing();
        else if (e.stage === "writing") this.state.toWriting();
      }
    });

    this.sidecar.on("exit", () => {
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
      this.state.toError(e.code ?? "unknown", e.message ?? String(err), e.recoverable ?? false);
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
          const vaultPath = this.getVaultPath();
          if (vaultPath) {
            const relPath = path.relative(vaultPath, result.md_path);
            await this.app.workspace.openLinkText(relPath, "", false);
          }
        }
      } else {
        this.state.toError("no_md_path", "Sidecar did not return a markdown path", false);
      }
    } catch (err) {
      const e = err as SidecarError;
      this.state.toError(e.code ?? "unknown", e.message ?? String(err), e.recoverable ?? false);
    } finally {
      await this.sidecar.shutdown();
      this.sidecar = null;
    }
  }
}
