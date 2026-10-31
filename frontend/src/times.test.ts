import { describe, expect, it } from "vitest";
import { ago, fillTimes, inOrder, pretty, rangeText, resizeTimes, timeLabel, timesOn, withLectureCount } from "./times";
import type { Calendar } from "./types";

const cal = (extra: Partial<Calendar> = {}): Calendar =>
  ({ day_names: ["Mon", "Tue", "Wed"], lectures_per_day: 4, break_after: [2], ...extra });

const FOUR = [
  { start: "08:00", end: "08:50" }, { start: "08:55", end: "09:45" },
  { start: "09:50", end: "10:40" }, { start: "11:00", end: "11:50" },
];

describe("times", () => {
  it("shows clock times without a leading zero", () => {
    expect(pretty("09:30")).toBe("9:30");
    expect(pretty("14:05")).toBe("14:05");
  });

  it("labels a block from its first start to its last end, or says nothing without times", () => {
    expect(timeLabel(cal({ times: FOUR }), 0, 0)).toBe("8:00\u20138:50");
    expect(timeLabel(cal({ times: FOUR }), 0, 1, 2)).toBe("8:55\u201310:40");
    expect(timeLabel(cal(), 0, 0)).toBeNull();
    expect(timeLabel(cal({ times: FOUR }), 0, 3, 2)).toBeNull(); // runs off the day
  });

  it("uses a day's own times when it has them", () => {
    const short = FOUR.map((t) => ({ ...t, end: t.end.replace(":50", ":40").replace(":45", ":35") }));
    const c = cal({ times: FOUR, day_times: { 2: short } });
    expect(timesOn(c, 2)).toBe(short);
    expect(timesOn(c, 0)).toBe(FOUR);
    expect(timeLabel(c, 2, 0)).toBe("8:00\u20138:40");
  });

  it("fills a day from a start, a length and the gaps, with a longer break where chosen", () => {
    const rows = fillTimes({ count: 4, start: "08:00", minutes: 50, gap: 5, breakAfter: [2], breakMinutes: 20 });
    expect(rows.map((r) => `${r.start}-${r.end}`)).toEqual(["08:00-08:50", "08:55-09:45", "09:50-10:40", "11:00-11:50"]);
    const noBreak = fillTimes({ count: 3, start: "09:30", minutes: 60, gap: 0, breakAfter: [], breakMinutes: 0 });
    expect(noBreak.map((r) => r.end)).toEqual(["10:30", "11:30", "12:30"]);
  });

  it("checks order", () => {
    expect(inOrder(FOUR)).toBe(true);
    expect(inOrder([{ start: "09:00", end: "08:00" }])).toBe(false);
    expect(inOrder([{ start: "08:00", end: "09:00" }, { start: "08:30", end: "09:30" }])).toBe(false);
  });

  it("keeps times consistent when the number of lectures changes", () => {
    expect(resizeTimes(FOUR, 2)).toEqual(FOUR.slice(0, 2));
    const longer = resizeTimes(FOUR, 6);
    expect(longer).toHaveLength(6);
    expect(longer[4]).toEqual({ start: "11:55", end: "12:45" }); // same length, same gap as the last
    expect(inOrder(longer)).toBe(true);
    expect(resizeTimes([], 5)).toEqual([]); // no times stays no times
    const c = withLectureCount(cal({ times: FOUR, day_times: { 1: FOUR } }), 6);
    expect(c.lectures_per_day).toBe(6);
    expect(c.times).toHaveLength(6);
    expect(c.day_times?.[1]).toHaveLength(6);
  });
});

import { clockOfBatch, clockOfOffering, removeClock, renameClock, sideTimes } from "./times";
import type { Institution } from "./types";

