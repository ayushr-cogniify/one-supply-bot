import { useState } from "react";
import { redirectTo, supabase } from "../auth";

type Mode = "signin" | "forgot" | "set";

function Logo() {
  return (
    <span className="logo">
      <svg width="16" height="16" viewBox="0 0 16 16"><path d="M2.5 11.5l3.6-3.6 2.8 2.2 4.6-5.6" stroke="#fff" strokeWidth="1.8" fill="none" strokeLinecap="round" strokeLinejoin="round" /></svg>
    </span>
  );
}

export default function SignIn({ setPassword, onPasswordSet, linkError }: { setPassword?: boolean; onPasswordSet?: () => void; linkError?: string | null }) {
  const [mode, setMode] = useState<Mode>(setPassword ? "set" : "signin");
  const [email, setEmail] = useState("");
  const [pw, setPw] = useState("");
  const [pw2, setPw2] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(linkError ?? null);
  const [info, setInfo] = useState<string | null>(null);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setErr(null);
    setInfo(null);
    setBusy(true);
    try {
      if (mode === "signin") {
        const { error } = await supabase.auth.signInWithPassword({ email: email.trim(), password: pw });
        if (error) setErr(error.message === "Invalid login credentials" ? "That email and password do not match an invited account." : error.message);
      } else if (mode === "forgot") {
        const { error } = await supabase.auth.resetPasswordForEmail(email.trim(), { redirectTo: redirectTo() });
        if (error) setErr(error.message);
        else setInfo("If that address has an account, a link to set a new password is on its way.");
      } else {
        if (pw.length < 10) return setErr("Use at least 10 characters.");
        if (pw !== pw2) return setErr("The two passwords do not match.");
        const { error } = await supabase.auth.updateUser({ password: pw });
        if (error) setErr(error.message);
        else {
          window.history.replaceState(null, "", window.location.pathname);
          onPasswordSet?.();
        }
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="boot">
      <form className="boot-card" onSubmit={submit}>
        <Logo />
        <h1>One Supply Bot</h1>
        <p>
          {mode === "signin" && "Sign in with the account you were invited with. Access is by invitation only."}
          {mode === "forgot" && "Enter your email and we will send a link to set a new password."}
          {mode === "set" && "Choose a password for your account."}
        </p>
        {mode !== "set" && (
          <div className="field">
            <label htmlFor="email">Email</label>
            <input id="email" type="email" autoComplete="email" required value={email} onChange={(e) => setEmail(e.target.value)} />
          </div>
        )}
        {mode !== "forgot" && (
          <div className="field">
            <label htmlFor="pw">{mode === "set" ? "New password" : "Password"}</label>
            <input id="pw" type="password" autoComplete={mode === "set" ? "new-password" : "current-password"} required value={pw} onChange={(e) => setPw(e.target.value)} />
          </div>
        )}
        {mode === "set" && (
          <div className="field">
            <label htmlFor="pw2">Repeat password</label>
            <input id="pw2" type="password" autoComplete="new-password" required value={pw2} onChange={(e) => setPw2(e.target.value)} />
          </div>
        )}
        {err && <div className="boot-err" style={{ margin: "4px 0 12px" }}>{err}</div>}
        {info && <div className="msg good" style={{ margin: "4px 0 12px" }}>{info}</div>}
        <div className="row" style={{ display: "flex", gap: 10, alignItems: "center", marginTop: 6 }}>
          <button className="btn primary" type="submit" disabled={busy}>
            {busy ? "Please wait…" : mode === "signin" ? "Sign in" : mode === "forgot" ? "Send link" : "Save password"}
          </button>
          {mode === "signin" && <button type="button" className="linkbtn" onClick={() => setMode("forgot")}>Forgot password?</button>}
          {mode === "forgot" && <button type="button" className="linkbtn" onClick={() => setMode("signin")}>Back to sign in</button>}
        </div>
      </form>
    </div>
  );
}
