import { useState } from "react";
import type { Block, Card, Column, GridRow, WocCell } from "../types";
import { colourClass, fmtInt, fmtSigned, fmtWoc, prettyFile, shortHash } from "../format";

const ICON: Record<string, string> = { warn: "?", bad: "!", good: "✓", info: "i" };

function Woc({ v, c }: { v?: number | null; c?: string | null }) {
  return <span className={`w ${colourClass(c)}`}>{fmtWoc(v)}</span>;
}

function Pair({ cell }: { cell: WocCell }) {
  const same = cell.before === cell.after && cell.cb === cell.ca;
  if (cell.before === undefined || same) return <Woc v={cell.after} c={cell.ca} />;
  return (
    <span className="pair" title={`before ${fmtWoc(cell.before)} (${cell.cb}) → after ${fmtWoc(cell.after)} (${cell.ca})`}>
      <span className="b">{fmtWoc(cell.before)}</span>
      <span className="arr">{"→"}</span>
      <Woc v={cell.after} c={cell.ca} />
    </span>
  );
}

function Cell({ col, row }: { col: Column; row: Record<string, unknown> }) {
  const v = row[col.key];
  switch (col.format) {
    case "int":
      return <>{fmtInt(v)}</>;
    case "signed":
      return <span className={Number(v) > 0 ? "pos" : Number(v) < 0 ? "neg" : "zero"}>{fmtSigned(v)}</span>;
    case "pct0":
      return v === null || v === undefined ? <span className="zero">excluded</span> : <>{Math.round(Number(v))}%</>;
    case "pct1":
      return <>{Number(v).toFixed(1)}%</>;
    case "woc":
      return <Woc v={v as number} c={row[col.colour_key ?? "colour"] as string} />;
    case "share":
      return (
        <span className="sharebar">
          <i style={{ width: `${Math.max(2, Number(v) * 1.6)}px` }} />
          {Number(v).toFixed(1)}%
        </span>
      );
    case "code":
      return v ? <span className="code">{String(v)}</span> : <span className="zero">{"–"}</span>;
    case "int_or_input":
      return v === null || v === undefined ? <span className="need">needs input</span> : <>{fmtInt(v)}</>;
    case "gap":
      if (v === null || v === undefined) return <span className="need">needs input</span>;
      return Number(v) > 0 ? <span className="gapv">{fmtInt(v)}</span> : <span className="zero">0</span>;
    default:
      return <>{v === null || v === undefined ? "" : String(v)}</>;
  }
}

const numeric = (c: Column) => ["int", "signed", "pct0", "pct1", "int_or_input", "gap"].includes(c.format ?? "");

