// Undo / redo for the data editor.
//
// Typing in one cell would otherwise create one undo step per keystroke. Edits that carry the same
// `key` (for example "rooms.2.name") within a short time are folded into a single step, so one
// Undo reverts the whole word. Structural changes (add, delete, paste, bulk edit) pass no key and
// are always their own step.

export interface History<T> {
  past: T[];
  present: T;
  future: T[];
  lastKey: string | null;
  lastAt: number;
}

export const MAX_STEPS = 100;
export const COALESCE_MS = 1200;

export const initHistory = <T>(value: T): History<T> => ({ past: [], present: value, future: [], lastKey: null, lastAt: 0 });

export function pushHistory<T>(h: History<T>, next: T, key?: string, now: number = Date.now()): History<T> {
  if (JSON.stringify(next) === JSON.stringify(h.present)) return h; // nothing actually changed
  if (key !== undefined && key === h.lastKey && now - h.lastAt < COALESCE_MS && h.past.length > 0) {
    return { ...h, present: next, future: [], lastAt: now }; // same cell, moments ago: one step
  }
  const past = [...h.past, h.present];
  if (past.length > MAX_STEPS) past.shift();
  return { past, present: next, future: [], lastKey: key ?? null, lastAt: now };
}

export const canUndo = <T>(h: History<T>) => h.past.length > 0;
export const canRedo = <T>(h: History<T>) => h.future.length > 0;

export function undo<T>(h: History<T>): History<T> {
  if (!canUndo(h)) return h;
  return { past: h.past.slice(0, -1), present: h.past[h.past.length - 1], future: [h.present, ...h.future], lastKey: null, lastAt: 0 };
}

export function redo<T>(h: History<T>): History<T> {
  if (!canRedo(h)) return h;
  return { past: [...h.past, h.present], present: h.future[0], future: h.future.slice(1), lastKey: null, lastAt: 0 };
}
