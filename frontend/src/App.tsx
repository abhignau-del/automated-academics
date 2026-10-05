import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import * as api from "./api";
import { DataEditor } from "./DataEditor";
import { Grid } from "./Grid";
import { WorkloadImport } from "./WorkloadImport";
import { lock, pinnedKeys, unlock } from "./pins";
import { changeRoom, moveSession, placementsFor, sessionKey } from "./timetable";
import type {
  ConflictReport, Institution, InstitutionSummary, Pin, Placement, Quality, UploadIssue, ViewKind,
} from "./types";


const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
const sameList = (a: Placement[], b: Placement[]) => JSON.stringify(a) === JSON.stringify(b);

const GOALS: { key: keyof Quality; label: string; hint: string }[] = [
  { key: "batch_gaps", label: "Class idle gaps", hint: "Free lectures between a class's first and last lecture of a day" },
  { key: "faculty_gaps", label: "Faculty idle gaps", hint: "Free lectures between a teacher's first and last lecture of a day" },
  { key: "peak_day_load", label: "Busiest-day load", hint: "Lectures on each class's busiest day, added up. Lower means a more even week" },
  { key: "repeat_course_day", label: "Repeated courses", hint: "Extra sessions of the same course on one day" },
  { key: "avoid_slot", label: "Avoided slots used", hint: "Lectures placed where a teacher asked not to teach" },
];

