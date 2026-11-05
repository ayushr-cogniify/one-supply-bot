export type Tone = "good" | "warn" | "bad" | "info" | "muted" | null | undefined;

export interface Action {
  type: "option" | "approve" | "reject" | "alternate" | "open_gap" | "focus";
  slot?: string;
  value?: unknown;
  label?: string;
  draft_id?: string;
  code?: string;
  gap?: string;
}

export interface Option {
  label: string;
  primary?: boolean;
  action: Action;
}

export interface Message {
  kind: "user" | "bot" | "echo" | "system" | "answer";
  text: string;
  template?: string | null;
  options: Option[];
  note?: string | null;
  tone?: Tone;
  card_id?: string;
  used?: boolean;
}

export interface Column {
  key: string;
  label: string;
  format?: string;
  strong?: boolean;
}

export interface WocCell {
  before?: number | null;
  after?: number | null;
  cb?: string | null;
  ca?: string | null;
  demand?: number | null;
  supply?: number | null;
  balance?: number | null;
}

export interface GridRow {
  loc: string;
  name: string;
  changed?: boolean;
  level?: string;
  material?: string;
  cells: WocCell[];
}

export type Block =
  | { type: "kv"; items: { label: string; value: string; sub?: string | null; badge?: string | null }[] }
  | { type: "banner"; tone: Tone; title: string; text?: string }
  | { type: "note"; tone?: string; title?: string; text: string }
  | { type: "table"; title?: string; tone?: string; columns: Column[]; rows: Record<string, unknown>[]; note?: string | null; collapsed?: boolean; text?: string }
  | { type: "wocgrid"; title: string; single?: boolean; row_label?: string; weeks: { calweek: number; label: string }[]; rows: GridRow[]; note?: string }
  | { type: "pir"; title: string; weeks: { calweek: number; label: string }[]; rows: GridRow[]; note?: string }
  | { type: "path"; title: string; rows: { rule: string; test: string; result: string; result_label: string; evidence: string }[]; note?: string };

export interface Source {
  file: string;
  sheet: string;
  hash?: string | null;
  rows?: string | null;
  count: number;
}

export interface Card {
  id: string;
  scenario: string;
  tag: string;
  title: string;
  subtitle: string;
  status: string;
  blocks: Block[];
  sources: Source[];
  result_ids: string[];
  narrative?: string | null;
  validator: { passed: boolean; unverified: string[]; note?: string };
  trace_id: string;
  trace_ids?: string[];
  export?: { filename: string; rows: Record<string, unknown>[] };
}

export interface Gap {
  id: string;
  title: string;
  sub: string;
  sheet: string | null;
  state: "idle" | "open" | "needed" | "entered";
  context: Record<string, unknown>;
  entered: string[];
}

export interface Span {
  step: string;
  ms: number;
  [k: string]: unknown;
}

export interface ToolResult {
  result_id: string;
  tool: string;
  status: string;
  values: Record<string, unknown>;
  table?: { columns: string[]; rows: unknown[][]; truncated_rows?: number } | null;
  rows_used: { source_file?: string; source_sheet?: string; source_row?: number; rows?: number; hash?: string }[];
  rows_used_total: number;
  warnings: string[];
  missing: unknown[];
}

export interface Trace {
  id: string;
  spans: Span[];
  results: ToolResult[];
  request?: string;
}

export interface Session {
  planner: string;
  seq: number;
  pending: { scenario: string; awaiting: string | null } | null;
  supplements: Record<string, Record<string, unknown>[]>;
  audit: { at: string; user: string; event: string; ref: string; detail: Record<string, unknown> }[];
  cards: number;
  weeks_shown: number;
  approvals: number;
  last_validator: { passed: boolean; unverified: string[] } | null;
  last_route: string | null;
  [k: string]: unknown;
}

export interface BootInfo {
  snapshot: string;
  stock_to: string;
  lcs: { code: string; name: string }[];
  load_report: {
    sheets?: { file: string; sheet: string; rows: number; headers: number | null; dropped?: number; note?: string }[];
    matches?: { label: string; matched: number; of: number }[];
    calendar?: { first_week: number; first_start: string; last_week: number; last_start: string; weeks: number };
    inventory_dates?: { first: string; last: string; count: number };
  };
  frames: Record<string, { rows: number; bytes: number }>;
  bundle_created: string;
  models: { stage: string; model: string; endpoint: string; running: string }[];
  route: Record<string, string>;
  boot_ms?: number;
}

export interface Reply {
  messages: Message[];
  cards: Card[];
  session: Session;
  gaps: Gap[];
  trace: Trace;
}

export interface Check {
  tag: string;
  name: string;
  passed: boolean;
  detail: string;
}
