// Joint classes: one offering attended by several classes together (a shared elective, or the same subject
// taught once to two programmes). In the data that is simply an offering with more than one class.
// Pure helpers to find, combine and split them; the editor only calls these.

import { occupiedBatches } from "./timetable";
import type { Institution, Offering, Room } from "./types";

const strength = (inst: Institution, batchIds: string[]) => {
  const by = new Map(inst.batches.map((b) => [b.id, b.strength]));
  return batchIds.reduce((n, b) => n + (by.get(b) ?? 0), 0);
};

/** Same rule as the solver: a room can hold the offering if it is the right kind and big enough. */
function roomFits(room: Room, needed: string, students: number): boolean {
  if (room.capacity < students) return false;
  if (needed === "lab") return room.kind === "lab";
  if (needed === "hall") return room.kind === "hall";
  return room.kind === "classroom" || room.kind === "hall";
}

export const isJoint = (o: Offering) => o.batch_ids.length > 1;

export interface Suggestion {
  ids: string[]; // the offerings that would become one
  batchIds: string[];
  students: number;
  sessions: number[]; // what the combined offering would have
  sessionsDiffer: boolean; // the offerings had different weekly lectures; the busiest one is kept
  roomFits: boolean; // some room is big enough for everyone together
}

/** True if no class in the list is, or contains, another (a class and its own lab group can't be "joint"). */
function distinctClasses(inst: Institution, offerings: Offering[]): boolean {
  const seen = new Set<string>();
  for (const o of offerings) {
    for (const b of occupiedBatches(inst, o.batch_ids)) {
      if (seen.has(b)) return false;
      seen.add(b);
    }
  }
  return true;
}

const total = (s: number[]) => s.reduce((a, b) => a + b, 0);
const same = (a: number[], b: number[]) => a.length === b.length && a.every((x, i) => x === b[i]);

function merged(inst: Institution, group: Offering[]): Omit<Suggestion, "ids"> {
  const busiest = group.reduce((best, o) => (total(o.sessions) > total(best.sessions) ? o : best));
  const batchIds = [...new Set(group.flatMap((o) => o.batch_ids))];
  const students = strength(inst, batchIds);
  const course = inst.courses.find((c) => c.code === group[0].course_code);
  return {
    batchIds, students, sessions: [...busiest.sessions],
    sessionsDiffer: group.some((o) => !same(o.sessions, busiest.sessions)),
    roomFits: inst.rooms.some((r) => roomFits(r, course?.room_kind ?? "classroom", students)),
  };
}

/** Offerings of the same course by the same teacher, to different classes: probably meant to be taught together. */
export function suggestJoint(inst: Institution): Suggestion[] {
  const groups = new Map<string, Offering[]>();
  for (const o of inst.offerings) {
    const key = `${o.course_code}\u0000${o.faculty_id}`;
    groups.set(key, [...(groups.get(key) ?? []), o]);
  }
  const out: Suggestion[] = [];
  for (const g of groups.values()) {
    if (g.length < 2 || !distinctClasses(inst, g)) continue;
    out.push({ ids: g.map((o) => o.id), ...merged(inst, g) });
  }
  return out.sort((a, b) => b.students - a.students);
}

export type Result = { inst: Institution; kept: string; note: string | null } | { error: string };

/** Make the chosen offerings one joint offering. They must be the same course and teacher, for different classes. */
export function combine(inst: Institution, ids: string[]): Result {
  const group = ids.map((id) => inst.offerings.find((o) => o.id === id)).filter((o): o is Offering => !!o);
  if (group.length < 2) return { error: "Choose at least two offerings to combine." };
  if (new Set(group.map((o) => o.course_code)).size > 1) return { error: "Only offerings of the same course can be taught together." };
  if (new Set(group.map((o) => o.faculty_id)).size > 1) return { error: "Only offerings with the same teacher can be taught together." };
  if (!distinctClasses(inst, group)) return { error: "Some of these are the same class, or a class and its own lab group." };

  const m = merged(inst, group);
  const keep = group[0];
  const drop = new Set(group.slice(1).map((o) => o.id));
  const notes: string[] = [];
  if (m.sessionsDiffer) notes.push(`they had different weekly lectures, so the most (${m.sessions.join(", ")}) is kept`);
  if (!m.roomFits) notes.push(`no room holds all ${m.students} students at once`);
  return {
    kept: keep.id,
    note: notes.length ? `Combined, but ${notes.join(", and ")}.` : null,
    inst: {
      ...inst,
      offerings: inst.offerings
        .filter((o) => !drop.has(o.id))
        .map((o) => (o.id === keep.id ? { ...o, batch_ids: m.batchIds, sessions: m.sessions } : o)),
      // locked sessions of the offerings that vanished go with them; the kept offering's pins stay if still valid
      pins: inst.pins.filter((p) => !drop.has(p.offering_id) && (p.offering_id !== keep.id || p.session_index < m.sessions.length)),
    },
  };
}

/** Give each class of a joint offering its own offering again (the first keeps the original id). */
export function split(inst: Institution, id: string): Result {
  const o = inst.offerings.find((x) => x.id === id);
  if (!o || !isJoint(o)) return { error: "That offering is not shared by several classes." };
  const taken = new Set(inst.offerings.map((x) => x.id));
  const parts = o.batch_ids.slice(1).map((b) => {
    let nid = `${o.id}-${b}`;
    for (let n = 2; taken.has(nid); n++) nid = `${o.id}-${b}-${n}`;
    taken.add(nid);
    return { ...o, id: nid, batch_ids: [b] };
  });
  return {
    kept: o.id, note: null,
    inst: {
      ...inst,
      offerings: inst.offerings.flatMap((x) => (x.id === id ? [{ ...o, batch_ids: [o.batch_ids[0]] }, ...parts] : [x])),
    },
  };
}
