import { useEffect, useRef, useState } from "react";
import * as api from "./api";
import type { GridDraft, GridOptions } from "./api";

const DEFAULTS = { students: "60", seats: "70", doubles: true };

interface Props { onCreated: (id: string) => void; onClose: () => void }

const plural = (n: number, one: string, many = one + "s") => `${n} ${n === 1 ? one : many}`;

/**
 * Import an existing grid timetable (one block per class, days across, lectures down) as a new institution.
 * It is read into a draft first, with everything assumed or merged shown, and the existing timetable is
 * scored; "Create" saves the institution and keeps the existing timetable as its first timetable.
 */
export function GridImport({ onCreated, onClose }: Props) {
  const [file, setFile] = useState<File | null>(null);
  const [form, setForm] = useState(DEFAULTS);
  const [draft, setDraft] = useState<GridDraft | null>(null);
  const [skipped, setSkipped] = useState<Set<string>>(new Set()); // sheets the person unticked
  const [keep, setKeep] = useState(true);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const input = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const options = (sheets: string[] | null): GridOptions => ({
    sheets,
    default_students: Number(form.students) > 0 ? Number(form.students) : 60,
    seats: Number(form.seats) > 0 ? Number(form.seats) : 70,
    spare_rooms: null,
    merge_consecutive: form.doubles,
  });

  async function read(sheets: string[] | null) {
    if (!file) return;
    setBusy(true); setError(null);
    try {
      const d = await api.importGrid(file, options(sheets));
      setDraft(d);
      if (sheets === null) setSkipped(new Set());
      setName((n) => n || file.name.replace(/\.[^.]+$/, ""));
    } catch (e) { setDraft(null); setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  }

  async function create() {
    if (!draft) return;
    setBusy(true); setError(null);
    try {
      const r = await api.createInstitution({ ...draft.institution, name: name.trim() || "Imported timetable" });
      if (keep) await api.keepImportedTimetable(r.id, draft.placements);
      onCreated(r.id);
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); setBusy(false); }
  }

  const rep = draft?.report;
  const inst = draft?.institution;
  const errors = draft?.issues.filter((i) => i.level === "error") ?? [];
  const sheetNames = rep?.sheets.map((s) => s.name) ?? [];
  const selected = sheetNames.filter((n) => !skipped.has(n));

  return (
    <div className="modal" role="dialog" aria-modal="true" aria-label="Import an existing timetable"
      onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="modal-card wide">
        <h3>Import an existing timetable</h3>
        <p className="muted small">
          A sheet that already holds your timetable as grids: a block for each class, the days across the top and the lectures
          down the side, with the subject and teacher in each cell. It is read into a new institution, and your current timetable is
          kept so you can look at it, check it and compare it with a generated one.
        </p>

        <div className="importrow">
          <input ref={input} type="file" accept=".xlsx" hidden aria-label="timetable file"
            onChange={(e) => { setFile(e.target.files?.[0] ?? null); setDraft(null); }} />
          <button className="btn" onClick={() => input.current?.click()}>{file ? "Choose another file" : "Choose Excel file…"}</button>
          <span className="muted">{file ? file.name : "no file chosen"}</span>
        </div>

        <div className="importform">
          <label>Students in a class<input value={form.students} inputMode="numeric" onChange={(e) => { setForm({ ...form, students: e.target.value }); setDraft(null); }} /></label>
          <label>Seats in a room<input value={form.seats} inputMode="numeric" onChange={(e) => { setForm({ ...form, seats: e.target.value }); setDraft(null); }} /></label>
          <label className="check" title="Two lectures in a row of one subject and teacher become one double lecture, as they are in your grid">
            <input type="checkbox" checked={form.doubles} onChange={(e) => { setForm({ ...form, doubles: e.target.checked }); setDraft(null); }} />
            Keep lectures in a row together
          </label>
        </div>
        <p className="muted small">The grid doesn't say how many students a class has or how big a room is, so these are assumed.</p>

        <div className="modal-actions left">
          <button className="btn primary" disabled={!file || busy} onClick={() => read(null)}>
            {busy && !draft ? "Reading…" : draft ? "Read again" : "Read the timetable"}
          </button>
        </div>

        {error && <div className="alert" role="alert">{error}</div>}

        {draft && rep && inst && (
          <div className="importreport">
            <p>
              Found <b>{plural(rep.classes.length, "class", "classes")}</b>,
              <b> {plural(inst.faculty.length, "teacher")}</b>, <b>{plural(inst.courses.length, "subject")}</b> and
              <b> {plural(inst.offerings.reduce((n, o) => n + o.sessions.length, 0), "lecture")}</b> a week
              (<b>{inst.calendar.lectures_per_day}</b> lectures a day, {inst.calendar.day_names.join(", ")}).
            </p>

            <div className={"existing " + (rep.existing.clashes ? "bad" : "ok")}>
              <b>Your current timetable:</b>{" "}
              {rep.existing.clashes === 0 ? "no clashes" : plural(rep.existing.clashes, "clash", "clashes")}
              {", "}{plural(rep.existing.quality.faculty_gaps, "idle gap")} for teachers,{" "}
              {plural(rep.existing.quality.batch_gaps, "idle gap")} for classes,{" "}
              {plural(rep.existing.quality.repeat_course_day, "repeated subject")} on one day.
              {rep.existing.clash_examples.length > 0 && (
                <details><summary>Examples</summary><ul>{rep.existing.clash_examples.map((c, i) => <li key={i}>{c}</li>)}</ul></details>
              )}
            </div>

            <details open={rep.sheets.length > 1}>
              <summary><b>Sheets</b> {rep.sheets.length > 1 && <span className="muted small">untick any that are another week, an older version or a summary</span>}</summary>
              <ul className="sheetlist">
                {rep.sheets.map((s) => (
                  <li key={s.name}>
                    <label className="check">
                      <input type="checkbox" checked={!skipped.has(s.name)} disabled={s.blocks === 0}
                        onChange={(e) => { const n = new Set(skipped); if (e.target.checked) n.delete(s.name); else n.add(s.name); setSkipped(n); }} />
                      <b>{s.name}</b>
                    </label>{" "}
                    <span className="muted small">
                      {s.blocks === 0 ? "no timetable grids" : `${plural(s.blocks, "grid")}, ${s.used} used`}
                      {s.note ? ` · ${s.note}` : ""}
                    </span>
                  </li>
                ))}
              </ul>
              {skipped.size > 0 && (
                <button className="btn small" disabled={busy || selected.length === 0} onClick={() => read(selected)}>Read only the ticked sheets</button>
              )}
            </details>

            <details open>
              <summary><b>Classes</b></summary>
              <table className="issues">
                <thead><tr><th>Class</th><th>Room</th><th>Lectures a week</th></tr></thead>
                <tbody>{rep.classes.map((c) => (
                  <tr key={c.id}><td>{c.name}</td><td>{c.room ?? "—"}</td><td>{c.sessions} of {c.slots}</td></tr>
                ))}</tbody>
              </table>
            </details>

            {rep.problems.length > 0 && (
              <details open><summary className="warnline"><b>{plural(rep.problems.length, "thing")} worth a second look</b></summary>
                <ul>{rep.problems.map((p, i) => <li key={i}>{p}</li>)}</ul></details>
            )}
            {rep.merged.length > 0 && (
              <details><summary><b>{plural(rep.merged.length, "spelling group")} treated as one</b> <span className="muted small">check these</span></summary>
                <ul>{rep.merged.map((m, i) => <li key={i}>{m}</li>)}</ul></details>
            )}
            {rep.parallel.length > 0 && (
              <details><summary><b>{plural(rep.parallel.length, "parallel elective group")}</b></summary>
                <ul>{rep.parallel.map((m, i) => <li key={i}>{m}</li>)}</ul></details>
            )}
            <details><summary><b>What was assumed</b></summary>
              <ul>{rep.assumptions.map((a, i) => <li key={i}>{a}</li>)}</ul></details>
            {errors.length > 0 && (
              <details open><summary className="warnline"><b>{plural(errors.length, "problem")} to fix after creating</b></summary>
                <ul>{errors.slice(0, 6).map((e, i) => <li key={i}>{e.message}</li>)}</ul></details>
            )}

            <label className="namefield">Name this institution
              <input value={name} onChange={(e) => setName(e.target.value)} aria-label="institution name" /></label>
            <label className="check">
              <input type="checkbox" checked={keep} onChange={(e) => setKeep(e.target.checked)} />
              Keep my current timetable so I can see it and compare it
            </label>
          </div>
        )}

        <div className="modal-actions">
          <button className="btn" onClick={onClose}>Cancel</button>
          <button className="btn primary" disabled={!draft || busy} onClick={create}>Create institution</button>
        </div>
      </div>
    </div>
  );
}
