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
