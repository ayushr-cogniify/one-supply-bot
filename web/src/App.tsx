import { useEffect, useRef, useState } from "react";
import type { Session as AuthSession } from "@supabase/supabase-js";
import { ask, boot, runChecks } from "./engine";
import { dataSource, linkError, linkType, supabase } from "./auth";
import SignIn from "./components/SignIn";
import type { Action, BootInfo, Card, Gap, Message, Reply, Session, Trace } from "./types";
import CardView from "./components/CardView";
import Left from "./components/Left";
import Rail from "./components/Rail";
import { AdminDrawer, TraceDrawer } from "./components/Drawers";

const KEY = "osb.session.v1";
const WELCOME: Message = {
  kind: "bot", options: [],
  text: "Ask about any of the five scenarios, or pick a question above. Everything I show comes from your extracts or from what you enter. If I am missing something, I will ask for it.",
};

interface Saved { session: Session; messages: Message[]; cards: Card[]; traces: Record<string, Trace> }

function load(): Saved | null {
  try {
    const raw = localStorage.getItem(KEY);
    return raw ? (JSON.parse(raw) as Saved) : null;
  } catch {
    return null;
  }
}

function save(s: Saved) {
  try {
    const traces = Object.fromEntries(Object.entries(s.traces).slice(-30));
    localStorage.setItem(KEY, JSON.stringify({ ...s, traces }));
  } catch {
    /* storage full or blocked: the session still works in memory */
  }
}

const STEPS: [string, string][] = [
  ["runtime", "Python runtime"], ["packages", "pandas and numpy"], ["engine", "Calculation engines"],
  ["data", "Reckitt extracts"], ["start", "Reading sheets into memory"],
];

function Logo() {
  return (
    <span className="logo">
      <svg width="16" height="16" viewBox="0 0 16 16"><path d="M2.5 11.5l3.6-3.6 2.8 2.2 4.6-5.6" stroke="#fff" strokeWidth="1.8" fill="none" strokeLinecap="round" strokeLinejoin="round" /></svg>
    </span>
  );
}

function Boot({ prog, error }: { prog: Record<string, { detail: string; done: boolean }>; error: string | null }) {
  return (
    <div className="boot">
      <div className="boot-card">
        <Logo />
        <h1>One Supply Bot</h1>
        <p>Starting the calculation engines in your browser. The first visit downloads about 20 MB; later visits load from cache.</p>
        <div className="steps">
          {STEPS.map(([k, label]) => {
            const s = prog[k];
            const cls = s?.done ? "done" : s ? "active" : "wait";
            return (
              <div key={k} className={`step ${cls}`}>
                <span className="dot" />
                <span>{label}</span>
                <span className="d">{s?.done ? "ready" : s?.detail ?? ""}</span>
              </div>
            );
          })}
        </div>
        {error && <div className="boot-err">Could not start: {error}. <button className="linkbtn" onClick={() => window.location.reload()}>Reload</button></div>}
      </div>
    </div>
  );
}

export default function App() {
  const [auth, setAuth] = useState<AuthSession | null | undefined>(undefined);
  const [needsPassword, setNeedsPassword] = useState(linkType === "invite" || linkType === "recovery");

  useEffect(() => {
    supabase.auth.getSession().then(({ data }) => setAuth(data.session));
    const { data } = supabase.auth.onAuthStateChange((event, s) => {
      setAuth(s);
      if (event === "PASSWORD_RECOVERY") setNeedsPassword(true);
    });
    return () => data.subscription.unsubscribe();
  }, []);

  if (auth === undefined) return null;
  if (!auth || needsPassword)
    return <SignIn setPassword={!!auth && needsPassword} linkError={linkError} onPasswordSet={() => setNeedsPassword(false)} />;
  return <Planner auth={auth} />;
}

