// osb-data: serves the One Supply Bot data bundle from the private GCP bucket to signed-in users.
// The bucket stays private: this function reads it with a read-only service-account key (secret
// GCS_SA_KEY, the JSON key file) and only serves files under gs://<GCS_BUCKET>/osb-web/ that the
// bundle manifest lists. Without the key it answers 503 and the app falls back to its other source.

const SUPABASE_URL = Deno.env.get("SUPABASE_URL")!;
const ANON_KEY = Deno.env.get("SUPABASE_ANON_KEY")!;
const BUCKET = Deno.env.get("GCS_BUCKET") ?? "supply-bot-adgjlp";
const PREFIX = "osb-web/";
const FILE = /^[a-z_]+\.csv\.gz$|^manifest\.json$/;

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type",
  "Access-Control-Allow-Methods": "GET, OPTIONS",
};

const fail = (status: number, error: string) =>
  new Response(JSON.stringify({ ok: false, error }), { status, headers: { ...CORS, "Content-Type": "application/json" } });

async function signedInUser(req: Request): Promise<boolean> {
  const auth = req.headers.get("Authorization") ?? "";
  if (!auth.startsWith("Bearer ")) return false;
  const r = await fetch(`${SUPABASE_URL}/auth/v1/user`, { headers: { apikey: ANON_KEY, Authorization: auth } });
  return r.ok;
}

// ---- Google access token from the service-account key (JWT bearer grant, RS256 via WebCrypto) ----
let cached: { token: string; exp: number } | null = null;

const b64url = (data: ArrayBuffer | string) => {
  const bytes = typeof data === "string" ? new TextEncoder().encode(data) : new Uint8Array(data);
  let s = "";
  for (const b of bytes) s += String.fromCharCode(b);
  return btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
};

async function googleToken(sa: { client_email: string; private_key: string }): Promise<string> {
  const now = Math.floor(Date.now() / 1000);
  if (cached && cached.exp - 60 > now) return cached.token;
  const pem = sa.private_key.replace(/-----[^-]+-----/g, "").replace(/\s+/g, "");
  const der = Uint8Array.from(atob(pem), (c) => c.charCodeAt(0));
  const key = await crypto.subtle.importKey("pkcs8", der, { name: "RSASSA-PKCS1-v1_5", hash: "SHA-256" }, false, ["sign"]);
  const head = b64url(JSON.stringify({ alg: "RS256", typ: "JWT" }));
  const claims = b64url(JSON.stringify({
    iss: sa.client_email, scope: "https://www.googleapis.com/auth/devstorage.read_only",
    aud: "https://oauth2.googleapis.com/token", iat: now, exp: now + 3600,
  }));
  const sig = await crypto.subtle.sign("RSASSA-PKCS1-v1_5", key, new TextEncoder().encode(`${head}.${claims}`));
  const r = await fetch("https://oauth2.googleapis.com/token", {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({ grant_type: "urn:ietf:params:oauth:grant-type:jwt-bearer",
                                assertion: `${head}.${claims}.${b64url(sig)}` }),
  });
  if (!r.ok) throw new Error(`token exchange ${r.status}`);
  const t = await r.json();
  cached = { token: t.access_token, exp: now + (t.expires_in ?? 3600) };
  return cached.token;
}

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return new Response("ok", { headers: CORS });
  if (req.method !== "GET") return fail(405, "GET only");
  if (!(await signedInUser(req))) return fail(401, "sign-in required");

  const name = new URL(req.url).pathname.split("/").pop() ?? "";
  if (!FILE.test(name)) return fail(404, "not a bundle file");

  const raw = Deno.env.get("GCS_SA_KEY");
  if (!raw) return fail(503, "GCS not configured");
  let token: string;
  try {
    token = await googleToken(JSON.parse(raw));
  } catch (e) {
    return fail(502, `GCS auth failed: ${e}`);
  }
  const obj = encodeURIComponent(PREFIX + name);
  const r = await fetch(`https://storage.googleapis.com/storage/v1/b/${BUCKET}/o/${obj}?alt=media`,
                        { headers: { Authorization: `Bearer ${token}` } });
  if (!r.ok) return fail(r.status === 404 ? 404 : 502, `GCS ${r.status}`);
  return new Response(r.body, {
    headers: { ...CORS, "Content-Type": name.endsWith(".json") ? "application/json" : "application/gzip",
               "Cache-Control": "private, no-store", "X-Data-Source": `gs://${BUCKET}/${PREFIX}` },
  });
});