/** How good the timetable is on the soft goals. With unsaved edits, shows the change since the saved version. */
function QualityPanel({ quality, saved }: { quality: Quality; saved: Quality | null }) {
  return (
    <section>
      <h3>Quality</h3>
      <p className="muted small">Lower is better. 0 is ideal.</p>
      <table className="quality">
        <tbody>
          {GOALS.map(({ key, label, hint }) => {
            const delta = saved ? quality[key] - saved[key] : 0;
            return (
              <tr key={key} title={hint}>
                <td>{label}</td>
                <td className="num">{quality[key]}</td>
                <td className={"num delta " + (delta < 0 ? "better" : delta > 0 ? "worse" : "")}>
                  {delta ? (delta > 0 ? `+${delta}` : `${delta}`) : ""}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </section>
  );
}

export default function App() {
  const [list, setList] = useState<InstitutionSummary[]>([]);
  const [iid, setIid] = useState<string | null>(null);
  const [inst, setInst] = useState<Institution | null>(null);

  const [jobId, setJobId] = useState<string | null>(null);
  const [placements, setPlacements] = useState<Placement[]>([]);
  const [saved, setSaved] = useState<Placement[]>([]);
  const [history, setHistory] = useState<Placement[][]>([]);
  const [report, setReport] = useState<ConflictReport | null>(null);
  const [savedQuality, setSavedQuality] = useState<Quality | null>(null); // quality of the saved timetable

  const [solving, setSolving] = useState(false);
  const [timeLimit, setTimeLimit] = useState(30);
  const [error, setError] = useState<string | null>(null);
  const [issues, setIssues] = useState<UploadIssue[]>([]);

  const [kind, setKind] = useState<ViewKind>("batch");
  const [viewId, setViewId] = useState<string>("");
  const [selected, setSelected] = useState<string | null>(null);
  const [menuOpen, setMenuOpen] = useState(false);

  const [view, setView] = useState<"timetable" | "data">("timetable");
  const [jobStale, setJobStale] = useState(false); // data was edited after the shown timetable was made
  const [dataDirty, setDataDirty] = useState(false); // unsaved edits in the Data tab
  const [dataErrors, setDataErrors] = useState(0); // problems in the saved data that block generating
  const [newMenuOpen, setNewMenuOpen] = useState(false);
  const [importing, setImporting] = useState(false);

  const menuRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!menuOpen) return;
    const close = (e: MouseEvent) => { if (!menuRef.current?.contains(e.target as Node)) setMenuOpen(false); };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [menuOpen]);

  const savedRef = useRef<Placement[]>([]); // latest `saved`, readable from async callbacks
  useEffect(() => { savedRef.current = saved; }, [saved]);

  const epoch = useRef(0); // bumps when the institution changes, to drop stale async results
  const dirty = !sameList(placements, saved);

  const fail = (e: unknown) => setError(e instanceof Error ? e.message : String(e));
  const refreshList = useCallback(() => api.listInstitutions().then(setList).catch(fail), []);

  useEffect(() => { refreshList(); }, [refreshList]);

  useEffect(() => {
    const onBeforeUnload = (e: BeforeUnloadEvent) => { if (dirty || dataDirty) e.preventDefault(); };
    window.addEventListener("beforeunload", onBeforeUnload);
    return () => window.removeEventListener("beforeunload", onBeforeUnload);
  }, [dirty, dataDirty]);

  /** Switching institutions would throw away unsaved data edits, so ask first. */
  const selectInstitution = (id: string | null) => {
    if (dataDirty && !confirm("You have unsaved changes to this institution's data. Discard them?")) return false;
    setDataDirty(false);
    setIid(id);
    return true;
  };

  const countErrors = (i: Institution) =>
    api.checkInstitution(i).then((r) => setDataErrors(r.issues.filter((x) => x.level === "error").length)).catch(() => setDataErrors(0));

  /** After the Data tab saves: reload the data and ask the server whether the timetable is now out of date. */
  async function reloadInstitution() {
    if (!iid) return;
    const fresh = await api.getInstitution(iid);
    setInst(fresh);
    countErrors(fresh);
    refreshList();
    if (jobId) setJobStale(!!(await api.getJob(jobId)).stale);
  }

  /** A workload list was imported and created: show it, in the Data tab so it can be reviewed and completed. */
  async function afterImport(id: string) {
    setImporting(false);
    if (dataDirty && !confirm("You have unsaved changes to this institution's data. Discard them?")) return;
    await refreshList();
    setDataDirty(false);
    setIid(id);
    setView("data");
  }

  async function createNew(kind: "blank" | "sample") {
    setNewMenuOpen(false);
    if (dataDirty && !confirm("You have unsaved changes to this institution's data. Discard them?")) return;
    try {
      const created = await api.createInstitution(await api.starterInstitution(kind));
      await refreshList();
      setDataDirty(false);
      setIid(created.id);
      setView("data");
    } catch (e) { fail(e); }
  }

  // load the chosen institution and its latest saved timetable, if any
  useEffect(() => {
    const mine = ++epoch.current;
    setInst(null); setJobId(null); setPlacements([]); setSaved([]); setHistory([]);
    setReport(null); setSelected(null); setError(null); setJobStale(false); setDataErrors(0);
    if (!iid) return;
    (async () => {
      const i = await api.getInstitution(iid);
      if (mine !== epoch.current) return;
      setInst(i);
      countErrors(i);
      // a brand-new, empty institution has nothing to schedule yet: start in the Data tab
      if (i.offerings.length === 0) setView("data");
      const job = await api.latestJob(iid);
      if (job && mine === epoch.current) await loadTimetable(job.id, mine, !!job.stale);
    })().catch(fail);
  }, [iid]);

  async function loadTimetable(jid: string, mine = epoch.current, stale = false) {
    const tt = await api.getTimetable(jid);
    if (mine !== epoch.current) return;
    setJobId(jid); setPlacements(tt.placements); setSaved(tt.placements); setHistory([]); setSelected(null);
    setJobStale(stale);
  }

  // live clash checking: the server validator is the source of truth
  useEffect(() => {
    if (!iid || !jobId) { setReport(null); return; }
    let cancelled = false;
    const t = setTimeout(() => {
      api.validate(iid, { placements, status: "MANUAL", penalty: 0, breakdown: {} })
        .then((r) => {
          if (cancelled) return;
          setReport(r);
          if (sameList(placements, savedRef.current)) setSavedQuality(r.quality); // this IS the saved one
        })
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
  const pinned = useMemo(() => pinnedKeys(inst?.pins ?? []), [inst]);
  const onMove = (key: string, day: number, start: number) => {
    if (!inst || pinned.has(key)) return;
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
      if (dataDirty && !confirm("You have unsaved changes to this institution's data. Discard them?")) return;
      const r = await api.uploadInstitution(file);
      await refreshList();
      setDataDirty(false);
      setIid(r.id);
    } catch (e) {
      if (e instanceof api.ApiError && e.issues.length) { setIssues(e.issues); setError(e.message); }
      else fail(e);
    }
  }

  /** Pins are saved straight away, apart from the timetable's own edits; they steer the next Generate. */
  async function savePins(pins: Pin[]) {
    if (!inst || !iid) return;
    try {
      await api.updateInstitution(iid, { ...inst, pins });
      setInst({ ...inst, pins });
    } catch (e) { fail(e); }
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
      const r = await api.saveTimetable(jobId, { placements, status: "MANUAL", penalty: 0, breakdown: {} });
      setSaved(placements); setReport(r); setSavedQuality(r.quality);
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
          <div className="menu">
            <button className="btn primary" aria-haspopup="menu" aria-expanded={newMenuOpen} onClick={() => setNewMenuOpen((o) => !o)}>
              + New ▾
            </button>
            {newMenuOpen && (
              <div className="menu-list left" role="menu">
                <button role="menuitem" onClick={() => { setNewMenuOpen(false); setImporting(true); }}>Import a workload list…</button>
                <button role="menuitem" onClick={() => createNew("blank")}>Blank institution</button>
                <button role="menuitem" onClick={() => createNew("sample")}>Copy of the sample</button>
              </div>
            )}
          </div>
          <label className="btn">
            Upload Excel
            <input type="file" accept=".xlsx" hidden onChange={(e) => { onUpload(e.target.files?.[0]); e.target.value = ""; }} />
          </label>
        </div>
        <a className="muted small" href={api.templateUrl}>Download the Excel template</a>
        <ul className="inst-list">
          {list.map((i) => (
            <li key={i.id}>
              <button className={i.id === iid ? "active" : ""} onClick={() => selectInstitution(i.id)}>
                {i.name}<small>{new Date(i.created_at).toLocaleDateString()}</small>
              </button>
            </li>
          ))}
        </ul>
        {!list.length && <p className="muted">No institutions yet. Start a blank one and enter your data here, or upload an Excel workbook.</p>}
      </aside>

      {importing && <WorkloadImport onCreated={afterImport} onClose={() => setImporting(false)} />}

      <main>
        {error && <div className="alert" role="alert">{error}<button onClick={() => { setError(null); setIssues([]); }}>×</button></div>}
        {issues.length > 0 && (
          <table className="issues">
            <thead><tr><th>Sheet</th><th>Row</th><th>Problem</th></tr></thead>
            <tbody>{issues.map((i, n) => <tr key={n}><td>{i.sheet}</td><td>{i.row ?? "-"}</td><td>{i.message}</td></tr>)}</tbody>
          </table>
        )}

        {!inst && !error && <p className="muted">Select an institution, start a new one, or upload a workbook to begin.</p>}

        {inst && iid && (
          <>
            <div className="viewtabs" role="tablist">
              <button role="tab" aria-selected={view === "timetable"} className={view === "timetable" ? "active" : ""} onClick={() => setView("timetable")}>
                Timetable
              </button>
              <button role="tab" aria-selected={view === "data"} className={view === "data" ? "active" : ""} onClick={() => setView("data")}>
                Data
                {(dataErrors > 0 || dataDirty) && (
                  <span className={"badge" + (dataErrors > 0 ? "" : " soft")}>{dataErrors > 0 ? dataErrors : "•"}</span>
                )}
              </button>
            </div>

            {/* kept mounted (just hidden) so unsaved edits survive switching tabs */}
            <div hidden={view !== "data"}>
              <DataEditor iid={iid} institution={inst} onSaved={reloadInstitution} onDirtyChange={setDataDirty}
                onDeleted={() => { setDataDirty(false); setView("timetable"); setIid(null); refreshList(); }} />
            </div>
          </>
        )}

        {inst && view === "timetable" && (
          <>
            {jobId && jobStale && (
              <div className="alert stale" role="status">
                The data was changed after this timetable was generated, so it may no longer match.
                Regenerate it to apply your changes.
              </div>
            )}
            <div className="toolbar">
              <h2>{inst.name}</h2>
              <span className="muted">{inst.batches.length} batches · {inst.faculty.length} faculty · {inst.rooms.length} rooms · {inst.offerings.reduce((n, o) => n + o.sessions.length, 0)} sessions/week</span>
              <span className="spacer" />
              <label>Time limit
                <select value={timeLimit} onChange={(e) => setTimeLimit(Number(e.target.value))} disabled={solving}>
                  {[10, 30, 60, 120, 300, 600, 900].map((s) => (
                    <option key={s} value={s}>{s >= 60 ? `${s / 60} min` : `${s}s`}</option>
                  ))}
                </select>
              </label>
              <button className="btn primary" onClick={onSolve}
                disabled={solving || dataErrors > 0 || inst.offerings.length === 0 || dataDirty}
                title={dataErrors > 0 ? `Fix the ${dataErrors} problem${dataErrors === 1 ? "" : "s"} in the Data tab first`
                  : inst.offerings.length === 0 ? "Add some offerings in the Data tab first"
                  : dataDirty ? "Save or discard your changes in the Data tab first" : undefined}>
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
                        {jobStale ? (
                          <p className="muted">The data changed after this timetable was made. Regenerate it before exporting.</p>
                        ) : dirty ? (
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

                <p className="hint">Drag a session to a new slot. Clashes are checked as you go; sessions involved turn red.
                  Lock sessions you are happy with and they stay put when you regenerate.
                  {inst.pins.length > 0 && ` ${inst.pins.length} locked.`}
                  {items.length > 0 && (
                    <>
                      {" "}<button className="linkish" onClick={() => savePins(lock(inst.pins, items))} disabled={dataDirty}>Lock all shown</button>
                      {items.some((p) => pinned.has(sessionKey(p))) && (
                        <>{" · "}<button className="linkish" onClick={() => savePins(unlock(inst.pins, new Set(items.map(sessionKey))))} disabled={dataDirty}>Unlock all shown</button></>
                      )}
                    </>
                  )}
                </p>

                <div className="work">
                  <Grid inst={inst} items={items} kind={kind} conflictOfferings={conflictOfferings}
                    pinned={pinned} selected={selected} onSelect={setSelected} onMove={onMove} />

                  <div className="side">
                    {sel && selOffering ? (
                      <section>
                        <h3>{selOffering.course_code}</h3>
                        <p className="muted">{inst.courses.find((c) => c.code === selOffering.course_code)?.name}</p>
                        <p>{inst.calendar.day_names[sel.day]}, L{sel.start + 1}{sel.length > 1 ? `–L${sel.start + sel.length}` : ""}</p>
                        <p>Faculty: {inst.faculty.find((f) => f.id === selOffering.faculty_id)?.name}</p>
                        <p>Batches: {selOffering.batch_ids.join(", ")}</p>
                        {pinned.has(sessionKey(sel)) ? (
                          <button className="btn" onClick={() => savePins(unlock(inst.pins, new Set([sessionKey(sel)])))} disabled={dataDirty}>
                            🔓 Unlock this session
                          </button>
                        ) : (
                          <button className="btn" onClick={() => savePins(lock(inst.pins, [sel]))} disabled={dataDirty}
                            title="Keep it exactly here, in this room, when you regenerate">
                            🔒 Lock in place
                          </button>
                        )}
                        <label>Room
                          <select disabled={pinned.has(sessionKey(sel))} value={sel.room_id} onChange={(e) => commit(changeRoom(placements, sessionKey(sel), e.target.value))}>
                            {inst.rooms.map((r) => <option key={r.id} value={r.id}>{r.name} ({r.kind}, {r.capacity})</option>)}
                          </select>
                        </label>
                      </section>
                    ) : <p className="muted">Select a session to see details or change its room.</p>}

                    {report && <QualityPanel quality={report.quality} saved={dirty ? savedQuality : null} />}

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
