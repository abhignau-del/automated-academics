// Spreadsheet-style editing: reading pasted text, mapping its columns onto a table, filling cells,
// and acting on several rows at once. Everything here is a pure function of its inputs (nothing
// is mutated), so it is easy to test and safe to use with React state.

import { addRow, ID_FIELD, parseSessions, removeRow, renameId, type TableKey } from "./dataops";
import type { Calendar, Institution, Slot } from "./types";

export type ColKind = "id" | "text" | "number" | "select" | "ids" | "sessions" | "slots";

export interface Col {
  key: string;
  label: string; // table heading
  aria: string; // accessible name of the cell's control
  kind: ColKind;
  min?: number;
  aliases?: string[]; // other headings a pasted sheet may use for this column
  shown?: boolean; // false: can be pasted (by heading) but is not a table column
}

/** The columns of each table, in display order. */
export const COLUMNS: Record<TableKey, Col[]> = {
  rooms: [
    { key: "id", label: "ID", aria: "id", kind: "id" },
    { key: "name", label: "Name", aria: "name", kind: "text" },
    { key: "capacity", label: "Seats", aria: "seats", kind: "number", min: 1, aliases: ["seats"] },
    { key: "kind", label: "Kind", aria: "kind", kind: "select" },
  ],
  faculty: [
    { key: "id", label: "ID", aria: "id", kind: "id" },
    { key: "name", label: "Name", aria: "name", kind: "text" },
    { key: "department", label: "Department", aria: "department", kind: "text" },
    { key: "max_lectures_per_day", label: "Max/day", aria: "max per day", kind: "number", min: 1, aliases: ["max"] },
    { key: "unavailable", label: "Unavailable", aria: "unavailable", kind: "slots", shown: false },
    { key: "avoid", label: "Avoid", aria: "avoid", kind: "slots", shown: false },
  ],
  batches: [
    { key: "id", label: "ID", aria: "id", kind: "id" },
    { key: "program", label: "Program", aria: "program", kind: "text" },
    { key: "level", label: "Level", aria: "level", kind: "select" },
    { key: "semester", label: "Sem", aria: "semester", kind: "number", min: 1, aliases: ["semester"] },
    { key: "section", label: "Section", aria: "section", kind: "text" },
    { key: "department", label: "Department", aria: "department", kind: "text" },
    { key: "strength", label: "Students", aria: "students", kind: "number", min: 1, aliases: ["strength"] },
    { key: "group_of", label: "Parent", aria: "parent", kind: "select", aliases: ["groupof"] },
  ],
  courses: [
    { key: "code", label: "Code", aria: "id", kind: "id" },
    { key: "name", label: "Name", aria: "name", kind: "text" },
    { key: "department", label: "Department", aria: "department", kind: "text" },
    { key: "credits", label: "Credits", aria: "credits", kind: "number", min: 0 },
    { key: "category", label: "Category", aria: "category", kind: "text" },
    { key: "room_kind", label: "Needs", aria: "room kind", kind: "select", aliases: ["roomkind"] },
  ],
  offerings: [
    { key: "id", label: "ID", aria: "id", kind: "id" },
    { key: "course_code", label: "Course", aria: "course", kind: "select", aliases: ["coursecode"] },
    { key: "faculty_id", label: "Taught by", aria: "faculty", kind: "select", aliases: ["faculty", "facultyid"] },
    { key: "batch_ids", label: "Classes", aria: "add class", kind: "ids", aliases: ["batches", "batchids"] },
    { key: "sessions", label: "Sessions", aria: "sessions", kind: "sessions" },
  ],
};

/** Columns that appear in the table (the ones a plain paste fills, left to right). */
export const shownColumns = (table: TableKey): Col[] => COLUMNS[table].filter((c) => c.shown !== false);

// ---------------------------------------------------------------- options for choice columns

export interface Opt { value: string; label: string; alts: string[] }

const KINDS: Opt[] = ["classroom", "lab", "hall"].map((v) => ({ value: v, label: v, alts: [] }));
const LEVELS: Opt[] = ["UG", "PG"].map((v) => ({ value: v, label: v, alts: [] }));

