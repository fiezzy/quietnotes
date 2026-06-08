import { App, Modal, Setting, Notice } from "obsidian";

export interface FriendlyError {
  title: string;
  message: string;
  hint?: string;
  copyText?: string;
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
    case "summarizer_not_found":
      return {
        title: "Summarizer not found",
        message,
        hint: "The selected CLI (claude/codex) isn't on PATH. Open Settings → Summarizer and set its full path, or pick a different backend.",
      };
    case "summarizer_timeout":
      return {
        title: "Summarizer timed out",
        message,
        hint: "The agent didn't respond in time. Try again, or pick a faster model/backend.",
        retryable: true,
      };
    case "summarizer_bad_output":
      return {
        title: "Summarizer returned unusable output",
        message,
        hint: "The backend didn't return valid JSON. Retry; if it persists, try another backend or model.",
        retryable: true,
        copyText: message,
      };
    case "summarizer_failed":
      return {
        title: "Summarizer failed",
        message,
        hint: "The summarization backend errored. Check the details and retry.",
        retryable: true,
        copyText: message,
      };
    case "audio_capture_failed":
    case "audio_capture_hung":
      return {
        title: "Audio capture failed",
        message,
        hint: "Check Screen Recording and Microphone permissions for Obsidian in System Settings → Privacy & Security.",
        openSystemSettings: true,
        retryable: true,
      };
    case "no_audio_data":
      return {
        title: "No audio recorded",
        message: "The audio file came out empty.",
        hint: "This usually means Obsidian doesn't have Screen Recording or Microphone permission, or the recording was stopped instantly.",
        openSystemSettings: true,
        retryable: true,
      };
    case "sidecar_exited":
    case "not_spawned":
      return {
        title: "Sidecar process exited",
        message,
        hint: "Try again. If it persists, check the Developer Console for the sidecar's stderr.",
        retryable: true,
      };
    case "already_recording":
      return {
        title: "Already recording",
        message: "A recording is already in progress.",
        hint: "Stop the current recording before starting a new one.",
      };
    case "not_recording":
      return {
        title: "Nothing to stop",
        message: "There is no recording in progress.",
      };
    case "no_md_path":
      return {
        title: "Markdown not written",
        message,
        hint: "Check that the vault path is writeable and the configured subfolder is valid.",
        retryable: true,
      };
    case "unknown_method":
    case "internal_error":
    case "bad_json":
      return {
        title: "Internal error",
        message,
        hint: "This is likely a bug. Copy details and open an issue.",
        copyText: `${code}: ${message}`,
      };
    default:
      return {
        title: `Error: ${code}`,
        message,
        copyText: `${code}: ${message}`,
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

    const buttons = new Setting(contentEl);

    if (this.err.copyText) {
      buttons.addButton((b) =>
        b.setButtonText("Copy details").onClick(() => {
          navigator.clipboard.writeText(this.err.copyText!);
          new Notice("Copied");
        }),
      );
    }
    if (this.err.openSystemSettings) {
      buttons.addButton((b) =>
        b.setButtonText("Open System Settings").onClick(() => {
          // Privacy & Security pane. Requires the renderer to allow opening
          // external URLs of this scheme, which Obsidian does on macOS.
          window.open("x-apple.systempreferences:com.apple.preference.security?Privacy", "_blank");
        }),
      );
    }
    if (this.err.retryable && this.onRetry) {
      buttons.addButton((b) =>
        b
          .setButtonText("Retry")
          .setCta()
          .onClick(() => {
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
