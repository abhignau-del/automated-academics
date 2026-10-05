// Mirrors backend/src/automated_academics/models.py. Lectures and days are zero-based.

export type RoomKind = "classroom" | "lab" | "hall";

export interface Calendar { day_names: string[]; lectures_per_day: number; break_after: number[] }
export interface Room { id: string; name: string; capacity: number; kind: RoomKind }
export interface Slot { day: number; lecture: number }
export interface Faculty {
  id: string; name: string; department: string;
  unavailable: Slot[]; // hard: never scheduled here
  avoid: Slot[]; // soft: scheduled here only if there is no better way
  max_lectures_per_day: number;
}
export interface Batch {
  id: string; program: string; level: "UG" | "PG"; semester: number; section: string;
  department: string; strength: number; group_of: string | null;
}
export interface Course {
  code: string; name: string; department: string; credits: number; category: string; room_kind: RoomKind;
}
export interface Offering { id: string; course_code: string; faculty_id: string; batch_ids: string[]; sessions: number[] }

/** Importance of each soft goal (0 switches it off). Names match `Quality`. */
export interface Weights {
  repeat_course_day: number; batch_gaps: number; faculty_gaps: number; peak_day_load: number; avoid_slot: number;
}

/** A session fixed in place: the solver keeps it here and plans everything else around it. */
export interface Pin { offering_id: string; session_index: number; day: number; start: number; room_id: string | null }

export interface Institution {
  name: string; calendar: Calendar; rooms: Room[]; faculty: Faculty[];
  batches: Batch[]; courses: Course[]; offerings: Offering[]; weights: Weights; pins: Pin[];
}

export interface Placement {
  offering_id: string; session_index: number; day: number; start: number; length: number; room_id: string;
}
export interface Timetable {
  placements: Placement[]; status: string; penalty: number;
  breakdown: Partial<Quality>; // soft-goal measures, filled in by the solver
}

export interface InstitutionSummary { id: string; name: string; created_at: string; updated_at: string }
export interface Job {
  id: string; institution_id: string; status: "queued" | "running" | "done" | "failed";
  error: string | null;
  stale?: boolean; // the institution's data was edited after this timetable was generated
}

export interface ConflictDetail { message: string; offering_ids: string[] }
/** Soft-goal measures of a timetable. Lower is better; 0 is ideal. */
export interface Quality {
  repeat_course_day: number; batch_gaps: number; faculty_gaps: number; peak_day_load: number; avoid_slot: number;
}
export interface ConflictReport {
  ok: boolean; conflicts: string[]; details: ConflictDetail[];
  quality: Quality; penalty: number;
}

export interface UploadIssue { sheet: string; row: number | null; message: string }

export type ViewKind = "batch" | "faculty" | "room";

// ---- data entry ----

/** The parts of the data the editor shows as separate tabs. */
export type Section = "settings" | "rooms" | "faculty" | "batches" | "courses" | "offerings" | "pins";

/** A problem or note about entered data, located by section and row so the editor can highlight it. */
export interface DataIssue {
  level: "error" | "warning" | "info";
  section: Section | null;
  index: number | null; // zero-based row within the section
  field: string | null;
  message: string;
}
/** `valid` means the data can be saved; impossible-to-schedule data is an error issue but still valid. */
export interface CheckResult { valid: boolean; issues: DataIssue[] }
