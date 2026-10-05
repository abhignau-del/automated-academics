// Mirrors backend/src/automated_academics/models.py. Lectures and days are zero-based.

export type RoomKind = "classroom" | "lab" | "hall";

export interface Calendar { day_names: string[]; lectures_per_day: number; break_after: number[] }
export interface Room { id: string; name: string; capacity: number; kind: RoomKind }
export interface Faculty {
  id: string; name: string; department: string;
  unavailable: { day: number; lecture: number }[]; max_lectures_per_day: number;
}
export interface Batch {
  id: string; program: string; level: "UG" | "PG"; semester: number; section: string;
  department: string; strength: number; group_of: string | null;
}
export interface Course {
  code: string; name: string; department: string; credits: number; category: string; room_kind: RoomKind;
}
export interface Offering { id: string; course_code: string; faculty_id: string; batch_ids: string[]; sessions: number[] }

export interface Institution {
  name: string; calendar: Calendar; rooms: Room[]; faculty: Faculty[];
  batches: Batch[]; courses: Course[]; offerings: Offering[];
}

export interface Placement {
  offering_id: string; session_index: number; day: number; start: number; length: number; room_id: string;
}
export interface Timetable {
  placements: Placement[]; status: string; penalty: number;
  breakdown: Partial<Quality>; // soft-goal measures, filled in by the solver
}

export interface InstitutionSummary { id: string; name: string; created_at: string }
export interface Job {
  id: string; institution_id: string; status: "queued" | "running" | "done" | "failed";
  error: string | null;
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
