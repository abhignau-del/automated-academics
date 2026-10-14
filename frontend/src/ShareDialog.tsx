import { useCallback, useEffect, useState, type FormEvent } from "react";
import * as api from "./api";
import { ROLE_LABEL } from "./access";
import type { Member } from "./types";

const message = (e: unknown) => (e instanceof Error ? e.message : String(e));

/** Who can see and change one institution. Only an owner (or an administrator) gets here. */
export function ShareDialog({ iid, name, departments, onClose }: { iid: string; name: string; departments: string[]; onClose: () => void }) {
  const [members, setMembers] = useState<Member[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [username, setUsername] = useState("");
  const [role, setRole] = useState<Member["role"]>("viewer");
  const [limit, setLimit] = useState<string[]>([]); // departments for a new editor; empty = the whole institution

  const load = useCallback(() => api.listMembers(iid).then(setMembers).catch((e) => setError(message(e))), [iid]);
  useEffect(() => { void load(); }, [load]);

  const run = async (what: () => Promise<Member[]>) => {
    setError(null);
    try { setMembers(await what()); } catch (e) { setError(message(e)); }
  };

  async function add(e: FormEvent) {
    e.preventDefault();
    await run(() => api.setMember(iid, username, role, role === "editor" && limit.length ? limit : null));
    setUsername("");
    setLimit([]);
  }

  return (
    <div className="modal" role="dialog" aria-modal="true" aria-label={`Share ${name}`}
      onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="modal-card wide">
        <h3>Share “{name}”</h3>
        {error && <div className="alert" role="alert">{error}<button onClick={() => setError(null)}>×</button></div>}

        <table className="issues userstable">
          <thead><tr><th>Person</th><th>Access</th><th /></tr></thead>
          <tbody>
            {members.map((m) => (
              <tr key={m.id}>
                <td>{m.display_name} <small className="muted">({m.username})</small></td>
                <td>
                  <select value={m.role} aria-label={`access of ${m.username}`} onChange={(e) => run(() => api.setMember(iid, m.username, e.target.value as Member["role"]))}>
                    {(Object.keys(ROLE_LABEL) as Member["role"][]).map((r) => <option key={r} value={r}>{ROLE_LABEL[r]}</option>)}
                  </select>
                  {m.role === "editor" && (
                    <details className="deptscope">
                      <summary>{m.departments ? `Only ${m.departments.join(", ")}` : "Whole institution"}</summary>
                      <label className="check">
                        <input type="radio" name={`scope-${m.id}`} checked={!m.departments}
                          onChange={() => run(() => api.setMember(iid, m.username, "editor", null))} /> The whole institution
                      </label>
                      <div className="muted small">or only these departments:</div>
                      {departments.map((d) => (
                        <label key={d} className="check">
                          <input type="checkbox" checked={!!m.departments?.includes(d)} aria-label={`${m.username} may change ${d}`}
                            onChange={(e) => {
                              const now = new Set(m.departments ?? []);
                              if (e.target.checked) now.add(d); else now.delete(d);
                              void run(() => api.setMember(iid, m.username, "editor", now.size ? [...now] : null));
                            }} /> {d}
                        </label>
                      ))}
                    </details>
                  )}
                </td>
                <td className="rowactions"><button className="btn small" onClick={() => run(() => api.removeMember(iid, m.username))}>Remove</button></td>
              </tr>
            ))}
          </tbody>
        </table>
        {members.length === 0 && <p className="muted">Only administrators can see this institution so far.</p>}

        <h4>Give someone access</h4>
        <form className="addform" onSubmit={add}>
          <input placeholder="their username" aria-label="username to share with" value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="off" />
          <select aria-label="access to give" value={role} onChange={(e) => setRole(e.target.value as Member["role"])}>
            {(Object.keys(ROLE_LABEL) as Member["role"][]).map((r) => <option key={r} value={r}>{ROLE_LABEL[r]}</option>)}
          </select>
          <button className="btn primary" disabled={!username.trim()}>Share</button>
        </form>
        {role === "editor" && departments.length > 0 && (
          <fieldset className="deptpick">
            <legend>Limit this editor to some departments <span className="muted small">(none ticked = the whole institution)</span></legend>
            {departments.map((d) => (
              <label key={d} className="check">
                <input type="checkbox" checked={limit.includes(d)} onChange={(e) => setLimit(e.target.checked ? [...limit, d] : limit.filter((x) => x !== d))} /> {d}
              </label>
            ))}
          </fieldset>
        )}
        <p className="muted small">They must already have an account (the administrator adds people under Account → People).</p>

        <div className="modal-actions"><button className="btn" onClick={onClose}>Close</button></div>
      </div>
    </div>
  );
}
