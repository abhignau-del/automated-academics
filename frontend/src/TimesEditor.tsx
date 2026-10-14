import { useState } from "react";
import { fillTimes, inOrder, range } from "./times";
import type { Calendar, LectureTime } from "./types";

interface Props {
  calendar: Calendar;
  onChange: (calendar: Calendar) => void;
  clockUse: Record<string, number>; // how many classes keep each clock
  onRenameClock: (from: string, to: string) => void;
  onRemoveClock: (name: string) => void;
}

/** A clock's name, edited in place and applied when you leave the box (so every keystroke isn't a rename). */
function NameField({ name, taken, onCommit }: { name: string; taken: string[]; onCommit: (to: string) => void }) {
  const [text, setText] = useState(name);
  const clash = text.trim() !== name && taken.includes(text.trim());
  const commit = () => {
    const t = text.trim();
    if (!t || clash) { setText(name); return; }
    if (t !== name) onCommit(t);
  };
  return (
    <input className={"clockname" + (clash ? " bad" : "")} value={text} aria-label={`name of clock ${name}`}
      onChange={(e) => setText(e.target.value)} onBlur={commit}
      onKeyDown={(e) => { if (e.key === "Enter") (e.target as HTMLInputElement).blur(); if (e.key === "Escape") { setText(name); (e.target as HTMLInputElement).blur(); } }} />
  );
}

function Rows({ rows, label, onChange }: { rows: LectureTime[]; label: string; onChange: (rows: LectureTime[]) => void }) {
  const ok = inOrder(rows);
  return (
    <>
      <table className="timestable">
        <thead><tr><th>Lecture</th><th>Starts</th><th>Ends</th></tr></thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i}>
              <th>L{i + 1}</th>
              <td><input type="time" value={r.start} aria-label={`${label} lecture ${i + 1} starts`}
                onChange={(e) => onChange(rows.map((x, j) => (j === i ? { ...x, start: e.target.value } : x)))} /></td>
              <td><input type="time" value={r.end} aria-label={`${label} lecture ${i + 1} ends`}
                onChange={(e) => onChange(rows.map((x, j) => (j === i ? { ...x, end: e.target.value } : x)))} /></td>
            </tr>
          ))}
        </tbody>
      </table>
      {!ok && <p className="setting-note error">Each lecture must end after it starts, and start after the one before it ends.</p>}
    </>
  );
}

