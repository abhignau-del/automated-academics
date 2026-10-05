import { describe, expect, it } from "vitest";
import {
  addRow, cycleSlot, formatSessions, hasImpact, idOf, impact, parseSessions, removeRow, renameId,
  setSlots, slotState, uniqueId,
} from "./dataops";
import type { Faculty, Institution } from "./types";

const base = (): Institution => ({
  name: "T",
  calendar: { day_names: ["Mon", "Tue"], lectures_per_day: 4, break_after: [] },
  rooms: [{ id: "R1", name: "Room 1", capacity: 60, kind: "classroom" }],
  faculty: [
    { id: "F1", name: "Asha", department: "CS", unavailable: [], avoid: [], max_lectures_per_day: 6 },
    { id: "F2", name: "Ravi", department: "CS", unavailable: [], avoid: [], max_lectures_per_day: 6 },
  ],
  batches: [
    { id: "S", program: "BSc", level: "UG", semester: 1, section: "A", department: "CS", strength: 60, group_of: null },
    { id: "S-A", program: "BSc", level: "UG", semester: 1, section: "A", department: "CS", strength: 30, group_of: "S" },
    { id: "T", program: "BSc", level: "UG", semester: 3, section: "A", department: "CS", strength: 50, group_of: null },
  ],
  courses: [
    { code: "C1", name: "Maths", department: "CS", credits: 3, category: "DSC", room_kind: "classroom" },
    { code: "C2", name: "Lab", department: "CS", credits: 2, category: "LAB", room_kind: "lab" },
  ],
  offerings: [
    { id: "O1", course_code: "C1", faculty_id: "F1", batch_ids: ["S"], sessions: [1, 1, 1] },
    { id: "O2", course_code: "C2", faculty_id: "F2", batch_ids: ["S-A"], sessions: [2] },
    { id: "O3", course_code: "C1", faculty_id: "F1", batch_ids: ["S", "T"], sessions: [1, 1] }, // shared
  ],
  weights: { repeat_course_day: 5, batch_gaps: 10, faculty_gaps: 3, peak_day_load: 4, avoid_slot: 6 },
  pins: [],
});

describe("uniqueId", () => {
  it("starts after the current count and skips taken ids", () => {
    expect(uniqueId([], "R")).toBe("R1");
    expect(uniqueId(["R1", "R2"], "R")).toBe("R3");
    expect(uniqueId(["R1", "R3"], "R")).toBe("R4"); // R3 is taken, count + 1 = 3
    expect(uniqueId(["x"], "R")).toBe("R2");
  });
});

describe("addRow", () => {
  it("adds a row to each table with a fresh id, without touching the original", () => {
    const inst = base();
    const frozen = JSON.stringify(inst);
    for (const [table, n] of [["rooms", 2], ["faculty", 3], ["batches", 4], ["courses", 3], ["offerings", 4]] as const) {
      const out = addRow(inst, table);
      expect(out[table]).toHaveLength(n);
      const ids = out[table].map((_, i) => idOf(out, table, i));
      expect(new Set(ids).size).toBe(ids.length);
    }
    expect(JSON.stringify(inst)).toBe(frozen);
  });

  it("a new offering points at rows that exist, so it starts valid", () => {
    const o = addRow(base(), "offerings").offerings.at(-1)!;
    expect(o).toMatchObject({ course_code: "C1", faculty_id: "F1", batch_ids: ["S"], sessions: [1, 1, 1] });
  });

  it("works on an empty institution", () => {
    const blank: Institution = { ...base(), rooms: [], faculty: [], batches: [], courses: [], offerings: [] };
    const o = addRow(blank, "offerings").offerings[0];
    expect(o.course_code).toBe("");
    expect(o.batch_ids).toEqual([]);
    expect(addRow(blank, "batches").batches[0].level).toBe("UG");
  });
});

