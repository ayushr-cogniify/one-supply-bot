import type { Session } from "./types";
import { SUPABASE_URL, supabase } from "./auth";

// Scope gate and slot extraction on Claude (Opus 5.5), through the osb-nlu Edge Function. The key
// stays in the function's secrets. Any failure or a slow answer returns null and the engine falls
// back to its rules, so a planner never waits on the model.
const NAMES: Record<string, string> = {
  S1: "S1_demand_spike", S2: "S2_attainment_drop", S3: "S3_segment_growth",
  S4: "S4_pfr_reason_coding", S5: "S5_what_changed", PIR: "PIR_report",
};

export interface ClaudeParse {
  ok: boolean;
  parse?: unknown;
  model?: string;
  ms?: number;
  error?: string;
}

export async function understand(text: string, session: Session, snapshotStart: string): Promise<ClaudeParse | null> {
  const { data } = await supabase.auth.getSession();
  const token = data.session?.access_token;
  if (!token) return null;
  const s = session as unknown as { pending?: { scenario?: string }; last?: { scenario?: string } };
  const active = s.pending?.scenario ?? s.last?.scenario;
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), 15000);   // Opus thinks first: allow up to 15 s
  try {
    const r = await fetch(`${SUPABASE_URL}/functions/v1/osb-nlu`, {
      method: "POST",
      signal: ctl.signal,
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${token}`,
        apikey: import.meta.env.VITE_SUPABASE_ANON_KEY as string,
      },
      body: JSON.stringify({
        text,
        context: { today: new Date().toISOString().slice(0, 10), snapshot_week_start: snapshotStart,
                   active_scenario: (active && NAMES[active]) || "none" },
      }),
    });
    const body = (await r.json()) as ClaudeParse;
    return body.ok ? body : null;
  } catch {
    return null;
  } finally {
    clearTimeout(timer);
  }
}
