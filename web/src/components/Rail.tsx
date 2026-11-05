import { useEffect, useState } from "react";
import type { BootInfo, Gap, Session } from "../types";

interface Props {
  gaps: Gap[];
  session: Session;
  info: BootInfo;
  cardsOpen: number;
  openGap: string | null;
  setOpenGap: (g: string | null) => void;
  onSubmit: (gap: string, values: Record<string, unknown>) => void;
  busy: boolean;
}

const STATE_TEXT: Record<string, string> = { idle: "Not needed yet", needed: "Needed", entered: "Entered", open: "Form open" };

function CapacityForm({ gap, onSubmit, onCancel, busy }: { gap: Gap; onSubmit: Props["onSubmit"]; onCancel: () => void; busy: boolean }) {
  const [cap, setCap] = useState("");
  const [basis, setBasis] = useState("Bottles");
  const [from, setFrom] = useState("");
  const [sizes, setSizes] = useState("");
  const [note, setNote] = useState("");
  const [err, setErr] = useState("");
  const wc = String(gap.context.work_center ?? "PL11");
  const submit = () => {
    const n = Number(cap.replace(/,/g, ""));
    if (!cap || !Number.isFinite(n) || n <= 0) return setErr("Enter a positive number of units per month.");
    if (from && !/^\d{4}-\d{2}-\d{2}$/.test(from)) return setErr("Valid from must be yyyy-mm-dd.");
    onSubmit("rated_capacity", { work_center: wc, factory_site: gap.context.label, rated_capacity_units_per_month: n,
      unit_basis: basis, valid_from: from || null, allowed_sizes: sizes || null, note: note || null });
  };
  return (
    <div className="form" data-form="rated_capacity">
      <h4>Add rated capacity</h4>
      <div className="field"><label>Work centre</label><input readOnly value={String(gap.context.label ?? wc)} /></div>
      <div className="field">
        <label htmlFor="cap">Rated capacity, units per month</label>
        <input id="cap" className="mono" inputMode="numeric" placeholder="Enter a number" value={cap} onChange={(e) => { setCap(e.target.value); setErr(""); }} />
        {err ? <div className="err">{err}</div> : <div className="help">Required. I will not fill this in for you.</div>}
      </div>
      <div className="field"><label>Unit basis</label>
        <select value={basis} onChange={(e) => setBasis(e.target.value)}><option>Bottles</option><option>Cases</option></select>
      </div>
      <div className="field"><label>Valid from</label><input placeholder="yyyy-mm-dd" value={from} onChange={(e) => setFrom(e.target.value)} /></div>
      <div className="field"><label>Sizes this line can run (optional)</label><input placeholder="For example 90 oz, 41 oz" value={sizes} onChange={(e) => setSizes(e.target.value)} /></div>
      <div className="field"><label>Note (optional)</label><input placeholder="Where this number comes from" value={note} onChange={(e) => setNote(e.target.value)} /></div>
      <div className="foot">Saved to the Work_Centres sheet with your name and the time, and used in this conversation straight away, marked planner-entered.</div>
      <div className="row">
        <button className="btn primary" disabled={busy} onClick={submit}>Save and continue</button>
        <button className="btn" onClick={onCancel}>Cancel</button>
      </div>
    </div>
  );
}

function PirForm({ gap, onSubmit, onCancel, busy }: { gap: Gap; onSubmit: Props["onSubmit"]; onCancel: () => void; busy: boolean }) {
  const [items, setItems] = useState("");
  const suggest = (gap.context.suggest as string[]) ?? [];
  const add = (m: string) => setItems((s) => (s.split(/[\s,]+/).includes(m) ? s : `${s ? s + ", " : ""}${m}`));
  return (
    <div className="form" data-form="pir_list">
      <h4>PIR item list</h4>
      <div className="field">
        <label htmlFor="pir">Item codes (GIC), comma separated</label>
        <textarea id="pir" className="mono" rows={3} value={items} onChange={(e) => setItems(e.target.value)} placeholder="3283407, 3313455" />
        <div className="help">Demo items from the golden set:</div>
        <div className="opts" style={{ marginTop: 4 }}>{suggest.map((m) => <button key={m} className="btn sm mono" onClick={() => add(m)}>{m}</button>)}</div>
      </div>
      <div className="foot">Saved to the PIR_List sheet. Codes are checked against the Kinaxis plan.</div>
      <div className="row">
        <button className="btn primary" disabled={busy || !items.trim()} onClick={() => onSubmit("pir_list", { items })}>Save and continue</button>
        <button className="btn" onClick={onCancel}>Cancel</button>
      </div>
    </div>
  );
}

function PacingForm({ gap, onSubmit, onCancel, busy }: { gap: Gap; onSubmit: Props["onSubmit"]; onCancel: () => void; busy: boolean }) {
  const [pct, setPct] = useState("");
  return (
    <div className="form" data-form={gap.id}>
      <h4>{gap.title}</h4>
      <div className="help" style={{ marginBottom: 10 }}>{gap.sub}. Ordered cases as a % of the lag-1 forecast for the cut month.</div>
      <div className="row" style={{ marginBottom: 10 }}>
        <button className="btn" disabled={busy} onClick={() => onSubmit(gap.id, { value: "yes" })}>Above 130%</button>
        <button className="btn" disabled={busy} onClick={() => onSubmit(gap.id, { value: "no" })}>Not above 130%</button>
      </div>
      <div className="field"><label>Or enter the %</label><input className="mono" inputMode="decimal" placeholder="for example 142" value={pct} onChange={(e) => setPct(e.target.value)} /></div>
      <div className="row">
        <button className="btn primary" disabled={busy || !pct || !Number.isFinite(Number(pct))} onClick={() => onSubmit(gap.id, { value: Number(pct) })}>Save</button>
        <button className="btn" onClick={onCancel}>Cancel</button>
      </div>
    </div>
  );
}

