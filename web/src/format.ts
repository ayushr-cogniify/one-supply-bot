export const fmtInt = (v: unknown) =>
  v === null || v === undefined || v === "" || Number.isNaN(Number(v)) ? "" : Math.round(Number(v)).toLocaleString("en-US");

export const fmtSigned = (v: unknown) => {
  const n = Math.round(Number(v));
  return n > 0 ? `+${n.toLocaleString("en-US")}` : n.toLocaleString("en-US");
};

export const fmtWoc = (v: unknown) => (v === null || v === undefined ? "–" : Number(v).toFixed(1));

export const colourClass = (c?: string | null) =>
  c === "red" || c === "yellow" || c === "green" || c === "blue" ? c : "na";

export const shortHash = (h?: string | null) => (h ? h.slice(0, 7) : "");

export const prettyFile = (f: string) => f.replace(/\.xlsx$/i, "").replace(/_/g, " ").replace(/^Data Extract (for )?Cogniify - /i, "");
