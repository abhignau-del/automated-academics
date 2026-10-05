import { cycleSlot, setSlots, slotState, type SlotState } from "./dataops";
import type { Calendar, Faculty } from "./types";

const LABEL: Record<SlotState, string> = { free: "", avoid: "avoid", unavailable: "✕" };
const NAME: Record<SlotState, string> = { free: "free", avoid: "would rather avoid", unavailable: "unavailable" };

interface Props { cal: Calendar; faculty: Faculty; onChange: (f: Faculty) => void }

/**
 * A teacher's week. Click a slot to cycle free -> avoid -> unavailable. Click a day or lecture
 * heading to block that whole line, and again to clear it.
 */
export function SlotGrid({ cal, faculty, onChange }: Props) {
  const days = cal.day_names.length;
  const lectures = cal.lectures_per_day;

  const toggleLine = (cells: { day: number; lecture: number }[]) => {
    const allBlocked = cells.every((c) => slotState(faculty, c.day, c.lecture) === "unavailable");
    onChange(setSlots(faculty, cells, allBlocked ? "free" : "unavailable"));
  };

  return (
    <div className="slotgrid">
      <table>
        <thead>
          <tr>
            <th />
            {cal.day_names.map((d, day) => (
              <th key={day}>
                <button type="button" className="linkish" title={`Block or clear every lecture on ${d}`}
                  onClick={() => toggleLine(Array.from({ length: lectures }, (_, lecture) => ({ day, lecture })))}>
                  {d}
                </button>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {Array.from({ length: lectures }, (_, lecture) => (
            <tr key={lecture}>
              <th>
                <button type="button" className="linkish" title={`Block or clear lecture ${lecture + 1} on every day`}
                  onClick={() => toggleLine(Array.from({ length: days }, (_, day) => ({ day, lecture })))}>
                  L{lecture + 1}
                </button>
              </th>
              {cal.day_names.map((d, day) => {
                const state = slotState(faculty, day, lecture);
                return (
                  <td key={day}>
                    <button type="button" className={"slot " + state}
                      aria-label={`${d} lecture ${lecture + 1}: ${NAME[state]}`}
                      onClick={() => onChange(cycleSlot(faculty, day, lecture))}>
                      {LABEL[state]}
                    </button>
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
      <p className="muted small">
        Click a slot to cycle <span className="chip avoid">avoid</span> (the solver tries not to use it) and{" "}
        <span className="chip unavailable">✕ unavailable</span> (never used). Click a heading to block a whole line.
      </p>
    </div>
  );
}