/** Settings → the clock time of each lecture, so the timetable can say "9:30–10:30" instead of "L1". Optional. */
export function TimesEditor({ calendar, onChange, clockUse, onRenameClock, onRemoveClock }: Props) {
  const times = calendar.times ?? [];
  const dayTimes = calendar.day_times ?? {};
  const n = calendar.lectures_per_day;
  const valid = Number.isFinite(n) && n >= 1;
  const [spec, setSpec] = useState({ start: "09:00", minutes: 60, gap: 0, breakMinutes: 30 });
  const [open, setOpen] = useState(false);
  const [newClock, setNewClock] = useState("");
  const clocks = calendar.clocks ?? {};
  const clockNames = Object.keys(clocks);

  const fill = () =>
    onChange({
      ...calendar,
      times: fillTimes({ count: n, start: spec.start, minutes: spec.minutes, gap: spec.gap, breakAfter: calendar.break_after, breakMinutes: spec.breakMinutes }),
    });

  const setDay = (day: number, rows: LectureTime[] | null) => {
    const next = { ...dayTimes };
    if (rows) next[day] = rows;
    else delete next[day];
    onChange({ ...calendar, day_times: next });
  };

  return (
    <fieldset className="timesfield">
      <legend>Lecture times <small className="muted">optional: shown on the timetable, in PDFs and in Excel</small></legend>

      {times.length === 0 && !open && (
        <p>
          Lectures are called L1, L2 … at the moment. <button type="button" className="btn small" disabled={!valid}
            onClick={() => setOpen(true)}>Set lecture times…</button>
        </p>
      )}

      {(times.length > 0 || open) && (
        <>
          <div className="timesfill">
            <b>Fill in all {valid ? n : ""} lectures from:</b>
            <label>first lecture starts<input type="time" value={spec.start} onChange={(e) => setSpec({ ...spec, start: e.target.value })} /></label>
            <label>minutes each<input type="number" min={5} max={240} value={spec.minutes}
              onChange={(e) => setSpec({ ...spec, minutes: Number(e.target.value) })} /></label>
            <label>minutes between<input type="number" min={0} max={120} value={spec.gap}
              onChange={(e) => setSpec({ ...spec, gap: Number(e.target.value) })} /></label>
            <label title="The break after the lectures ticked under Breaks above">minutes at a break<input type="number" min={0} max={240} value={spec.breakMinutes}
              onChange={(e) => setSpec({ ...spec, breakMinutes: Number(e.target.value) })} /></label>
            <button type="button" className="btn small primary" disabled={!valid || !spec.start || spec.minutes < 1} onClick={fill}>Fill in</button>
          </div>
          <p className="muted small">Then change any single time below. A lecture's time is what a class's and teacher's week shows.</p>

          {times.length > 0 && <Rows rows={times} label="default" onChange={(rows) => onChange({ ...calendar, times: rows })} />}

          {times.length > 0 && (
            <div className="daytimes">
              <b>Different times on some days</b>
              <p className="muted small">For a day that runs to another clock, such as a shorter Saturday.</p>
              {calendar.day_names.map((d, day) => (
                <div key={day} className="daytime">
                  {dayTimes[day] ? (
                    <details open>
                      <summary>
                        <b>{d}</b> <span className="muted">{dayTimes[day].length ? `${range(dayTimes[day][0])} … ${range(dayTimes[day][dayTimes[day].length - 1])}` : ""}</span>
                        {" "}<button type="button" className="linkish" onClick={() => setDay(day, null)}>Use the default times</button>
                      </summary>
                      <Rows rows={dayTimes[day]} label={d} onChange={(rows) => setDay(day, rows)} />
                    </details>
                  ) : (
                    <button type="button" className="linkish" onClick={() => setDay(day, times.map((t) => ({ ...t })))}>{d}: use different times</button>
                  )}
                </div>
              ))}
            </div>
          )}

          {times.length > 0 && (
            <div className="clocks">
              <b>Classes that keep other hours</b>
              <p className="muted small">
                For classes that run to a different clock (50-minute and 40-minute lectures, a morning and an evening batch). Name a set
                of times, then choose it for those classes in the Classes tab. Lecture numbers stay the same, so a teacher is still kept
                to one place per lecture.
              </p>
              {clockNames.map((name) => (
                <details key={name} open>
                  <summary>
                    <NameField name={name} taken={clockNames} onCommit={(to) => onRenameClock(name, to)} />
                    {" "}<span className="muted small">{clockUse[name] ?? 0} class{(clockUse[name] ?? 0) === 1 ? "" : "es"} use it</span>
                    {" "}<button type="button" className="linkish" onClick={() => onRemoveClock(name)}>Remove this clock</button>
                  </summary>
                  <Rows rows={clocks[name]} label={name} onChange={(rows) => onChange({ ...calendar, clocks: { ...clocks, [name]: rows } })} />
                </details>
              ))}
              <div className="addform">
                <input placeholder="name of a new clock, e.g. 40-minute lectures" aria-label="name of a new clock" value={newClock}
                  onChange={(e) => setNewClock(e.target.value)} />
                <button type="button" className="btn small" disabled={!newClock.trim() || clockNames.includes(newClock.trim())}
                  onClick={() => { onChange({ ...calendar, clocks: { ...clocks, [newClock.trim()]: times.map((t) => ({ ...t })) } }); setNewClock(""); }}>
                  Add a clock
                </button>
              </div>
            </div>
          )}

          <p>
            <button type="button" className="btn small danger" onClick={() => { onChange({ ...calendar, times: [], day_times: {}, clocks: {} }); setOpen(false); }}>
              Remove all lecture times
            </button>
          </p>
        </>
      )}
    </fieldset>
  );
}
