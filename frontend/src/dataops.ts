// Pure operations for editing institution data. Every function returns a new object and leaves its
// input alone, so they are easy to test and safe to use with React state.

import type { Faculty, Institution, Offering, Slot } from "./types";

export type TableKey = "rooms" | "faculty" | "batches" | "courses" | "offerings";

/** The field that identifies a row in each table. */
export const ID_FIELD = { rooms: "id", faculty: "id", batches: "id", courses: "code", offerings: "id" } as const;

export const idOf = (inst: Institution, table: TableKey, index: number): string =>
  (inst[table][index] as unknown as Record<string, string>)[ID_FIELD[table]];

/** `prefix` + the smallest number (starting after the current count) that is not already taken. */
export function uniqueId(existing: string[], prefix: string): string {
  const taken = new Set(existing);
  let n = existing.length + 1;
  while (taken.has(`${prefix}${n}`)) n++;
  return `${prefix}${n}`;
}

// ---------------------------------------------------------------- adding

export function addRow(inst: Institution, table: TableKey): Institution {
  switch (table) {
    case "rooms": {
      const id = uniqueId(inst.rooms.map((r) => r.id), "R");
      return { ...inst, rooms: [...inst.rooms, { id, name: `Room ${id}`, capacity: 60, kind: "classroom" }] };
    }
    case "faculty": {
      const id = uniqueId(inst.faculty.map((f) => f.id), "F");
      const dept = inst.faculty.at(-1)?.department ?? "";
      const row: Faculty = { id, name: `Faculty ${id}`, department: dept, unavailable: [], avoid: [], max_lectures_per_day: 6 };
      return { ...inst, faculty: [...inst.faculty, row] };
    }
    case "batches": {
      const id = uniqueId(inst.batches.map((b) => b.id), "B");
      const last = inst.batches.at(-1);
      return {
        ...inst,
        batches: [...inst.batches, {
          id, program: last?.program ?? "", level: last?.level ?? "UG", semester: last?.semester ?? 1,
          section: "A", department: last?.department ?? "", strength: 60, group_of: null,
        }],
      };
    }
    case "courses": {
      const code = uniqueId(inst.courses.map((c) => c.code), "C");
      const last = inst.courses.at(-1);
      return {
        ...inst,
        courses: [...inst.courses, {
          code, name: "", department: last?.department ?? "", credits: 3, category: "DSC", room_kind: "classroom",
        }],
      };
    }
    case "offerings": {
      const id = uniqueId(inst.offerings.map((o) => o.id), "O");
      const row: Offering = {
        id, course_code: inst.courses[0]?.code ?? "", faculty_id: inst.faculty[0]?.id ?? "",
        batch_ids: inst.batches[0] ? [inst.batches[0].id] : [], sessions: [1, 1, 1],
      };
      return { ...inst, offerings: [...inst.offerings, row] };
    }
  }
}

// ---------------------------------------------------------------- renaming

/** Change a row's id and update everything that refers to it. */
export function renameId(inst: Institution, table: TableKey, oldId: string, newId: string): Institution {
  if (oldId === newId) return inst;
  const swap = (v: string) => (v === oldId ? newId : v);
  const field = ID_FIELD[table];
  const renamed = {
    ...inst,
    [table]: (inst[table] as unknown as Record<string, unknown>[]).map((r) =>
      r[field] === oldId ? { ...r, [field]: newId } : r),
  } as Institution;

  if (table === "faculty") {
    return { ...renamed, offerings: renamed.offerings.map((o) => ({ ...o, faculty_id: swap(o.faculty_id) })) };
  }
  if (table === "courses") {
    return { ...renamed, offerings: renamed.offerings.map((o) => ({ ...o, course_code: swap(o.course_code) })) };
  }
  if (table === "batches") {
    return {
      ...renamed,
      batches: renamed.batches.map((b) => (b.group_of === oldId ? { ...b, group_of: newId } : b)),
      offerings: renamed.offerings.map((o) => ({ ...o, batch_ids: o.batch_ids.map(swap) })),
    };
  }
  return renamed;
}

// ---------------------------------------------------------------- removing

export interface Impact {
  /** offerings that would be deleted because they can no longer exist */
  offeringsRemoved: string[];
  /** offerings that stay but lose this batch */
  offeringsEdited: string[];
  /** batches that stop being sub-groups */
  subgroups: string[];
}

