import { spawn, ChildProcessWithoutNullStreams } from "child_process";
import { EventEmitter } from "events";
import * as fs from "fs";
import * as os from "os";
import * as path from "path";

export type StageEvent = { event: "stage"; stage: "transcribing" | "summarizing" | "writing" };
export type SidecarEvent = StageEvent;

export interface SidecarError {
  code: string;
  message: string;
  recoverable: boolean;
}

export type SummarizerProvider = "ollama" | "claude" | "codex" | "custom";

export interface SpawnOptions {
  pythonPath: string;
  sidecarScript: string;
  vault: string;
  subfolder: string;
  whisperModel: string;
  ollamaUrl: string;
  ollamaModel: string;
  language: string; // "" = auto
  summarizer: SummarizerProvider;
  summarizerModel: string; // "" = CLI default (claude/codex)
  summarizerCommand: string; // path/command for claude|codex|custom
  systemPrompt: string; // "" = built-in
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
  private systemPromptFile: string | null = null;

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
      "--summarizer", this.opts.summarizer,
      "--ollama-model", this.opts.ollamaModel,
      "--ollama-url", this.opts.ollamaUrl,
    ];
    if (this.opts.summarizerModel) {
      args.push("--summarizer-model", this.opts.summarizerModel);
    }
    if (this.opts.summarizerCommand) {
      args.push("--summarizer-command", this.opts.summarizerCommand);
    }
    if (this.opts.systemPrompt && this.opts.systemPrompt.trim()) {
      // Long prompt text goes via a temp file, never argv.
      this.systemPromptFile = path.join(
        os.tmpdir(),
        `quietnotes-prompt-${process.pid}-${this.nextId}.txt`,
      );
      try {
        fs.writeFileSync(this.systemPromptFile, this.opts.systemPrompt, "utf-8");
        args.push("--system-prompt-file", this.systemPromptFile);
      } catch (e) {
        console.warn("[quietnotes] could not write system prompt file", e);
        this.systemPromptFile = null;
      }
    }
    if (this.opts.language) {
      args.push("--language", this.opts.language);
    }

    // Obsidian (Electron) inherits a minimal PATH from launchd that doesn't
    // include Homebrew. mlx-whisper shells out to `ffmpeg` for audio decoding,
    // so we extend PATH explicitly to cover both Apple Silicon and Intel
    // Homebrew install locations plus the standard system paths.
    const enhancedPath = [
      "/opt/homebrew/bin",
      "/opt/homebrew/sbin",
      "/usr/local/bin",
      "/usr/local/sbin",
      process.env.PATH,
    ]
      .filter(Boolean)
      .join(":");

    this.proc = spawn(this.opts.pythonPath, args, {
      stdio: ["pipe", "pipe", "pipe"],
      env: { ...process.env, PATH: enhancedPath },
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

      let msg: { id?: string; result?: unknown; error?: SidecarError; event?: string; stage?: string };
      try {
        msg = JSON.parse(line);
      } catch {
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
    if (this.systemPromptFile) {
      try {
        fs.unlinkSync(this.systemPromptFile);
      } catch {
        /* best effort */
      }
      this.systemPromptFile = null;
    }
    if (!this.proc) return;
    const proc = this.proc;
    proc.stdin.end();
    await new Promise<void>((r) => {
      proc.once("exit", () => r());
      setTimeout(() => {
        if (!proc.killed) {
          proc.kill("SIGTERM");
        }
        r();
      }, 3000);
    });
  }
}
