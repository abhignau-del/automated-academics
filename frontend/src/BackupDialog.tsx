import { useState } from "react";
import * as api from "./api";
import type { BackupSummary } from "./api";

const message = (e: unknown) => (e instanceof Error ? e.message : String(e));
const plural = (n: number, one: string, many = one + "s") => `${n} ${n === 1 ? one : many}`;

const describe = (s: BackupSummary) =>
  `${plural(s.institutions, "institution")}, ${plural(s.timetables, "timetable")}, ${plural(s.people, "person", "people")}, ${plural(s.links, "published link")}` +
  (s.latest_timetable ? `; latest timetable made ${new Date(s.latest_timetable).toLocaleString()}` : "");

/** Download a copy of all the data, or replace everything with one. `shared`: the shared (sign-in) mode, where everyone signs in again afterwards. */
export function BackupDialog({ shared, onClose }: { shared: boolean; onClose: () => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [found, setFound] = useState<BackupSummary | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);

  async function choose(f: File | undefined) {
    setFile(f ?? null); setFound(null); setError(null); setDone(null);
    if (!f) return;
    setBusy(true);
    try { setFound((await api.restoreBackup(f, true)).summary); } catch (e) { setError(message(e)); } finally { setBusy(false); }
  }

  async function restore() {
    if (!file) return;
    if (!confirm("Replace ALL the data in the app with this backup? Everything made since the backup was taken will be gone from the app (a copy of the current data is saved first).")) return;
    setBusy(true); setError(null);
    try {
      const r = await api.restoreBackup(file, false);
      setDone(`Restored: ${describe(r.summary)}. The earlier data was kept as ${r.safety_copy} in the “backups” folder next to the database.`);
      setFile(null); setFound(null);
      setTimeout(() => window.location.reload(), 2500);
    } catch (e) { setError(message(e)); } finally { setBusy(false); }
  }

  return (
    <div className="modal" role="dialog" aria-modal="true" aria-label="Back up and restore"
      onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="modal-card wide">
        <h3>Back up and restore</h3>
        {error && <div className="alert" role="alert">{error}<button onClick={() => setError(null)}>×</button></div>}
        {done && <div className="alert note" role="status">{done}</div>}

        <h4>Back up</h4>
        <p className="muted small">
          Downloads one file with everything in the app: every institution and timetable, {shared ? "the accounts (with their scrambled passwords), " : ""}
          published links and the activity log. Keep it somewhere private, and take one before any big change.
        </p>
        <p><a className="btn primary" href={api.backupUrl} download>Download a backup</a></p>

        <h4>Restore</h4>
        <p className="muted small">
          Choose a backup file made here. Restoring <b>replaces everything</b> in the app with what the file holds; a copy of the current data is saved first, in a
          “backups” folder beside the database.{shared ? " Everyone will be signed out and signs in again with the accounts in the backup." : ""}
        </p>
        <label className="btn">
          Choose a backup file…
          <input type="file" accept=".db,application/octet-stream" hidden aria-label="backup file"
            onChange={(e) => { void choose(e.target.files?.[0]); e.target.value = ""; }} />
        </label>
        {file && <p className="small"><b>{file.name}</b>{busy && !found ? " (checking…)" : ""}</p>}
        {found && (
          <>
            <p role="status">This backup holds {describe(found)}.</p>
            <button className="btn danger" disabled={busy} onClick={() => void restore()}>Restore this backup</button>
          </>
        )}
        <div className="modal-actions"><button className="btn" onClick={onClose}>Close</button></div>
      </div>
    </div>
  );
}
