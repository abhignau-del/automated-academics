import { memo, useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent as ReactKeyboardEvent, type ClipboardEvent as ReactClipboardEvent } from "react";
import * as api from "./api";
import { addRow, formatSessions, hasImpact, ID_FIELD, impact, parseSessions, removeRow, renameId, type TableKey } from "./dataops";
import { combine, isJoint, split, suggestJoint, type Result } from "./joint";
import { canRedo, canUndo, initHistory, pushHistory, redo, undo, type History } from "./history";
import { PasteDialog } from "./PasteDialog";
import {
  bulkSet, duplicateRows, matchesFilter, matchOption, optionsFor, parseTSV, pasteGrid, removeRows, shownColumns, summariseRemoval,
  type Col, type Opt, type PasteResult,
} from "./sheet";
import { SlotGrid } from "./SlotGrid";
import type { Calendar, CheckResult, DataIssue, Faculty, Institution, Section, Weights } from "./types";

const TABS: { key: Section; label: string }[] = [
  { key: "settings", label: "Settings" },
  { key: "rooms", label: "Rooms" },
  { key: "faculty", label: "Faculty" },
  { key: "batches", label: "Classes" },
  { key: "courses", label: "Courses" },
  { key: "offerings", label: "Offerings" },
  { key: "pins", label: "Locked" },
];
const TABLES: TableKey[] = ["rooms", "faculty", "batches", "courses", "offerings"];
const NOUN: Record<TableKey, { one: string; many: string; add: string }> = {
  rooms: { one: "room", many: "rooms", add: "Add room" },
  faculty: { one: "faculty member", many: "faculty", add: "Add faculty member" },
  batches: { one: "class", many: "classes", add: "Add class" },
  courses: { one: "course", many: "courses", add: "Add course" },
  offerings: { one: "offering", many: "offerings", add: "Add offering" },
};
const HELP: Partial<Record<TableKey, string>> = {
  batches: "A class is a fixed group of students. For labs that split a class in two, add each group as its own class and set its Parent.",
  offerings: "An offering is one course taught by one person to one or more classes (list several for a shared elective). Sessions are the lecture counts per week: 1, 1, 1 is three one-lecture sessions, 2 is one two-lecture block (a lab).",
};
const EMPTY_STATE: Partial<Record<TableKey, string>> = {
  rooms: "No rooms yet. Add the classrooms, labs and halls sessions can use.",
  offerings: "No offerings yet. Add courses, faculty and classes first, then say who teaches what to whom.",
};
const PAGE = 200; // rows drawn at a time, so very large tables stay responsive

const WEIGHT_FIELDS: { key: keyof Weights; label: string; hint: string }[] = [
  { key: "batch_gaps", label: "Class idle gaps", hint: "Free lectures between a class's first and last lecture of a day" },
  { key: "faculty_gaps", label: "Faculty idle gaps", hint: "Free lectures between a teacher's first and last lecture of a day" },
  { key: "peak_day_load", label: "Even week", hint: "Penalty per lecture on each class's busiest day, which spreads the week out" },
  { key: "repeat_course_day", label: "Repeated courses", hint: "Penalty per extra session of one course on the same day" },
  { key: "avoid_slot", label: "Avoided slots", hint: "Penalty per lecture placed where a teacher asked not to teach" },
];

const message = (e: unknown) => (e instanceof Error ? e.message : String(e));

/** The previous value, as long as the new one is structurally the same: a stable identity for memoised children. */
function useStable<T>(value: T): T {
  const ref = useRef(value);
  if (ref.current !== value && JSON.stringify(ref.current) !== JSON.stringify(value)) ref.current = value;
  return ref.current;
}
type Row = Record<string, unknown>;
const NO_ISSUES: DataIssue[] = []; // one shared empty list, so rows without problems never re-render for it

// ------------------------------------------------------------------ small controls

function IdCell({ value, onCommit, issue }: { value: string; onCommit: (v: string) => boolean; issue?: DataIssue }) {
  const [text, setText] = useState(value);
  const cancelled = useRef(false); // Escape: leaving the field must not apply what was typed
  useEffect(() => setText(value), [value]);
  const commit = () => {
    if (cancelled.current) { cancelled.current = false; setText(value); return; }
    if (!text.trim()) { setText(value); return; } // an id cannot be blank; keep the old one
    if (text !== value && !onCommit(text)) setText(value); // refused (already used): show the old id again
  };
  return (
    <input value={text} className={issue ? "bad" : ""} title={issue?.message} aria-label="id"
      onChange={(e) => setText(e.target.value)} onBlur={commit}
      onKeyDown={(e) => {
        if (e.key === "Enter") e.currentTarget.blur();
        if (e.key === "Escape") { cancelled.current = true; e.currentTarget.blur(); }
      }} />
  );
}

function Text({ value, onChange, issue, label }: { value: string; onChange: (v: string) => void; issue?: DataIssue; label: string }) {
  return <input value={value} aria-label={label} className={issue ? "bad" : ""} title={issue?.message}
    onChange={(e) => onChange(e.target.value)} />;
}

function Num({ value, onChange, issue, label, min = 0 }: {
  value: number; onChange: (v: number) => void; issue?: DataIssue; label: string; min?: number;
}) {
  return (
    <input type="number" min={min} className={"num " + (issue ? "bad" : "")} title={issue?.message} aria-label={label}
      value={Number.isNaN(value) ? "" : value}
      onChange={(e) => onChange(e.target.value === "" ? NaN : Number(e.target.value))} />
  );
}

/** Above this many choices a plain dropdown (one copy of every option per row) is too heavy; see ListInput. */
const LARGE = 40;
export const listId = (table: string, key: string) => `choices-${table}-${key}`;

