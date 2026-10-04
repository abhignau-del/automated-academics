import { useMemo, useRef, useState } from "react";
import { assignLanes, blockFits, courseColour, sessionKey } from "./timetable";
import type { Institution, Placement, ViewKind } from "./types";

export const ROW_H = 60;

interface Props {
  inst: Institution;
  items: Placement[];
  kind: ViewKind;
  conflictOfferings: Set<string>;
  selected: string | null;
  onSelect: (key: string | null) => void;
  onMove: (key: string, day: number, start: number) => void;
}

interface Hover { day: number; start: number; length: number; ok: boolean }

export function Grid({ inst, items, kind, conflictOfferings, selected, onSelect, onMove }: Props) {
  const cal = inst.calendar;
  const drag = useRef<{ key: string; grab: number; length: number } | null>(null);
  const [hover, setHover] = useState<Hover | null>(null);

  const lookup = useMemo(() => ({
    offerings: new Map(inst.offerings.map((o) => [o.id, o])),
    courses: new Map(inst.courses.map((c) => [c.code, c])),
    faculty: new Map(inst.faculty.map((f) => [f.id, f])),
    rooms: new Map(inst.rooms.map((r) => [r.id, r])),
  }), [inst]);
  const lanes = useMemo(() => assignLanes(items), [items]);

  const periodAt = (e: React.DragEvent<HTMLElement>) => {
    const rect = e.currentTarget.getBoundingClientRect();
    const grab = drag.current?.grab ?? 0;
    return Math.floor((e.clientY - rect.top) / ROW_H) - grab;
  };

  const onDragOver = (e: React.DragEvent<HTMLElement>, day: number) => {
    const d = drag.current;
    if (!d) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
    const start = periodAt(e);
    if (!hover || hover.day !== day || hover.start !== start) {
      setHover({ day, start, length: d.length, ok: blockFits(cal, start, d.length) });
    }
  };

  const onDrop = (e: React.DragEvent<HTMLElement>, day: number) => {
    const d = drag.current;
    e.preventDefault();
    drag.current = null;
    setHover(null);
    if (!d) return;
    const start = periodAt(e);
    if (blockFits(cal, start, d.length)) onMove(d.key, day, start);
  };

  const describe = (p: Placement) => {
    const o = lookup.offerings.get(p.offering_id)!;
    const course = lookup.courses.get(o.course_code);
    const fac = lookup.faculty.get(o.faculty_id)?.name ?? o.faculty_id;
    const room = lookup.rooms.get(p.room_id)?.name ?? p.room_id;
    const batches = o.batch_ids.join(", ");
    const lines =
      kind === "batch" ? [fac, room] : kind === "faculty" ? [batches, room] : [fac, batches];
    return { code: o.course_code, name: course?.name ?? o.course_code, lines };
  };

  return (
    <div className="grid" style={{ gridTemplateColumns: `64px repeat(${cal.day_names.length}, minmax(120px, 1fr))` }}>
      <div className="grid-corner" />
      {cal.day_names.map((d) => <div key={d} className="grid-day">{d}</div>)}

      <div className="grid-times">
        {Array.from({ length: cal.periods_per_day }, (_, p) => (
          <div key={p} className={"grid-time" + (cal.break_after.includes(p) ? " break" : "")} style={{ height: ROW_H }}>
            <b>P{p + 1}</b>
          </div>
        ))}
      </div>

      {cal.day_names.map((_, day) => (
        <div
          key={day}
          className="grid-col"
          style={{ height: ROW_H * cal.periods_per_day }}
          onDragOver={(e) => onDragOver(e, day)}
          onDragLeave={(e) => { if (!e.currentTarget.contains(e.relatedTarget as Node)) setHover(null); }}
          onDrop={(e) => onDrop(e, day)}
          onClick={() => onSelect(null)}
        >
          {Array.from({ length: cal.periods_per_day }, (_, p) => (
            <div key={p} className={"grid-cell" + (cal.break_after.includes(p) ? " break" : "")} style={{ height: ROW_H }} />
          ))}

          {hover && hover.day === day && (
            <div className={"ghost" + (hover.ok ? "" : " bad")}
              style={{ top: Math.max(0, hover.start) * ROW_H, height: hover.length * ROW_H }} />
          )}

          {items.filter((p) => p.day === day).map((p) => {
            const key = sessionKey(p);
            const { lane, lanes: n } = lanes.get(key) ?? { lane: 0, lanes: 1 };
            const info = describe(p);
            const cls = ["card"];
            if (selected === key) cls.push("selected");
            if (conflictOfferings.has(p.offering_id)) cls.push("conflict");
            return (
              <div
                key={key}
                className={cls.join(" ")}
                role="button"
                tabIndex={0}
                draggable
                title={`${info.name}\n${info.lines.join(" · ")}`}
                style={{
                  top: p.start * ROW_H, height: p.length * ROW_H - 2,
                  left: `calc(${(lane / n) * 100}% + 2px)`, width: `calc(${100 / n}% - 4px)`,
                  background: courseColour(info.code),
                }}
                onClick={(e) => { e.stopPropagation(); onSelect(key); }}
                onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onSelect(key); } }}
                onDragStart={(e) => {
                  const rect = e.currentTarget.getBoundingClientRect();
                  drag.current = { key, grab: Math.floor((e.clientY - rect.top) / ROW_H), length: p.length };
                  e.dataTransfer.effectAllowed = "move";
                  e.dataTransfer.setData("text/plain", key);
                  onSelect(key);
                }}
                onDragEnd={() => { drag.current = null; setHover(null); }}
              >
                <strong>{info.code}</strong>
                <span>{info.name}</span>
                {info.lines.map((l, i) => <em key={i}>{l}</em>)}
              </div>
            );
          })}
        </div>
      ))}
    </div>
  );
}
