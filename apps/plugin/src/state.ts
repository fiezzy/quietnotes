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
