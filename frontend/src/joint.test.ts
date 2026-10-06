import { describe, expect, it } from "vitest";
import { combine, isJoint, split, suggestJoint } from "./joint";
import type { Institution } from "./types";

const base = (): Institution => ({
  name: "T",
  calendar: { day_names: ["Mon", "Tue"], lectures_per_day: 4, break_after: [] },
  rooms: [{ id: "R1", name: "Room 1", capacity: 70, kind: "classroom" }, { id: "L1", name: "Lab", capacity: 30, kind: "lab" }],
  faculty: [
    { id: "F1", name: "Asha", department: "D", unavailable: [], avoid: [], max_lectures_per_day: 6 },
    { id: "F2", name: "Ravi", department: "D", unavailable: [], avoid: [], max_lectures_per_day: 6 },
  ],
  batches: [
    { id: "A", program: "BSc", level: "UG", semester: 1, section: "A", department: "D", strength: 40, group_of: null },
    { id: "B", program: "BCom", level: "UG", semester: 1, section: "A", department: "D", strength: 20, group_of: null },
    { id: "A-1", program: "BSc", level: "UG", semester: 1, section: "A", department: "D", strength: 20, group_of: "A" },
  ],
  courses: [
    { code: "C1", name: "Maths", department: "D", credits: 3, category: "DSC", room_kind: "classroom" },
    { code: "C2", name: "Lab", department: "D", credits: 1, category: "LAB", room_kind: "lab" },
  ],
  offerings: [
    { id: "O1", course_code: "C1", faculty_id: "F1", batch_ids: ["A"], sessions: [1, 1, 1] },
    { id: "O2", course_code: "C1", faculty_id: "F1", batch_ids: ["B"], sessions: [1, 1, 1] },
    { id: "O3", course_code: "C1", faculty_id: "F2", batch_ids: ["B"], sessions: [1, 1] },
  ],
  weights: { repeat_course_day: 5, batch_gaps: 10, faculty_gaps: 3, peak_day_load: 4, avoid_slot: 6 },
  pins: [],
});

describe("suggestJoint", () => {
  it("proposes same course + same teacher for different classes, and nothing else", () => {
    const s = suggestJoint(base());
    expect(s).toHaveLength(1);
    expect(s[0]).toMatchObject({ ids: ["O1", "O2"], batchIds: ["A", "B"], students: 60, sessions: [1, 1, 1], sessionsDiffer: false, roomFits: true });
  });

  it("flags differing hours and rooms that are too small", () => {
    const inst = base();
    inst.offerings[1] = { ...inst.offerings[1], sessions: [1, 1] };
    inst.rooms[0].capacity = 50;
    expect(suggestJoint(inst)[0]).toMatchObject({ sessionsDiffer: true, sessions: [1, 1, 1], roomFits: false });
  });

  it("never pairs a class with its own lab group", () => {
    const inst = base();
    inst.offerings = [
      { id: "O1", course_code: "C1", faculty_id: "F1", batch_ids: ["A"], sessions: [1] },
      { id: "O2", course_code: "C1", faculty_id: "F1", batch_ids: ["A-1"], sessions: [1] },
    ];
    expect(suggestJoint(inst)).toEqual([]);
  });

  it("uses the lab kind when judging room fit", () => {
    const inst = base();
    inst.offerings = [
      { id: "O1", course_code: "C2", faculty_id: "F1", batch_ids: ["A"], sessions: [2] },
      { id: "O2", course_code: "C2", faculty_id: "F1", batch_ids: ["B"], sessions: [2] },
    ];
    expect(suggestJoint(inst)[0].roomFits).toBe(false); // 60 students, the only lab holds 30
  });
});

describe("combine and split", () => {
  it("combines into the first offering and drops the others", () => {
    const r = combine(base(), ["O1", "O2"]);
    if ("error" in r) throw new Error(r.error);
    expect(r.kept).toBe("O1");
    expect(r.inst.offerings.map((o) => o.id)).toEqual(["O1", "O3"]);
    expect(r.inst.offerings[0].batch_ids).toEqual(["A", "B"]);
    expect(isJoint(r.inst.offerings[0])).toBe(true);
    expect(r.note).toBeNull();
  });

  it("keeps the busiest weekly hours and says so", () => {
    const inst = base();
    inst.offerings[1] = { ...inst.offerings[1], sessions: [2] };
    const r = combine(inst, ["O1", "O2"]);
    if ("error" in r) throw new Error(r.error);
    expect(r.inst.offerings[0].sessions).toEqual([1, 1, 1]);
    expect(r.note).toMatch(/different weekly lectures/);
  });

  it("refuses different courses, different teachers, overlapping classes and single choices", () => {
    expect("error" in combine(base(), ["O1"])).toBe(true);
    expect((combine(base(), ["O1", "O3"]) as { error: string }).error).toMatch(/same teacher/);
    const inst = base();
    inst.offerings.push({ id: "O4", course_code: "C2", faculty_id: "F1", batch_ids: ["B"], sessions: [2] });
    expect((combine(inst, ["O1", "O4"]) as { error: string }).error).toMatch(/same course/);
    const nested = base();
    nested.offerings.push({ id: "O5", course_code: "C1", faculty_id: "F1", batch_ids: ["A-1"], sessions: [1, 1, 1] });
    expect((combine(nested, ["O1", "O5"]) as { error: string }).error).toMatch(/same class/);
  });

  it("drops locks of removed offerings and invalid ones of the kept offering", () => {
    const inst = base();
    inst.offerings[1] = { ...inst.offerings[1], sessions: [1, 1] };
    inst.pins = [
      { offering_id: "O2", session_index: 0, day: 0, start: 0, room_id: null },
      { offering_id: "O1", session_index: 2, day: 1, start: 0, room_id: null },
    ];
    const r = combine(inst, ["O2", "O1"]); // O2 is kept, with its 2 sessions...
    if ("error" in r) throw new Error(r.error);
    expect(r.inst.offerings[0].sessions).toEqual([1, 1, 1]);
    expect(r.inst.pins.map((p) => p.offering_id)).toEqual(["O2"]); // O1 is gone, so its lock is too
  });

  it("splits back into one offering per class with fresh ids", () => {
    const joined = combine(base(), ["O1", "O2"]);
    if ("error" in joined) throw new Error(joined.error);
    const r = split(joined.inst, "O1");
    if ("error" in r) throw new Error(r.error);
    expect(r.inst.offerings.map((o) => [o.id, o.batch_ids])).toEqual([["O1", ["A"]], ["O1-B", ["B"]], ["O3", ["B"]]]);
    expect("error" in split(r.inst, "O3")).toBe(true);
  });
});
