import { describe, expect, it } from "vitest";
import {
  bulkSet, COLUMNS, detectColumns, duplicateRows, matchesFilter, matchOption, optionsFor, parseSlots, parseTSV,
  pasteGrid, removeRows, shownColumns, summariseRemoval,
} from "./sheet";
import type { Institution } from "./types";

const base = (): Institution => ({
  name: "T",
  calendar: { day_names: ["Mon", "Tue", "Wed"], lectures_per_day: 5, break_after: [] },
  rooms: [
    { id: "R1", name: "Room 1", capacity: 60, kind: "classroom" },
    { id: "R2", name: "Lab 1", capacity: 40, kind: "lab" },
  ],
  faculty: [
    { id: "F1", name: "Asha Rao", department: "CS", unavailable: [], avoid: [], max_lectures_per_day: 6 },
    { id: "F2", name: "Ravi Iyer", department: "CS", unavailable: [], avoid: [], max_lectures_per_day: 6 },
  ],
  batches: [
    { id: "S", program: "BSc", level: "UG", semester: 1, section: "A", department: "CS", strength: 60, group_of: null },
    { id: "S-A", program: "BSc", level: "UG", semester: 1, section: "A", department: "CS", strength: 30, group_of: "S" },
    { id: "T", program: "BSc", level: "UG", semester: 3, section: "A", department: "CS", strength: 50, group_of: null },
  ],
  courses: [
    { code: "C1", name: "Maths", department: "CS", credits: 3, category: "DSC", room_kind: "classroom" },
    { code: "C2", name: "Physics Lab", department: "CS", credits: 2, category: "LAB", room_kind: "lab" },
  ],
  offerings: [
    { id: "O1", course_code: "C1", faculty_id: "F1", batch_ids: ["S"], sessions: [1, 1, 1] },
    { id: "O2", course_code: "C2", faculty_id: "F2", batch_ids: ["S-A"], sessions: [2] },
    { id: "O3", course_code: "C1", faculty_id: "F1", batch_ids: ["S", "T"], sessions: [1, 1] },
  ],
  weights: { repeat_course_day: 5, batch_gaps: 10, faculty_gaps: 3, peak_day_load: 4, avoid_slot: 6 },
  pins: [],
});

describe("parseTSV", () => {
  it("splits tabs and lines, handling Windows line endings and a trailing newline", () => {
    expect(parseTSV("a\tb\r\nc\td\r\n")).toEqual([["a", "b"], ["c", "d"]]);
    expect(parseTSV("a\tb\nc\td")).toEqual([["a", "b"], ["c", "d"]]);
  });
  it("keeps empty cells in place", () => {
    expect(parseTSV("a\t\tc\n\tb\t")).toEqual([["a", "", "c"], ["", "b", ""]]);
  });
  it("reads quoted cells, including embedded tabs, newlines and doubled quotes", () => {
    expect(parseTSV('"x\ty"\t"line1\nline2"\t"say ""hi"""')).toEqual([["x\ty", "line1\nline2", 'say "hi"']]);
  });
  it("drops blank trailing rows but not blank cells inside data", () => {
    expect(parseTSV("a\n\n\n")).toEqual([["a"]]);
    expect(parseTSV("")).toEqual([]);
  });
  it("a single value is one cell", () => {
    expect(parseTSV("hello")).toEqual([["hello"]]);
  });
});

