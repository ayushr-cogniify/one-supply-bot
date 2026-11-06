import { useEffect, useRef, useState, type RefObject } from "react";
import type { Action, Message } from "../types";

export const STARTERS = [
  { k: "S1", label: "+5k cases of 4x90 Odor at Vegas, week of 10/19", ask: "Can we take +5k cases of Lysol 4x90 Odor at Vegas in week 10/19?" },
  { k: "S2", label: "PL11 at 75% and +3,000 cases of 4x90 Odor", ask: "PL11 attainment drops to 75% and I need to serve 3,000 more cases of 3283407 in week of 10/19. Can we?" },
  { k: "S3", label: "4x90 segment grows 30% in November", ask: "The Lysol 4x90 segment is expected to grow 30% in November. Does current inventory and weeks of cover support it?" },
  { k: "S4", label: "Code the 10/15 Vegas cut", ask: "Code the 10/15 cut of Crisp Linen 6x21 at Vegas." },
  { k: "S5", label: "What changed this week?", ask: "What changed since last week?" },
  { k: "NET", label: "Network dashboard", ask: "Give me the network dashboard" },
];

const SCENARIOS = [
  ["S1", "S1 Demand spike"], ["S2", "S2 Attainment drop"], ["S3", "S3 Segment growth"],
  ["S4", "S4 Reason coding"], ["S5", "S5 What changed"],
];

interface Props {
  messages: Message[];
  busy: boolean;
  active: string | null;
  inputRef: RefObject<HTMLInputElement>;
  onSend: (text: string) => void;
  onAction: (a: Action, label: string, index: number) => void;
}

function Bubble({ m, i, onAction, busy }: { m: Message; i: number; busy: boolean; onAction: Props["onAction"] }) {
  if (m.kind === "user") return <div className="msg user">{m.text}</div>;
  if (m.kind === "echo")
    return (
      <div className="msg echo">
        <div className="eyebrow">Echo-back</div>
        <div className="t">{m.text}</div>
      </div>
    );
  if (m.kind === "system")
    return (
      <div className="msg system">
        <svg width="13" height="13" viewBox="0 0 16 16"><path d="M3 8.5l3 3 7-7" stroke="currentColor" strokeWidth="2" fill="none" /></svg>
        {m.text}
      </div>
    );
  return (
    <div className={`msg ${m.kind} ${m.tone ?? ""}`}>
      <div>{m.text}</div>
      {m.options.length > 0 && (
        <div className={`opts ${m.used ? "used" : ""}`}>
          {m.options.map((o, j) => (
            <button key={j} className={`btn ${o.primary ? "primary" : ""}`} disabled={busy && o.action.type !== "open_gap"}
              onClick={() => onAction(o.action, o.label, i)}>{o.label}</button>
          ))}
        </div>
      )}
      {m.note && <div className="note">{m.note}</div>}
      {m.kind === "answer" && (
        <div className="vtag">
          <svg width="12" height="12" viewBox="0 0 16 16"><path d="M3 8.5l3 3 7-7" stroke="currentColor" strokeWidth="2" fill="none" /></svg>
          Every number checked against the tool results
        </div>
      )}
    </div>
  );
}

export default function Left({ messages, busy, active, inputRef, onSend, onAction }: Props) {
  const [text, setText] = useState("");
  const end = useRef<HTMLDivElement>(null);
  const started = messages.some((m) => m.kind === "user");
  useEffect(() => {
    end.current?.scrollIntoView({ block: "end" });
  }, [messages, busy]);

  const submit = () => {
    const t = text.trim();
    if (!t || busy) return;
    setText("");
    onSend(t);
  };

  return (
    <aside className="left">
      <div className="prompts">
        {!started ? (
          <>
            <div className="eyebrow">Try asking</div>
            <div className="chips">
              {STARTERS.map((s) => (
                <button key={s.k} className="chip" disabled={busy} onClick={() => onSend(s.ask)}>
                  <span className="k">{s.k}</span>{s.label}
                </button>
              ))}
            </div>
          </>
        ) : (
          <>
            <div className="eyebrow">Scenarios</div>
            <div className="scen">
              {SCENARIOS.map(([k, l]) => (
                <button key={k} className={active === k ? "on" : ""} disabled={busy}
                  onClick={() => onAction({ type: "option", slot: "scenario", value: k }, l, -1)}>{l}</button>
              ))}
            </div>
          </>
        )}
      </div>
      <div className="thread" aria-live="polite">
        {messages.map((m, i) => <Bubble key={i} m={m} i={i} busy={busy} onAction={onAction} />)}
        {busy && <div className="typing">Working<span>{"…"}</span></div>}
        <div ref={end} />
      </div>
      <form className="composer" onSubmit={(e) => { e.preventDefault(); submit(); }}>
        <input ref={inputRef} value={text} onChange={(e) => setText(e.target.value)} placeholder="Ask a question" aria-label="Message" />
        <button className="send" type="submit" disabled={busy || !text.trim()} aria-label="Send">
          <svg width="16" height="16" viewBox="0 0 16 16"><path d="M3 8h10M9 4l4 4-4 4" stroke="currentColor" strokeWidth="1.8" fill="none" strokeLinecap="round" strokeLinejoin="round" /></svg>
        </button>
      </form>
    </aside>
  );
}
