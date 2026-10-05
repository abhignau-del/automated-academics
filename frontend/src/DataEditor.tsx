import { useEffect, useMemo, useState, type ReactNode } from "react";
import * as api from "./api";
import {
  addRow, formatSessions, hasImpact, idOf, impact, parseSessions, removeRow, renameId, type TableKey,
} from "./dataops";
import { SlotGrid } from "./SlotGrid";
import type { CheckResult, DataIssue, Faculty, Institution, RoomKind, Section, Weights } from "./types";

const TABS: { key: Section; label: string }[] = [
  { key: "settings", label: "Settings" },
  { key: "rooms", label: "Rooms" },
  { key: "faculty", label: "Faculty" },
  { key: "batches", label: "Classes" },
  { key: "courses", label: "Courses" },
  { key: "offerings", label: "Offerings" },
];
const ROOM_KINDS: RoomKind[] = ["classroom", "lab", "hall"];

const WEIGHT_FIELDS: { key: keyof Weights; label: string; hint: string }[] = [
  { key: "batch_gaps", label: "Class idle gaps", hint: "Free lectures between a class's first and last lecture of a day" },
  { key: "faculty_gaps", label: "Faculty idle gaps", hint: "Free lectures between a teacher's first and last lecture of a day" },
  { key: "peak_day_load", label: "Even week", hint: "Penalty per lecture on each class's busiest day, which spreads the week out" },
  { key: "repeat_course_day", label: "Repeated courses", hint: "Penalty per extra session of one course on the same day" },
  { key: "avoid_slot", label: "Avoided slots", hint: "Penalty per lecture placed where a teacher asked not to teach" },
];

const message = (e: unknown) => (e instanceof Error ? e.message : String(e));

// ------------------------------------------------------------------ small controls

