import { useCallback, useEffect, useState } from "react";
import * as api from "./api";
import type { AuditEntry, AuditFilter } from "./api";

const message = (e: unknown) => (e instanceof Error ? e.message : String(e));

const AREAS: { label: string; action: string }[] = [
  { label: "Everything", action: "" }, { label: "Signing in", action: "auth" }, { label: "People", action: "user" },
  { label: "Institution data", action: "institution" }, { label: "Timetables", action: "timetable" },
  { label: "Share links", action: "publication" }, { label: "Who has access", action: "member" }, { label: "Refused", action: "access" },
];

/** Who did what, newest first. `iid` set: this institution's history (owner); omitted: everything (administrator). */
export function AuditDialog({ iid, name, onClose }: { iid?: string; name?: string; onClose: () => void }) {
  const [rows, setRows] = useState<AuditEntry[]>([]);
  const [more, setMore] = useState(false);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [area, setArea] = useState("");
  const [q, setQ] = useState("");
  const [user, setUser] = useState("");
  const [since, setSince] = useState("");
  const [integrity, setIntegrity] = useState<string | null>(null);
  const [keepDays, setKeepDays] = useState(365);
  const [tidied, setTidied] = useState<string | null>(null);

  const filter: AuditFilter = { action: area, q, user, since: since ? new Date(since).toISOString() : "" };
  const key = JSON.stringify(filter);

  const load = useCallback(async (append: boolean, before?: number) => {
    setBusy(true); setError(null);
    try {
      const got = await api.listAudit(JSON.parse(key), before, iid);
      setRows((r) => (append ? [...r, ...got] : got));
      setMore(got.length === 50);
    } catch (e) { setError(message(e)); } finally { setBusy(false); }
  }, [key, iid]);
  useEffect(() => { const t = setTimeout(() => void load(false), 250); return () => clearTimeout(t); }, [load]);

  async function verify() {
    setIntegrity("Checking…");
    try {
      const v = await api.verifyAudit();
      const earlier = v.pruned ? ` Older entries (${v.pruned.removed} in all, up to ${new Date(v.pruned.at).toLocaleDateString()}) were deleted on purpose.` : "";
      setIntegrity((v.ok ? `Intact: all ${v.checked} entries check out.`
        : `Something was altered or removed at or near entry ${v.first_bad} (${v.checked} entries before it were fine).`) + earlier);
    } catch (e) { setIntegrity(message(e)); }
  }

  return (
    <div className="modal" role="dialog" aria-modal="true" aria-label="Activity log"
      onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="modal-card wide">
        <h3>{name ? `History of “${name}”` : "Activity log"}</h3>
        <p className="muted small">
          Who signed in, changed data, generated or edited timetables, shared or published, newest first. Passwords and link addresses are never recorded.
          Entries can't be edited{iid ? "" : "; an administrator can delete old ones (below)"}.
        </p>
        {error && <div className="alert" role="alert">{error}<button onClick={() => setError(null)}>×</button></div>}
        <div className="toolbar">
          <label>Show <select value={area} onChange={(e) => setArea(e.target.value)} aria-label="Kind of activity">
            {AREAS.map((a) => <option key={a.action} value={a.action}>{a.label}</option>)}</select></label>
          <label>Person <input value={user} onChange={(e) => setUser(e.target.value.trim().toLowerCase())} placeholder="username" size={12} /></label>
          <label>Since <input type="date" value={since} onChange={(e) => setSince(e.target.value)} /></label>
          <label>Search <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="any word" size={14} /></label>
          <span className="spacer" />
          <a className="btn" href={api.auditCsvUrl(filter, iid)} download>Download CSV</a>
          {!iid && <button className="btn" onClick={() => void verify()} title="Check that no entry has been altered or removed">Verify integrity</button>}
        </div>
        {integrity && <div className="alert note" role="status">{integrity}</div>}
        <table className="issues userstable auditlog">
          <thead><tr><th>When</th><th>What happened</th>{!iid && <th>Where</th>}</tr></thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.seq}>
                <td className="nowrap"><span title={r.at}>{new Date(r.at).toLocaleString()}</span></td>
                <td>{r.summary}{r.ip && <small className="muted"> · from {r.ip}</small>}</td>
                {!iid && <td>{r.institution ?? ""}</td>}
              </tr>
            ))}
            {!busy && rows.length === 0 && <tr><td colSpan={3} className="muted">Nothing recorded yet{area || q || user || since ? " for these filters" : ""}.</td></tr>}
          </tbody>
        </table>
        {more && <button className="btn" disabled={busy} onClick={() => void load(true, rows[rows.length - 1]?.seq)}>Load more</button>}
        {!iid && (
          <div className="termrow">
            <label className="inline">Delete entries older than
              <input type="number" min={30} max={3650} value={keepDays} aria-label="days to keep" onChange={(e) => setKeepDays(Number(e.target.value))} style={{ width: "5em" }} /> days
            </label>
            <button className="btn small danger" disabled={!(keepDays >= 30 && keepDays <= 3650)}
              onClick={() => {
                if (!confirm(`Delete every activity-log entry older than ${keepDays} days? This cannot be undone. The rest of the log stays verifiable.`)) return;
                setTidied(null);
                void api.pruneAudit(keepDays).then((r) => { setTidied(`Deleted ${r.removed} entr${r.removed === 1 ? "y" : "ies"}.`); void load(false); }).catch((e) => setError(message(e)));
              }}>Delete older entries</button>
            {tidied && <span role="status" className="muted small">{tidied}</span>}
          </div>
        )}
        <div className="modal-actions"><button className="btn" onClick={onClose}>Close</button></div>
      </div>
    </div>
  );
}
