import * as XLSX from "xlsx";
import type { Card, Session } from "./types";

const MEASURES: [string, string][] = [
  ["total_demand", "Total demand"],
  ["available_supply", "Available supply"],
  ["balance", "Balance"],
  ["woc", "Weeks of cover"],
];

// PIR layout: one row per item / location / measure, one column per week (Rushi's LC, Agg and Segment sheets)
function wide(rows: Record<string, unknown>[]) {
  const weeks = [...new Set(rows.map((r) => String(r.week_start)))].sort();
  const keys = new Map<string, Record<string, unknown>[]>();
  for (const r of rows) {
    const k = `${r.group}|${r.material}|${r.location}`;
    keys.set(k, [...(keys.get(k) ?? []), r]);
  }
  const out: Record<string, unknown>[] = [];
  for (const [k, rs] of keys) {
    const [group, material, location] = k.split("|");
    for (const [m, label] of MEASURES) {
      const row: Record<string, unknown> = { Group: group, Item: material, Location: location, Measure: label };
      for (const w of weeks) {
        const hit = rs.find((x) => String(x.week_start) === w);
        row[w] = hit ? hit[m] : null;
      }
      out.push(row);
    }
  }
  return out;
}

export function downloadPir(card: Card) {
  if (!card.export) return;
  const rows = card.export.rows;
  const wb = XLSX.utils.book_new();
  for (const [level, sheet] of [["LC", "LC"], ["NETWORK", "Agg"], ["GROUP", "Segment"]]) {
    const part = rows.filter((r) => r.level === level);
    if (part.length) XLSX.utils.book_append_sheet(wb, XLSX.utils.json_to_sheet(wide(part)), sheet);
  }
  XLSX.utils.book_append_sheet(wb, XLSX.utils.json_to_sheet(rows), "Long");
  XLSX.writeFile(wb, card.export.filename);
}

export function downloadSupplements(session: Session) {
  const wb = XLSX.utils.book_new();
  for (const [sheet, rows] of Object.entries(session.supplements)) {
    XLSX.utils.book_append_sheet(wb, rows.length ? XLSX.utils.json_to_sheet(rows) : XLSX.utils.aoa_to_sheet([["(no rows)"]]), sheet.slice(0, 31));
  }
  XLSX.utils.book_append_sheet(wb, XLSX.utils.json_to_sheet(session.audit.map((a) => ({ ...a, detail: JSON.stringify(a.detail) }))), "audit_log");
  XLSX.writeFile(wb, "OSB_Supplements.xlsx");
}
