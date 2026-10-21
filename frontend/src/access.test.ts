import { describe, expect, it } from "vitest";
import { canDelete, canEdit, canShare, roleOf } from "./access";

describe("access", () => {
  it("treats no listed role as full access (sign-in off, or an administrator)", () => {
    expect(roleOf(undefined)).toBe("owner");
    expect(roleOf({})).toBe("owner");
    expect(roleOf({ role: "viewer" })).toBe("viewer");
  });

  it("lets viewers look only, editors change, and only owners share or delete", () => {
    expect([canEdit("viewer"), canEdit("editor"), canEdit("owner")]).toEqual([false, true, true]);
    expect([canShare("viewer"), canShare("editor"), canShare("owner")]).toEqual([false, false, true]);
    expect([canDelete("viewer"), canDelete("editor"), canDelete("owner")]).toEqual([false, false, true]);
  });
});

import { departmentsOf, rowEditable, scopeOf, settingsEditable } from "./access";
import type { Institution } from "./types";

describe("department scope", () => {
  const inst = {
    name: "T", calendar: { day_names: ["Mon"], lectures_per_day: 1, break_after: [] }, rooms: [],
    faculty: [
      { id: "F1", name: "A", department: "CS", unavailable: [], avoid: [], max_lectures_per_day: 6 },
      { id: "F2", name: "B", department: "MG", unavailable: [], avoid: [], max_lectures_per_day: 6 },
    ],
    batches: [
      { id: "B1", program: "P", level: "UG", semester: 1, section: "A", department: "CS", strength: 30, group_of: null },
      { id: "B2", program: "P", level: "UG", semester: 1, section: "A", department: "MG", strength: 30, group_of: null },
    ],
    courses: [{ code: "C1", name: "X", department: "", credits: 3, category: "DSC", room_kind: "classroom" }],
    offerings: [], weights: { repeat_course_day: 1, batch_gaps: 1, faculty_gaps: 1, peak_day_load: 1, avoid_slot: 1, room_changes: 0, odd_hours: 0 }, pins: [],
  } as unknown as Institution;

  it("reads a person's scope from the institution list", () => {
    expect(scopeOf(undefined)).toBeNull();
    expect(scopeOf({})).toBeNull();
    expect(scopeOf({ departments: [] })).toBeNull();
    expect(scopeOf({ departments: ["CS"] })).toEqual(["CS"]);
  });

  it("lists the departments named in the data, once each", () => {
    expect(departmentsOf(inst)).toEqual(["CS", "MG"]);
  });

  it("lets a limited editor change only their departments' rows, never rooms or settings", () => {
    const cs = ["CS"];
    expect(rowEditable(inst, "faculty", inst.faculty[0] as never, cs)).toBe(true);
    expect(rowEditable(inst, "faculty", inst.faculty[1] as never, cs)).toBe(false);
    expect(rowEditable(inst, "batches", inst.batches[1] as never, cs)).toBe(false);
    expect(rowEditable(inst, "rooms", { id: "R" }, cs)).toBe(false);
    expect(rowEditable(inst, "courses", inst.courses[0] as never, cs)).toBe(true); // no department yet: theirs to fill in
    expect(settingsEditable(cs)).toBe(false);
    expect(settingsEditable(null)).toBe(true);
    expect(rowEditable(inst, "rooms", { id: "R" }, null)).toBe(true);
  });

  it("an offering needs every class's department; an empty new one is theirs", () => {
    const cs = ["CS"];
    expect(rowEditable(inst, "offerings", { batch_ids: ["B1"] }, cs)).toBe(true);
    expect(rowEditable(inst, "offerings", { batch_ids: ["B1", "B2"] }, cs)).toBe(false);
    expect(rowEditable(inst, "offerings", { batch_ids: ["B1", "B2"] }, ["CS", "MG"])).toBe(true);
    expect(rowEditable(inst, "offerings", { batch_ids: [] }, cs)).toBe(true);
    expect(rowEditable(inst, "offerings", { batch_ids: ["ghost"] }, cs)).toBe(false);
  });
});
