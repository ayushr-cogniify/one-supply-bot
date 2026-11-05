import { useState } from "react";
import type { BootInfo, Check, Session, Trace } from "../types";
import { fmtInt, prettyFile, shortHash } from "../format";

function Shell({ title, sub, onClose, children, tabs }: { title: string; sub?: string; onClose: () => void; children: React.ReactNode; tabs?: React.ReactNode }) {
  return (
    <>
      <div className="scrim" onClick={onClose} />
      <div className="drawer" role="dialog" aria-label={title}>
        <div className="drawer-h">
          <div><h2>{title}</h2>{sub && <div className="muted" style={{ fontSize: 12.5 }}>{sub}</div>}</div>
          <button className="btn sm" style={{ marginLeft: "auto" }} onClick={onClose}>Close</button>
        </div>
        {tabs}
        <div className="drawer-b">{children}</div>
      </div>
    </>
  );
}

const STEP_NAME: Record<string, string> = {
  scope_gate: "Scope gate", slots: "Slot extraction", clarify: "Clarifying question", clarified: "Planner answered",
  follow_up: "Follow-up", echo_back: "Echo-back", tool: "Tool call", narrative: "Narrative drafted",
  validator: "Numeric validator", draft_action: "Action drafted", approval: "Approval", gap_saved: "Planner entry saved",
  clarify_cap: "Question cap reached", pacing_skipped: "Planner skipped", alternate_code: "Alternate code", error: "Error", done: "Done",
};

function spanDetail(s: Record<string, unknown>) {
  const { step, ms, ...rest } = s;
  void ms;
  if (step === "scope_gate") return `${rest.label} · confidence ${rest.confidence} · ${rest.engine} · ${rest.reason}`;
  if (step === "tool") return `${rest.name} → ${rest.status} · ${rest.result_id} · ${rest.rows_used} source rows · ${rest.tool_ms} ms`;
  if (step === "validator") return rest.passed ? "Passed: every number in the narrative matches a tool result" : `Failed: ${(rest.unverified as string[]).join(", ")}`;
  if (step === "echo_back") return String(rest.text);
  if (step === "clarify") return `${rest.template ?? rest.slot} · question ${rest.question}`;
  return null;
}

export function TraceDrawer({ traces, onClose }: { traces: Trace[]; onClose: () => void }) {
  const first = traces[0];
  return (
    <Shell title={`Trace ${traces.map((t) => t.id).join(" \u2192 ")}`} sub={first.request ? `\u201c${first.request}\u201d` : undefined} onClose={onClose}>
      <p className="muted" style={{ marginTop: 0, fontSize: 12.5 }}>Every step of this answer, in order, across {traces.length === 1 ? "one turn" : `${traces.length} turns`}. In the Databricks build this is the MLflow trace linked from the card.</p>
      {traces.map((trace, ti) => <TraceTurn key={trace.id} trace={trace} index={ti} total={traces.length} />)}
    </Shell>
  );
}

function TraceTurn({ trace, index, total }: { trace: Trace; index: number; total: number }) {
  return (
    <div style={{ marginBottom: 18 }}>
      {total > 1 && <div className="eyebrow" style={{ marginTop: index ? 14 : 0 }}>Turn {index + 1} \u00b7 {trace.request}</div>}
      {trace.spans.map((s, i) => {
        const d = spanDetail(s);
        const extra = { ...s } as Record<string, unknown>;
        delete extra.step; delete extra.ms;
        return (
          <div className="span" key={i}>
            <div className="ms">{s.ms} ms</div>
            <div>
              <div className="nm">{STEP_NAME[s.step] ?? s.step}</div>
              {d && <div className="dt">{d}</div>}
              {s.step === "tool" && <pre>{JSON.stringify(s.args, null, 1)}</pre>}
              {s.step === "slots" && <pre>{JSON.stringify(s.slots, null, 1)}</pre>}
              {!d && !["tool", "slots", "done"].includes(s.step) && Object.keys(extra).length > 0 && <pre>{JSON.stringify(extra, null, 1)}</pre>}
            </div>
          </div>
        );
      })}
      {trace.results.length > 0 && <h3 style={{ fontSize: 14, margin: "20px 0 6px" }}>Tool results</h3>}
      {trace.results.map((r) => (
        <details key={r.result_id} style={{ borderBottom: "1px solid var(--line-2)", padding: "8px 0" }}>
          <summary style={{ cursor: "pointer", fontSize: 13 }}>
            <span className="mono">{r.result_id}</span> {"·"} {r.status} {"·"} {r.rows_used_total} source rows
          </summary>
          {r.warnings.length > 0 && <div className="muted" style={{ fontSize: 12.5, marginTop: 6 }}>{r.warnings.join(" · ")}</div>}
          <pre>{JSON.stringify(r.values, null, 1)}</pre>
          {r.rows_used.length > 0 && (
            <pre>{r.rows_used.map((u) => `${u.source_file} · ${u.source_sheet} · ${u.source_row ? "row " + u.source_row : u.rows + " rows"} · #${shortHash(u.hash)}`).join("\n")}{r.rows_used_total > r.rows_used.length ? `\n… and ${r.rows_used_total - r.rows_used.length} more` : ""}</pre>
          )}
        </details>
      ))}
    </div>
  );
}

