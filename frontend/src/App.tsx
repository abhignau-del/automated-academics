import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import * as api from "./api";
import { Grid } from "./Grid";
import { changeRoom, moveSession, placementsFor, sessionKey } from "./timetable";
import type {
  ConflictReport, Institution, InstitutionSummary, Placement, UploadIssue, ViewKind,
} from "./types";

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
const sameList = (a: Placement[], b: Placement[]) => JSON.stringify(a) === JSON.stringify(b);

export default function App() {
  const [list, setList] = useState<InstitutionSummary[]>([]);
  const [iid, setIid] = useState<string | null>(null);
  const [inst, setInst] = useState<Institution | null>(null);

  const [jobId, setJobId] = useState<string | null>(null);
  const [placements, setPlacements] = useState<Placement[]>([]);
  const [saved, setSaved] = useState<Placement[]>([]);
  const [history, setHistory] = useState<Placement[][]>([]);
  const [report, setReport] = useState<ConflictReport | null>(null);

  const [solving, setSolving] = useState(false);
  const [timeLimit, setTimeLimit] = useState(30);
  const [error, setError] = useState<string | null>(null);
  const [issues, setIssues] = useState<UploadIssue[]>([]);

  const [kind, setKind] = useState<ViewKind>("batch");
  const [viewId, setViewId] = useState<string>("");
  const [selected, setSelected] = useState<string | null>(null);
  const [menuOpen, setMenuOpen] = useState(false);

  const menuRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!menuOpen) return;
    const close = (e: MouseEvent) => { if (!menuRef.current?.contains(e.target as Node)) setMenuOpen(false); };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [menuOpen]);

  const epoch = useRef(0); // bumps when the institution changes, to drop stale async results
  const dirty = !sameList(placements, saved);

  const fail = (e: unknown) => setError(e instanceof Error ? e.message : String(e));
  const refreshList = useCallback(() => api.listInstitutions().then(setList).catch(fail), []);

  useEffect(() => { refreshList(); }, [refreshList]);

  useEffect(() => {
    const onBeforeUnload = (e: BeforeUnloadEvent) => { if (dirty) e.preventDefault(); };
    window.addEventListener("beforeunload", onBeforeUnload);
    return () => window.removeEventListener("beforeunload", onBeforeUnload);
  }, [dirty]);

  // load the chosen institution and its latest saved timetable, if any
  useEffect(() => {
    const mine = ++epoch.current;
    setInst(null); setJobId(null); setPlacements([]); setSaved([]); setHistory([]);
    setReport(null); setSelected(null); setError(null);
    if (!iid) return;
    (async () => {
      const i = await api.getInstitution(iid);
      if (mine !== epoch.current) return;
      setInst(i);
      const job = await api.latestJob(iid);
      if (job && mine === epoch.current) await loadTimetable(job.id, mine);
    })().catch(fail);
  }, [iid]);

  async function loadTimetable(jid: string, mine = epoch.current) {
    const tt = await api.getTimetable(jid);
    if (mine !== epoch.current) return;
    setJobId(jid); setPlacements(tt.placements); setSaved(tt.placements); setHistory([]); setSelected(null);
  }

  // live clash checking: the server validator is the source of truth
  useEffect(() => {
    if (!iid || !jobId) { setReport(null); return; }
    let cancelled = false;
    const t = setTimeout(() => {
      api.validate(iid, { placements, status: "MANUAL", penalty: 0 })
        .then((r) => { if (!cancelled) setReport(r); })
        .catch((e) => { if (!cancelled) fail(e); });
    }, 120);
    return () => { cancelled = true; clearTimeout(t); };
  }, [iid, jobId, placements]);

  // default the view selector whenever the institution or view kind changes
  const options = useMemo(() => {
    if (!inst) return [];
    if (kind === "batch") return inst.batches.map((b) => ({ id: b.id, label: `${b.id} · ${b.program} Sem ${b.semester}${b.group_of ? " (group)" : ""}` }));
    if (kind === "faculty") return inst.faculty.map((f) => ({ id: f.id, label: `${f.name} (${f.department})` }));
    return inst.rooms.map((r) => ({ id: r.id, label: `${r.name} · ${r.capacity}` }));
  }, [inst, kind]);
  useEffect(() => {
    if (options.length && !options.some((o) => o.id === viewId)) setViewId(options[0].id);
  }, [options, viewId]);

  const items = useMemo(
    () => (inst && viewId ? placementsFor(inst, placements, kind, viewId) : []),
    [inst, placements, kind, viewId],
  );
  const conflictOfferings = useMemo(
    () => new Set(report?.details.flatMap((d) => d.offering_ids) ?? []),
    [report],
  );

  const commit = (next: Placement[]) => {
    setHistory((h) => [...h, placements]);
    setPlacements(next);
  };
  const onMove = (key: string, day: number, start: number) => {
    if (!inst) return;
    const next = moveSession(inst.calendar, placements, key, day, start);
    if (next) commit(next);
  };
  const undo = () => {
    const prev = history[history.length - 1];
    if (!prev) return;
    setHistory((h) => h.slice(0, -1));
    setPlacements(prev);
  };

  async function onUpload(file: File | undefined) {
    if (!file) return;
    setError(null); setIssues([]);
    try {
      const r = await api.uploadInstitution(file);
      await refreshList();
      setIid(r.id);
    } catch (e) {
      if (e instanceof api.ApiError && e.issues.length) { setIssues(e.issues); setError(e.message); }
      else fail(e);
    }
  }

  async function onSolve() {
    if (!iid) return;
    if (dirty && !confirm("You have unsaved edits. Generating a new timetable replaces them. Continue?")) return;
    const mine = epoch.current;
    setSolving(true); setError(null);
    try {
      const { job_id } = await api.startSolve(iid, timeLimit);
      for (;;) {
        await sleep(1000);
        if (mine !== epoch.current) return;
        const job = await api.getJob(job_id);
        if (job.status === "done") { await loadTimetable(job_id, mine); break; }
        if (job.status === "failed") { setError(`Could not generate a timetable: ${job.error}`); break; }
      }
    } catch (e) { fail(e); }
    finally { if (mine === epoch.current) setSolving(false); }
  }

  async function onSave() {
    if (!jobId) return;
    try {
      const r = await api.saveTimetable(jobId, { placements, status: "MANUAL", penalty: 0 });
      setSaved(placements); setReport(r);
    } catch (e) { fail(e); }
  }

  const sel = selected ? placements.find((p) => sessionKey(p) === selected) : undefined;
  const selOffering = sel && inst?.offerings.find((o) => o.id === sel.offering_id);

  return (
    <div className="app">
      <header>
        <h1>Automated Academics</h1>
        <span className="muted">Timetable generator for UG/PG programs</span>
      </header>

      <aside>
        <h2>Institutions</h2>
        <div className="row">
          <label className="btn primary">
            Upload Excel
            <input type="file" accept=".xlsx" hidden onChange={(e) => { onUpload(e.target.files?.[0]); e.target.value = ""; }} />
          </label>
          <a className="btn" href={api.templateUrl}>Template</a>
        </div>
        <ul className="inst-list">
          {list.map((i) => (
            <li key={i.id}>
              <button className={i.id === iid ? "active" : ""} onClick={() => setIid(i.id)}>
                {i.name}<small>{new Date(i.created_at).toLocaleDateString()}</small>
              </button>
            </li>
          ))}
        </ul>
        {!list.length && <p className="muted">No institutions yet. Download the template, fill it in, and upload it.</p>}
      </aside>

      <main>
        {error && <div className="alert" role="alert">{error}<button onClick={() => { setError(null); setIssues([]); }}>×</button></div>}
        {issues.length > 0 && (
          <table className="issues">
            <thead><tr><th>Sheet</th><th>Row</th><th>Problem</th></tr></thead>
            <tbody>{issues.map((i, n) => <tr key={n}><td>{i.sheet}</td><td>{i.row ?? "-"}</td><td>{i.message}</td></tr>)}</tbody>
          </table>
        )}

        {!inst && !error && <p className="muted">Select or upload an institution to begin.</p>}

        {inst && (
          <>
            <div className="toolbar">
              <h2>{inst.name}</h2>
              <span className="muted">{inst.batches.length} batches · {inst.faculty.length} faculty · {inst.rooms.length} rooms · {inst.offerings.reduce((n, o) => n + o.sessions.length, 0)} sessions/week</span>
              <span className="spacer" />
              <label>Time limit
                <select value={timeLimit} onChange={(e) => setTimeLimit(Number(e.target.value))} disabled={solving}>
                  {[10, 30, 60, 120, 300].map((s) => <option key={s} value={s}>{s}s</option>)}
                </select>
              </label>
              <button className="btn primary" onClick={onSolve} disabled={solving}>
                {solving ? "Generating…" : jobId ? "Regenerate" : "Generate timetable"}
              </button>
            </div>

            {jobId && (
              <>
                <div className="toolbar">
                  <div className="tabs" role="tablist">
                    {(["batch", "faculty", "room"] as ViewKind[]).map((k) => (
                      <button key={k} role="tab" aria-selected={kind === k} className={kind === k ? "active" : ""}
                        onClick={() => { setKind(k); setSelected(null); }}>
                        {k === "batch" ? "Class" : k === "faculty" ? "Faculty" : "Room"}
                      </button>
                    ))}
                  </div>
                  <select value={viewId} onChange={(e) => { setViewId(e.target.value); setSelected(null); }} aria-label="View">
                    {options.map((o) => <option key={o.id} value={o.id}>{o.label}</option>)}
                  </select>
                  <span className="spacer" />
                  <span className={"status " + (report ? (report.ok ? "ok" : "bad") : "")}>
                    {!report ? "Checking…" : report.ok ? "No clashes" : `${report.details.length} problem${report.details.length === 1 ? "" : "s"}`}
                  </span>
                  <button className="btn" onClick={undo} disabled={!history.length}>Undo</button>
                  <button className="btn" onClick={() => { setPlacements(saved); setHistory([]); }} disabled={!dirty}>Revert</button>
                  <button className="btn primary" onClick={onSave} disabled={!dirty}>{dirty ? "Save changes" : "Saved"}</button>
                  <div className="menu" ref={menuRef} onKeyDown={(e) => { if (e.key === "Escape") setMenuOpen(false); }}>
                    <button className="btn" aria-haspopup="menu" aria-expanded={menuOpen} onClick={() => setMenuOpen((o) => !o)}>
                      Export ▾
                    </button>
                    {menuOpen && (
                      <div className="menu-list" role="menu">
                        {dirty ? (
                          <p className="muted">Save your changes first. Exports use the saved timetable.</p>
                        ) : (
                          <>
                            {report && !report.ok && (
                              <p className="warn">⚠ This timetable still has {report.details.length} unresolved problem{report.details.length === 1 ? "" : "s"}.</p>
                            )}
                            <a role="menuitem" href={api.exportPdfUrl(jobId, kind, viewId)} onClick={() => setMenuOpen(false)}>
                              PDF: this {kind === "batch" ? "class" : kind}
                            </a>
                            <a role="menuitem" href={api.exportPdfUrl(jobId, "batch")} onClick={() => setMenuOpen(false)}>PDF: all classes</a>
                            <a role="menuitem" href={api.exportPdfUrl(jobId, "faculty")} onClick={() => setMenuOpen(false)}>PDF: all faculty</a>
                            <a role="menuitem" href={api.exportPdfUrl(jobId, "room")} onClick={() => setMenuOpen(false)}>PDF: all rooms</a>
                            <a role="menuitem" href={api.exportPdfUrl(jobId)} onClick={() => setMenuOpen(false)}>PDF: everything</a>
                            <hr />
                            <a role="menuitem" href={api.exportXlsxUrl(jobId)} onClick={() => setMenuOpen(false)}>Excel workbook (all sheets)</a>
                          </>
                        )}
                      </div>
                    )}
                  </div>
                </div>

                <p className="hint">Drag a session to a new slot. Clashes are checked as you go; sessions involved turn red.</p>

                <div className="work">
                  <Grid inst={inst} items={items} kind={kind} conflictOfferings={conflictOfferings}
                    selected={selected} onSelect={setSelected} onMove={onMove} />

                  <div className="side">
                    {sel && selOffering ? (
                      <section>
                        <h3>{selOffering.course_code}</h3>
                        <p className="muted">{inst.courses.find((c) => c.code === selOffering.course_code)?.name}</p>
                        <p>{inst.calendar.day_names[sel.day]}, P{sel.start + 1}{sel.length > 1 ? `–P${sel.start + sel.length}` : ""}</p>
                        <p>Faculty: {inst.faculty.find((f) => f.id === selOffering.faculty_id)?.name}</p>
                        <p>Batches: {selOffering.batch_ids.join(", ")}</p>
                        <label>Room
                          <select value={sel.room_id} onChange={(e) => commit(changeRoom(placements, sessionKey(sel), e.target.value))}>
                            {inst.rooms.map((r) => <option key={r.id} value={r.id}>{r.name} ({r.kind}, {r.capacity})</option>)}
                          </select>
                        </label>
                      </section>
                    ) : <p className="muted">Select a session to see details or change its room.</p>}

                    {report && !report.ok && (
                      <section>
                        <h3>Problems</h3>
                        <ul className="problems">
                          {report.details.slice(0, 40).map((d, i) => <li key={i}>{d.message}</li>)}
                        </ul>
                        {report.details.length > 40 && <p className="muted">…and {report.details.length - 40} more</p>}
                      </section>
                    )}
                  </div>
                </div>
              </>
            )}
          </>
        )}
      </main>
    </div>
  );
}
