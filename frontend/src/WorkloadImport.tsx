import { useEffect, useRef, useState } from "react";
import * as api from "./api";
import type { WorkloadDraft, WorkloadOptions } from "./api";

const DEFAULTS = {
  days: "Mon, Tue, Wed, Thu, Fri, Sat", lectures: "4", breaks: "2", classrooms: "", seats: "", labs: "0",
  joint: "0", students: "60",
};

const num = (s: string): number | null => (s.trim() === "" || Number.isNaN(Number(s)) ? null : Number(s));

function toOptions(f: typeof DEFAULTS): WorkloadOptions {
  return {
    days: f.days.split(",").map((d) => d.trim()).filter(Boolean),
    lectures_per_day: num(f.lectures) ?? 4,
    break_after: f.breaks.split(",").map((b) => Number(b.trim())).filter((b) => Number.isInteger(b) && b > 0),
    classrooms: num(f.classrooms), seats: num(f.seats), labs: num(f.labs) ?? 0, lab_seats: null,
    joint_max_students: num(f.joint) ?? 0, default_students: num(f.students) ?? 60,
  };
}

interface Props { onCreated: (id: string) => void; onClose: () => void }

/**
 * Import a flat workload list (subject, teacher, programme, hours...) as a new institution. The list is
 * read into a draft first and everything assumed is shown, so nothing is silent; "Create" saves it.
 */
export function WorkloadImport({ onCreated, onClose }: Props) {
  const [file, setFile] = useState<File | null>(null);
  const [form, setForm] = useState(DEFAULTS);
  const [draft, setDraft] = useState<WorkloadDraft | null>(null);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const input = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const set = (k: keyof typeof DEFAULTS) => (e: React.ChangeEvent<HTMLInputElement>) => { setForm({ ...form, [k]: e.target.value }); setDraft(null); };

  async function read() {
    if (!file) return;
    setBusy(true); setError(null);
    try {
      const d = await api.importWorkload(file, toOptions(form));
      setDraft(d);
      setName(name || file.name.replace(/\.[^.]+$/, ""));
    } catch (e) { setDraft(null); setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  }

  async function create() {
    if (!draft) return;
    setBusy(true); setError(null);
    try {
      const r = await api.createInstitution({ ...draft.institution, name: name.trim() || "Imported workload" });
      onCreated(r.id);
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); setBusy(false); }
  }

  const inst = draft?.institution;
  const errors = draft?.issues.filter((i) => i.level === "error") ?? [];

  return (
    <div className="modal" role="dialog" aria-modal="true" aria-label="Import a workload list"
      onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="modal-card wide">
        <h3>Import a workload list</h3>
        <p className="muted small">
          A sheet with one row per subject: the subject, who teaches it, the programme, the hours a week and (if you have it) the
          number of students. Rooms, availability and which subjects are labs aren't in such a list, so they are assumed
          and shown below; you can change all of it afterwards in the Data tab.
        </p>

        <div className="importrow">
          <input ref={input} type="file" accept=".xlsx" hidden aria-label="workload file"
            onChange={(e) => { setFile(e.target.files?.[0] ?? null); setDraft(null); }} />
          <button className="btn" onClick={() => input.current?.click()}>{file ? "Choose another file" : "Choose Excel file…"}</button>
          <span className="muted">{file ? file.name : "no file chosen"}</span>
        </div>

        <div className="importform">
          <label>Days<input value={form.days} onChange={set("days")} /></label>
          <label>Lectures per day<input value={form.lectures} onChange={set("lectures")} inputMode="numeric" /></label>
          <label>Break after lecture<input value={form.breaks} onChange={set("breaks")} placeholder="e.g. 2" /></label>
          <label>Classrooms<input value={form.classrooms} onChange={set("classrooms")} placeholder="one per class" inputMode="numeric" /></label>
          <label>Seats each<input value={form.seats} onChange={set("seats")} placeholder="auto" inputMode="numeric" /></label>
          <label>Labs<input value={form.labs} onChange={set("labs")} inputMode="numeric" /></label>
          <label title="Teach a subject to several programmes together when they have the same teacher and total this many students or fewer. 0 = never; you can also decide later, as the Data tab suggests joint classes.">
            Combine classes up to<input value={form.joint} onChange={set("joint")} inputMode="numeric" /></label>
          <label>Students if not given<input value={form.students} onChange={set("students")} inputMode="numeric" /></label>
        </div>

        <div className="modal-actions left">
          <button className="btn primary" disabled={!file || busy} onClick={read}>{busy && !draft ? "Reading…" : draft ? "Read again" : "Read the list"}</button>
        </div>

        {error && <div className="alert" role="alert">{error}</div>}

        {draft && inst && (
          <div className="importreport">
            <p>
              Read <b>{draft.report.rows_used}</b> of {draft.report.rows_read} rows into <b>{inst.batches.length}</b> classes,
              <b> {inst.faculty.length}</b> teachers, <b>{inst.courses.length}</b> courses and <b>{inst.offerings.length}</b> offerings
              (<b>{inst.offerings.reduce((n, o) => n + o.sessions.length, 0)}</b> lectures a week).
            </p>
            {draft.report.joint.length > 0 && (
              <details open><summary><b>{draft.report.joint.length} subjects taught to several programmes together</b></summary>
                <ul>{draft.report.joint.map((j, i) => <li key={i}>{j}</li>)}</ul></details>
            )}
            {draft.report.problems.length > 0 && (
              <details open><summary className="warnline"><b>{draft.report.problems.length} things in your list worth a second look</b></summary>
                <ul>{draft.report.problems.map((p, i) => <li key={i}>{p}</li>)}</ul></details>
            )}
            <details><summary><b>What was assumed</b></summary>
              <ul>{draft.report.assumptions.map((a, i) => <li key={i}>{a}</li>)}</ul></details>
            {errors.length > 0 && (
              <details open><summary className="warnline"><b>{errors.length} problem{errors.length === 1 ? "" : "s"} to fix after creating</b></summary>
                <ul>{errors.slice(0, 6).map((e, i) => <li key={i}>{e.message}</li>)}</ul></details>
            )}
            <label className="namefield">Name this institution
              <input value={name} onChange={(e) => setName(e.target.value)} aria-label="institution name" /></label>
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