/** The choices for a select / ids column, or null if the column is free-form. */
export function optionsFor(inst: Institution, table: TableKey, key: string): Opt[] | null {
  if ((table === "rooms" && key === "kind") || (table === "courses" && key === "room_kind")) return KINDS;
  if (table === "batches" && key === "level") return LEVELS;
  if (table === "batches" && key === "group_of") {
    return inst.batches.filter((b) => !b.group_of).map((b) => ({ value: b.id, label: b.id, alts: [] }));
  }
  if (table === "offerings" && key === "course_code") {
    return inst.courses.map((c) => ({ value: c.code, label: `${c.code}${c.name ? " — " + c.name : ""}`, alts: c.name ? [c.name] : [] }));
  }
  if (table === "offerings" && key === "faculty_id") {
    return inst.faculty.map((f) => ({ value: f.id, label: `${f.name} (${f.id})`, alts: f.name ? [f.name] : [] }));
  }
  if (table === "offerings" && key === "batch_ids") return inst.batches.map((b) => ({ value: b.id, label: b.id, alts: [] }));
  return null;
}

/** Match typed text to a choice (by value, label or name, ignoring case); otherwise keep it as typed. */
export function matchOption(options: Opt[], text: string): string {
  const t = text.trim().toLowerCase();
  const hit = options.find((o) => o.value.toLowerCase() === t || o.label.toLowerCase() === t || o.alts.some((a) => a.toLowerCase() === t));
  return hit ? hit.value : text.trim();
}

// ---------------------------------------------------------------- reading text

/** Parse tab-separated text as Excel and Google Sheets copy it, including quoted cells. */
export function parseTSV(text: string): string[][] {
  const rows: string[][] = [];
  let row: string[] = [];
  let cell = "";
  let quoted = false;
  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (quoted) {
      if (ch === '"') {
        if (text[i + 1] === '"') { cell += '"'; i++; } else quoted = false;
      } else cell += ch;
    } else if (ch === '"' && cell === "") {
      quoted = true;
    } else if (ch === "\t") {
      row.push(cell); cell = "";
    } else if (ch === "\n" || ch === "\r") {
      if (ch === "\r" && text[i + 1] === "\n") i++;
      row.push(cell); cell = "";
      rows.push(row); row = [];
    } else cell += ch;
  }
  if (cell !== "" || row.length > 0) { row.push(cell); rows.push(row); }
  while (rows.length && rows[rows.length - 1].every((c) => c.trim() === "")) rows.pop();
  return rows;
}

const norm = (s: string) => s.toLowerCase().replace(/[^a-z0-9]/g, "");

/**
 * If the first row of pasted text is a header (most of its cells name this table's columns),
 * return the column each cell maps to (null for a heading we don't know); otherwise null.
 */
export function detectColumns(table: TableKey, headerRow: string[]): (Col | null)[] | null {
  const byName = new Map<string, Col>();
  for (const col of COLUMNS[table]) {
    for (const n of [col.key, col.label, col.aria, ...(col.aliases ?? [])]) byName.set(norm(n), col);
  }
  const mapped = headerRow.map((h) => byName.get(norm(h)) ?? null);
  const filled = headerRow.filter((h) => h.trim() !== "").length;
  const hits = mapped.filter(Boolean).length;
  return hits >= 1 && hits >= Math.ceil(filled / 2) ? mapped : null;
}

/** Slots written as "Mon:1, Tue:3" (lectures counted from 1) into zero-based slots. */
export function parseSlots(text: string, cal: Calendar): { slots: Slot[]; bad: string[] } {
  const slots: Slot[] = [];
  const bad: string[] = [];
  for (const tok of text.split(/[,;]/).map((t) => t.trim()).filter(Boolean)) {
    const [d, l] = tok.split(":").map((x) => x.trim());
    const day = cal.day_names.findIndex((n) => n.toLowerCase() === (d ?? "").toLowerCase());
    const lecture = Number(l);
    if (day < 0 || !Number.isInteger(lecture) || lecture < 1) bad.push(tok);
    else slots.push({ day, lecture: lecture - 1 });
  }
  return { slots, bad };
}

// ---------------------------------------------------------------- interpreting one cell

const INVALID = Symbol("invalid");