/**
 * A type-ahead field backed by one shared <datalist> for the whole table, for long lists of choices
 * (hundreds of courses): each row costs one small input instead of its own copy of every option.
 * A choice is applied when the text matches one exactly or the field is left; Escape puts back the old value.
 */
function ListInput({ value, options, list, onCommit, issue, label, placeholder, clearOnCommit }: {
  value: string; options: Opt[]; list: string; onCommit: (v: string) => void; issue?: DataIssue; label: string;
  placeholder?: string; clearOnCommit?: boolean;
}) {
  const [text, setText] = useState(value);
  const cancelled = useRef(false); // Escape: leaving the field must not apply what was typed
  useEffect(() => setText(value), [value]);
  const commit = (raw: string) => {
    const v = matchOption(options, raw);
    if (clearOnCommit) { if (raw.trim() !== "") onCommit(v); setText(""); return; }
    if (v !== value) onCommit(v);
    setText(v);
  };
  const known = value === "" || options.some((o) => o.value === value);
  return (
    <input list={list} value={text} aria-label={label} placeholder={placeholder}
      className={issue || (!known && !clearOnCommit) ? "bad" : ""} title={issue?.message ?? options.find((o) => o.value === text)?.label}
      onChange={(e) => {
        setText(e.target.value);
        if (options.some((o) => o.value === e.target.value)) commit(e.target.value); // picked from the list
      }}
      onBlur={() => {
        if (cancelled.current) { cancelled.current = false; setText(clearOnCommit ? "" : value); return; }
        if (clearOnCommit ? text.trim() !== "" : text !== value) commit(text);
      }}
      onKeyDown={(e) => { if (e.key === "Escape") { cancelled.current = true; e.currentTarget.blur(); } }} />
  );
}

