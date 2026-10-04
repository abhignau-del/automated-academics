import type {
  ConflictReport, Institution, InstitutionSummary, Job, Timetable, UploadIssue, ViewKind,
} from "./types";

export const API_BASE: string = import.meta.env.VITE_API_URL ?? "http://127.0.0.1:8000";

export class ApiError extends Error {
  constructor(public status: number, message: string, public issues: UploadIssue[] = []) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(API_BASE + path, init);
  } catch {
    throw new ApiError(0, `Cannot reach the server at ${API_BASE}. Is the backend running?`);
  }
  if (!res.ok) {
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
  return res.json() as Promise<T>;
}

const json = (method: string, body: unknown): RequestInit => ({
  method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
});

export const templateUrl = `${API_BASE}/institutions/template`;

export const listInstitutions = () => request<InstitutionSummary[]>("/institutions");
export const getInstitution = (id: string) => request<Institution>(`/institutions/${id}`);

export function uploadInstitution(file: File) {
  const form = new FormData();
  form.append("file", file);
  return request<{ id: string; name: string }>("/institutions/upload", { method: "POST", body: form });
}

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
