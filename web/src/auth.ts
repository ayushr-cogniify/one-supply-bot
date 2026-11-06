import { createClient, type Session } from "@supabase/supabase-js";

// The publishable key is meant for the browser: it can only do what the bucket policy allows a
// signed-in user to do (read the data bundle). Sign-ups are disabled; people are invited.
export const SUPABASE_URL = import.meta.env.VITE_SUPABASE_URL as string;
const ANON_KEY = import.meta.env.VITE_SUPABASE_ANON_KEY as string;
export const BUCKET = "osb-data";

// Read the link type before the client consumes the URL hash: invite and recovery links land
// signed in, and the person still has to choose a password.
const hash = new URLSearchParams(window.location.hash.slice(1));
export const linkType = hash.get("type");
export const linkError = hash.get("error_description");

export const supabase = createClient(SUPABASE_URL, ANON_KEY, {
  auth: { persistSession: true, autoRefreshToken: true, detectSessionInUrl: true, flowType: "implicit" },
});

// Data sources, in order: the private GCP bucket (through the osb-data function), then the private
// Supabase bucket. The worker uses the first one that serves the manifest.
export function dataSources(session: Session) {
  const headers = { apikey: ANON_KEY, Authorization: `Bearer ${session.access_token}` };
  return [
    { label: "GCP Cloud Storage (gs://supply-bot-adgjlp/osb-web)", base: `${SUPABASE_URL}/functions/v1/osb-data/`, headers },
    { label: "Supabase Storage (osb-data)", base: `${SUPABASE_URL}/storage/v1/object/authenticated/${BUCKET}/`, headers },
  ];
}

export const redirectTo = () => new URL(import.meta.env.BASE_URL, window.location.href).href;
