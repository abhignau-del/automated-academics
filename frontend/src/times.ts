// Clock times of the lectures ("9:30-10:30"). Optional: with none set the app just says L1, L2, ...

import type { Calendar, LectureTime } from "./types";

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

/** The times a day runs to: its own, if it has any, else the default ones. */
export const timesOn = (cal: Calendar, day: number): LectureTime[] => cal.day_times?.[day] ?? (cal.times ?? []);

/** "9:30-11:30" for the block starting at lecture `start` on `day`, or null when no times are set. */
export function timeLabel(cal: Calendar, day: number, start: number, length = 1): string | null {
  const rows = timesOn(cal, day);
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
  return { ...cal, lectures_per_day: count, times: resizeTimes(cal.times ?? [], count), day_times };
}