describe("detectColumns", () => {
  it("recognises headings by key, label or alias, in any order, ignoring case and punctuation", () => {
    const m = detectColumns("rooms", ["Seats", "ID", "kind", "Name"])!;
    expect(m.map((c) => c?.key)).toEqual(["capacity", "id", "kind", "name"]);
    expect(detectColumns("offerings", ["id", "Course_Code", "Taught by", "BATCH IDS", "sessions"])!.map((c) => c?.key))
      .toEqual(["id", "course_code", "faculty_id", "batch_ids", "sessions"]);
  });
  it("marks unknown headings as null but still detects the header", () => {
    expect(detectColumns("rooms", ["id", "Floor", "seats"])!.map((c) => c?.key ?? null)).toEqual(["id", null, "capacity"]);
  });
  it("does not mistake a data row for a header", () => {
    expect(detectColumns("rooms", ["R9", "Seminar Hall", "150", "hall"])).toBeNull();
  });
  it("a data row that happens to contain one heading-like word is still data", () => {
    // a course literally called "Name": 1 of 6 cells matches a column, which is not enough to be a header
    expect(detectColumns("courses", ["C7", "Name", "CS", "3", "DSC", "classroom"])).toBeNull();
    expect(detectColumns("rooms", ["id", "floor", "extra", "more"])).toBeNull();
  });
  it("but half the cells matching is enough", () => {
    expect(detectColumns("rooms", ["id", "name", "floor", "extra"])!.map((c) => c?.key ?? null)).toEqual(["id", "name", null, null]);
    expect(detectColumns("rooms", ["name"])!.map((c) => c?.key)).toEqual(["name"]);
  });
  it("finds the hidden faculty slot columns by heading", () => {
    expect(detectColumns("faculty", ["id", "name", "unavailable", "avoid"])!.map((c) => c?.key)).toEqual(["id", "name", "unavailable", "avoid"]);
  });
});

describe("matching typed text to choices", () => {
  const inst = base();
  it("matches by value, label or name, ignoring case", () => {
    const fac = optionsFor(inst, "offerings", "faculty_id")!;
    expect(matchOption(fac, "f2")).toBe("F2");
    expect(matchOption(fac, "asha rao")).toBe("F1");
    expect(matchOption(fac, "Ravi Iyer (F2)")).toBe("F2");
    expect(matchOption(optionsFor(inst, "offerings", "course_code")!, "physics lab")).toBe("C2");
  });
  it("keeps unknown text as typed so validation can flag it", () => {
    expect(matchOption(optionsFor(inst, "offerings", "faculty_id")!, "  Nobody ")).toBe("Nobody");
  });
  it("only top-level classes are offered as parents", () => {
    expect(optionsFor(inst, "batches", "group_of")!.map((o) => o.value)).toEqual(["S", "T"]);
  });
  it("free-form columns have no options", () => {
    expect(optionsFor(inst, "rooms", "name")).toBeNull();
  });
});

describe("parseSlots", () => {
  const cal = base().calendar;
  it("reads Day:Lecture lists, counting lectures from 1", () => {
    expect(parseSlots("Mon:1, Wed:3", cal)).toEqual({ slots: [{ day: 0, lecture: 0 }, { day: 2, lecture: 2 }], bad: [] });
    expect(parseSlots("tue:2", cal).slots).toEqual([{ day: 1, lecture: 1 }]);
  });
  it("reports what it could not read", () => {
    expect(parseSlots("Someday:1, Mon:0, Mon:x, Tue:2", cal)).toEqual({ slots: [{ day: 1, lecture: 1 }], bad: ["Someday:1", "Mon:0", "Mon:x"] });
  });
});