function Pick({ value, options, onChange, issue, label, allowBlank, list }: {
  value: string; options: Opt[]; onChange: (v: string) => void; issue?: DataIssue; label: string; allowBlank?: boolean; list?: string;
}) {
  if (list && options.length > LARGE) return <ListInput value={value} options={options} list={list} onCommit={onChange} issue={issue} label={label} />;
  const known = options.some((o) => o.value === value);
  return (
    <select value={value} aria-label={label} className={issue || (!known && value !== "") ? "bad" : ""} title={issue?.message}
      onChange={(e) => onChange(e.target.value)}>
      {allowBlank && <option value="">(none)</option>}
      {!allowBlank && value === "" && <option value="">(choose)</option>}
      {!known && value !== "" && <option value={value}>{value} (missing)</option>}
      {options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
    </select>
  );
}

/** Several ids from a list, shown as removable chips. */
function Chips({ values, options, onChange, issue, label, list }: {
  values: string[]; options: Opt[]; onChange: (v: string[]) => void; issue?: DataIssue; label: string; list?: string;
}) {
  const add = (v: string) => { if (v && !values.includes(v)) onChange([...values, v]); };
  return (
    <div className={"chips " + (issue ? "bad" : "")} title={issue?.message}>
      {values.map((v) => (
        <span key={v} className={"chip " + (options.some((o) => o.value === v) ? "" : "bad")}>
          {v}
          <button type="button" aria-label={`remove ${v}`} onClick={() => onChange(values.filter((x) => x !== v))}>×</button>
        </span>
      ))}
      {list && options.length > LARGE ? (
        <ListInput value="" options={options} list={list} onCommit={add} label={label} placeholder="+ add" clearOnCommit />
      ) : (
        <select aria-label={label} value="" onChange={(e) => add(e.target.value)}>
          <option value="">+ add</option>
          {options.filter((o) => !values.includes(o.value)).map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
        </select>
      )}
    </div>
  );
}

/** "1, 1, 1" style list of session lengths; only valid text is stored. */
function Sessions({ value, onChange, issue }: { value: number[]; onChange: (v: number[]) => void; issue?: DataIssue }) {
  const [text, setText] = useState(formatSessions(value));
  const [invalid, setInvalid] = useState(false);
  useEffect(() => { // an outside change (undo, paste, discard) wins over what is typed
    if (JSON.stringify(parseSessions(text)) !== JSON.stringify(value)) { setText(formatSessions(value)); setInvalid(false); }
  }, [value]); // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <input value={text} aria-label="sessions" className={invalid || issue ? "bad" : ""}
      title={invalid ? "Lecture counts per session, e.g. 1, 1, 1 (three one-lecture sessions) or 2 (one two-lecture block)" : issue?.message}
      onChange={(e) => {
        setText(e.target.value);
        const parsed = parseSessions(e.target.value);
        setInvalid(parsed === null);
        if (parsed) onChange(parsed);
      }} />
  );
}

/** Days as comma-separated text; applied when the field loses focus so typing is not disturbed. */
function DaysField({ value, onChange }: { value: string[]; onChange: (v: string[]) => void }) {
  const [text, setText] = useState(value.join(", "));
  useEffect(() => setText(value.join(", ")), [value]);
  const commit = () => {
    const days = text.split(",").map((d) => d.trim()).filter(Boolean);
    if (days.length === 0 || JSON.stringify(days) === JSON.stringify(value)) { setText(value.join(", ")); return; }
    onChange(days);
  };
  return <input value={text} aria-label="days" onChange={(e) => setText(e.target.value)} onBlur={commit}
    onKeyDown={(e) => { if (e.key === "Enter") e.currentTarget.blur(); }} />;
}

// ------------------------------------------------------------------ rows

/** What a row can do. One stable object, so memoised rows are not re-rendered by new callbacks. */
interface Actions {
  patch: (table: TableKey, index: number, key: string, value: unknown, typed: boolean) => void;
  rename: (table: TableKey, oldId: string, newId: string) => boolean;
  remove: (table: TableKey, index: number) => void;
  select: (table: TableKey, id: string, on: boolean) => void;
  toggleFaculty: (id: string) => void;
  setFaculty: (index: number, f: Faculty) => void;
}

const pickIssue = (issues: DataIssue[], key: string): DataIssue | undefined =>
  issues.filter((i) => i.field === key || i.field?.startsWith(key + ".")).sort((a, b) => (a.level === "error" ? -1 : 1) - (b.level === "error" ? -1 : 1))[0];

interface RowProps {
  table: TableKey; index: number; row: Row; issues: DataIssue[]; selected: boolean;
  opts: Record<string, Opt[]>; cal: Calendar; open: boolean; act: Actions;
}

const DataRow = memo(function DataRow({ table, index, row, issues, selected, opts, cal, open, act }: RowProps) {
  const cols = shownColumns(table);
  const id = String(row[ID_FIELD[table]] ?? "");
  const notes = issues.filter((i) => !i.field);

  const cell = (col: Col) => {
    const issue = pickIssue(issues, col.key);
    const value = row[col.key];
    const typed = col.kind === "text" || col.kind === "number" || col.kind === "sessions";
    const set = (v: unknown) => act.patch(table, index, col.key, v, typed);
    switch (col.kind) {
      case "id": return <IdCell value={String(value ?? "")} issue={issue} onCommit={(v) => act.rename(table, id, v)} />;
      case "text": return <Text label={col.aria} value={String(value ?? "")} issue={issue} onChange={set} />;
      case "number": return <Num label={col.aria} min={col.min} value={value as number} issue={issue} onChange={set} />;
      case "select": {
        const isParent = col.key === "group_of";
        const options = (opts[`${table}.${col.key}`] ?? []).filter((o) => !(isParent && o.value === id));
        return <Pick label={col.aria} allowBlank={isParent} list={listId(table, col.key)} value={String(value ?? "")} options={options} issue={issue}
          onChange={(v) => set(isParent ? v || null : v)} />;
      }
      case "ids": return <Chips label={col.aria} list={listId(table, col.key)} values={value as string[]} options={opts[`${table}.${col.key}`] ?? []} issue={issue} onChange={set} />;
      case "sessions": return <Sessions value={value as number[]} issue={issue} onChange={set} />;
      default: return null;
    }
  };

  const fac = table === "faculty" ? (row as unknown as Faculty) : null;
  const summary = fac
    ? [fac.unavailable.length ? `${fac.unavailable.length} unavailable` : "", fac.avoid.length ? `${fac.avoid.length} to avoid` : ""]
        .filter(Boolean).join(" · ") || "always available"
    : "";

  return (
    <>
      <tr data-row={index} className={selected ? "selected" : ""}>
        <td className="selcell">
          <input type="checkbox" aria-label={`select row ${index + 1}`} checked={selected} onChange={(e) => act.select(table, id, e.target.checked)} />
        </td>
        {cols.map((c) => <td key={c.key}>{cell(c)}</td>)}
        {fac && (
          <td>
            <button type="button" className="btn small" aria-expanded={open} onClick={() => act.toggleFaculty(fac.id)}>
              {open ? "Hide" : "Edit"} · {summary}
            </button>
          </td>
        )}
        <td className="rowend">
          {notes.map((n, k) => (
            <span key={k} className={"mark " + n.level} title={n.message}>{n.level === "error" ? "!" : n.level === "warning" ? "⚠" : "i"}</span>
          ))}
          <button type="button" className="icon" aria-label={`delete row ${index + 1}`} title="Delete this row" onClick={() => act.remove(table, index)}>🗑</button>
        </td>
      </tr>
      {fac && open && (
        <tr className="subrow">
          <td colSpan={cols.length + 3}><SlotGrid cal={cal} faculty={fac} onChange={(f) => act.setFaculty(index, f)} /></td>
        </tr>
      )}
    </>
  );
});

/**
 * The shared suggestion lists for a table's long choice columns. Memoised: it only renders again when
 * a list's contents change, not on every keystroke elsewhere in the table.
 */
const ChoiceLists = memo(function ChoiceLists({ table, opts }: { table: TableKey; opts: Record<string, Opt[]> }) {
  return (
    <>
      {Object.entries(opts).filter(([, o]) => o.length > LARGE).map(([k, o]) => (
        <datalist key={k} id={listId(table, k.split(".")[1])}>
          {o.map((opt) => <option key={opt.value} value={opt.value} label={opt.label} />)}
        </datalist>
      ))}
    </>
  );
});

// ------------------------------------------------------------------ joint classes

function JointPanel({ inst, onCombine }: { inst: Institution; onCombine: (ids: string[]) => void }) {
  const found = useMemo(() => suggestJoint(inst), [inst]);
  const joint = inst.offerings.filter(isJoint).length;
  const course = (code: string) => inst.courses.find((c) => c.code === code)?.name ?? code;
  const teacher = (id: string) => inst.faculty.find((f) => f.id === id)?.name ?? id;
  if (!found.length && !joint) return null;
  return (
    <details className="jointpanel" open={found.length > 0}>
      <summary>
        Joint classes: <b>{joint}</b> shared now{found.length > 0 && <>, <b>{found.length}</b> suggested</>}
      </summary>
      <p className="muted small">
        A joint class is one offering attended by several classes together, such as a shared elective. These offerings
        have the same course and teacher but separate classes. If they really meet together, combine them.
      </p>
      <ul className="pinlist">
        {found.map((g) => {
          const first = inst.offerings.find((o) => o.id === g.ids[0])!;
          return (
            <li key={g.ids.join("+")} className={g.roomFits ? "" : "bad"}>
              <span>
                <b>{course(first.course_code)}</b> by {teacher(first.faculty_id)}: {g.batchIds.join(", ")}
                {" "}({g.students} students, {g.sessions.length} lecture{g.sessions.length === 1 ? "" : "s"} a week)
              </span>
              {!g.roomFits && <small className="error">no room holds everyone together</small>}
              {g.sessionsDiffer && <small className="warning">their weekly lectures differ; the most is kept</small>}
              <span className="spacer" />
              <button type="button" className="btn small" onClick={() => onCombine(g.ids)}>Combine</button>
            </li>
          );
        })}
      </ul>
    </details>
  );
}

// ------------------------------------------------------------------ bulk actions

function BulkBar({ table, count, opts, onSet, onDuplicate, onDelete, onClear, onCombine, onSplit }: {
  table: TableKey; count: number; opts: Record<string, Opt[]>;
  onSet: (key: string, text: string) => void; onDuplicate: () => void; onDelete: () => void; onClear: () => void;
  onCombine?: () => void; onSplit?: () => void; // offerings only: make selected ones one joint class, or undo that
}) {
  const columns = shownColumns(table).filter((c) => c.kind !== "id");
  const [key, setKey] = useState(columns[0]?.key ?? "");
  const [text, setText] = useState("");
  useEffect(() => { setKey(columns[0]?.key ?? ""); setText(""); }, [table]); // eslint-disable-line react-hooks/exhaustive-deps
  const col = columns.find((c) => c.key === key);
  const choices = col ? opts[`${table}.${col.key}`] : undefined;
  return (
    <div className="bulkbar" role="group" aria-label="Actions for the selected rows">
      <b>{count} selected</b>
      <span className="bulkset">
        Set
        <select aria-label="column to set" value={key} onChange={(e) => { setKey(e.target.value); setText(""); }}>
          {columns.map((c) => <option key={c.key} value={c.key}>{c.label}</option>)}
        </select>
        to
        {col?.kind === "select" && choices ? (
          <select aria-label="new value" value={text} onChange={(e) => setText(e.target.value)}>
            <option value="">{col.key === "group_of" ? "(none)" : "(choose)"}</option>
            {choices.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
          </select>
        ) : (
          <input aria-label="new value" value={text} onChange={(e) => setText(e.target.value)}
            placeholder={col?.kind === "ids" ? "S1, S2" : col?.kind === "sessions" ? "1, 1, 1" : ""} />
        )}
        <button type="button" className="btn small" disabled={!col || (col.kind !== "text" && text === "" && col.key !== "group_of")}
          onClick={() => { onSet(key, text); }}>Apply</button>
      </span>
      <button type="button" className="btn small" onClick={onDuplicate}>Duplicate</button>
      {onCombine && <button type="button" className="btn small" onClick={onCombine} disabled={count < 2}
        title="Teach these together as one joint class (same course and teacher, different classes)">Combine as joint class</button>}
      {onSplit && <button type="button" className="btn small" onClick={onSplit}
        title="Give each class of a joint offering its own offering again">Split joint class</button>}
      <button type="button" className="btn small danger" onClick={onDelete}>Delete</button>
      <button type="button" className="linkish" onClick={onClear}>Clear selection</button>
    </div>
  );
}

// ------------------------------------------------------------------ the editor

interface Props {
  iid: string;
  institution: Institution; // the saved data
  onSaved: () => Promise<void>;
  onDeleted: () => void;
  onDirtyChange: (dirty: boolean) => void;
  canDelete?: boolean; // only an owner may delete (default true: sign-in off)
}

type SetDraft = (next: Institution | ((d: Institution) => Institution), key?: string) => void;

export function DataEditor({ iid, institution, onSaved, onDeleted, onDirtyChange, canDelete = true }: Props) {
  const [hist, setHist] = useState<History<Institution>>(() => initHistory(institution));
  const draft = hist.present;
  const draftRef = useRef(draft);
  draftRef.current = draft;
  /** Change the draft. Edits to the same cell in quick succession (same `key`) form one undo step. */
  const setDraft = useCallback<SetDraft>((next, key) => {
    setHist((h) => pushHistory(h, typeof next === "function" ? next(h.present) : next, key));
  }, []);

  const [tab, setTab] = useState<Section>("settings");
  const [check, setCheck] = useState<CheckResult | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [conflict, setConflict] = useState(false); // someone else saved first
  const [notice, setNotice] = useState<string | null>(null);
  const [openFaculty, setOpenFaculty] = useState<string | null>(null);
  const [showNotes, setShowNotes] = useState(false);
  const [filters, setFilters] = useState<Record<TableKey, string>>({ rooms: "", faculty: "", batches: "", courses: "", offerings: "" });
  const [limits, setLimits] = useState<Record<TableKey, number>>({ rooms: PAGE, faculty: PAGE, batches: PAGE, courses: PAGE, offerings: PAGE });
  const [selected, setSelected] = useState<Record<TableKey, string[]>>({ rooms: [], faculty: [], batches: [], courses: [], offerings: [] });
  const [pasting, setPasting] = useState<TableKey | null>(null);

  useEffect(() => {
    setHist(initHistory(institution));
    setError(null);
    setSelected({ rooms: [], faculty: [], batches: [], courses: [], offerings: [] });
  }, [institution]);

  const dirty = useMemo(() => JSON.stringify(draft) !== JSON.stringify(institution), [draft, institution]);
  useEffect(() => { onDirtyChange(dirty); }, [dirty, onDirtyChange]);

  // live validation: the server decides what is valid
  useEffect(() => {
    let cancelled = false;
    const t = setTimeout(() => {
      api.checkInstitution(draft)
        .then((r) => { if (!cancelled) setCheck(r); })
        .catch((e) => { if (!cancelled) setError(message(e)); });
    }, 300);
    return () => { cancelled = true; clearTimeout(t); };
  }, [draft]);

  const issues = check?.issues ?? NO_ISSUES;
  const errors = issues.filter((i) => i.level === "error");
  const notes = issues.filter((i) => i.level !== "error");
  const inSection = (s: Section) => issues.filter((i) => i.section === s);
  /** Issues grouped by section and row, so each row looks up its own without scanning them all. */
  const issueMap = useMemo(() => {
    const m = new Map<string, DataIssue[]>();
    for (const i of issues) {
      if (i.index === null || !i.section) continue;
      const k = `${i.section}:${i.index}`;
      m.set(k, [...(m.get(k) ?? []), i]);
    }
    return m;
  }, [issues]);

  /**
   * Choices for each select / chips column, per table. Each table's set keeps the same identity
   * while its contents are unchanged: editing a course must not hand every course row a "new"
   * list of 900 choices, which would re-render (and make React compare) all of them.
   */
  const allOpts = useMemo(() => {
    const o: Record<string, Opt[]> = {};
    for (const table of TABLES) for (const col of shownColumns(table)) {
      const list = optionsFor(draft, table, col.key);
      if (list) o[`${table}.${col.key}`] = list;
    }
    return o;
  }, [draft.courses, draft.faculty, draft.batches]); // eslint-disable-line react-hooks/exhaustive-deps
  const optsFor1 = (table: TableKey) => Object.fromEntries(Object.entries(allOpts).filter(([k]) => k.startsWith(table + ".")));
  const tableOpts: Record<TableKey, Record<string, Opt[]>> = {
    rooms: useStable(optsFor1("rooms")),
    faculty: useStable(optsFor1("faculty")),
    batches: useStable(optsFor1("batches")),
    courses: useStable(optsFor1("courses")),
    offerings: useStable(optsFor1("offerings")),
  };

  // ---- row actions (one stable object; reads the latest draft through the ref)
  const act = useMemo<Actions>(() => ({
    // typing in a cell is folded into one undo step; picking from a list or a chip is always its own
    patch: (table, index, key, value, typed) => setDraft((d) => ({
      ...d,
      [table]: (d[table] as unknown as Row[]).map((r, k) => (k === index ? { ...r, [key]: value } : r)),
    }) as Institution, typed ? `${table}.${index}.${key}` : undefined),

    rename: (table, oldId, newId) => {
      const field = ID_FIELD[table];
      if ((draftRef.current[table] as unknown as Row[]).some((r) => r[field] === newId)) {
        setNotice(`Can't rename to "${newId}": it is already used.`);
        return false;
      }
      setNotice(null);
      setDraft((d) => renameId(d, table, oldId, newId));
      setOpenFaculty((o) => (o === oldId ? newId : o));
      setSelected((s) => ({ ...s, [table]: s[table].map((x) => (x === oldId ? newId : x)) }));
      return true;
    },

    remove: (table, index) => {
      const d = draftRef.current;
      const id = String((d[table] as unknown as Row[])[index][ID_FIELD[table]]);
      const gone = impact(d, table, id);
      let text = `Delete ${id}?`;
      if (hasImpact(gone)) {
        const bits = [];
        if (gone.offeringsRemoved.length) bits.push(`delete the offering${gone.offeringsRemoved.length > 1 ? "s" : ""} ${gone.offeringsRemoved.join(", ")}`);
        if (gone.offeringsEdited.length) bits.push(`remove it from ${gone.offeringsEdited.join(", ")}`);
        if (gone.subgroups.length) bits.push(`make ${gone.subgroups.join(", ")} stand-alone classes`);
        text += `\n\nThis will also ${bits.join(", and ")}.`;
      }
      if (confirm(text)) { setNotice(null); setDraft((x) => removeRow(x, table, index)); }
    },

    select: (table, id, on) => setSelected((s) => ({ ...s, [table]: on ? [...new Set([...s[table], id])] : s[table].filter((x) => x !== id) })),
    toggleFaculty: (id) => setOpenFaculty((o) => (o === id ? null : id)),
    setFaculty: (index, f) => setDraft((d) => ({ ...d, faculty: d.faculty.map((x, k) => (k === index ? f : x)) }), `faculty.${index}.slots`),
  }), [setDraft]);

  // ---- bulk, paste, undo
  const idsOf = (table: TableKey) => {
    const have = new Set((draft[table] as unknown as Row[]).map((r) => String(r[ID_FIELD[table]])));
    return selected[table].filter((id) => have.has(id));
  };
  const report = (what: string, r: Pick<PasteResult, "problems">) => {
    const bits = r.problems.slice(0, 5).map((p) => `• ${p}`);
    if (r.problems.length > 5) bits.push(`• …and ${r.problems.length - 5} more`);
    setNotice([what, ...bits].join("\n"));
  };

  const applyPaste = (table: TableKey, grid: string[][], at: { row: number; col: number }) => {
    const r = pasteGrid(draftRef.current, table, grid, { startRow: at.row, startCol: at.col });
    setDraft(r.inst);
    const total = grid.length;
    report(`Pasted ${total} row${total === 1 ? "" : "s"}${r.added ? ` (${r.added} new)` : ""}.`, r);
    if (!r.problems.length && !r.added) setNotice(null);
  };

  const onTablePaste = (table: TableKey) => (e: ReactClipboardEvent<HTMLDivElement>) => {
    const text = e.clipboardData.getData("text/plain");
    if (!/[\t\n]/.test(text.replace(/\r?\n$/, ""))) return; // a single value: let the field take it as usual
    const td = (e.target as HTMLElement).closest("td");
    const tr = td?.closest("tr[data-row]") as HTMLElement | null;
    if (!td || !tr) return;
    const col = Array.from(tr.children).indexOf(td) - 1; // the first cell is the row's checkbox
    if (col < 0 || col >= shownColumns(table).length) return;
    e.preventDefault();
    applyPaste(table, parseTSV(text), { row: Number(tr.dataset.row), col });
  };

  const bulkApply = (table: TableKey, key: string, text: string) => {
    const r = bulkSet(draftRef.current, table, idsOf(table), key, text);
    if (r.problems.length) { setNotice(r.problems.join("\n")); return; }
    setNotice(null);
    setDraft(r.inst);
  };
  const bulkDuplicate = (table: TableKey) => {
    const { inst, newIds } = duplicateRows(draftRef.current, table, idsOf(table));
    setNotice(null);
    setDraft(inst);
    setSelected((s) => ({ ...s, [table]: newIds }));
  };
  const applyJoint = (r: Result) => {
    if ("error" in r) { setNotice(r.error); return; }
    setNotice(r.note);
    setDraft(r.inst);
    setSelected((s) => ({ ...s, offerings: [r.kept] }));
  };
  const bulkSplit = () => {
    const joint = idsOf("offerings").filter((id) => draftRef.current.offerings.some((o) => o.id === id && isJoint(o)));
    if (!joint.length) { setNotice("None of the selected offerings is shared by several classes."); return; }
    let inst = draftRef.current;
    for (const id of joint) { const r = split(inst, id); if ("error" in r) { setNotice(r.error); return; } inst = r.inst; }
    setNotice(null);
    setDraft(inst);
  };
  const bulkDelete = (table: TableKey) => {
    const ids = idsOf(table);
    const s = summariseRemoval(draftRef.current, table, ids);
    const bits = [];
    if (s.offeringsRemoved.length) bits.push(`delete ${s.offeringsRemoved.length} offering${s.offeringsRemoved.length > 1 ? "s" : ""} (${s.offeringsRemoved.slice(0, 6).join(", ")}${s.offeringsRemoved.length > 6 ? ", …" : ""})`);
    if (s.offeringsEdited.length) bits.push(`remove them from ${s.offeringsEdited.length} shared offering${s.offeringsEdited.length > 1 ? "s" : ""}`);
    if (s.subgroupsFreed.length) bits.push(`make ${s.subgroupsFreed.join(", ")} stand-alone classes`);
    if (!confirm(`Delete ${ids.length} ${ids.length === 1 ? NOUN[table].one : NOUN[table].many}?` + (bits.length ? `\n\nThis will also ${bits.join(", and ")}.` : ""))) return;
    setNotice(null);
    setDraft((d) => removeRows(d, table, ids));
    setSelected((x) => ({ ...x, [table]: [] }));
  };

  const doUndo = () => { setHist(undo); setNotice(null); };
  const doRedo = () => { setHist(redo); setNotice(null); };
  const onEditorKeys = (e: ReactKeyboardEvent<HTMLDivElement>) => {
    if (!(e.ctrlKey || e.metaKey) || e.altKey) return;
    const t = e.target as HTMLElement;
    if (t instanceof HTMLTextAreaElement || (t instanceof HTMLInputElement && t.type !== "checkbox")) return; // the field's own undo
    const k = e.key.toLowerCase();
    if (k === "z" && !e.shiftKey) { e.preventDefault(); doUndo(); }
    else if (k === "y" || (k === "z" && e.shiftKey)) { e.preventDefault(); doRedo(); }
  };

  /** Spreadsheet-style movement: Enter and (in text cells) the up / down arrows move between rows. */
  const onTableKeys = (e: ReactKeyboardEvent<HTMLDivElement>) => {
    if (e.altKey || e.ctrlKey || e.metaKey) return;
    const el = e.target as HTMLElement;
    if (!(el instanceof HTMLInputElement) || el.type === "checkbox") return;
    const vertical = e.key === "ArrowDown" || e.key === "ArrowUp";
    if (!(e.key === "Enter" || (vertical && el.type === "text"))) return;
    const down = e.key === "ArrowDown" || (e.key === "Enter" && !e.shiftKey);
    const td = el.closest("td");
    const tr = td?.closest("tr[data-row]");
    if (!td || !tr) return;
    const at = Array.from(tr.children).indexOf(td);
    let next = (down ? tr.nextElementSibling : tr.previousElementSibling) as HTMLElement | null;
    while (next && !next.matches("tr[data-row]")) next = (down ? next.nextElementSibling : next.previousElementSibling) as HTMLElement | null;
    const target = next?.children[at]?.querySelector<HTMLInputElement | HTMLSelectElement>("input:not([type=checkbox]), select");
    if (!target) return;
    e.preventDefault();
    target.focus();
    if (target instanceof HTMLInputElement) target.select();
  };

  async function save() {
    setSaving(true); setError(null);
    try { await api.updateInstitution(iid, draft); setConflict(false); await onSaved(); }
    catch (e) { setError(message(e)); setConflict(e instanceof api.ApiError && e.status === 409); }
    finally { setSaving(false); }
  }

  async function del() {
    if (!confirm(`Permanently delete "${institution.name}" and all of its timetables?\n\nThis cannot be undone.`)) return;
    try { await api.deleteInstitution(iid); onDeleted(); } catch (e) { setError(message(e)); }
  }

  // ---- one table
  const tableView = (table: TableKey) => {
    const rows = draft[table] as unknown as Row[];
    const filter = filters[table];
    const idKey = ID_FIELD[table];
    const visible = rows.map((_, i) => i).filter((i) => matchesFilter(rows[i], filter));
    const drawn = visible.slice(0, limits[table]);
    const ids = idsOf(table);
    const chosen = new Set(ids);
    const allShown = drawn.length > 0 && drawn.every((i) => chosen.has(String(rows[i][idKey])));
    const cols = shownColumns(table);
    return (
      <div className="tablewrap" onPaste={onTablePaste(table)} onKeyDown={onTableKeys}>
        {HELP[table] && <p className="muted small">{HELP[table]}</p>}
        {table === "offerings" && <JointPanel inst={draft} onCombine={(ids) => applyJoint(combine(draftRef.current, ids))} />}
        <div className="tabletools">
          <input type="search" className="filter" placeholder={`Filter ${NOUN[table].many}…`} aria-label={`filter ${NOUN[table].many}`}
            value={filter} onChange={(e) => { setFilters({ ...filters, [table]: e.target.value }); setLimits({ ...limits, [table]: PAGE }); }} />
          {filter && <span className="muted small">{visible.length} of {rows.length}</span>}
          <span className="spacer" />
          <button type="button" className="btn small" onClick={() => setPasting(table)}>Paste from spreadsheet…</button>
          <button type="button" className="btn" onClick={() => setDraft((d) => addRow(d, table))}>+ {NOUN[table].add}</button>
        </div>
        {ids.length > 0 && (
          <BulkBar table={table} count={ids.length} opts={tableOpts[table]}
            onSet={(key, text) => bulkApply(table, key, text)} onDuplicate={() => bulkDuplicate(table)} onDelete={() => bulkDelete(table)}
            onClear={() => setSelected((s) => ({ ...s, [table]: [] }))}
            onCombine={table === "offerings" ? () => applyJoint(combine(draftRef.current, idsOf(table))) : undefined}
            onSplit={table === "offerings" ? bulkSplit : undefined} />
        )}
        <ChoiceLists table={table} opts={tableOpts[table]} />

        <table className="etable">
          <thead>
            <tr>
              <th className="selcell">
                <input type="checkbox" aria-label="select all shown rows" checked={allShown}
                  onChange={(e) => {
                    const shownIds = drawn.map((i) => String(rows[i][idKey]));
                    setSelected((s) => ({ ...s, [table]: e.target.checked ? [...new Set([...s[table], ...shownIds])] : s[table].filter((x) => !shownIds.includes(x)) }));
                  }} />
              </th>
              {cols.map((c) => <th key={c.key} title={c.kind === "number" ? "number" : undefined}>{c.label}</th>)}
              {table === "faculty" && <th>Availability</th>}
              <th />
            </tr>
          </thead>
          <tbody>
            {drawn.map((i) => (
              <DataRow key={i} table={table} index={i} row={rows[i]} issues={issueMap.get(`${table}:${i}`) ?? NO_ISSUES}
                selected={chosen.has(String(rows[i][idKey]))} opts={tableOpts[table]} cal={draft.calendar}
                open={table === "faculty" && openFaculty === String(rows[i].id)} act={act} />
            ))}
          </tbody>
        </table>
        {visible.length > drawn.length && (
          <button type="button" className="btn" onClick={() => setLimits({ ...limits, [table]: limits[table] + PAGE })}>
            Show {Math.min(PAGE, visible.length - drawn.length)} more ({visible.length - drawn.length} not shown)
          </button>
        )}
        {rows.length === 0 && <p className="muted">{EMPTY_STATE[table] ?? `No ${NOUN[table].many} yet.`}</p>}
        {rows.length > 0 && visible.length === 0 && <p className="muted">Nothing matches "{filter}".</p>}
      </div>
    );
  };

  return (
    <div className="editor" onKeyDown={onEditorKeys}>
      <div className="toolbar">
        <h2>Data</h2>
        <span className="muted">Edit the institution here, or upload a workbook. Changes are applied when you save.</span>
        <span className="spacer" />
        {dirty && <span className="status bad">Unsaved changes</span>}
        <button className="btn" onClick={doUndo} disabled={!canUndo(hist)} title="Undo the last change (Ctrl+Z)">Undo</button>
        <button className="btn" onClick={doRedo} disabled={!canRedo(hist)} title="Redo (Ctrl+Y)">Redo</button>
        <button className="btn" onClick={() => { setHist(initHistory(institution)); setNotice(null); }} disabled={!dirty}>Discard</button>
        <button className="btn primary" onClick={save} disabled={!dirty || saving || !check?.valid}
          title={dirty && check && !check.valid ? "Fix the errors below first" : undefined}>
          {saving ? "Saving…" : "Save changes"}
        </button>
        <a className="btn" href={api.workbookUrl(iid)} title="Download the saved data as an Excel workbook (a backup you can upload again)">
          Download Excel
        </a>
        {canDelete && <button className="btn danger" onClick={del}>Delete institution</button>}
      </div>

      {error && (
        <div className="alert" role="alert">
          <span>{error}
            {conflict && (
              <> <button className="linkish" onClick={async () => {
                if (!confirm("Throw away your unsaved changes and load the other person's version?")) return;
                setError(null); setConflict(false);
                await onSaved(); // reloading replaces the draft with the saved data
              }}>Load their version (discards my changes)</button></>
            )}
          </span>
          <button onClick={() => { setError(null); setConflict(false); }}>×</button>
        </div>
      )}
      {notice && <div className="alert note" role="status">{notice}<button onClick={() => setNotice(null)}>×</button></div>}

      <div className="summary">
        {check === null ? <span className="muted">Checking…</span> : errors.length === 0 ? (
          <span className="status ok">No problems found</span>
        ) : (
          <span className="status bad">{errors.length} problem{errors.length === 1 ? "" : "s"}</span>
        )}
        {notes.length > 0 && (
          <button type="button" className="linkish" onClick={() => setShowNotes((s) => !s)}>
            {showNotes ? "hide" : "show"} {notes.length} note{notes.length === 1 ? "" : "s"}
          </button>
        )}
        {check && !check.valid && <span className="muted">This can't be saved until the problems are fixed.</span>}
        {check?.valid && errors.length > 0 && <span className="muted">It can be saved, but no timetable can be generated until these are fixed.</span>}
      </div>
      {(errors.length > 0 || showNotes) && (
        <ul className="issuelist">
          {[...errors, ...(showNotes ? notes : [])].slice(0, 30).map((i, k) => (
            <li key={k} className={i.level}>
              <button type="button" className="linkish" onClick={() => i.section && setTab(i.section)}>
                {i.section ? TABS.find((t) => t.key === i.section)?.label : "General"}
                {i.index !== null ? ` · row ${i.index + 1}` : ""}
              </button>
              {" "}{i.message}
            </li>
          ))}
        </ul>
      )}

      <div className="etabs" role="tablist">
        {TABS.map((t) => {
          const n = inSection(t.key).filter((i) => i.level === "error").length;
          return (
            <button key={t.key} role="tab" aria-selected={tab === t.key} className={tab === t.key ? "active" : ""} onClick={() => setTab(t.key)}>
              {t.label}
              {t.key !== "settings" && <small>{draft[t.key].length}</small>}
              {n > 0 && <span className="badge">{n}</span>}
            </button>
          );
        })}
      </div>

      {tab === "settings" && (
        <div className="settings">
          <label>Institution name
            <input value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value }, "settings.name")}
              className={inSection("settings").some((i) => i.field === "name" && i.level === "error") ? "bad" : ""} />
          </label>
          <label>Days <small className="muted">comma-separated, in order</small>
            <DaysField value={draft.calendar.day_names} onChange={(v) => setDraft({ ...draft, calendar: { ...draft.calendar, day_names: v } })} />
          </label>
          <label>Lectures per day
            <Num label="lectures per day" min={1} value={draft.calendar.lectures_per_day}
              issue={inSection("settings").find((i) => i.field === "lectures_per_day")}
              onChange={(v) => setDraft({ ...draft, calendar: { ...draft.calendar, lectures_per_day: v } }, "settings.lectures")} />
          </label>
          <fieldset>
            <legend>Breaks <small className="muted">a lecture block never runs across a break</small></legend>
            <div className="breaks">
              {Array.from({ length: Math.max(0, (Number.isNaN(draft.calendar.lectures_per_day) ? 0 : draft.calendar.lectures_per_day) - 1) }, (_, b) => (
                <label key={b} className="check">
                  <input type="checkbox" checked={draft.calendar.break_after.includes(b)}
                    onChange={(e) => setDraft({
                      ...draft,
                      calendar: {
                        ...draft.calendar,
                        break_after: e.target.checked
                          ? [...draft.calendar.break_after, b].sort((x, y) => x - y)
                          : draft.calendar.break_after.filter((x) => x !== b),
                      },
                    })} />
                  after L{b + 1}
                </label>
              ))}
            </div>
          </fieldset>
          <fieldset>
            <legend>What makes a good timetable <small className="muted">higher means the solver works harder at it; 0 switches it off</small></legend>
            <div className="weights">
              {WEIGHT_FIELDS.map((w) => (
                <label key={w.key} title={w.hint}>{w.label}
                  <Num label={w.label} value={draft.weights[w.key]}
                    onChange={(v) => setDraft({ ...draft, weights: { ...draft.weights, [w.key]: v } }, `settings.${w.key}`)} />
                </label>
              ))}
            </div>
          </fieldset>
          {inSection("settings").filter((i) => i.field !== "lectures_per_day").map((i, k) => (
            <p key={k} className={"setting-note " + i.level}>{i.message}</p>
          ))}
        </div>
      )}

      {tab === "pins" && (
        <div className="pins">
          <p className="muted">Locked sessions stay exactly where they are when you generate a timetable. Lock them from the Timetable tab (select a session, then Lock in place).</p>
          {draft.pins.length === 0 && <p>Nothing is locked.</p>}
          <ul className="pinlist">
            {draft.pins.map((pin, i) => {
              const off = draft.offerings.find((o) => o.id === pin.offering_id);
              const course = draft.courses.find((c) => c.code === off?.course_code)?.name ?? off?.course_code ?? pin.offering_id;
              const room = draft.rooms.find((r) => r.id === pin.room_id)?.name;
              const problems = inSection("pins").filter((x) => x.index === i);
              return (
                <li key={`${pin.offering_id}#${pin.session_index}`} className={problems.some((x) => x.level === "error") ? "bad" : ""}>
                  <span>🔒 <b>{course}</b> ({pin.offering_id}) session {pin.session_index + 1}: {draft.calendar.day_names[pin.day] ?? `day ${pin.day + 1}`} L{pin.start + 1}{room ? `, ${room}` : ""}</span>
                  {problems.map((x, k) => <small key={k} className={x.level}>{x.message}</small>)}
                  <span className="spacer" />
                  <button className="btn" onClick={() => setDraft({ ...draft, pins: draft.pins.filter((_, j) => j !== i) })}>Unlock</button>
                </li>
              );
            })}
          </ul>
          {draft.pins.length > 1 && <button className="btn" onClick={() => setDraft({ ...draft, pins: [] })}>Unlock all</button>}
        </div>
      )}

      {tab !== "settings" && tab !== "pins" && tableView(tab as TableKey)}

      {pasting && (
        <PasteDialog table={pasting} title={NOUN[pasting].many} inst={draft}
          onClose={() => setPasting(null)}
          onApply={(r) => { setDraft(r.inst); report(`Pasted: ${r.added} new${r.updated ? `, ${r.updated} updated` : ""}.`, r); setPasting(null); }} />
      )}
    </div>
  );
}