function Planner({ auth }: { auth: AuthSession }) {
  const [info, setInfo] = useState<BootInfo | null>(null);
  const [prog, setProg] = useState<Record<string, { detail: string; done: boolean }>>({});
  const [bootErr, setBootErr] = useState<string | null>(null);
  const [session, setSession] = useState<Session | null>(null);
  const [gaps, setGaps] = useState<Gap[]>([]);
  const [messages, setMessages] = useState<Message[]>([WELCOME]);
  const [cards, setCards] = useState<Card[]>([]);
  const [traces, setTraces] = useState<Record<string, Trace>>({});
  const [busy, setBusy] = useState(false);
  const [openGap, setOpenGap] = useState<string | null>(null);
  const [traceIds, setTraceIds] = useState<string[] | null>(null);
  const [admin, setAdmin] = useState(false);
  const [flash, setFlash] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const canvasRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    boot((p) => setProg((s) => ({ ...s, [p.step]: { detail: p.detail, done: p.done } })), dataSource(auth))
      .then(async (r) => {
        setInfo(r.info);
        const saved = load();
        if (saved) {
          setMessages(saved.messages);
          setCards(saved.cards);
          setTraces(saved.traces);
          // re-derive the gap list for the restored session
          const reply = await ask({ type: "noop" }, saved.session);
          setSession(reply.session);
          setGaps(reply.gaps);
        } else {
          setSession(r.session);
          setGaps(r.gaps);
        }
      })
      .catch((e) => setBootErr(String(e.message ?? e)));
  }, []);

  useEffect(() => {
    if (session) save({ session, messages, cards, traces });
  }, [session, messages, cards, traces]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") { setTraceIds(null); setAdmin(false); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const apply = (reply: Reply, request: string) => {
    setSession(reply.session);
    setGaps(reply.gaps);
    setMessages((m) => [...m, ...reply.messages]);
    setTraces((t) => ({ ...t, [reply.trace.id]: { ...reply.trace, request } }));
    if (reply.cards.length) {
      setCards((cs) => {
        let next = [...cs];
        for (const c of reply.cards) {
          const i = next.findIndex((x) => x.id === c.id);
          if (i >= 0) next[i] = c;
          else next = [c, ...next];
        }
        return next;
      });
      setFlash(reply.cards[reply.cards.length - 1].id);
      canvasRef.current?.scrollTo({ top: 0, behavior: "smooth" });
      setTimeout(() => setFlash(null), 1500);
    }
    if (!reply.gaps.some((g) => g.state === "open")) setOpenGap(null);
  };

  const run = async (request: Record<string, unknown>, label: string) => {
    if (!session) return;
    setBusy(true);
    try {
      apply(await ask(request, session), label);
    } catch (e) {
      setMessages((m) => [...m, { kind: "bot", tone: "bad", options: [], text: `The engine stopped with an error: ${String((e as Error).message)}` }]);
    } finally {
      setBusy(false);
    }
  };

  const send = (text: string) => {
    setMessages((m) => [...m, { kind: "user", text, options: [] }]);
    run({ type: "message", text }, text);
  };

  const onAction = (a: Action, label: string, index: number) => {
    if (a.type === "open_gap") return setOpenGap(a.gap ?? null);
    if (a.type === "focus") return inputRef.current?.focus();
    if (index >= 0) setMessages((m) => m.map((x, i) => (i === index ? { ...x, used: true } : x)));
    setMessages((m) => [...m, { kind: "user", text: label, options: [] }]);
    run(a as unknown as Record<string, unknown>, label);
  };

  const onGap = (gap: string, values: Record<string, unknown>) => {
    setOpenGap(null);
    run({ type: "gap_submit", gap, values }, `Data Gaps: ${gap}`);
  };

  const reset = () => {
    try { localStorage.removeItem(KEY); } catch { /* ignore */ }
    window.location.reload();
  };

  const signOut = async () => {
    try { localStorage.removeItem(KEY); } catch { /* ignore */ }
    await supabase.auth.signOut();
    window.location.reload();
  };

  if (!info || !session) return <Boot prog={prog} error={bootErr} />;

  const active = cards[0]?.tag ?? null;
  const chain = (traceIds ?? []).map((id) => traces[id]).filter(Boolean);

  return (
    <>
      <header className="hdr">
        <Logo />
        <span className="brand">One Supply Bot</span>
        <span className="brand-sub">Lysol LDS and Sanitizers {"·"} US network</span>
        <div className="hdr-right">
          <span className="who">{auth.user.email} {"·"} Desk: LDS + Sanitizers</span>
          <span className="pill">Kinaxis snapshot {info.snapshot} {"·"} SAP stock to {info.stock_to}</span>
          <button className="btn" onClick={() => setAdmin(true)}>Admin</button>
          <button className="linkbtn" title={auth.user.email} onClick={signOut}>Sign out</button>
        </div>
      </header>
      <main className="shell">
        <Left messages={messages} busy={busy} active={active} inputRef={inputRef} onSend={send} onAction={onAction} />
        <div className="col canvas" ref={canvasRef}>
          {cards.length === 0 ? (
            <div className="intro">
              <h1>One place for every supply question</h1>
              <p>Ask in plain words. The answer appears here as a card, and cards stay on the canvas so you can compare them. Every number comes from a tool that reads your sheets, never from the model, and each card says where it came from.</p>
              <div className="eyebrow">How the views connect</div>
              <div className="rel"><b>S5 What changed</b> points you to the item and the line behind a move, which opens <b>S1</b> or <b>S2</b>.</div>
              <div className="rel"><b>S2 Attainment</b> sets the effective capacity that <b>S3 Segment growth</b> uses, and the same entered line capacity feeds both.</div>
              <div className="rel"><b>S4 Reason coding</b> reads attainment and stock to explain a cut, and links back to <b>S1</b> for the item.</div>
            </div>
          ) : (
            cards.map((c) => (
              <CardView key={c.id} card={c} flash={flash === c.id} onTrace={() => setTraceIds(c.trace_ids?.length ? c.trace_ids : [c.trace_id])}
                onClose={() => setCards((cs) => cs.filter((x) => x.id !== c.id))} />
            ))
          )}
        </div>
        <Rail gaps={gaps} session={session} info={info} cardsOpen={cards.length} openGap={openGap} setOpenGap={setOpenGap} onSubmit={onGap} busy={busy} />
      </main>
      {chain.length > 0 && <TraceDrawer traces={chain} onClose={() => setTraceIds(null)} />}
      {admin && (
        <AdminDrawer info={info} session={session} onClose={() => setAdmin(false)} onChecks={runChecks} onReset={reset}
          onRetract={(sheet, row_id) => run({ type: "retract", sheet, row_id }, `Retract ${row_id}`)} />
      )}
    </>
  );
}