type Tab = "load" | "checks" | "sheets" | "audit" | "models";

export function AdminDrawer({ info, session, onClose, onChecks, onReset, onRetract }: {
  info: BootInfo; session: Session; onClose: () => void; onChecks: () => Promise<Check[]>; onReset: () => void; onRetract: (sheet: string, rowId: string) => void;
}) {
  const [tab, setTab] = useState<Tab>("load");
  const [checks, setChecks] = useState<Check[] | null>(null);
  const [running, setRunning] = useState(false);
  const [ms, setMs] = useState(0);
  const lr = info.load_report;
  const run = async () => {
    setRunning(true);
    const t = performance.now();
    try { setChecks(await onChecks()); } finally { setMs(Math.round(performance.now() - t)); setRunning(false); }
  };
  const tabs = (
    <div className="tabs">
      {([["load", "Load report"], ["checks", "Golden checks"], ["sheets", "Planner entries"], ["audit", "Audit log"], ["models", "Rules and models"]] as [Tab, string][]).map(([k, l]) => (
        <button key={k} className={tab === k ? "on" : ""} onClick={() => setTab(k)}>{l}</button>
      ))}
    </div>
  );
  return (
    <Shell title="Admin" sub={`Bundle built ${info.bundle_created?.replace("T", " ").slice(0, 16)} UTC · engine ready in ${((info.boot_ms ?? 0) / 1000).toFixed(1)} s`} onClose={onClose} tabs={tabs}>
      {tab === "load" && (
        <>
          <p className="muted" style={{ marginTop: 0, fontSize: 12.5 }}>Each workbook is read as delivered and checked against the schema contract. A file whose headers differ is rejected. Cleaning happens in memory; the files are never edited.</p>
          <table className="t">
            <thead><tr><th>Workbook · sheet</th><th className="r">Headers</th><th className="r">Rows</th><th>Notes</th></tr></thead>
            <tbody>
              {(lr.sheets ?? []).map((s, i) => (
                <tr key={i}>
                  <td>{prettyFile(s.file)}<div className="muted" style={{ fontSize: 12 }}>{s.sheet}</div></td>
                  <td className="r n">{s.headers ?? "–"}</td>
                  <td className="r n">{fmtInt(s.rows)}</td>
                  <td style={{ fontSize: 12.5 }}>{s.dropped ? `${fmtInt(s.dropped)} ${s.note}` : s.note ?? ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <h3 style={{ fontSize: 14, margin: "20px 0 6px" }}>Matched across extracts</h3>
          <table className="t"><tbody>
            {(lr.matches ?? []).map((m) => <tr key={m.label}><td>{m.label}</td><td className="r n">{fmtInt(m.matched)} of {fmtInt(m.of)}</td></tr>)}
            {lr.calendar && <tr><td>Kinaxis calendar</td><td className="r n">{lr.calendar.first_week} ({lr.calendar.first_start}) to {lr.calendar.last_week}, {lr.calendar.weeks} weeks</td></tr>}
            {lr.inventory_dates && <tr><td>SAP inventory snapshot dates</td><td className="r n">{lr.inventory_dates.first} to {lr.inventory_dates.last}, {lr.inventory_dates.count} dates</td></tr>}
          </tbody></table>
        </>
      )}
      {tab === "checks" && (
        <>
          <p className="muted" style={{ marginTop: 0, fontSize: 12.5 }}>The golden set from the Engineering appendix, recomputed now in this browser from the loaded extracts. The same checks run in the test suite against the original workbooks.</p>
          <button className="btn primary" disabled={running} onClick={run}>{running ? "Running…" : checks ? "Run again" : "Run golden checks"}</button>
          {checks && (
            <>
              <p style={{ fontSize: 13, margin: "14px 0 4px" }}><b className={checks.every((c) => c.passed) ? "ok" : "bad"}>{checks.filter((c) => c.passed).length} of {checks.length} passed</b> <span className="muted">in {(ms / 1000).toFixed(1)} s</span></p>
              {checks.map((c, i) => (
                <div className="check" key={i}>
                  <span className={c.passed ? "ok" : "bad"}>{c.passed ? "✓" : "✗"}</span>
                  <span className="tg">{c.tag}</span>
                  <div><div style={{ fontSize: 13 }}>{c.name}</div><div className="d">{c.detail}</div></div>
                </div>
              ))}
            </>
          )}
        </>
      )}
      {tab === "sheets" && (
        <>
          <div style={{ display: "flex", gap: 10, alignItems: "center", marginBottom: 10 }}>
            <p className="muted" style={{ margin: 0, fontSize: 12.5, flex: 1 }}>Supplement sheets in the delivered sheets' style. Rows are append-only; retracting flags a row and never deletes it.</p>
            <button className="btn sm" onClick={() => import("../exporter").then((m) => m.downloadSupplements(session))}>Download OSB_Supplements.xlsx</button>
          </div>
          {Object.entries(session.supplements).map(([sheet, rows]) => (
            <div key={sheet} style={{ marginBottom: 16 }}>
              <div className="blk-t">{sheet} <span className="muted" style={{ fontWeight: 400 }}>{rows.length} row{rows.length === 1 ? "" : "s"}</span></div>
              {rows.length > 0 && (
                <div className="tw"><table className="t">
                  <thead><tr>{Object.keys(rows[0]).filter((k) => k !== "retracted").map((k) => <th key={k}>{k}</th>)}<th /></tr></thead>
                  <tbody>{rows.map((r, i) => (
                    <tr key={i} className={r.retracted ? "dim" : ""}>
                      {Object.entries(r).filter(([k]) => k !== "retracted").map(([k, v]) => <td key={k} className="n" style={{ fontSize: 12 }}>{v === null || v === undefined ? "" : String(v)}</td>)}
                      <td>{r.retracted ? <span className="muted">retracted</span> : !["Approvals", "Draft_Actions"].includes(sheet) && <button className="linkbtn" onClick={() => onRetract(sheet, String(r.row_id))}>Retract</button>}</td>
                    </tr>
                  ))}</tbody>
                </table></div>
              )}
            </div>
          ))}
        </>
      )}
      {tab === "audit" && (
        session.audit.length === 0 ? <p className="muted">No entries yet. Every planner entry, retraction and approval is recorded here.</p> :
          <table className="t"><thead><tr><th>Time (UTC)</th><th>User</th><th>Event</th><th>Ref</th></tr></thead>
            <tbody>{[...session.audit].reverse().map((a, i) => <tr key={i}><td className="n" style={{ fontSize: 12 }}>{a.at.replace("T", " ").slice(0, 19)}</td><td>{a.user}</td><td>{a.event}</td><td className="n" style={{ fontSize: 12 }}>{a.ref}</td></tr>)}</tbody></table>
      )}
      {tab === "models" && (
        <>
          <table className="t">
            <thead><tr><th>Stage</th><th>PRD model</th><th>Endpoint</th><th>Running now</th></tr></thead>
            <tbody>{info.models.map((m) => <tr key={m.stage}><td>{m.stage}</td><td>{m.model}</td><td className="n" style={{ fontSize: 12 }}>{m.endpoint}</td><td>{m.running}</td></tr>)}</tbody>
          </table>
          <p className="muted" style={{ fontSize: 12.5 }}>Claude Haiku 5.5 reads each question (scope and request details) through the osb-nlu function; the API key stays server-side and only signed-in users can call it. If Claude is slow or unavailable the rules engine takes over. Claude never sees Reckitt data and never produces a number: items are resolved by the item tool, weeks are converted by code, every number comes from the calculation engines, and the numeric validator checks every narrative before it is shown.</p>
          <h3 style={{ fontSize: 14, margin: "18px 0 6px" }}>Rules</h3>
          <dl className="kvl">
            <dt>WoC</dt><dd>Projected stock in week w / mean total demand of weeks w+1 to w+6</dd>
            <dt>Colour bands</dt><dd>On the 1-decimal value: under 2 red, 2 to 3.9 yellow, 4 to 5.0 green, above 5.0 blue</dd>
            <dt>Attainment</dt><dd>Sum of actuals / sum of fixed volume per month; months with no fixed volume excluded; trailing 3 calendar months</dd>
            <dt>PFR</dt><dd>1 − PFR cuts / cases ordered; NonPFR cuts excluded</dd>
            <dt>Decision tree</dt><dd>19 rules in order, first pass sets the code; pacing limit 130%; attainment floor 90%; nearest snapshot within 3 days</dd>
            <dt>Clarification</dt><dd>One question at a time, at most three per request</dd>
            <dt>Site crosswalk</dt><dd className="mono">USP6=USD4 USP8=USF2 USQ2=USF6 USQ4=USL6</dd>
          </dl>
        </>
      )}
      <div style={{ borderTop: "1px solid var(--line)", marginTop: 28, paddingTop: 14 }}>
        <button className="btn" onClick={onReset}>Reset demo session</button>
        <span className="muted" style={{ fontSize: 12, marginLeft: 10 }}>Clears cards, chat and planner entries in this browser.</span>
      </div>
    </Shell>
  );
}
