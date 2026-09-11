/** One private worker per engine, prewarmed without inspecting any user content. */
import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import { randomUUID } from "node:crypto";

interface Reply { id?: string; event: string; data?: unknown; code?: string; message?: string; elapsed_ms?: number }
type Pending = { id: string; resolve: (reply: Reply) => void; reject: (error: Error) => void };
type Phase = (phase: string, elapsed?: number) => void;

class ReaderService {
  child?: ChildProcessWithoutNullStreams;
  startup?: Promise<void>;
  pending?: Pending;
  active?: string;
  phase?: Phase;
  private buffer = "";
  private timer?: ReturnType<typeof setTimeout>;
  constructor(readonly binary: string) {}

  warm(): Promise<void> {
    if (this.startup) return this.startup;
    this.startup = new Promise<void>((resolve, reject) => {
      const child = this.child = spawn(this.binary, ["--serve"], { stdio: ["pipe", "pipe", "pipe"] });
      let ready = false;
      const fail = (error: Error) => {
        reject(error);
        if (this.child === child) this.stop(error);
      };
      this.timer = setTimeout(() => fail(new Error("读取服务启动超时，请重试。")), 20_000);
      child.stdout.setEncoding("utf8");
      child.stderr.setEncoding("utf8");
      child.stderr.on("data", () => {});
      child.stdin.on("error", fail);
      child.on("error", fail);
      child.on("close", () => fail(new Error("读取服务已退出；未改读其他窗口。")));
      child.stdout.on("data", (chunk: string) => {
        if (this.child !== child) return;
        this.buffer += chunk;
        // Keep room for all 15 x 4 MiB originals after base64 expansion + snapshot.
        if (this.buffer.length > 128 * 1024 * 1024) return fail(new Error("阅读结果过大，已停止。"));
        let newline: number;
        while ((newline = this.buffer.indexOf("\n")) !== -1) {
          const line = this.buffer.slice(0, newline);
          this.buffer = this.buffer.slice(newline + 1);
          let reply: Reply;
          try { reply = JSON.parse(line); }
          catch { return fail(new Error("读取服务协议错误，请更新安装包。")); }
          if (!ready) {
            if (reply.event !== "ready") return fail(new Error("读取服务版本不匹配。"));
            ready = true;
            clearTimeout(this.timer);
            resolve();
          } else if (reply.id === this.active) {
            if (reply.event === "snapshot") this.phase?.("snapshot", reply.elapsed_ms);
            else if (this.pending?.id === reply.id) {
              const pending = this.pending;
              this.pending = undefined;
              clearTimeout(this.timer);
              if (reply.event === "error") pending.reject(Object.assign(new Error(reply.message || "读取失败"), { code: reply.code }));
              else pending.resolve(reply);
            }
          }
        }
      });
    });
    void this.startup.catch(() => {});
    return this.startup;
  }

  stop(error = new Error("本轮已取消，未改读其他窗口。")) {
    clearTimeout(this.timer);
    const child = this.child;
    this.child = undefined;
    this.startup = undefined;
    this.buffer = "";
    this.pending?.reject(error);
    this.pending = undefined;
    this.active = undefined;
    this.phase = undefined;
    child?.kill("SIGKILL");
  }

  command(id: string, op: string, target?: object): Promise<Reply> {
    return new Promise((resolve, reject) => {
      if (!this.child || this.active !== id) return reject(new Error("阅读请求已失效。"));
      this.pending = { id, resolve, reject };
      this.timer = setTimeout(() => this.stop(new Error("读取超时，已释放目标。")), op === "bind" ? 10_000 : 120_000);
      this.child.stdin.write(JSON.stringify({ id, op, target }) + "\n");
    });
  }

  async bind(target: object, signal?: AbortSignal, phase?: Phase) {
    if (this.active) throw new Error("已有阅读请求进行中，请等待当前阅读完成。");
    const id = randomUUID();
    this.active = id;
    this.phase = phase;
    let reading = false;
    let expiry: ReturnType<typeof setTimeout> | undefined;
    const cancel = () => {
      clearTimeout(expiry);
      signal?.removeEventListener("abort", cancel);
      if (this.active !== id) return;
      if (this.pending || reading) this.stop();
      else {
        this.child?.stdin.write(JSON.stringify({ id, op: "cancel" }) + "\n");
        this.active = undefined;
        this.phase = undefined;
      }
    };
    signal?.addEventListener("abort", cancel, { once: true });
    try {
      await this.warm();
      if (signal?.aborted || this.active !== id) throw new Error("本轮已取消，未读取窗口。");
      const bound = await this.command(id, "bind", target);
      if (bound.event !== "bound") throw new Error("目标绑定未完成。");
      phase?.("bound", bound.elapsed_ms);
      expiry = setTimeout(cancel, 125_000);
      return {
        cancel,
        read: async () => {
          clearTimeout(expiry);
          reading = true;
          phase?.("capturing");
          try {
            const result = await this.command(id, "read");
            if (result.event !== "result") throw new Error("阅读结果未完成。");
            return JSON.stringify(result.data);
          } finally { reading = false; }
        },
      };
    } catch (error) { cancel(); throw error; }
  }
}

const services = new Map<string, ReaderService>();
function service(binary: string) {
  let reader = services.get(binary);
  if (!reader) { reader = new ReaderService(binary); services.set(binary, reader); }
  return reader;
}
export function warmReader(binary: string) { void service(binary).warm().catch(() => {}); }
export function stopReaders() { for (const reader of services.values()) reader.stop(); services.clear(); }
export function bindReader(binary: string, target: object, signal?: AbortSignal, phase?: Phase) {
  return service(binary).bind(target, signal, phase);
}