describe("pasteGrid into existing cells", () => {
  it("fills across and down from a starting cell, overwriting what is there", () => {
    const out = pasteGrid(base(), "rooms", [["Hall A", "150"], ["Hall B", "90"]], { startRow: 0, startCol: 1 });
    expect(out.inst.rooms.map((r) => [r.id, r.name, r.capacity])).toEqual([["R1", "Hall A", 150], ["R2", "Hall B", 90]]);
    expect(out.problems).toEqual([]);
    expect([out.added, out.updated]).toEqual([0, 2]);
  });

  it("adds rows when the paste runs past the end of the table", () => {
    const out = pasteGrid(base(), "rooms", [["R2", "Lab X", "45", "lab"], ["R3", "New Hall", "100", "hall"], ["R4", "Another", "20", "classroom"]], { startRow: 1, startCol: 0 });
    expect(out.inst.rooms.map((r) => [r.id, r.name, r.capacity, r.kind])).toEqual([
      ["R1", "Room 1", 60, "classroom"], ["R2", "Lab X", 45, "lab"], ["R3", "New Hall", 100, "hall"], ["R4", "Another", 20, "classroom"],
    ]);
    expect([out.added, out.updated]).toEqual([2, 1]);
  });

  it("pasting past the last column simply ignores the extra cells", () => {
    const out = pasteGrid(base(), "rooms", [["x", "99", "lab", "ignored", "also ignored"]], { startRow: 0, startCol: 1 });
    expect(out.inst.rooms[0]).toMatchObject({ name: "x", capacity: 99, kind: "lab" });
  });

  it("changing an id by paste renames it everywhere it is used", () => {
    const out = pasteGrid(base(), "faculty", [["ASHA"]], { startRow: 0, startCol: 0 });
    expect(out.inst.faculty[0].id).toBe("ASHA");
    expect(out.inst.offerings.map((o) => o.faculty_id)).toEqual(["ASHA", "F2", "ASHA"]);
  });

  it("refuses an id that is already taken and says so", () => {
    const out = pasteGrid(base(), "faculty", [["F2"]], { startRow: 0, startCol: 0 });
    expect(out.inst.faculty.map((f) => f.id)).toEqual(["F1", "F2"]);
    expect(out.problems[0]).toMatch(/already used/);
  });

  it("understands numbers with thousands separators, and blanks as not-a-number so validation flags them", () => {
    const out = pasteGrid(base(), "rooms", [["1,200"], [""]], { startRow: 0, startCol: 2 });
    expect(out.inst.rooms[0].capacity).toBe(1200);
    expect(Number.isNaN(out.inst.rooms[1].capacity)).toBe(true);
  });

  it("matches teachers, courses and classes by name, and keeps unknown text so it is flagged", () => {
    const out = pasteGrid(base(), "offerings", [["physics lab", "ravi iyer", "S, t, Ghost", "1, 2"]], { startRow: 0, startCol: 1 });
    expect(out.inst.offerings[0]).toMatchObject({ course_code: "C2", faculty_id: "F2", batch_ids: ["S", "T", "Ghost"], sessions: [1, 2] });
  });

  it("leaves a cell alone and reports it when it can't be read", () => {
    const out = pasteGrid(base(), "offerings", [["C1", "F1", "S", "three"]], { startRow: 0, startCol: 1 });
    expect(out.inst.offerings[0].sessions).toEqual([1, 1, 1]);
    expect(out.problems[0]).toMatch(/could not read "three"/);
  });

  it("an empty parent cell makes a class stand-alone", () => {
    const out = pasteGrid(base(), "batches", [[""]], { startRow: 1, startCol: 7 });
    expect(out.inst.batches[1].group_of).toBeNull();
  });

  it("does not modify its input", () => {
    const inst = base();
    const frozen = JSON.stringify(inst);
    pasteGrid(inst, "rooms", [["a", "1"]], { startRow: 0, startCol: 1 });
    pasteGrid(inst, "offerings", [["x"]], {});
    expect(JSON.stringify(inst)).toBe(frozen);
  });
});

