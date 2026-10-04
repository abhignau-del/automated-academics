// Pure helpers for displaying and editing a timetable. The server's validator
// remains the source of truth for clashes; nothing here decides what is legal
// beyond "does this block fit inside the day and avoid breaks".

import type { Calendar, Institution, Placement, ViewKind } from "./types";

export const sessionKey = (p: Pick<Placement, "offering_id" | "session_index">) =>
  `${p.offering_id}#${p.session_index}`;

export function blockFits(cal: Calendar, start: number, length: number): boolean {
  const end = start + length - 1;
  if (start < 0 || end >= cal.lectures_per_day) return false;
  return !cal.break_after.some((b) => start <= b && b < end);
}

/** Batches blocked when `ids` attend: themselves plus their practical sub-groups. */
export function occupiedBatches(inst: Institution, ids: string[]): Set<string> {
  const out = new Set(ids);
  for (const b of inst.batches) if (b.group_of && out.has(b.group_of)) out.add(b.id);
  return out;
}

/** Placements shown in the week view of one batch, faculty member or room. */
export function placementsFor(
  inst: Institution, placements: Placement[], kind: ViewKind, id: string,
): Placement[] {
  const offerings = new Map(inst.offerings.map((o) => [o.id, o]));
  const batches = new Map(inst.batches.map((b) => [b.id, b]));
  return placements.filter((p) => {
    const o = offerings.get(p.offering_id);
    if (!o) return false;
    if (kind === "faculty") return o.faculty_id === id;
    if (kind === "room") return p.room_id === id;
    // a section also shows its sub-groups' labs; a sub-group also shows its parent's sessions
    return occupiedBatches(inst, o.batch_ids).has(id) || o.batch_ids.some((b) => batches.get(b)?.group_of === id);
  });
}

/** Returns a new list with one session moved, or null if the block cannot fit there. */
export function moveSession(
  cal: Calendar, placements: Placement[], key: string, day: number, start: number,
): Placement[] | null {
  const target = placements.find((p) => sessionKey(p) === key);
  if (!target) return null;
  if (day < 0 || day >= cal.day_names.length || !blockFits(cal, start, target.length)) return null;
  if (target.day === day && target.start === start) return null;
  return placements.map((p) => (p === target ? { ...p, day, start } : p));
}

export function changeRoom(placements: Placement[], key: string, roomId: string): Placement[] {
  return placements.map((p) => (sessionKey(p) === key ? { ...p, room_id: roomId } : p));
}

/** Side-by-side lanes for sessions that overlap in time on the same day (e.g. parallel lab groups). */
export function assignLanes(items: Placement[]): Map<string, { lane: number; lanes: number }> {
  const out = new Map<string, { lane: number; lanes: number }>();
  const byDay = new Map<number, Placement[]>();
  for (const p of items) byDay.set(p.day, [...(byDay.get(p.day) ?? []), p]);

  for (const day of byDay.values()) {
    day.sort((a, b) => a.start - b.start || b.length - a.length);
    // cluster = run of transitively overlapping sessions; lanes are shared within a cluster
    let cluster: Placement[] = [];
    let clusterEnd = -1;
    const flush = () => {
      const laneEnds: number[] = [];
      const assigned = new Map<string, number>();
      for (const p of cluster) {
        let lane = laneEnds.findIndex((end) => end <= p.start);
        if (lane === -1) { lane = laneEnds.length; laneEnds.push(0); }
        laneEnds[lane] = p.start + p.length;
        assigned.set(sessionKey(p), lane);
      }
      for (const p of cluster) out.set(sessionKey(p), { lane: assigned.get(sessionKey(p))!, lanes: laneEnds.length });
      cluster = [];
      clusterEnd = -1;
    };
    for (const p of day) {
      if (cluster.length && p.start >= clusterEnd) flush();
      cluster.push(p);
      clusterEnd = Math.max(clusterEnd, p.start + p.length);
    }
    if (cluster.length) flush();
  }
  return out;
}

/** Stable pastel colour per course code. */
export function courseColour(code: string): string {
  let h = 0;
  for (const ch of code) h = (h * 31 + ch.charCodeAt(0)) % 360;
  return `hsl(${h} 70% 88%)`;
}