/** Turn text from a cell into the value its column holds, or INVALID if it can't be understood. */
function parseCell(inst: Institution, table: TableKey, col: Col, text: string): unknown | typeof INVALID {
  const opts = optionsFor(inst, table, col.key);
  switch (col.kind) {
    case "text": return text.trim();
    case "number": {
      const t = text.trim().replace(/,/g, "");
      return t === "" ? NaN : Number(t);
    }
    case "select": {
      const v = opts ? matchOption(opts, text) : text.trim();
      return col.key === "group_of" && v === "" ? null : v;
    }
    case "ids": {
      const parts = text.split(/[,;]/).map((p) => p.trim()).filter(Boolean);
      return parts.map((p) => (opts ? matchOption(opts, p) : p));
    }
    case "sessions": return parseSessions(text) ?? INVALID;
    case "slots": {
      const { slots, bad } = parseSlots(text, inst.calendar);
      return bad.length ? INVALID : slots;
    }
    case "id": return text.trim();
  }
}

type Row = Record<string, unknown>;
const rowsOf = (inst: Institution, table: TableKey) => inst[table] as unknown as Row[];
const withRows = (inst: Institution, table: TableKey, rows: Row[]) => ({ ...inst, [table]: rows }) as Institution;
const setField = (inst: Institution, table: TableKey, index: number, key: string, value: unknown) =>
  withRows(inst, table, rowsOf(inst, table).map((r, i) => (i === index ? { ...r, [key]: value } : r)));

/** A new row for pasted data: sensible defaults, but no invented links to other tables. */
function blankRow(inst: Institution, table: TableKey): Institution {
  const out = addRow(inst, table);
  if (table !== "offerings") return out;
  const last = out.offerings.length - 1;
  return {
    ...out,
    offerings: out.offerings.map((o, i) => (i === last ? { ...o, course_code: "", faculty_id: "", batch_ids: [], sessions: [] } : o)),
  };
}

// ---------------------------------------------------------------- pasting

export interface PasteOptions {
  /** Fill from this existing row down (rows past the end are added). Omit to add new rows. */
  startRow?: number;
  /** With no `columns`, fill the table's shown columns from this one rightwards. */
  startCol?: number;
  /** Which column each pasted cell belongs to (from a header row). */
  columns?: (Col | null)[];
  /** With an id column and no startRow: update the row with that id if there is one. */
  upsert?: boolean;
}

export interface PasteResult { inst: Institution; added: number; updated: number; problems: string[] }

/** Put a grid of text into a table the way a spreadsheet would. Never throws; says what it could not use. */
export function pasteGrid(inst: Institution, table: TableKey, grid: string[][], opts: PasteOptions = {}): PasteResult {
  const idKey = ID_FIELD[table];
  const cols: (Col | null)[] = opts.columns ?? shownColumns(table).slice(opts.startCol ?? 0);
  const idCol = cols.findIndex((c) => c?.key === idKey);
  let cur = inst;
  let added = 0;
  let updated = 0;
  const problems: string[] = [];

  grid.forEach((cells, r) => {
    const label = `row ${r + 1}`;
    let index: number | null = null;
    if (opts.startRow !== undefined) {
      if (opts.startRow + r < rowsOf(cur, table).length) index = opts.startRow + r;
    } else if (opts.upsert && idCol >= 0 && (cells[idCol] ?? "").trim() !== "") {
      const found = rowsOf(cur, table).findIndex((x) => x[idKey] === (cells[idCol] ?? "").trim());
      if (found >= 0) index = found;
    }
    let isNew = false;
    if (index === null) {
      cur = blankRow(cur, table);
      index = rowsOf(cur, table).length - 1;
      isNew = true;
      added++;
    } else updated++;

    cells.forEach((text, c) => {
      const col = cols[c];
      if (!col) return;
      if (col.kind === "id") {
        const wanted = text.trim();
        const current = String(rowsOf(cur, table)[index!][idKey]);
        if (wanted === "" || wanted === current) return;
        const taken = rowsOf(cur, table).some((x, k) => k !== index && x[idKey] === wanted);
        if (taken) { problems.push(`${label}: ${col.label} "${wanted}" is already used, so ${isNew ? "a new one was made" : "it was not changed"}`); return; }
        cur = isNew ? setField(cur, table, index!, idKey, wanted) : renameId(cur, table, current, wanted);
        return;
      }
      const value = parseCell(cur, table, col, text);
      if (value === INVALID) {
        problems.push(`${label}: could not read "${text.trim()}" as ${col.label.toLowerCase()}`);
        return;
      }
      cur = setField(cur, table, index!, col.key, value);
    });
  });
  return { inst: cur, added, updated, problems };
}