describe("clocks", () => {
  const SHORT = FOUR.map((t) => ({ ...t, end: t.end.slice(0, 3) + "40" }));
  const inst = (): Institution => ({
    name: "T", calendar: cal({ times: FOUR, clocks: { "40-minute": SHORT } }), rooms: [], faculty: [], courses: [], offerings: [],
    batches: [
      { id: "A", program: "P", level: "UG", semester: 1, section: "A", department: "D", strength: 30, group_of: null, clock: "40-minute" },
      { id: "A-1", program: "P", level: "UG", semester: 1, section: "1", department: "D", strength: 15, group_of: "A", clock: null },
      { id: "B", program: "P", level: "UG", semester: 1, section: "B", department: "D", strength: 30, group_of: null, clock: null },
    ],
    weights: { repeat_course_day: 1, batch_gaps: 1, faculty_gaps: 1, peak_day_load: 1, avoid_slot: 1, room_changes: 0, odd_hours: 0, day_spacing: 0 }, pins: [],
  });

  it("uses a class's clock, then the default; a special day wins for everyone", () => {
    const i = inst();
    expect(timeLabel(i.calendar, 0, 0, 1, "40-minute")).toBe("8:00\u20138:40");
    expect(timeLabel(i.calendar, 0, 0, 1, null)).toBe("8:00\u20138:50");
    expect(timeLabel(i.calendar, 0, 0, 1, "ghost")).toBe("8:00\u20138:50");
    const special = { ...i.calendar, day_times: { 2: FOUR.map((t) => ({ ...t, end: t.end.slice(0, 3) + "30" })) } };
    expect(timeLabel(special, 2, 0, 1, "40-minute")).toBe("8:00\u20138:30");
    expect(sideTimes(i.calendar, "40-minute")).toBe(SHORT);
    expect(sideTimes(i.calendar, null)).toBe(FOUR);
  });

  it("a practical group keeps its parent's clock, and an offering its first class's", () => {
    const i = inst();
    expect(clockOfBatch(i, "A-1")).toBe("40-minute");
    expect(clockOfBatch(i, "B")).toBeNull();
    expect(clockOfBatch(i, "nobody")).toBeNull();
    expect(clockOfOffering(i, { batch_ids: ["A", "B"] })).toBe("40-minute");
  });

  it("renaming a clock moves its classes; removing it sends them back to the default", () => {
    const renamed = renameClock(inst(), "40-minute", "short");
    expect(Object.keys(renamed.calendar.clocks!)).toEqual(["short"]);
    expect(renamed.batches.find((b) => b.id === "A")!.clock).toBe("short");
    expect(renameClock(inst(), "40-minute", "40-minute")).toEqual(inst());
    expect(renameClock(inst(), "40-minute", "  ")).toEqual(inst());
    const removed = removeClock(inst(), "40-minute");
    expect(removed.calendar.clocks).toEqual({});
    expect(removed.batches.find((b) => b.id === "A")!.clock).toBeNull();
  });

  it("changing the number of lectures keeps every clock in step", () => {
    const c = withLectureCount(inst().calendar, 6);
    expect(c.clocks!["40-minute"]).toHaveLength(6);
    expect(inOrder(c.clocks!["40-minute"])).toBe(true);
  });
});

describe("rangeText", () => {
  it("shows one day once, a run as a range, and the name when there is one", () => {
    const day = new Date("2026-11-16T00:00:00").toLocaleDateString(undefined, { day: "numeric", month: "short" });
    const end = new Date("2026-11-22T00:00:00").toLocaleDateString(undefined, { day: "numeric", month: "short" });
    expect(rangeText({ start: "2026-11-16", end: "2026-11-16", label: "" })).toBe(day);
    expect(rangeText({ start: "2026-11-16", end: "2026-11-22", label: "Diwali" })).toBe(`${day} – ${end} · Diwali`);
  });
});

describe("ago", () => {
  const now = new Date("2026-11-01T12:00:00Z").getTime();
  const at = (secondsBefore: number) => new Date(now - secondsBefore * 1000).toISOString();
  it("speaks in the largest sensible unit", () => {
    expect(ago(at(5), now)).toBe("just now");
    expect(ago(at(60), now)).toBe("1 minute ago");
    expect(ago(at(5 * 60 + 30), now)).toBe("5 minutes ago");
    expect(ago(at(3600), now)).toBe("1 hour ago");
    expect(ago(at(23 * 3600), now)).toBe("23 hours ago");
    expect(ago(at(2 * 86400), now)).toBe("2 days ago");
    expect(ago(at(29 * 86400), now)).toBe("29 days ago");
    expect(ago(at(45 * 86400), now)).toMatch(/^on /);
  });
  it("never goes negative when the clocks differ a little", () => {
    expect(ago(new Date(now + 5000).toISOString(), now)).toBe("just now");
  });
});