function IdCell({ value, onCommit, issue }: { value: string; onCommit: (v: string) => boolean; issue?: DataIssue }) {
  const [text, setText] = useState(value);
  useEffect(() => setText(value), [value]);
  const commit = () => {
    if (!text.trim()) { setText(value); return; } // an id cannot be blank; keep the old one
    if (text !== value && !onCommit(text)) setText(value); // refused (already used): show the old id again
  };
  return (
    <input value={text} className={issue ? "bad" : ""} title={issue?.message} aria-label="id"
      onChange={(e) => setText(e.target.value)} onBlur={commit}
      onKeyDown={(e) => {
        if (e.key === "Enter") e.currentTarget.blur();
        if (e.key === "Escape") { setText(value); e.currentTarget.blur(); }
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

function Pick({ value, options, onChange, issue, label, allowBlank }: {
  value: string; options: { value: string; label: string }[]; onChange: (v: string) => void;
  issue?: DataIssue; label: string; allowBlank?: boolean;
}) {
  const known = options.some((o) => o.value === value);
  return (
    <select value={value} aria-label={label} className={issue || (!known && value !== "") ? "bad" : ""} title={issue?.message}
      onChange={(e) => onChange(e.target.value)}>
      {allowBlank && <option value="">(none)</option>}
      {!known && value !== "" && <option value={value}>{value} (missing)</option>}
      {options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
    </select>
  );
}

/** Several ids from a list, shown as removable chips. */
function Chips({ values, options, onChange, issue, label }: {
  values: string[]; options: { value: string; label: string }[]; onChange: (v: string[]) => void;
  issue?: DataIssue; label: string;
}) {
  const left = options.filter((o) => !values.includes(o.value));
  return (
    <div className={"chips " + (issue ? "bad" : "")} title={issue?.message}>
      {values.map((v) => (
        <span key={v} className={"chip " + (options.some((o) => o.value === v) ? "" : "bad")}>
          {v}
          <button type="button" aria-label={`remove ${v}`} onClick={() => onChange(values.filter((x) => x !== v))}>×</button>
        </span>
      ))}
      <select aria-label={label} value="" onChange={(e) => e.target.value && onChange([...values, e.target.value])}>
        <option value="">+ add</option>
        {left.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
      </select>
    </div>
  );
}

/** "1, 1, 1" style list of session lengths; only valid text is stored. */
function Sessions({ value, onChange, issue }: { value: number[]; onChange: (v: number[]) => void; issue?: DataIssue }) {
  const [text, setText] = useState(formatSessions(value));
  const [invalid, setInvalid] = useState(false);
  useEffect(() => { // an outside change (undo, discard) wins over what is typed
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

function TableSection({ children, onAdd, addLabel }: { children: ReactNode; onAdd: () => void; addLabel: string }) {
  return (
    <div className="tablewrap">
      {children}
      <button type="button" className="btn" onClick={onAdd}>+ {addLabel}</button>
    </div>
  );
}

/** The last cell of a row: any notes about the row, and its delete button. */
function RowEnd({ notes, index, onDelete }: { notes: DataIssue[]; index: number; onDelete: () => void }) {
  return (
    <td className="rowend">
      {notes.map((n, k) => (
        <span key={k} className={"mark " + n.level} title={n.message}>{n.level === "error" ? "!" : n.level === "warning" ? "⚠" : "i"}</span>
      ))}
      <button type="button" className="icon" aria-label={`delete row ${index + 1}`} title="Delete this row" onClick={onDelete}>🗑</button>
    </td>
  );
}

// ------------------------------------------------------------------ the editor

interface Props {
  iid: string;
  institution: Institution; // the saved data
  onSaved: () => Promise<void>;
  onDeleted: () => void;
  onDirtyChange: (dirty: boolean) => void;
}

export function DataEditor({ iid, institution, onSaved, onDeleted, onDirtyChange }: Props) {
  const [draft, setDraft] = useState<Institution>(institution);
  const [tab, setTab] = useState<Section>("settings");
  const [check, setCheck] = useState<CheckResult | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [openFaculty, setOpenFaculty] = useState<string | null>(null);
  const [showNotes, setShowNotes] = useState(false);

  useEffect(() => { setDraft(institution); setError(null); }, [institution]);

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

  const issues = check?.issues ?? [];
  const errors = issues.filter((i) => i.level === "error");
  const notes = issues.filter((i) => i.level !== "error");
  const inSection = (s: Section) => issues.filter((i) => i.section === s);
  const matches = (i: DataIssue, section: Section, index: number, field: string) =>
    i.section === section && i.index === index && (i.field === field || i.field?.startsWith(field + ".") === true);
  /** The most serious problem recorded against one cell. */
  const cellIssue = (section: Section, index: number, field: string) =>
    issues.filter((i) => matches(i, section, index, field)).sort((a, b) => (a.level === "error" ? -1 : 1) - (b.level === "error" ? -1 : 1))[0];
  const rowNotes = (section: Section, index: number) =>
    issues.filter((i) => i.section === section && i.index === index && !i.field);

  const patch = <T extends TableKey>(table: T, i: number, p: Partial<Institution[T][number]>) =>
    setDraft((d) => ({
      ...d,
      [table]: (d[table] as unknown[]).map((r, k) => (k === i ? { ...(r as object), ...p } : r)),
    }) as Institution);

  /** Rename a row's id everywhere it is used. Returns false (and says why) if the new id is taken. */
  const rename = (table: TableKey, oldId: string, newId: string): boolean => {
    const clash = (draft[table] as unknown as Record<string, string>[]).some((r) => r[table === "courses" ? "code" : "id"] === newId);
    if (clash) { setNotice(`Can't rename to "${newId}": it is already used.`); return false; }
    setNotice(null);
    setDraft((d) => renameId(d, table, oldId, newId));
    return true;
  };

  const remove = (table: TableKey, i: number) => {
    const id = idOf(draft, table, i);
    const gone = impact(draft, table, id);
    let text = `Delete ${id}?`;
    if (hasImpact(gone)) {
      const bits = [];
      if (gone.offeringsRemoved.length) bits.push(`delete the offering${gone.offeringsRemoved.length > 1 ? "s" : ""} ${gone.offeringsRemoved.join(", ")}`);
      if (gone.offeringsEdited.length) bits.push(`remove it from ${gone.offeringsEdited.join(", ")}`);
      if (gone.subgroups.length) bits.push(`make ${gone.subgroups.join(", ")} stand-alone classes`);
      text += `\n\nThis will also ${bits.join(", and ")}.`;
    }
    if (confirm(text)) { setNotice(null); setDraft((d) => removeRow(d, table, i)); }
  };

  async function save() {
    setSaving(true); setError(null);
    try { await api.updateInstitution(iid, draft); await onSaved(); }
    catch (e) { setError(message(e)); }
    finally { setSaving(false); }
  }

  async function del() {
    if (!confirm(`Permanently delete "${institution.name}" and all of its timetables?\n\nThis cannot be undone.`)) return;
    try { await api.deleteInstitution(iid); onDeleted(); } catch (e) { setError(message(e)); }
  }

  const opts = {
    courses: draft.courses.map((c) => ({ value: c.code, label: `${c.code}${c.name ? " — " + c.name : ""}` })),
    faculty: draft.faculty.map((f) => ({ value: f.id, label: `${f.name} (${f.id})` })),
    batches: draft.batches.map((b) => ({ value: b.id, label: b.id })),
    parents: draft.batches.filter((b) => !b.group_of).map((b) => ({ value: b.id, label: b.id })),
    kinds: ROOM_KINDS.map((k) => ({ value: k, label: k })),
    levels: ["UG", "PG"].map((k) => ({ value: k, label: k })),
  };

  // Plain functions returning elements (not components), so React keeps the same inputs between
  // renders and typing never loses focus.
  const section = (add: TableKey, addLabel: string, children: ReactNode) => (
    <TableSection onAdd={() => setDraft((d) => addRow(d, add))} addLabel={addLabel}>{children}</TableSection>
  );
  const rowEnd = (table: TableKey, i: number) => (
    <RowEnd notes={rowNotes(table, i)} index={i} onDelete={() => remove(table, i)} />
  );

  return (
    <div className="editor">
      <div className="toolbar">
        <h2>Data</h2>
        <span className="muted">Edit the institution here, or upload a workbook. Changes are applied when you save.</span>
        <span className="spacer" />
        {dirty && <span className="status bad">Unsaved changes</span>}
        <button className="btn" onClick={() => { setDraft(institution); setNotice(null); }} disabled={!dirty}>Discard</button>
        <button className="btn primary" onClick={save} disabled={!dirty || saving || !check?.valid}
          title={dirty && check && !check.valid ? "Fix the errors below first" : undefined}>
          {saving ? "Saving…" : "Save changes"}
        </button>
        <a className="btn" href={api.workbookUrl(iid)} title="Download the saved data as an Excel workbook (a backup you can upload again)">
          Download Excel
        </a>
        <button className="btn danger" onClick={del}>Delete institution</button>
      </div>

      {error && <div className="alert" role="alert">{error}<button onClick={() => setError(null)}>×</button></div>}
      {notice && <div className="alert" role="status">{notice}<button onClick={() => setNotice(null)}>×</button></div>}

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
            <input value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })}
              className={inSection("settings").some((i) => i.field === "name" && i.level === "error") ? "bad" : ""} />
          </label>
          <label>Days <small className="muted">comma-separated, in order</small>
            <DaysField value={draft.calendar.day_names} onChange={(v) => setDraft({ ...draft, calendar: { ...draft.calendar, day_names: v } })} />
          </label>
          <label>Lectures per day
            <Num label="lectures per day" min={1} value={draft.calendar.lectures_per_day}
              issue={inSection("settings").find((i) => i.field === "lectures_per_day")}
              onChange={(v) => setDraft({ ...draft, calendar: { ...draft.calendar, lectures_per_day: v } })} />
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
                    onChange={(v) => setDraft({ ...draft, weights: { ...draft.weights, [w.key]: v } })} />
                </label>
              ))}
            </div>
          </fieldset>
          {inSection("settings").filter((i) => i.field !== "lectures_per_day").map((i, k) => (
            <p key={k} className={"setting-note " + i.level}>{i.message}</p>
          ))}
        </div>
      )}

      {tab === "rooms" && (
        section("rooms", "Add room", <>
          <table className="etable"><thead><tr><th>ID</th><th>Name</th><th>Seats</th><th>Kind</th><th /></tr></thead>
            <tbody>
              {draft.rooms.map((r, i) => (
                <tr key={i}>
                  <td><IdCell value={r.id} issue={cellIssue("rooms", i, "id")} onCommit={(v) => rename("rooms", r.id, v)} /></td>
                  <td><Text label="name" value={r.name} issue={cellIssue("rooms", i, "name")} onChange={(v) => patch("rooms", i, { name: v })} /></td>
                  <td><Num label="seats" min={1} value={r.capacity} issue={cellIssue("rooms", i, "capacity")} onChange={(v) => patch("rooms", i, { capacity: v })} /></td>
                  <td><Pick label="kind" value={r.kind} options={opts.kinds} onChange={(v) => patch("rooms", i, { kind: v as RoomKind })} /></td>
                  {rowEnd("rooms", i)}
                </tr>
              ))}
            </tbody></table>
          {draft.rooms.length === 0 && <p className="muted">No rooms yet. Add the classrooms, labs and halls sessions can use.</p>}
        </>)
      )}

      {tab === "faculty" && (
        section("faculty", "Add faculty member", <>
          <table className="etable"><thead><tr><th>ID</th><th>Name</th><th>Department</th><th title="Most lectures they can teach in a day">Max/day</th><th>Availability</th><th /></tr></thead>
            <tbody>
              {draft.faculty.map((f, i) => (
                <FacultyRows key={i} f={f} open={openFaculty === f.id} cal={draft.calendar}
                  onToggle={() => setOpenFaculty(openFaculty === f.id ? null : f.id)}
                  onChange={(nf: Faculty) => patch("faculty", i, nf)}
                  idCell={<IdCell value={f.id} issue={cellIssue("faculty", i, "id")} onCommit={(v) => { const ok = rename("faculty", f.id, v); if (ok && openFaculty === f.id) setOpenFaculty(v); return ok; }} />}
                  nameCell={<Text label="name" value={f.name} issue={cellIssue("faculty", i, "name")} onChange={(v) => patch("faculty", i, { name: v })} />}
                  deptCell={<Text label="department" value={f.department} issue={cellIssue("faculty", i, "department")} onChange={(v) => patch("faculty", i, { department: v })} />}
                  maxCell={<Num label="max per day" min={1} value={f.max_lectures_per_day} issue={cellIssue("faculty", i, "max_lectures_per_day")} onChange={(v) => patch("faculty", i, { max_lectures_per_day: v })} />}
                  end={rowEnd("faculty", i)} />
              ))}
            </tbody></table>
          {draft.faculty.length === 0 && <p className="muted">No faculty yet.</p>}
        </>)
      )}

      {tab === "batches" && (
        section("batches", "Add class", <>
          <p className="muted small">A class is a fixed group of students. For labs that split a class in two, add each group as its own class and set its <b>Parent</b>.</p>
          <table className="etable"><thead><tr><th>ID</th><th>Program</th><th>Level</th><th>Sem</th><th>Section</th><th>Department</th><th>Students</th><th>Parent</th><th /></tr></thead>
            <tbody>
              {draft.batches.map((b, i) => (
                <tr key={i}>
                  <td><IdCell value={b.id} issue={cellIssue("batches", i, "id")} onCommit={(v) => rename("batches", b.id, v)} /></td>
                  <td><Text label="program" value={b.program} issue={cellIssue("batches", i, "program")} onChange={(v) => patch("batches", i, { program: v })} /></td>
                  <td><Pick label="level" value={b.level} options={opts.levels} issue={cellIssue("batches", i, "level")} onChange={(v) => patch("batches", i, { level: v as "UG" | "PG" })} /></td>
                  <td><Num label="semester" min={1} value={b.semester} issue={cellIssue("batches", i, "semester")} onChange={(v) => patch("batches", i, { semester: v })} /></td>
                  <td><Text label="section" value={b.section} issue={cellIssue("batches", i, "section")} onChange={(v) => patch("batches", i, { section: v })} /></td>
                  <td><Text label="department" value={b.department} issue={cellIssue("batches", i, "department")} onChange={(v) => patch("batches", i, { department: v })} /></td>
                  <td><Num label="students" min={1} value={b.strength} issue={cellIssue("batches", i, "strength")} onChange={(v) => patch("batches", i, { strength: v })} /></td>
                  <td><Pick label="parent" allowBlank value={b.group_of ?? ""} options={opts.parents.filter((o) => o.value !== b.id)} issue={cellIssue("batches", i, "group_of")} onChange={(v) => patch("batches", i, { group_of: v || null })} /></td>
                  {rowEnd("batches", i)}
                </tr>
              ))}
            </tbody></table>
          {draft.batches.length === 0 && <p className="muted">No classes yet.</p>}
        </>)
      )}

      {tab === "courses" && (
        section("courses", "Add course", <>
          <table className="etable"><thead><tr><th>Code</th><th>Name</th><th>Department</th><th>Credits</th><th>Category</th><th>Needs</th><th /></tr></thead>
            <tbody>
              {draft.courses.map((c, i) => (
                <tr key={i}>
                  <td><IdCell value={c.code} issue={cellIssue("courses", i, "code")} onCommit={(v) => rename("courses", c.code, v)} /></td>
                  <td><Text label="name" value={c.name} issue={cellIssue("courses", i, "name")} onChange={(v) => patch("courses", i, { name: v })} /></td>
                  <td><Text label="department" value={c.department} issue={cellIssue("courses", i, "department")} onChange={(v) => patch("courses", i, { department: v })} /></td>
                  <td><Num label="credits" value={c.credits} issue={cellIssue("courses", i, "credits")} onChange={(v) => patch("courses", i, { credits: v })} /></td>
                  <td><Text label="category" value={c.category} issue={cellIssue("courses", i, "category")} onChange={(v) => patch("courses", i, { category: v })} /></td>
                  <td><Pick label="room kind" value={c.room_kind} options={opts.kinds} onChange={(v) => patch("courses", i, { room_kind: v as RoomKind })} /></td>
                  {rowEnd("courses", i)}
                </tr>
              ))}
            </tbody></table>
          {draft.courses.length === 0 && <p className="muted">No courses yet.</p>}
        </>)
      )}

      {tab === "offerings" && (
        section("offerings", "Add offering", <>
          <p className="muted small">An offering is one course taught by one person to one or more classes (list several for a shared elective).
            <b> Sessions</b> are the lecture counts per week: <code>1, 1, 1</code> is three one-lecture sessions, <code>2</code> is one two-lecture block (a lab).</p>
          <table className="etable"><thead><tr><th>ID</th><th>Course</th><th>Taught by</th><th>Classes</th><th>Sessions</th><th /></tr></thead>
            <tbody>
              {draft.offerings.map((o, i) => (
                <tr key={i}>
                  <td><IdCell value={o.id} issue={cellIssue("offerings", i, "id")} onCommit={(v) => rename("offerings", o.id, v)} /></td>
                  <td><Pick label="course" value={o.course_code} options={opts.courses} issue={cellIssue("offerings", i, "course_code")} onChange={(v) => patch("offerings", i, { course_code: v })} /></td>
                  <td><Pick label="faculty" value={o.faculty_id} options={opts.faculty} issue={cellIssue("offerings", i, "faculty_id")} onChange={(v) => patch("offerings", i, { faculty_id: v })} /></td>
                  <td><Chips label="add class" values={o.batch_ids} options={opts.batches} issue={cellIssue("offerings", i, "batch_ids")} onChange={(v) => patch("offerings", i, { batch_ids: v })} /></td>
                  <td><Sessions value={o.sessions} issue={cellIssue("offerings", i, "sessions")} onChange={(v) => patch("offerings", i, { sessions: v })} /></td>
                  {rowEnd("offerings", i)}
                </tr>
              ))}
            </tbody></table>
          {draft.offerings.length === 0 && <p className="muted">No offerings yet. Add courses, faculty and classes first, then say who teaches what to whom.</p>}
        </>)
      )}
    </div>
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

/** A faculty row plus, when open, a second row with the availability grid. */
function FacultyRows({ f, open, cal, onToggle, onChange, idCell, nameCell, deptCell, maxCell, end }: {
  f: Faculty; open: boolean; cal: Institution["calendar"]; onToggle: () => void; onChange: (f: Faculty) => void;
  idCell: ReactNode; nameCell: ReactNode; deptCell: ReactNode; maxCell: ReactNode; end: ReactNode;
}) {
  const summary = [
    f.unavailable.length ? `${f.unavailable.length} unavailable` : "",
    f.avoid.length ? `${f.avoid.length} to avoid` : "",
  ].filter(Boolean).join(" · ") || "always available";
  return (
    <>
      <tr>
        <td>{idCell}</td><td>{nameCell}</td><td>{deptCell}</td><td>{maxCell}</td>
        <td>
          <button type="button" className="btn small" aria-expanded={open} onClick={onToggle}>
            {open ? "Hide" : "Edit"} · {summary}
          </button>
        </td>
        {end}
      </tr>
      {open && (
        <tr className="subrow">
          <td colSpan={6}><SlotGrid cal={cal} faculty={f} onChange={onChange} /></td>
        </tr>
      )}
    </>
  );
}