/** What deleting a row would take with it. */
export function impact(inst: Institution, table: TableKey, id: string): Impact {
  const none: Impact = { offeringsRemoved: [], offeringsEdited: [], subgroups: [] };
  if (table === "faculty") return { ...none, offeringsRemoved: inst.offerings.filter((o) => o.faculty_id === id).map((o) => o.id) };
  if (table === "courses") return { ...none, offeringsRemoved: inst.offerings.filter((o) => o.course_code === id).map((o) => o.id) };
  if (table === "batches") {
    const using = inst.offerings.filter((o) => o.batch_ids.includes(id));
    return {
      offeringsRemoved: using.filter((o) => o.batch_ids.length === 1).map((o) => o.id),
      offeringsEdited: using.filter((o) => o.batch_ids.length > 1).map((o) => o.id),
      subgroups: inst.batches.filter((b) => b.group_of === id).map((b) => b.id),
    };
  }
  return none;
}

export const hasImpact = (i: Impact) => i.offeringsRemoved.length + i.offeringsEdited.length + i.subgroups.length > 0;

/** Delete a row, cleaning up anything that depended on it (see `impact`). */
export function removeRow(inst: Institution, table: TableKey, index: number): Institution {
  const id = idOf(inst, table, index);
  const gone = impact(inst, table, id);
  const without = {
    ...inst,
    [table]: (inst[table] as unknown[]).filter((_, i) => i !== index),
  } as Institution;

  const drop = new Set(gone.offeringsRemoved);
  let out: Institution = { ...without, offerings: without.offerings.filter((o) => !drop.has(o.id)) };
  if (table === "batches") {
    out = {
      ...out,
      batches: out.batches.map((b) => (b.group_of === id ? { ...b, group_of: null } : b)),
      offerings: out.offerings.map((o) => (o.batch_ids.includes(id) ? { ...o, batch_ids: o.batch_ids.filter((b) => b !== id) } : o)),
    };
  }
  return out;
}

// ---------------------------------------------------------------- sessions text

/** "1, 1, 1" or "2" -> [1, 1, 1] / [2]; null if empty or not whole numbers of at least 1. */
export function parseSessions(text: string): number[] | null {
  const parts = text.split(/[,;\s]+/).filter(Boolean);
  if (!parts.length) return null;
  const nums = parts.map((p) => (/^\d+$/.test(p) ? Number(p) : NaN));
  return nums.every((n) => n >= 1) ? nums : null;
}

export const formatSessions = (s: number[]) => s.join(", ");

// ---------------------------------------------------------------- availability

export type SlotState = "free" | "avoid" | "unavailable";

const has = (slots: Slot[], day: number, lecture: number) => slots.some((s) => s.day === day && s.lecture === lecture);
const without = (slots: Slot[], day: number, lecture: number) => slots.filter((s) => !(s.day === day && s.lecture === lecture));

export function slotState(f: Faculty, day: number, lecture: number): SlotState {
  if (has(f.unavailable, day, lecture)) return "unavailable";
  if (has(f.avoid, day, lecture)) return "avoid";
  return "free";
}

/** free -> avoid -> unavailable -> free, so one click target covers all three states. */
export function cycleSlot(f: Faculty, day: number, lecture: number): Faculty {
  const next = ({ free: "avoid", avoid: "unavailable", unavailable: "free" } as const)[slotState(f, day, lecture)];
  const slot = { day, lecture };
  const unavailable = without(f.unavailable, day, lecture);
  const avoid = without(f.avoid, day, lecture);
  return {
    ...f,
    unavailable: next === "unavailable" ? [...unavailable, slot] : unavailable,
    avoid: next === "avoid" ? [...avoid, slot] : avoid,
  };
}

/** Set a whole row or column of a teacher's week to one state (used by the day / lecture headers). */
export function setSlots(f: Faculty, cells: Slot[], state: SlotState): Faculty {
  let out = f;
  for (const c of cells) {
    for (let guard = 0; guard < 3 && slotState(out, c.day, c.lecture) !== state; guard++) {
      out = cycleSlot(out, c.day, c.lecture);
    }
  }
  return out;
}
