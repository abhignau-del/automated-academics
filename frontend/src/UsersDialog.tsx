import { useCallback, useEffect, useState, type FormEvent } from "react";
import * as api from "./api";
import type { UserRow } from "./types";

const message = (e: unknown) => (e instanceof Error ? e.message : String(e));

/** The administrator's list of people: add, change role, set a new password, disable, delete. */
export function UsersDialog({ me, onClose }: { me: string; onClose: () => void }) {
  const [users, setUsers] = useState<UserRow[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [resetting, setResetting] = useState<string | null>(null);
  const [newPassword, setNewPassword] = useState("");
  const [form, setForm] = useState({ username: "", name: "", password: "", role: "member" as "admin" | "member" });

  const load = useCallback(() => api.listUsers().then(setUsers).catch((e) => setError(message(e))), []);
  useEffect(() => { void load(); }, [load]);

  const run = async (what: () => Promise<unknown>) => {
    setError(null);
    try { await what(); await load(); } catch (e) { setError(message(e)); }
  };

  async function add(e: FormEvent) {
    e.preventDefault();
    await run(async () => {
      await api.addUser(form.username, form.password, form.name, form.role);
      setForm({ username: "", name: "", password: "", role: "member" });
    });
  }

  return (
    <div className="modal" role="dialog" aria-modal="true" aria-label="People"
      onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="modal-card wide">
        <h3>People</h3>
        <p className="muted small">
          Everyone who can sign in. A person sees only the institutions shared with them (use Share on an institution), apart
          from administrators, who see everything.
        </p>
        {error && <div className="alert" role="alert">{error}<button onClick={() => setError(null)}>×</button></div>}

        <table className="issues userstable">
          <thead><tr><th>Username</th><th>Name</th><th>Role</th><th /></tr></thead>
          <tbody>
            {users.map((u) => (
              <tr key={u.id} className={u.disabled ? "off" : ""}>
                <td>{u.username}{u.id === me && " (you)"}{u.disabled && <small> · disabled</small>}</td>
                <td>{u.display_name}</td>
                <td>
                  <select value={u.role} aria-label={`role of ${u.username}`}
                    onChange={(e) => run(() => api.patchUser(u.id, { role: e.target.value as "admin" | "member" }))}>
                    <option value="member">Member</option>
                    <option value="admin">Administrator</option>
                  </select>
                </td>
                <td className="rowactions">
                  {resetting === u.id ? (
                    <>
                      <input type="password" value={newPassword} placeholder="new password" aria-label={`new password for ${u.username}`}
                        onChange={(e) => setNewPassword(e.target.value)} autoComplete="new-password" />
                      <button className="btn small primary" disabled={newPassword.length < 8}
                        onClick={() => run(async () => { await api.patchUser(u.id, { password: newPassword }); setResetting(null); setNewPassword(""); })}>Set</button>
                      <button className="btn small" onClick={() => { setResetting(null); setNewPassword(""); }}>Cancel</button>
                    </>
                  ) : (
                    <>
                      <button className="btn small" onClick={() => setResetting(u.id)}>Set password</button>
                      <button className="btn small" onClick={() => run(() => api.patchUser(u.id, { disabled: !u.disabled }))}>{u.disabled ? "Enable" : "Disable"}</button>
                      <button className="btn small danger" disabled={u.id === me}
                        onClick={() => { if (confirm(`Delete ${u.username}? They lose access to everything.`)) void run(() => api.deleteUser(u.id)); }}>Delete</button>
                    </>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>

        <h4>Add a person</h4>
        <form className="addform" onSubmit={add}>
          <input placeholder="username" aria-label="new username" value={form.username} onChange={(e) => setForm({ ...form, username: e.target.value })} autoComplete="off" />
          <input placeholder="name" aria-label="new name" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} autoComplete="off" />
          <input type="password" placeholder="password (8+ characters)" aria-label="new person's password" value={form.password}
            onChange={(e) => setForm({ ...form, password: e.target.value })} autoComplete="new-password" />
          <select aria-label="new person's role" value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value as "admin" | "member" })}>
            <option value="member">Member</option>
            <option value="admin">Administrator</option>
          </select>
          <button className="btn primary" disabled={!form.username || form.password.length < 8}>Add</button>
        </form>
        <p className="muted small">Tell them their username and password in person; they can change the password themselves after signing in.</p>

        <div className="modal-actions"><button className="btn" onClick={onClose}>Close</button></div>
      </div>
    </div>
  );
}
