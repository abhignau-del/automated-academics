import type {
  AuthStatus, AuthUser, CheckResult, ConflictReport, DataIssue, Institution, InstitutionSummary, Job, Timetable, UploadIssue,
  Member, UserRow, ViewKind,
} from "./types";

export const API_BASE: string = import.meta.env.VITE_API_URL ?? "http://127.0.0.1:8000";

export class ApiError extends Error {
  constructor(public status: number, message: string, public issues: UploadIssue[] = []) {
    super(message);
  }
}

/** Called when the server says the sign-in is missing or has expired (shared mode). */
let onUnauthorized: (() => void) | null = null;
export const setUnauthorizedHandler = (fn: (() => void) | null) => { onUnauthorized = fn; };

/** The version of each institution as last read (its ETag), sent back on save so a stale save is refused. */
const versions = new Map<string, string>();

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(API_BASE + path, { credentials: "include", ...init });
  } catch {
    throw new ApiError(0, `Cannot reach the server at ${API_BASE}. Is the backend running?`);
  }
  if (!res.ok) {
    if (res.status === 401 && !path.startsWith("/auth/")) onUnauthorized?.();
    let message = res.statusText;
    let issues: UploadIssue[] = [];
    try {
      const body = await res.json();
      const d = body.detail;
      if (typeof d === "string") message = d;
      else if (d && typeof d === "object" && !Array.isArray(d)) {
        message = d.message ?? message;
        issues = d.issues ?? [];
      } else if (Array.isArray(d)) message = d.map((e: { msg?: string }) => e.msg).join("; ");
    } catch { /* keep statusText */ }
    throw new ApiError(res.status, message, issues);
  }
  if (res.status === 204) return undefined as T; // e.g. DELETE: success with no body
  return res.json() as Promise<T>;
}

const json = (method: string, body: unknown): RequestInit => ({
  method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
});

export const templateUrl = `${API_BASE}/institutions/template`;

export const listInstitutions = () => request<InstitutionSummary[]>("/institutions");
export async function getInstitution(id: string): Promise<Institution> {
  const res = await fetch(`${API_BASE}/institutions/${id}`, { credentials: "include" }).catch(() => {
    throw new ApiError(0, `Cannot reach the server at ${API_BASE}. Is the backend running?`);
  });
  if (!res.ok) {
    if (res.status === 401) onUnauthorized?.();
    throw new ApiError(res.status, (await res.json().catch(() => ({}))).detail ?? res.statusText);
  }
  const tag = res.headers.get("ETag");
  if (tag) versions.set(id, tag);
  return res.json();
}

export function uploadInstitution(file: File) {
  const form = new FormData();
  form.append("file", file);
  return request<{ id: string; name: string }>("/institutions/upload", { method: "POST", body: form });
}

// ---- data entry ----

/** Empty institution or the fictional sample, to start a new one from. */
export const starterInstitution = (kind: "blank" | "sample") =>
  request<Institution>(`/institutions/starter?kind=${kind}`);

/** Check data without saving it. `valid` means it can be saved; issues are located by section and row. */
export const checkInstitution = (inst: Institution) =>
  request<CheckResult>("/institutions/check", json("POST", inst));

export const createInstitution = (inst: Institution) =>
  request<{ id: string; name: string; issues: DataIssue[] }>("/institutions", json("POST", inst));
export async function updateInstitution(id: string, inst: Institution) {
  const init = json("PUT", inst);
  const version = versions.get(id);
  if (version) init.headers = { ...init.headers, "If-Match": version };
  const r = await request<{ id: string; name: string; issues: DataIssue[]; version: number }>(`/institutions/${id}`, init);
  versions.set(id, `"${r.version}"`);
  return r;
}
export interface WorkloadOptions {
  days: string[]; lectures_per_day: number; break_after: number[];
  classrooms: number | null; seats: number | null; labs: number; lab_seats: number | null;
  joint_max_students: number; default_students: number;
}
export interface WorkloadReport {
  rows_read: number; rows_used: number; assumptions: string[]; joint: string[]; problems: string[];
}
export interface WorkloadDraft { institution: Institution; report: WorkloadReport; issues: DataIssue[] }

/** Read a flat workload list into a draft institution. Nothing is saved until the draft is created. */
export function importWorkload(file: File, options: WorkloadOptions) {
  const form = new FormData();
  form.append("file", file);
  form.append("options", JSON.stringify(options));
  return request<WorkloadDraft>("/institutions/import-workload", { method: "POST", body: form });
}

/** The saved data as an Excel workbook (same format as the upload). */
export const workbookUrl = (id: string) => `${API_BASE}/institutions/${id}/workbook.xlsx`;
export const deleteInstitution = (id: string) =>
  request<void>(`/institutions/${id}`, { method: "DELETE" });

export const startSolve = (iid: string, timeLimit: number) =>
  request<{ job_id: string }>(`/institutions/${iid}/solve`, json("POST", { time_limit_s: timeLimit }));
export const getJob = (jid: string) => request<Job>(`/jobs/${jid}`);
export const getTimetable = (jid: string) => request<Timetable>(`/jobs/${jid}/timetable`);

export async function latestJob(iid: string): Promise<Job | null> {
  try {
    return await request<Job>(`/institutions/${iid}/latest-job`);
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) return null;
    throw e;
  }
}

/** Download links. Exports are built from the saved timetable, not unsaved edits. */
export const exportXlsxUrl = (jid: string) => `${API_BASE}/jobs/${jid}/export.xlsx`;
export function exportPdfUrl(jid: string, kind?: ViewKind, id?: string): string {
  const q = new URLSearchParams();
  if (kind) q.set("kind", kind);
  if (kind && id) q.set("id", id);
  const qs = q.toString();
  return `${API_BASE}/jobs/${jid}/export.pdf${qs ? "?" + qs : ""}`;
}

export const validate = (iid: string, tt: Timetable) =>
  request<ConflictReport>(`/institutions/${iid}/validate`, json("POST", tt));
export const saveTimetable = (jid: string, tt: Timetable) =>
  request<ConflictReport>(`/jobs/${jid}/timetable`, json("PUT", tt));

// ---- accounts ----

export const authStatus = () => request<AuthStatus>("/auth/status");
export const login = (username: string, password: string) => request<AuthUser>("/auth/login", json("POST", { username, password }));
export const setupAdmin = (username: string, password: string, display_name: string) =>
  request<AuthUser>("/auth/setup", json("POST", { username, password, display_name }));
export const logout = () => request<void>("/auth/logout", { method: "POST" });
export const changePassword = (current: string, next: string) =>
  request<void>("/auth/password", json("POST", { current, new: next }));

export const listUsers = () => request<UserRow[]>("/users");
export const addUser = (username: string, password: string, display_name: string, role: "admin" | "member") =>
  request<AuthUser>("/users", json("POST", { username, password, display_name, role }));
export const patchUser = (id: string, changes: { display_name?: string; password?: string; role?: "admin" | "member"; disabled?: boolean }) =>
  request<AuthUser>(`/users/${id}`, json("PATCH", changes));
export const deleteUser = (id: string) => request<void>(`/users/${id}`, { method: "DELETE" });

export const listMembers = (iid: string) => request<Member[]>(`/institutions/${iid}/members`);
export const setMember = (iid: string, username: string, role: Member["role"]) =>
  request<Member[]>(`/institutions/${iid}/members/${encodeURIComponent(username)}`, json("PUT", { role }));
export const removeMember = (iid: string, username: string) =>
  request<Member[]>(`/institutions/${iid}/members/${encodeURIComponent(username)}`, { method: "DELETE" });
