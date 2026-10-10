import { describe, expect, it } from "vitest";
import { fillTimes, inOrder, pretty, resizeTimes, timeLabel, timesOn, withLectureCount } from "./times";
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