describe("renameId", () => {
  it("renaming a faculty member updates the offerings that use them", () => {
    const out = renameId(base(), "faculty", "F1", "ASHA");
    expect(out.faculty.map((f) => f.id)).toEqual(["ASHA", "F2"]);
    expect(out.offerings.map((o) => o.faculty_id)).toEqual(["ASHA", "F2", "ASHA"]);
  });

  it("renaming a course updates offerings", () => {
    const out = renameId(base(), "courses", "C1", "MATH");
    expect(out.offerings.map((o) => o.course_code)).toEqual(["MATH", "C2", "MATH"]);
  });

  it("renaming a batch updates offerings (including shared ones) and its sub-groups' parent", () => {
    const out = renameId(base(), "batches", "S", "SEC1");
    expect(out.batches.map((b) => [b.id, b.group_of])).toEqual([["SEC1", null], ["S-A", "SEC1"], ["T", null]]);
    expect(out.offerings.map((o) => o.batch_ids)).toEqual([["SEC1"], ["S-A"], ["SEC1", "T"]]);
  });

  it("is a no-op for the same id, and does not mutate", () => {
    const inst = base();
    expect(renameId(inst, "faculty", "F1", "F1")).toBe(inst);
    const frozen = JSON.stringify(inst);
    renameId(inst, "batches", "S", "X");
    expect(JSON.stringify(inst)).toBe(frozen);
  });

  it("leaves unrelated rows alone", () => {
    const out = renameId(base(), "faculty", "F2", "RAVI");
    expect(out.offerings[0].faculty_id).toBe("F1");
    expect(out.offerings[1].faculty_id).toBe("RAVI");
  });
});

describe("impact and removeRow", () => {
  it("deleting a teacher also deletes the offerings they teach", () => {
    const inst = base();
    expect(impact(inst, "faculty", "F1")).toEqual({ offeringsRemoved: ["O1", "O3"], offeringsEdited: [], subgroups: [] });
    const out = removeRow(inst, "faculty", 0);
    expect(out.faculty.map((f) => f.id)).toEqual(["F2"]);
    expect(out.offerings.map((o) => o.id)).toEqual(["O2"]);
  });

  it("deleting a course deletes its offerings", () => {
    const out = removeRow(base(), "courses", 0);
    expect(out.courses.map((c) => c.code)).toEqual(["C2"]);
    expect(out.offerings.map((o) => o.id)).toEqual(["O2"]);
  });

  it("deleting a batch removes it from shared offerings, deletes offerings left with no batch, and un-parents sub-groups", () => {
    const inst = base();
    const i = impact(inst, "batches", "S");
    expect(i).toEqual({ offeringsRemoved: ["O1"], offeringsEdited: ["O3"], subgroups: ["S-A"] });
    const out = removeRow(inst, "batches", 0);
    expect(out.batches.map((b) => [b.id, b.group_of])).toEqual([["S-A", null], ["T", null]]);
    expect(out.offerings.map((o) => [o.id, o.batch_ids])).toEqual([["O2", ["S-A"]], ["O3", ["T"]]]);
  });

  it("rooms and offerings have no dependants", () => {
    const inst = base();
    expect(hasImpact(impact(inst, "rooms", "R1"))).toBe(false);
    expect(hasImpact(impact(inst, "offerings", "O1"))).toBe(false);
    expect(removeRow(inst, "offerings", 0).offerings.map((o) => o.id)).toEqual(["O2", "O3"]);
    expect(removeRow(inst, "rooms", 0).rooms).toEqual([]);
  });

  it("never leaves a dangling reference, whatever is deleted", () => {
    const refsOk = (x: Institution) => {
      const f = new Set(x.faculty.map((r) => r.id)), c = new Set(x.courses.map((r) => r.code)), b = new Set(x.batches.map((r) => r.id));
      return x.offerings.every((o) => f.has(o.faculty_id) && c.has(o.course_code) && o.batch_ids.length > 0 && o.batch_ids.every((id) => b.has(id)))
        && x.batches.every((r) => r.group_of === null || b.has(r.group_of));
    };
    for (const table of ["rooms", "faculty", "batches", "courses", "offerings"] as const) {
      const n = base()[table].length;
      for (let i = 0; i < n; i++) expect(refsOk(removeRow(base(), table, i))).toBe(true);
    }
  });
});