describe("pasteGrid adding rows (with headings)", () => {
  it("maps columns by heading, whatever their order", () => {
    const grid = [["kind", "Seats", "id", "name"], ["hall", "200", "H1", "Big Hall"], ["lab", "30", "L9", "Lab 9"]];
    const cols = detectColumns("rooms", grid[0])!;
    const out = pasteGrid(base(), "rooms", grid.slice(1), { columns: cols });
    expect(out.inst.rooms.slice(2)).toEqual([
      { id: "H1", name: "Big Hall", capacity: 200, kind: "hall" },
      { id: "L9", name: "Lab 9", capacity: 30, kind: "lab" },
    ]);
    expect([out.added, out.updated]).toEqual([2, 0]);
  });

  it("updates a row whose id already exists when upserting, and adds the rest", () => {
    const cols = detectColumns("rooms", ["id", "seats"])!;
    const out = pasteGrid(base(), "rooms", [["R1", "75"], ["R9", "20"]], { columns: cols, upsert: true });
    expect(out.inst.rooms.find((r) => r.id === "R1")!.capacity).toBe(75);
    expect(out.inst.rooms.find((r) => r.id === "R9")!.capacity).toBe(20);
    expect(out.inst.rooms).toHaveLength(3);
    expect([out.added, out.updated]).toEqual([1, 1]);
  });

  it("without upsert the same id is treated as new and gets a different id, with a note", () => {
    const cols = detectColumns("rooms", ["id", "seats"])!;
    const out = pasteGrid(base(), "rooms", [["R1", "75"]], { columns: cols });
    expect(out.inst.rooms).toHaveLength(3);
    expect(new Set(out.inst.rooms.map((r) => r.id)).size).toBe(3);
    expect(out.problems[0]).toMatch(/already used/);
  });

  it("new offerings do not invent links: unfilled references stay blank so they are flagged", () => {
    const out = pasteGrid(base(), "offerings", [["Maths"]], { columns: detectColumns("offerings", ["Course"])! });
    expect(out.inst.offerings.at(-1)).toMatchObject({ course_code: "C1", faculty_id: "", batch_ids: [], sessions: [] });
  });

  it("without headings, plain paste fills the shown columns in order", () => {
    const out = pasteGrid(base(), "courses", [["C9", "Chemistry", "SC", "4", "DSC", "lab"]]);
    expect(out.inst.courses.at(-1)).toEqual({ code: "C9", name: "Chemistry", department: "SC", credits: 4, category: "DSC", room_kind: "lab" });
  });

  it("faculty slots can be pasted by heading, using day names", () => {
    const cols = detectColumns("faculty", ["id", "unavailable", "avoid"])!;
    const out = pasteGrid(base(), "faculty", [["F1", "Mon:1, Tue:2", "Wed:5"]], { columns: cols, upsert: true });
    expect(out.inst.faculty[0].unavailable).toEqual([{ day: 0, lecture: 0 }, { day: 1, lecture: 1 }]);
    expect(out.inst.faculty[0].avoid).toEqual([{ day: 2, lecture: 4 }]);
  });

  it("a sheet exported by the app pastes back as the same data", () => {
    const inst = base();
    const header = COLUMNS.batches.map((c) => c.key);
    const grid = inst.batches.map((b) => header.map((k) => String((b as unknown as Record<string, unknown>)[k] ?? "")));
    const out = pasteGrid(inst, "batches", grid, { columns: detectColumns("batches", header)!, upsert: true });
    expect(out.inst.batches).toEqual(inst.batches);
    expect([out.added, out.updated]).toEqual([0, 3]);
  });
});

