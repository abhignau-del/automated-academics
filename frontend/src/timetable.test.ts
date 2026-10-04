import { describe, expect, it } from "vitest";
import { assignLanes, blockFits, changeRoom, moveSession, occupiedBatches, placementsFor, sessionKey } from "./timetable";
import type { Calendar, Institution, Placement } from "./types";

const cal: Calendar = { day_names: ["Mon", "Tue", "Wed"], lectures_per_day: 7, break_after: [3] };

const inst = {
  name: "T", calendar: cal, rooms: [], faculty: [], courses: [],
  batches: [
    { id: "S", program: "P", level: "UG", semester: 1, section: "A", department: "D", strength: 60, group_of: null },
    { id: "S-A", program: "P", level: "UG", semester: 1, section: "A", department: "D", strength: 30, group_of: "S" },
    { id: "S-B", program: "P", level: "UG", semester: 1, section: "B", department: "D", strength: 30, group_of: "S" },
    { id: "X", program: "P", level: "UG", semester: 1, section: "A", department: "D", strength: 30, group_of: null },
  ],
  offerings: [
    { id: "LEC", course_code: "C1", faculty_id: "F1", batch_ids: ["S"], sessions: [1] },
    { id: "LABA", course_code: "C2", faculty_id: "F2", batch_ids: ["S-A"], sessions: [2] },
    { id: "LABB", course_code: "C2", faculty_id: "F3", batch_ids: ["S-B"], sessions: [2] },
    { id: "OTHER", course_code: "C3", faculty_id: "F1", batch_ids: ["X"], sessions: [1] },
  ],
} as unknown as Institution;

const P = (offering_id: string, day: number, start: number, length = 1, room_id = "R1"): Placement =>
  ({ offering_id, session_index: 0, day, start, length, room_id });

describe("blockFits", () => {
  it("rejects blocks that cross the break or leave the day", () => {
    expect(blockFits(cal, 2, 2)).toBe(true);
    expect(blockFits(cal, 3, 2)).toBe(false);
    expect(blockFits(cal, 4, 3)).toBe(true);
    expect(blockFits(cal, 6, 2)).toBe(false);
    expect(blockFits(cal, -1, 1)).toBe(false);
  });
});

describe("occupiedBatches", () => {
  it("includes sub-groups of a parent but not the parent of a sub-group", () => {
    expect([...occupiedBatches(inst, ["S"])].sort()).toEqual(["S", "S-A", "S-B"]);
    expect([...occupiedBatches(inst, ["S-A"])]).toEqual(["S-A"]);
  });
});

describe("placementsFor", () => {
  const all = [P("LEC", 0, 0), P("LABA", 0, 4, 2), P("LABB", 0, 4, 2), P("OTHER", 1, 0)];
  const ids = (xs: Placement[]) => xs.map((p) => p.offering_id).sort();

  it("a section shows its lectures and its sub-groups' labs", () => {
    expect(ids(placementsFor(inst, all, "batch", "S"))).toEqual(["LABA", "LABB", "LEC"]);
  });
  it("a sub-group shows its own lab and its parent's lectures, not its sibling's lab", () => {
    expect(ids(placementsFor(inst, all, "batch", "S-A"))).toEqual(["LABA", "LEC"]);
  });
  it("filters by faculty and room", () => {
    expect(ids(placementsFor(inst, all, "faculty", "F1"))).toEqual(["LEC", "OTHER"]);
    expect(ids(placementsFor(inst, [P("LEC", 0, 0, 1, "R2")], "room", "R2"))).toEqual(["LEC"]);
  });
});

describe("moveSession", () => {
  const list = [P("LEC", 0, 0), P("LABA", 0, 4, 2)];

  it("moves one session and leaves the original array untouched", () => {
    const moved = moveSession(cal, list, sessionKey(list[0]), 2, 1)!;
    expect(moved[0]).toMatchObject({ day: 2, start: 1 });
    expect(moved[1]).toBe(list[1]);
    expect(list[0]).toMatchObject({ day: 0, start: 0 });
  });
  it("refuses moves that do not fit, are no-ops, or target unknown sessions", () => {
    expect(moveSession(cal, list, sessionKey(list[1]), 0, 3)).toBeNull(); // lab would span lunch
    expect(moveSession(cal, list, sessionKey(list[0]), 0, 0)).toBeNull();
    expect(moveSession(cal, list, sessionKey(list[0]), 9, 0)).toBeNull();
    expect(moveSession(cal, list, "NOPE#0", 1, 1)).toBeNull();
  });
});

describe("changeRoom", () => {
  it("changes only the targeted session", () => {
    const out = changeRoom([P("LEC", 0, 0), P("OTHER", 1, 0)], "LEC#0", "R9");
    expect(out.map((p) => p.room_id)).toEqual(["R9", "R1"]);
  });
});

describe("assignLanes", () => {
  it("puts parallel sessions side by side and keeps lone sessions full width", () => {
    const lanes = assignLanes([P("LABA", 0, 4, 2), P("LABB", 0, 4, 2), P("LEC", 0, 0), P("OTHER", 1, 4)]);
    expect(lanes.get("LABA#0")).toEqual({ lane: 0, lanes: 2 });
    expect(lanes.get("LABB#0")).toEqual({ lane: 1, lanes: 2 });
    expect(lanes.get("LEC#0")).toEqual({ lane: 0, lanes: 1 });
    expect(lanes.get("OTHER#0")).toEqual({ lane: 0, lanes: 1 });
  });
  it("does not widen an earlier non-overlapping session because of a later cluster", () => {
    const lanes = assignLanes([P("LEC", 0, 0, 2), P("LABA", 0, 3, 2), P("LABB", 0, 3, 2)]);
    expect(lanes.get("LEC#0")).toEqual({ lane: 0, lanes: 1 });
    expect(lanes.get("LABB#0")).toEqual({ lane: 1, lanes: 2 });
  });
});