function TableBlock({ b }: { b: Extract<Block, { type: "table" }> }) {
  const [open, setOpen] = useState(!b.collapsed);
  if (!b.rows.length && b.text) return <div className="dashed"><div className="blk-t">{b.title}</div><div className="note-box">{b.text}</div></div>;
  const body = (
    <div className="tw">
      <table className="t">
        <thead>
          <tr>{b.columns.map((c) => <th key={c.key} className={numeric(c) || c.format === "woc" ? "r" : ""}>{c.label}</th>)}</tr>
        </thead>
        <tbody>
          {b.rows.map((r, i) => (
            <tr key={i} className={r.in_window ? "shade" : r.excluded ? "dim" : ""}>
              {b.columns.map((c) => (
                <td key={c.key} className={[numeric(c) || c.format === "woc" ? "r n" : c.key === "material" ? "n" : "", c.strong ? "strong" : ""].join(" ")}>
                  <Cell col={c} row={r} />
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
  return (
    <div className={b.tone === "dashed" ? "dashed" : ""}>
      {b.title && (
        <div className="blk-t">
          {b.title}
          {b.collapsed && <button className="linkbtn" onClick={() => setOpen(!open)}>{open ? "Hide" : `Show ${b.rows.length} rows`}</button>}
        </div>
      )}
      {open && body}
      {open && b.note && <div className="blk-note">{b.note}</div>}
    </div>
  );
}

function Legend() {
  return (
    <span className="legend">
      <span className="w red">&lt;2</span><span className="w yellow">2–3.9</span><span className="w green">4–5.0</span><span className="w blue">&gt;5.0</span>
    </span>
  );
}

function WocGrid({ b }: { b: Extract<Block, { type: "wocgrid" }> }) {
  return (
    <div>
      <div className="blk-t">{b.title}<span style={{ marginLeft: "auto" }}><Legend /></span></div>
      <div className="tw">
        <table className="t grid">
          <thead>
            <tr>
              <th>{b.row_label ?? "LC"}</th>
              {b.weeks.map((w) => <th key={w.calweek} className="r">{w.calweek}<div className="muted" style={{ textTransform: "none", fontWeight: 400, letterSpacing: 0 }}>{w.label.replace("week of ", "")}</div></th>)}
            </tr>
          </thead>
          <tbody>
            {b.rows.map((r) => (
              <tr key={r.loc} className={[r.changed ? "hl" : "", r.loc === "NETWORK" ? "net" : ""].join(" ")}>
                <td className="loc"><span className="c">{r.loc === "NETWORK" ? "Network" : r.loc}</span><span className="nm">{r.name}</span></td>
                {r.cells.map((c, i) => <td key={i} className="r">{b.single ? <Woc v={c.after} c={c.ca} /> : <Pair cell={c} />}</td>)}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {b.note && <div className="blk-note">{b.note}</div>}
    </div>
  );
}

type Measure = "woc" | "balance" | "demand" | "supply";

function PirBlock({ b, card }: { b: Extract<Block, { type: "pir" }>; card: Card }) {
  const [m, setM] = useState<Measure>("woc");
  const [open, setOpen] = useState<Record<string, boolean>>({});
  const items = b.rows.filter((r) => r.level === "NETWORK").length;
  const show = (r: GridRow) => r.level !== "LC" || open[r.material ?? ""] || items === 1;
  const val = (c: WocCell) =>
    m === "woc" ? <Woc v={c.after} c={c.ca} /> : <>{fmtInt(m === "balance" ? c.balance : m === "demand" ? c.demand : c.supply)}</>;
  return (
    <div>
      <div className="blk-t">
        <span className="seg">
          {(["woc", "balance", "demand", "supply"] as Measure[]).map((k) => (
            <button key={k} className={m === k ? "on" : ""} onClick={() => setM(k)}>
              {{ woc: "Weeks of cover", balance: "Balance", demand: "Total demand", supply: "Available supply" }[k]}
            </button>
          ))}
        </span>
        <span style={{ marginLeft: "auto", display: "flex", gap: 10, alignItems: "center" }}>
          {m === "woc" && <Legend />}
          {card.export && <button className="btn sm" onClick={() => import("../exporter").then((m) => m.downloadPir(card))}>Download .xlsx</button>}
        </span>
      </div>
      <div className="tw">
        <table className="t grid pir">
          <thead>
            <tr>
              <th>{items > 1 ? "Item · LC" : "LC"}</th>
              {b.weeks.map((w) => <th key={w.calweek} className="r">{w.calweek}<div className="muted" style={{ textTransform: "none", fontWeight: 400, letterSpacing: 0 }}>{w.label.replace("week of ", "")}</div></th>)}
            </tr>
          </thead>
          <tbody>
            {b.rows.filter(show).map((r, i) => (
              <tr key={i} className={r.level === "GROUP" ? "group" : r.level === "NETWORK" ? "item" : "lc"}
                onClick={r.level === "NETWORK" && items > 1 ? () => setOpen({ ...open, [r.material ?? ""]: !open[r.material ?? ""] }) : undefined}>
                <td className="loc">
                  {r.level === "NETWORK" && items > 1 && <span className="caret">{open[r.material ?? ""] ? "▾" : "▸"}</span>}
                  {r.level === "LC" ? <><span className="c">{r.loc}</span> <span className="muted" style={{ fontSize: 12 }}>{r.name}</span></> : <span style={{ fontSize: 12.5 }}>{r.level === "NETWORK" && items === 1 ? "Network" : r.name}</span>}
                </td>
                {r.cells.map((c, j) => <td key={j} className="r n">{val(c)}</td>)}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {b.note && <div className="blk-note">{items > 1 ? "Click an item to show its four LCs. " : ""}{b.note}</div>}
    </div>
  );
}

function PathBlock({ b }: { b: Extract<Block, { type: "path" }> }) {
  const mark: Record<string, string> = { unknown: "?", branch: "✓", pass: "✓", fail: "–" };
  return (
    <div>
      <div className="blk-t">{b.title}</div>
      <div className="tw">
        <table className="t">
          <thead><tr><th>Rule</th><th>Test</th><th>Result</th><th>Evidence</th></tr></thead>
          <tbody>
            {b.rows.map((r) => (
              <tr key={r.rule} className={r.result === "unknown" ? "unknown" : r.result === "pass" ? "pass" : ""}>
                <td className="rule">{r.rule}</td>
                <td style={{ minWidth: 200 }}>{r.test}</td>
                <td><span className={`res ${r.result}`}>{mark[r.result]} {r.result_label}</span></td>
                <td className="ev">{r.evidence}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {b.note && <div className="blk-note">{b.note}</div>}
    </div>
  );
}

function BlockView({ b, card }: { b: Block; card: Card }) {
  switch (b.type) {
    case "kv":
      return (
        <div className="kv">
          {b.items.map((it) => (
            <div key={it.label}>
              <div className="l">{it.label}</div>
              <div className="v">{it.value}</div>
              {it.sub && <div className="s">{it.sub}</div>}
              {it.badge && <span className={`badge ${it.badge}`}>{it.badge === "planner" ? "planner-entered" : "needs input"}</span>}
            </div>
          ))}
        </div>
      );
    case "banner":
      return (
        <div className={`banner ${b.tone ?? "info"}`}>
          <span className="ic">{ICON[b.tone ?? "info"]}</span>
          <div><div className="bt">{b.title}</div>{b.text && <div className="bx">{b.text}</div>}</div>
        </div>
      );
    case "note":
      return <div className={`note-box ${b.tone ?? ""}`}>{b.title && <div className="blk-t">{b.title}</div>}{b.text}</div>;
    case "table":
      return <TableBlock b={b} />;
    case "wocgrid":
      return <WocGrid b={b} />;
    case "pir":
      return <PirBlock b={b} card={card} />;
    case "path":
      return <PathBlock b={b} />;
  }
}

export default function CardView({ card, onTrace, onClose, flash }: { card: Card; onTrace: () => void; onClose: () => void; flash: boolean }) {
  const [min, setMin] = useState(false);
  return (
    <section className={`card ${min ? "min" : ""} ${flash ? "flash" : ""}`} data-card={card.scenario}>
      <header className="card-h">
        <span className="tag">{card.tag}</span>
        <h2>{card.title}</h2>
        <div className="acts">
          <button className="linkbtn" onClick={onTrace}>Open trace</button>
          <button className="iconbtn" title={min ? "Expand" : "Collapse"} onClick={() => setMin(!min)}>
            <svg width="16" height="16" viewBox="0 0 16 16"><path d={min ? "M4 6l4 4 4-4" : "M4 10l4-4 4 4"} stroke="currentColor" strokeWidth="1.6" fill="none" /></svg>
          </button>
          <button className="iconbtn" title="Close card" onClick={onClose}>
            <svg width="16" height="16" viewBox="0 0 16 16"><path d="M4 4l8 8M12 4l-8 8" stroke="currentColor" strokeWidth="1.6" /></svg>
          </button>
        </div>
      </header>
      <div className="card-b">
        <div className="sub">{card.subtitle}</div>
        {card.blocks.map((b, i) => <BlockView key={i} b={b} card={card} />)}
      </div>
      {!min && card.sources.length > 0 && (
        <div className="sources">
          <span className="eyebrow" style={{ margin: 0 }}>Sources</span>
          {card.sources.map((s, i) => (
            <div className="row" key={i}>
              <span className="f">{prettyFile(s.file)} {"·"} {s.sheet}</span>
              {s.rows ? <span className="rows">rows {s.rows}</span> : <span className="rows">{fmtInt(s.count)} rows</span>}
              {s.hash && <span className="h">#{shortHash(s.hash)}</span>}
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
