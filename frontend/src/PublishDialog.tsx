import { useCallback, useEffect, useState } from "react";
import * as api from "./api";
import type { Publication } from "./api";

const message = (e: unknown) => (e instanceof Error ? e.message : String(e));
type Kind = Publication["kinds"][number];

const KIND_TEXT: Record<Kind, { label: string; hint: string }> = {
  batch: { label: "Class timetables", hint: "for students: each class's week, with each subject's teacher and room" },
  faculty: { label: "Teacher timetables", hint: "each teacher's whole week, with every class they teach" },
  room: { label: "Room timetables", hint: "when each room is used" },
};

const EXPIRY: { label: string; days: number | null }[] = [
  { label: "never", days: null }, { label: "in 30 days", days: 30 }, { label: "in 90 days", days: 90 }, { label: "in a year", days: 365 },
];

const linkOf = (p: Publication) => `${window.location.origin}${p.path}`;
const describe = (kinds: Kind[]) => kinds.map((k) => KIND_TEXT[k].label.replace(" timetables", "").toLowerCase()).join(", ");

/** Make the timetable on screen readable by anyone with a link, no account needed; see, copy and withdraw the links. */
export function PublishDialog({ iid, name, jobId, onClose }: { iid: string; name: string; jobId: string; onClose: () => void }) {
  const [links, setLinks] = useState<Publication[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [label, setLabel] = useState("");
  const [kinds, setKinds] = useState<Kind[]>(["batch"]);
  const [days, setDays] = useState<number | null>(null);
  const [copied, setCopied] = useState<string | null>(null);
  const [termStart, setTermStart] = useState("");
  const [termEnd, setTermEnd] = useState("");
  const [notes, setNotes] = useState<Record<string, string>>({});
  const term = termStart && termEnd ? { term_start: termStart, term_end: termEnd } : {};

  const load = useCallback(() => api.listPublications(iid).then(setLinks).catch((e) => setError(message(e))), [iid]);
  useEffect(() => { void load(); }, [load]);
  const run = async (what: () => Promise<unknown>) => { setError(null); try { await what(); await load(); } catch (e) { setError(message(e)); } };

  async function copy(p: Publication) {
    try { await navigator.clipboard.writeText(linkOf(p)); setCopied(p.id); setTimeout(() => setCopied((c) => (c === p.id ? null : c)), 2000); }
    catch { setError("Couldn't copy automatically: select the address and copy it."); }
  }

  return (
    <div className="modal" role="dialog" aria-modal="true" aria-label={`Publish ${name}`}
      onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="modal-card wide">
        <h3>Publish “{name}”</h3>
        <p className="muted small">
          Publishing gives the timetable on screen a link. Anyone who has the link can look at it and download PDFs, with no account. Send it to
          students or staff; withdraw it at any time. Treat the link like a key: it is not listed anywhere, but it works for anyone who is given it.
        </p>
        {error && <div className="alert" role="alert">{error}<button onClick={() => setError(null)}>×</button></div>}

        {links.length > 0 && (
          <table className="issues userstable publinks">
            <thead><tr><th>Link</th><th>Shows</th><th /></tr></thead>
            <tbody>
              {links.map((p) => (
                <tr key={p.id} className={p.expired ? "off" : ""}>
                  <td>
                    <b>{p.label || "Timetable"}</b>{" "}
                    <small className="muted">
                      {new Date(p.created_at).toLocaleDateString()}
                      {p.expires_at ? ` · ${p.expired ? "expired" : "until"} ${new Date(p.expires_at).toLocaleDateString()}` : " · no expiry"}
                    </small>
                    <div className="termrow">
                      <label className="inline">Term from
                        <input type="date" aria-label={`term start of ${p.label || p.id}`} defaultValue={p.term_start ?? ""} key={"s" + p.id + p.term_start}
                          onBlur={(e) => { if (e.target.value !== (p.term_start ?? "") && (e.target.value && p.term_end || !e.target.value && !p.term_end)) void run(() => api.changePublication(iid, p.id, { term_start: e.target.value || null, term_end: p.term_end })); }} />
                      </label>
                      <label className="inline">to
                        <input type="date" aria-label={`term end of ${p.label || p.id}`} defaultValue={p.term_end ?? ""} key={"e" + p.id + p.term_end}
                          onBlur={(e) => { if (e.target.value !== (p.term_end ?? "") && (e.target.value && p.term_start || !e.target.value && !p.term_start)) void run(() => api.changePublication(iid, p.id, { term_start: p.term_start, term_end: e.target.value || null })); }} />
                      </label>
                      <small className="muted">
                        {!p.term_start ? "Set the term to offer students a calendar file." : p.times_set ? "Students can add it to their calendar." : "Set lecture times (Data → Settings) to offer a calendar file."}
                      </small>
                    </div>
                    <div className="linkrow">
                      <input readOnly value={linkOf(p)} aria-label={`link ${p.label || p.id}`} onFocus={(e) => e.target.select()} />
                      <button className="btn small" onClick={() => copy(p)}>{copied === p.id ? "Copied" : "Copy"}</button>
                    </div>
                  </td>
                  <td>
                    {describe(p.kinds)}
                    {p.job_id !== jobId && <div className="muted small">an older timetable</div>}
                  </td>
                  <td className="rowactions">
                    {p.job_id !== jobId && (
                      <>
                        <input placeholder="note for students (optional)" aria-label={`note for ${p.label || p.id}`} size={24} maxLength={200}
                          value={notes[p.id] ?? ""} onChange={(e) => setNotes({ ...notes, [p.id]: e.target.value })} />
                        <button className="btn small" onClick={() => run(async () => {
                          await api.changePublication(iid, p.id, { job_id: jobId, ...(notes[p.id]?.trim() ? { note: notes[p.id].trim() } : {}) });
                          setNotes({ ...notes, [p.id]: "" });
                        })} title="Make this link show the timetable on screen now. Students see what changed.">Show the current timetable</button>
                      </>
                    )}
                    <button className="btn small danger" onClick={() => { if (confirm("Withdraw this link? Anyone who has it will no longer be able to open it.")) void run(() => api.revokePublication(iid, p.id)); }}>
                      Withdraw
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}

        <h4>Make a new link</h4>
        <fieldset className="deptpick">
          <legend>What can people with the link look at?</legend>
          {(Object.keys(KIND_TEXT) as Kind[]).map((k) => (
            <label key={k} className="check block">
              <input type="checkbox" checked={kinds.includes(k)} aria-label={KIND_TEXT[k].label}
                onChange={(e) => setKinds(e.target.checked ? [...kinds, k] : kinds.filter((x) => x !== k))} />
              <span><b>{KIND_TEXT[k].label}</b> <span className="muted small">{KIND_TEXT[k].hint}</span></span>
            </label>
          ))}
        </fieldset>
        <div className="addform">
          <input placeholder="a name for this link, e.g. Semester 1 students" aria-label="link name" value={label} onChange={(e) => setLabel(e.target.value)} />
          <label className="inline">Expires
            <select aria-label="expires" value={String(days)} onChange={(e) => setDays(e.target.value === "null" ? null : Number(e.target.value))}>
              {EXPIRY.map((x) => <option key={x.label} value={String(x.days)}>{x.label}</option>)}
            </select>
          </label>
          <label className="inline">Term from
            <input type="date" aria-label="term start" value={termStart} onChange={(e) => setTermStart(e.target.value)} /></label>
          <label className="inline">to
            <input type="date" aria-label="term end" value={termEnd} onChange={(e) => setTermEnd(e.target.value)} /></label>
          <button className="btn primary" disabled={kinds.length === 0 || (!!termStart !== !!termEnd) || (!!termStart && termEnd < termStart)}
            onClick={() => run(async () => { await api.publish(iid, { job_id: jobId, label, kinds, expires_days: days, ...term }); setLabel(""); })}>
            Create link
          </button>
        </div>
        <p className="muted small">Give the first and last day of the term and students can add their timetable to a phone or computer calendar; it repeats every week of the term (holidays aren't known to the app), and updates when you point the link at a newer timetable.</p>
        <p className="muted small">The link shows the timetable as it is now, including changes you save to it later. It does not follow a newly generated timetable until you choose “Show the current timetable”.</p>

        <div className="modal-actions"><button className="btn" onClick={onClose}>Close</button></div>
      </div>
    </div>
  );
}
