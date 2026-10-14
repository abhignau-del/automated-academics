// What a person may do with an institution, from their role on it. Mirrors the server's rules; the server
// is what actually enforces them, this only decides which buttons to show.

export type Role = "owner" | "editor" | "viewer";

/** The list gives no role when sign-in is off or the person is an administrator: they can do everything. */
export const roleOf = (listed: { role?: Role } | undefined): Role => listed?.role ?? "owner";

export const canEdit = (r: Role) => r === "owner" || r === "editor";
export const canShare = (r: Role) => r === "owner";
export const canDelete = (r: Role) => r === "owner";

export const ROLE_LABEL: Record<Role, string> = {
  owner: "Owner (can do everything, including sharing and deleting)",
  editor: "Editor (can change data and timetables)",
  viewer: "Viewer (can look and download)",
};

// ---- editors limited to some departments (mirrors backend/scope.py; the server enforces it) ----

import type { Institution } from "./types";

type Scope = string[] | null; // null = the whole institution

/** The departments a person is limited to on an institution, from the institution list. */
export const scopeOf = (listed: { departments?: string[] | null } | undefined): Scope =>
  listed?.departments && listed.departments.length ? listed.departments : null;

/** Every department named on a class, teacher or course. */
export function departmentsOf(inst: Institution): string[] {
  const set = new Set<string>();
  for (const r of [...inst.batches, ...inst.faculty, ...inst.courses]) if (r.department?.trim()) set.add(r.department.trim());
  return [...set].sort((a, b) => a.localeCompare(b));
}

type TableName = "rooms" | "faculty" | "batches" | "courses" | "offerings";

/** May this person change this row? Rooms and settings are shared, so a limited editor can't. */
export function rowEditable(inst: Institution, table: TableName, row: Record<string, unknown>, scope: Scope): boolean {
  if (scope === null) return true;
  const mine = new Set(scope);
  if (table === "rooms") return false;
  if (table === "offerings") {
    const dept = new Map(inst.batches.map((b) => [b.id, b.department]));
    const batches = (row.batch_ids as string[] | undefined) ?? [];
    return batches.length > 0 ? batches.every((b) => mine.has(dept.get(b) ?? "")) : true; // a new, empty one is theirs to fill
  }
  const d = String(row.department ?? "");
  return d === "" || mine.has(d);
}

/** Days, lecture times, goal weights and the name are shared by every department. */
export const settingsEditable = (scope: Scope): boolean => scope === null;
