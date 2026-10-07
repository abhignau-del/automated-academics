// Sign-in for the shared (server) mode. With sign-in switched off the server says so and none of this shows:
// the app opens straight away, as the single-user desktop app always has.

import { createContext, useCallback, useContext, useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";
import * as api from "./api";
import { UsersDialog } from "./UsersDialog";
import type { AuthStatus, AuthUser } from "./types";

export interface Session {
  user: AuthUser;
  signOut: () => Promise<void>;
}

const Ctx = createContext<Session | null>(null);

/** The signed-in person, or null when sign-in is off. */
export const useSession = () => useContext(Ctx);

const message = (e: unknown) => (e instanceof Error ? e.message : String(e));

function Card({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="authwall">
      <div className="authcard">
        <h1>Automated Academics</h1>
        <h2>{title}</h2>
        {children}
      </div>
    </div>
  );
}

function LoginForm({ onDone }: { onDone: () => void }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true); setError(null);
    try { await api.login(username, password); onDone(); }
    catch (err) { setError(message(err)); setBusy(false); }
  }
  return (
    <Card title="Sign in">
      <form onSubmit={submit}>
        <label>Username<input value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="username" autoFocus /></label>
        <label>Password<input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" /></label>
        {error && <div className="alert" role="alert">{error}</div>}
        <button className="btn primary" disabled={busy || !username || !password}>{busy ? "Signing in…" : "Sign in"}</button>
        <p className="muted small">Forgotten your password? Ask your administrator to set a new one.</p>
      </form>
    </Card>
  );
}

function SetupForm({ onDone }: { onDone: () => void }) {
  const [username, setUsername] = useState("");
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [again, setAgain] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const mismatch = again !== "" && again !== password;
  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true); setError(null);
    try { await api.setupAdmin(username, password, name); onDone(); }
    catch (err) { setError(message(err)); setBusy(false); }
  }
  return (
    <Card title="Welcome. Create the administrator">
      <p className="muted small">
        This is the first run. The administrator can add the other people and see every institution. Choose a password of at
        least 8 characters and keep it somewhere safe.
      </p>
      <form onSubmit={submit}>
        <label>Username<input value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="username" autoFocus placeholder="e.g. admin" /></label>
        <label>Your name<input value={name} onChange={(e) => setName(e.target.value)} placeholder="shown to others" /></label>
        <label>Password<input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="new-password" /></label>
        <label>Password again<input type="password" value={again} onChange={(e) => setAgain(e.target.value)} autoComplete="new-password" /></label>
        {mismatch && <p className="setting-note error">The two passwords differ.</p>}
        {error && <div className="alert" role="alert">{error}</div>}
        <button className="btn primary" disabled={busy || !username || !password || password !== again}>{busy ? "Creating…" : "Create administrator"}</button>
      </form>
    </Card>
  );
}

export function PasswordDialog({ onClose }: { onClose: () => void }) {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [again, setAgain] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);
  async function submit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    try { await api.changePassword(current, next); setDone(true); }
    catch (err) { setError(message(err)); }
  }
  return (
    <div className="modal" role="dialog" aria-modal="true" aria-label="Change password"
      onMouseDown={(e) => { if (e.target === e.currentTarget && !done) onClose(); }}>
      <div className="modal-card">
        <h3>Change password</h3>
        {done ? (
          <>
            <p>Your password was changed and you were signed out everywhere. Sign in again with the new one.</p>
            <div className="modal-actions"><button className="btn primary" onClick={() => window.location.reload()}>Sign in again</button></div>
          </>
        ) : (
          <form onSubmit={submit} className="stackform">
            <label>Current password<input type="password" value={current} onChange={(e) => setCurrent(e.target.value)} autoComplete="current-password" autoFocus /></label>
            <label>New password<input type="password" value={next} onChange={(e) => setNext(e.target.value)} autoComplete="new-password" /></label>
            <label>New password again<input type="password" value={again} onChange={(e) => setAgain(e.target.value)} autoComplete="new-password" /></label>
            {error && <div className="alert" role="alert">{error}</div>}
            <div className="modal-actions">
              <button type="button" className="btn" onClick={onClose}>Cancel</button>
              <button className="btn primary" disabled={!current || !next || next !== again}>Change password</button>
            </div>
          </form>
        )}
      </div>
    </div>
  );
}

/** Shows the app only once signed in (or straight away when sign-in is off). */
export function AuthGate({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<AuthStatus | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const everSignedIn = useRef(false);

  const refresh = useCallback(() => api.authStatus().then((s) => { setStatus(s); setProblem(null); }).catch((e) => setProblem(message(e))), []);
  useEffect(() => {
    void refresh();
    // a 401 from anywhere in the app means the sign-in ended: ask again, keeping the screen (and unsaved work) behind it
    api.setUnauthorizedHandler(() => setStatus((s) => (s ? { ...s, user: null } : s)));
    return () => api.setUnauthorizedHandler(null);
  }, [refresh]);

  const signOut = useCallback(async () => {
    await api.logout().catch(() => undefined);
    window.location.reload(); // leave nothing of the last person's data in memory
  }, []);

  if (problem) return <Card title="Cannot reach the server"><p>{problem}</p><button className="btn" onClick={() => void refresh()}>Try again</button></Card>;
  if (!status) return <Card title="Loading…"><p className="muted">One moment.</p></Card>;
  if (!status.auth) return <>{children}</>;
  if (status.setup_needed) return <SetupForm onDone={refresh} />;
  if (status.user) {
    everSignedIn.current = true;
    return <Ctx.Provider value={{ user: status.user, signOut }}>{children}</Ctx.Provider>;
  }
  return (
    <>
      {everSignedIn.current && children}
      <LoginForm onDone={refresh} />
    </>
  );
}

/** The signed-in person's menu in the header: change password, manage people (administrators), sign out. */
export function AccountMenu() {
  const session = useSession();
  const [open, setOpen] = useState(false);
  const [dialog, setDialog] = useState<"password" | "people" | null>(null);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => { if (!ref.current?.contains(e.target as Node)) setOpen(false); };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [open]);
  if (!session) return null;
  const { user, signOut } = session;
  return (
    <>
      <span className="spacer" />
      <div className="menu" ref={ref}>
        <button className="btn" aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
          {user.display_name}{user.role === "admin" ? " (administrator)" : ""} ▾
        </button>
        {open && (
          <div className="menu-list" role="menu">
            <button role="menuitem" onClick={() => { setOpen(false); setDialog("password"); }}>Change password…</button>
            {user.role === "admin" && <button role="menuitem" onClick={() => { setOpen(false); setDialog("people"); }}>People…</button>}
            <button role="menuitem" onClick={() => void signOut()}>Sign out</button>
          </div>
        )}
      </div>
      {dialog === "password" && <PasswordDialog onClose={() => setDialog(null)} />}
      {dialog === "people" && <UsersDialog me={user.id} onClose={() => setDialog(null)} />}
    </>
  );
}