export default function Rail({ gaps, session, info, cardsOpen, openGap, setOpenGap, onSubmit, busy }: Props) {
  const [uploadNote, setUploadNote] = useState(false);
  const openCount = gaps.filter((g) => g.state === "open" || g.state === "needed").length;
  const active = gaps.find((g) => g.state === "open");
  useEffect(() => {
    if (active && (active.id === "rated_capacity" || active.id === "pir_list")) setOpenGap(active.id);
  }, [active?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  const formFor = (g: Gap) => {
    const cancel = () => setOpenGap(null);
    if (g.id === "rated_capacity") return <CapacityForm gap={g} onSubmit={onSubmit} onCancel={cancel} busy={busy} />;
    if (g.id === "pir_list") return <PirForm gap={g} onSubmit={onSubmit} onCancel={cancel} busy={busy} />;
    if (g.id.startsWith("pacing_")) return <PacingForm gap={g} onSubmit={onSubmit} onCancel={cancel} busy={busy} />;
    return null;
  };
  const v = session.last_validator;

  return (
    <aside className="col rail">
      <div className="rail-h">
        <h3>Data Gaps</h3>
        <span className={`count ${openCount ? "open" : "none"}`}>{openCount ? `${openCount} open` : "None open"}</span>
      </div>
      <p className="lead">{openCount ? "Turns green when you answer. Saved with your name and the time." : "One list for every scenario. A value you enter once is used wherever it is needed."}</p>
      {gaps.map((g) => {
        const showForm = openGap === g.id;
        const st = g.state === "open" ? (showForm ? "open" : "needed") : g.state;
        return (
          <div key={g.id}>
            <div className={`gap ${g.state === "open" || g.state === "needed" ? "open" : ""}`} data-gap={g.id}>
              <div>
                <div className="gt">{g.title}</div>
                <div className="gs">{g.sub}</div>
                <span className={`st ${st === "open" ? "needed" : st}`}>{STATE_TEXT[st]}</span>
                {g.entered.slice(-3).map((e, i) => <div className="ent" key={i}>{e}</div>)}
              </div>
              <div className="gx">
                {g.id === "rated_capacity" && !showForm && <button className="btn sm" onClick={() => setOpenGap(g.id)}>Add</button>}
                {g.id === "pir_list" && !showForm && <button className="btn sm" onClick={() => setOpenGap(g.id)}>{g.entered.length ? "Edit" : "Add"}</button>}
                {g.id.startsWith("pacing_") && g.state !== "entered" && !showForm && <button className="btn sm" onClick={() => setOpenGap(g.id)}>Enter</button>}
                {g.id === "kinaxis_snapshots" && <button className="btn sm" onClick={() => setUploadNote(!uploadNote)}>Upload</button>}
              </div>
            </div>
            {showForm && formFor(g)}
            {g.id === "kinaxis_snapshots" && uploadNote && (
              <div className="form"><div className="help" style={{ fontSize: 12.5 }}>
                A second Kinaxis extract is checked against the schema contract (41 headers, a different SNAPSHOT_WEEK) and loaded by the data build. This build carries the {info.snapshot} snapshot, so S5 compares SAP stock between two dates instead.
              </div></div>
            )}
          </div>
        );
      })}

      <div className="sect">
        <h3>Session</h3>
        <dl className="kvl">
          <dt>Brand scope</dt><dd>LDS and Sanitizers</dd>
          <dt>LC set</dt><dd className="mono">{info.lcs.map((l) => l.code).join(" ")}</dd>
          <dt>Basis</dt><dd>Planned</dd>
          <dt>Cards open</dt><dd>{cardsOpen}</dd>
          <dt>Cover window</dt><dd>6 weeks (default)</dd>
          <dt>Weeks shown</dt><dd>{session.weeks_shown} weeks</dd>
          <dt>Planner entries</dt><dd>{Object.values(session.supplements).reduce((a, r) => a + r.filter((x) => !x.retracted).length, 0) || "none yet"}</dd>
        </dl>
      </div>

      <div className="sect">
        <h3>Model routing</h3>
        <dl className="kvl">
          {info.models.map((m) => (
            <div key={m.stage} style={{ display: "contents" }}>
              <dt>{{ scope_gate: "Scope gate", slots: "Slots", orchestrator: "Orchestrator", s4_reasoning: "S4 reasoning" }[m.stage] ?? m.stage}</dt>
              <dd>{m.stage === "scope_gate" || m.stage === "slots" ? m.model : m.stage === "orchestrator" ? "Code, from tool values" : "Decision tree in code"}
                <div className="muted" style={{ fontSize: 11.5 }}>{m.stage === "scope_gate" || m.stage === "slots" ? "rules engine if Claude is unavailable" : `${m.model} in the Databricks build`}</div></dd>
            </div>
          ))}
          <dt>Last answer</dt><dd><b>{session.last_route ? (session.last_route === "rules" ? "rules engine" : session.last_route.replace("claude-haiku-5-5", "Claude Haiku 5.5")) : "none yet"}</b></dd>
        </dl>
      </div>

      <div className="sect">
        <h3>Audit</h3>
        <dl className="kvl">
          <dt>Validator</dt>
          <dd>{!v ? "Nothing answered yet" : v.passed ? "Passed. No estimate was made." : <span className="bad">Failed: {v.unverified.join(", ")}</span>}</dd>
          <dt>Approvals</dt><dd>{session.approvals} this session</dd>
          <dt>Audit log</dt><dd>{session.audit.length} entr{session.audit.length === 1 ? "y" : "ies"}</dd>
        </dl>
      </div>
    </aside>
  );
}