// ---------------------------------------------------------------- several rows at once

/** Set one column to the same value (given as text) on every selected row. */
export function bulkSet(inst: Institution, table: TableKey, ids: string[], key: string, text: string): PasteResult {
  const col = COLUMNS[table].find((c) => c.key === key);
  const idKey = ID_FIELD[table];
  if (!col || col.kind === "id") return { inst, added: 0, updated: 0, problems: ["that column can't be set in bulk"] };
  const value = parseCell(inst, table, col, text);
  if (value === INVALID) return { inst, added: 0, updated: 0, problems: [`could not read "${text.trim()}" as ${col.label.toLowerCase()}`] };
  const chosen = new Set(ids);
  let updated = 0;
  const rows = rowsOf(inst, table).map((r) => {
    if (!chosen.has(String(r[idKey]))) return r;
    updated++;
    return { ...r, [key]: value };
  });
  return { inst: withRows(inst, table, rows), added: 0, updated, problems: [] };
}

/** Copy the selected rows, each with a fresh id (and "(copy)" on its name, where it has one). */
export function duplicateRows(inst: Institution, table: TableKey, ids: string[]): { inst: Institution; newIds: string[] } {
  const idKey = ID_FIELD[table];
  const chosen = new Set(ids);
  const used = new Set(rowsOf(inst, table).map((r) => String(r[idKey])));
  const copies: Row[] = [];
  const newIds: string[] = [];
  for (const r of rowsOf(inst, table)) {
    const id = String(r[idKey]);
    if (!chosen.has(id)) continue;
    let n = 1;
    let fresh = `${id}-copy`;
    while (used.has(fresh)) fresh = `${id}-copy${++n}`;
    used.add(fresh);
    newIds.push(fresh);
    copies.push({ ...r, [idKey]: fresh, ...("name" in r && typeof r.name === "string" ? { name: `${r.name} (copy)` } : {}) });
  }
  return { inst: withRows(inst, table, [...rowsOf(inst, table), ...copies]), newIds };
}

/** Delete the selected rows, cleaning up whatever depended on them (as deleting one row does). */
export function removeRows(inst: Institution, table: TableKey, ids: string[]): Institution {
  let cur = inst;
  for (const id of ids) {
    const index = rowsOf(cur, table).findIndex((r) => r[ID_FIELD[table]] === id);
    if (index >= 0) cur = removeRow(cur, table, index);
  }
  return cur;
}

export interface RemovalSummary { offeringsRemoved: string[]; offeringsEdited: string[]; subgroupsFreed: string[] }

/** What deleting these rows would take with it, worked out by doing it and comparing. */
export function summariseRemoval(inst: Institution, table: TableKey, ids: string[]): RemovalSummary {
  const after = removeRows(inst, table, ids);
  const kept = new Map(after.offerings.map((o) => [o.id, o]));
  const removedBatches = new Set(table === "batches" ? ids : []);
  return {
    offeringsRemoved: inst.offerings.filter((o) => !kept.has(o.id)).map((o) => o.id),
    offeringsEdited: inst.offerings.filter((o) => {
      const k = kept.get(o.id);
      return k && JSON.stringify(k.batch_ids) !== JSON.stringify(o.batch_ids);
    }).map((o) => o.id),
    subgroupsFreed: inst.batches
      .filter((b) => b.group_of && !removedBatches.has(b.id))
      .filter((b) => after.batches.find((x) => x.id === b.id)?.group_of === null)
      .map((b) => b.id),
  };
}

/** Which rows match a typed filter (any column, ignoring case). */
export function matchesFilter(row: unknown, filter: string): boolean {
  const f = filter.trim().toLowerCase();
  if (!f) return true;
  return Object.values(row as Row).some((v) => (Array.isArray(v) ? v.map((x) => (typeof x === "object" ? "" : String(x))).join(" ") : typeof v === "object" ? "" : String(v ?? "")).toLowerCase().includes(f));
}