describe("bulk operations", () => {
  it("sets one column on every selected row and no others", () => {
    const out = bulkSet(base(), "faculty", ["F1", "F2"], "department", "Maths");
    expect(out.inst.faculty.map((f) => f.department)).toEqual(["Maths", "Maths"]);
    expect(out.updated).toBe(2);
    const some = bulkSet(base(), "rooms", ["R2"], "capacity", "55");
    expect(some.inst.rooms.map((r) => r.capacity)).toEqual([60, 55]);
  });
  it("understands choices by name and refuses text it cannot read", () => {
    expect(bulkSet(base(), "offerings", ["O1", "O3"], "faculty_id", "ravi iyer").inst.offerings.map((o) => o.faculty_id)).toEqual(["F2", "F2", "F2"]);
    const bad = bulkSet(base(), "offerings", ["O1"], "sessions", "lots");
    expect(bad.problems).toHaveLength(1);
    expect(bad.inst).toEqual(base());
  });
  it("won't set an id in bulk", () => {
    expect(bulkSet(base(), "rooms", ["R1"], "id", "X").problems[0]).toMatch(/can't be set in bulk/);
  });

  it("duplicates rows with fresh ids and a (copy) name", () => {
    const { inst, newIds } = duplicateRows(base(), "rooms", ["R1", "R2"]);
    expect(newIds).toEqual(["R1-copy", "R2-copy"]);
    expect(inst.rooms.slice(2).map((r) => [r.id, r.name, r.capacity])).toEqual([["R1-copy", "Room 1 (copy)", 60], ["R2-copy", "Lab 1 (copy)", 40]]);
    expect(new Set(duplicateRows(inst, "rooms", ["R1"]).inst.rooms.map((r) => r.id)).size).toBe(5);
  });
  it("a second duplicate of the same row still gets a unique id", () => {
    const once = duplicateRows(base(), "rooms", ["R1"]).inst;
    const twice = duplicateRows(once, "rooms", ["R1"]);
    expect(twice.newIds).toEqual(["R1-copy2"]);
  });
  it("duplicating a course keeps its code unique too", () => {
    expect(duplicateRows(base(), "courses", ["C1"]).inst.courses.map((c) => c.code)).toEqual(["C1", "C2", "C1-copy"]);
  });
});

describe("removing several rows", () => {
  it("removes them all and cleans up what depended on them", () => {
    const out = removeRows(base(), "faculty", ["F1", "F2"]);
    expect(out.faculty).toEqual([]);
    expect(out.offerings).toEqual([]);
  });
  it("summarises the effect without changing anything", () => {
    const inst = base();
    const frozen = JSON.stringify(inst);
    expect(summariseRemoval(inst, "batches", ["S"])).toEqual({ offeringsRemoved: ["O1"], offeringsEdited: ["O3"], subgroupsFreed: ["S-A"] });
    expect(summariseRemoval(inst, "faculty", ["F1"])).toEqual({ offeringsRemoved: ["O1", "O3"], offeringsEdited: [], subgroupsFreed: [] });
    expect(JSON.stringify(inst)).toBe(frozen);
  });
  it("deleting a class together with its sub-groups does not claim the groups are freed", () => {
    expect(summariseRemoval(base(), "batches", ["S", "S-A"]).subgroupsFreed).toEqual([]);
  });
  it("a shared offering disappears when every one of its classes is deleted", () => {
    expect(summariseRemoval(base(), "batches", ["S", "T"]).offeringsRemoved).toContain("O3");
  });
  it("ignores ids that are not there", () => {
    expect(removeRows(base(), "rooms", ["nope"]).rooms).toHaveLength(2);
  });
});

describe("filtering", () => {
  const inst = base();
  it("matches any column, ignoring case", () => {
    expect(inst.rooms.filter((r) => matchesFilter(r, "lab")).map((r) => r.id)).toEqual(["R2"]);
    expect(inst.faculty.filter((f) => matchesFilter(f, "RAVI")).map((f) => f.id)).toEqual(["F2"]);
    expect(inst.offerings.filter((o) => matchesFilter(o, "T")).length).toBeGreaterThan(0);
  });
  it("searches inside lists, and an empty filter matches everything", () => {
    expect(inst.offerings.filter((o) => matchesFilter(o, "S-A")).map((o) => o.id)).toEqual(["O2"]);
    expect(inst.rooms.every((r) => matchesFilter(r, ""))).toBe(true);
  });
});

describe("column definitions", () => {
  it("every table has exactly one id column, and shown columns exclude the hidden ones", () => {
    for (const table of ["rooms", "faculty", "batches", "courses", "offerings"] as const) {
      expect(COLUMNS[table].filter((c) => c.kind === "id")).toHaveLength(1);
      expect(shownColumns(table).every((c) => c.shown !== false)).toBe(true);
    }
    expect(shownColumns("faculty").map((c) => c.key)).toEqual(["id", "name", "department", "max_lectures_per_day"]);
  });
});