describe("sessions text", () => {
  it("parses lists of whole numbers", () => {
    expect(parseSessions("1,1,1")).toEqual([1, 1, 1]);
    expect(parseSessions(" 2 ")).toEqual([2]);
    expect(parseSessions("1; 2 3")).toEqual([1, 2, 3]);
  });
  it("rejects empty, zero, decimals and words", () => {
    for (const bad of ["", "  ", "0", "1,0", "1.5", "two", "1,-1"]) expect(parseSessions(bad)).toBeNull();
  });
  it("round-trips", () => {
    expect(parseSessions(formatSessions([1, 1, 2]))).toEqual([1, 1, 2]);
  });
});

describe("availability slots", () => {
  const f = (): Faculty => base().faculty[0];

  it("a click cycles free -> avoid -> unavailable -> free", () => {
    let t = f();
    expect(slotState(t, 1, 2)).toBe("free");
    t = cycleSlot(t, 1, 2); expect(slotState(t, 1, 2)).toBe("avoid");
    expect(t.avoid).toEqual([{ day: 1, lecture: 2 }]);
    expect(t.unavailable).toEqual([]);
    t = cycleSlot(t, 1, 2); expect(slotState(t, 1, 2)).toBe("unavailable");
    expect(t.avoid).toEqual([]);
    expect(t.unavailable).toEqual([{ day: 1, lecture: 2 }]);
    t = cycleSlot(t, 1, 2); expect(slotState(t, 1, 2)).toBe("free");
    expect(t.avoid).toEqual([]);
    expect(t.unavailable).toEqual([]);
  });

  it("a slot is never in both lists, and other slots are untouched", () => {
    let t = cycleSlot(cycleSlot(f(), 0, 0), 1, 1);
    t = cycleSlot(t, 0, 0); // now unavailable
    expect(t.unavailable).toEqual([{ day: 0, lecture: 0 }]);
    expect(t.avoid).toEqual([{ day: 1, lecture: 1 }]);
  });

  it("does not mutate the original", () => {
    const orig = f();
    cycleSlot(orig, 0, 0);
    expect(orig.avoid).toEqual([]);
  });

  it("setSlots puts a whole row or column in one state from any starting mix", () => {
    const mixed = cycleSlot(cycleSlot(cycleSlot(f(), 0, 0), 0, 0), 0, 1); // (0,0) unavailable, (0,1) avoid
    const col = [0, 1, 2, 3].map((lecture) => ({ day: 0, lecture }));
    const allBlocked = setSlots(mixed, col, "unavailable");
    expect(col.every((c) => slotState(allBlocked, c.day, c.lecture) === "unavailable")).toBe(true);
    const cleared = setSlots(allBlocked, col, "free");
    expect(cleared.unavailable).toEqual([]);
    expect(cleared.avoid).toEqual([]);
  });
});

describe("pins follow the data they refer to", () => {
  const pinned = (): Institution => ({
    ...base(),
    pins: [
      { offering_id: "O1", session_index: 0, day: 0, start: 0, room_id: "R1" },
      { offering_id: "O2", session_index: 0, day: 1, start: 0, room_id: null },
    ],
  });

  it("renaming an offering or a room updates its pins", () => {
    expect(renameId(pinned(), "offerings", "O1", "X1").pins.map((p) => p.offering_id)).toEqual(["X1", "O2"]);
    expect(renameId(pinned(), "rooms", "R1", "Z").pins.map((p) => p.room_id)).toEqual(["Z", null]);
  });

  it("deleting an offering (directly or through its teacher) drops its pins", () => {
    expect(removeRow(pinned(), "offerings", 0).pins.map((p) => p.offering_id)).toEqual(["O2"]);
    expect(removeRow(pinned(), "faculty", 0).pins.map((p) => p.offering_id)).toEqual(["O2"]); // F1 teaches O1
  });

  it("deleting a room keeps the pinned time but frees the room choice", () => {
    const out = removeRow(pinned(), "rooms", 0);
    expect(out.pins[0]).toMatchObject({ offering_id: "O1", day: 0, room_id: null });
  });
});
