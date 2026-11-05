/// <reference lib="webworker" />
// The engine: Python (pandas) running in this worker through Pyodide. It loads the same osb_tools
// and osb_agent packages that run natively in the tests, plus the data bundle, then answers
// requests from the page. Nothing here leaves the browser.

const PYODIDE = "https://cdn.jsdelivr.net/pyodide/v0.27.7/full/";

type Data = { base: string; headers: Record<string, string> };
type Req = { id: number; op: "boot" | "handle" | "checks"; base?: string; data?: Data; request?: unknown; session?: unknown };

// eslint-disable-next-line @typescript-eslint/no-explicit-any
let api: any = null;

const progress = (step: string, detail = "", done = false) =>
  (self as DedicatedWorkerGlobalScope).postMessage({ type: "progress", step, detail, done });

async function boot(base: string, data: Data) {
  const t0 = performance.now();
  progress("runtime", "Python runtime");
  const { loadPyodide } = await import(/* @vite-ignore */ `${PYODIDE}pyodide.mjs`);
  const py = await loadPyodide({ indexURL: PYODIDE });
  progress("runtime", "", true);

  progress("packages", "pandas, numpy");
  await py.loadPackage(["pandas", "pyyaml"]);
  progress("packages", "", true);

  progress("engine", "calculation engines");
  const eng = await fetch(`${base}engine.zip`);
  if (!eng.ok) throw new Error(`engine.zip: HTTP ${eng.status}`);
  py.unpackArchive(await eng.arrayBuffer(), "zip", { extractDir: "/engine" });
  progress("engine", "", true);

  progress("data", "extracts");
  // The extracts come from the private bucket with the signed-in user's token; never from the site
  const get = (f: string) => fetch(`${data.base}${f}`, { headers: data.headers, cache: "no-store" });
  const mres = await get("manifest.json");
  if (mres.status === 400 || mres.status === 401 || mres.status === 403) throw new Error("your session is not allowed to read the data. Sign out and sign in again");
  if (!mres.ok) throw new Error(`manifest.json: HTTP ${mres.status}`);
  const manifest = await mres.json();
  py.FS.mkdirTree("/data");
  py.FS.writeFile("/data/manifest.json", JSON.stringify(manifest));
  const frames = Object.values(manifest.frames) as { file: string; rows: number; bytes: number }[];
  let loaded = 0;
  const total = frames.reduce((a, f) => a + f.bytes, 0);
  await Promise.all(
    frames.map(async (f) => {
      const r = await get(f.file);
      if (!r.ok) throw new Error(`${f.file}: HTTP ${r.status}`);
      py.FS.writeFile(`/data/${f.file}`, new Uint8Array(await r.arrayBuffer()));
      loaded += f.bytes;
      progress("data", `${(loaded / 1e6).toFixed(1)} of ${(total / 1e6).toFixed(1)} MB`);
    }),
  );
  progress("data", "", true);

  progress("start", "reading sheets into memory");
  py.runPython("import sys; sys.path.insert(0, '/engine')");
  api = py.pyimport("osb_agent.api");
  const out = JSON.parse(api.boot("/data"));
  progress("start", "", true);
  out.info.boot_ms = Math.round(performance.now() - t0);
  return out;
}

self.onmessage = async (e: MessageEvent<Req>) => {
  const { id, op } = e.data;
  try {
    let result: unknown;
    if (op === "boot") result = await boot(e.data.base!, e.data.data!);
    else if (op === "handle") result = JSON.parse(api.handle(JSON.stringify(e.data.request), JSON.stringify(e.data.session)));
    else if (op === "checks") result = JSON.parse(api.checks());
    (self as DedicatedWorkerGlobalScope).postMessage({ type: "reply", id, ok: true, result });
  } catch (err) {
    (self as DedicatedWorkerGlobalScope).postMessage({ type: "reply", id, ok: false, error: String(err) });
  }
};
