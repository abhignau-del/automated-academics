// Clock times of the lectures ("9:30-10:30"). Optional: with none set the app just says L1, L2, ...

import type { Calendar, Institution, LectureTime, Offering } from "./types";

const toMinutes = (clock: string): number => {
  const [h, m] = clock.split(":").map(Number);
  return h * 60 + m;
};

const toClock = (minutes: number): string => {
  const h = Math.floor(minutes / 60) % 24;
  return `${String(h).padStart(2, "0")}:${String(minutes % 60).padStart(2, "0")}`;
};

/** "09:30" -> "9:30" */
export const pretty = (clock: string): string => {
  const [h, m] = clock.split(":");
  return `${Number(h)}:${m}`;
};

export const range = (t: LectureTime): string => `${pretty(t.start)}–${pretty(t.end)}`;

/**
 * The times that apply: a day that runs to its own clock, else the class's clock, else the institution's default.
 * (Same order as the server.)
 */
export const timesOn = (cal: Calendar, day: number, clock?: string | null): LectureTime[] =>
  cal.day_times?.[day] ?? (clock ? cal.clocks?.[clock] : undefined) ?? (cal.times ?? []);

/** The times written down the side of a grid: a class's clock, or the default. */
export const sideTimes = (cal: Calendar, clock?: string | null): LectureTime[] =>
  (clock ? cal.clocks?.[clock] : undefined) ?? (cal.times ?? []);

/** The clock a class keeps: its own, or its parent's for a practical group. */
export function clockOfBatch(inst: Institution, id: string | null | undefined): string | null {
  const b = inst.batches.find((x) => x.id === id);
  if (!b) return null;
  return b.clock ?? (b.group_of ? inst.batches.find((x) => x.id === b.group_of)?.clock ?? null : null);
}

/** An offering runs on the clock of its first class. */
export const clockOfOffering = (inst: Institution, o: Pick<Offering, "batch_ids">): string | null => clockOfBatch(inst, o.batch_ids[0]);

/** "9:30-11:30" for the block starting at lecture `start` on `day`, or null when no times are set. */
export function timeLabel(cal: Calendar, day: number, start: number, length = 1, clock?: string | null): string | null {
  const rows = timesOn(cal, day, clock);
  if (!rows.length || start < 0 || start + length > rows.length) return null;
  return `${pretty(rows[start].start)}–${pretty(rows[start + length - 1].end)}`;
}

export interface FillSpec {
  count: number; // lectures in the day
  start: string; // "HH:MM" of the first lecture
  minutes: number; // length of a lecture
  gap: number; // minutes between lectures
  breakAfter: number[]; // zero-based: a longer break follows these lectures
  breakMinutes: number; // how long that break is (instead of the gap)
}

/** Work out every lecture's times from a start time, a length and the gaps. */
export function fillTimes(spec: FillSpec): LectureTime[] {
  const out: LectureTime[] = [];
  let at = toMinutes(spec.start);
  for (let i = 0; i < spec.count; i++) {
    out.push({ start: toClock(at), end: toClock(at + spec.minutes) });
    at += spec.minutes + (spec.breakAfter.includes(i) ? spec.breakMinutes : spec.gap);
  }
  return out;
}

/** True if every start is at or after the previous end, and each lecture ends after it starts. */
export function inOrder(rows: LectureTime[]): boolean {
  return rows.every((r, i) => toMinutes(r.end) > toMinutes(r.start) && (i === 0 || toMinutes(r.start) >= toMinutes(rows[i - 1].end)));
}

/** Make a list of times `count` long: cut off the end, or carry on at the same length and gap. */
export function resizeTimes(rows: LectureTime[], count: number): LectureTime[] {
  if (rows.length === 0 || rows.length === count) return rows;
  if (rows.length > count) return rows.slice(0, count);
  const out = [...rows];
  const last = rows[rows.length - 1];
  const len = Math.max(1, toMinutes(last.end) - toMinutes(last.start));
  // the usual gap (the smallest), not the longer one at a break
  const gaps = rows.slice(1).map((r, i) => Math.max(0, toMinutes(r.start) - toMinutes(rows[i].end)));
  const gap = gaps.length ? Math.min(...gaps) : 5;
  let at = toMinutes(last.end) + gap;
  while (out.length < count) {
    out.push({ start: toClock(at), end: toClock(at + len) });
    at += len + gap;
  }
  return out;
}

/** The calendar with its lecture count changed, keeping the times consistent with it. */
export function withLectureCount(cal: Calendar, count: number): Calendar {
  const day_times: Record<number, LectureTime[]> = {};
  for (const [d, rows] of Object.entries(cal.day_times ?? {})) day_times[Number(d)] = resizeTimes(rows, count);
  const clocks: Record<string, LectureTime[]> = {};
  for (const [n, rows] of Object.entries(cal.clocks ?? {})) clocks[n] = resizeTimes(rows, count);
  return { ...cal, lectures_per_day: count, times: resizeTimes(cal.times ?? [], count), day_times, clocks };
}

/** Rename a clock, and move the classes that keep it. */
export function renameClock(inst: Institution, from: string, to: string): Institution {
  const clocks = inst.calendar.clocks ?? {};
  if (from === to || !(from in clocks) || to in clocks || !to.trim()) return inst;
  const renamed = Object.fromEntries(Object.entries(clocks).map(([n, rows]) => [n === from ? to : n, rows]));
  return {
    ...inst,
    calendar: { ...inst.calendar, clocks: renamed },
    batches: inst.batches.map((b) => (b.clock === from ? { ...b, clock: to } : b)),
  };
}

/** Remove a clock; the classes that kept it go back to the institution's own times. */
export function removeClock(inst: Institution, name: string): Institution {
  const { [name]: _gone, ...rest } = inst.calendar.clocks ?? {};
  return {
    ...inst,
    calendar: { ...inst.calendar, clocks: rest },
    batches: inst.batches.map((b) => (b.clock === name ? { ...b, clock: null } : b)),
  };
}

const dayText = (iso: string) => new Date(iso + "T00:00:00").toLocaleDateString(undefined, { day: "numeric", month: "short" });

/** A break such as "16 Nov – 22 Nov · Diwali" (a single day shows once). */
export const rangeText = (n: { start: string; end: string; label: string }) =>
  (n.start === n.end ? dayText(n.start) : `${dayText(n.start)} – ${dayText(n.end)}`) + (n.label ? ` · ${n.label}` : "");

/** "just now", "5 minutes ago", "3 hours ago", "2 days ago", or the date once it is over a month ago. */
export function ago(iso: string, now: number = Date.now()): string {
  const s = Math.max(0, (now - new Date(iso).getTime()) / 1000);
  const n = (v: number, unit: string) => `${v} ${unit}${v === 1 ? "" : "s"} ago`;
  if (s < 60) return "just now";
  if (s < 3600) return n(Math.floor(s / 60), "minute");
  if (s < 86400) return n(Math.floor(s / 3600), "hour");
  if (s < 30 * 86400) return n(Math.floor(s / 86400), "day");
  return "on " + new Date(iso).toLocaleDateString();
}
