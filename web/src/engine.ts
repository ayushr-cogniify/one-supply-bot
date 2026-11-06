import type { BootInfo, Check, Gap, Reply, Session } from "./types";

type Progress = { step: string; detail: string; done: boolean };
type Pending = { resolve: (v: unknown) => void; reject: (e: Error) => void };

const worker = new Worker(new URL("./worker.ts", import.meta.url), { type: "module" });
const pending = new Map<number, Pending>();
let seq = 0;
let onProgress: (p: Progress) => void = () => {};

worker.onmessage = (e) => {
  const m = e.data;
  if (m.type === "progress") return onProgress(m);
  const p = pending.get(m.id);
  if (!p) return;
  pending.delete(m.id);
  if (m.ok) p.resolve(m.result);
  else p.reject(new Error(m.error));
};

function call<T>(op: string, payload: Record<string, unknown> = {}): Promise<T> {
  const id = ++seq;
  return new Promise<T>((resolve, reject) => {
    pending.set(id, { resolve: resolve as (v: unknown) => void, reject });
    worker.postMessage({ id, op, ...payload });
  });
}

let booting: Promise<{ info: BootInfo; session: Session; gaps: Gap[] }> | null = null;

// One boot per page, even when React runs the effect twice in development
export function boot(progress: (p: Progress) => void, data: { label: string; base: string; headers: Record<string, string> }[]) {
  onProgress = progress;
  const base = new URL(import.meta.env.BASE_URL, window.location.href).href;
  booting ??= call<{ info: BootInfo; session: Session; gaps: Gap[] }>("boot", { base, data });
  booting.catch(() => { booting = null; });     // allow a retry after a failed boot
  return booting;
}

export const ask = (request: Record<string, unknown>, session: Session) => call<Reply>("handle", { request, session });
export const runChecks = () => call<Check[]>("checks");
