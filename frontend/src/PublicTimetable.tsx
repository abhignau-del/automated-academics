// The page a published link opens: a read-only timetable for people with no account. It talks only to /public/<token>/...
// and shows only the views the publisher chose.

import { useEffect, useMemo, useState } from "react";
import * as api from "./api";
import type { PublicMeta, PublicSession, RemovedSession } from "./api";
import { assignLanes, courseColour, sessionKey } from "./timetable";
import { range, rangeText, sideTimes } from "./times";
import type { Placement } from "./types";

type Kind = "batch" | "faculty" | "room";
const KIND_LABEL: Record<Kind, string> = { batch: "Class", faculty: "Teacher", room: "Room" };
const KIND_PLURAL: Record<Kind, string> = { batch: "classes", faculty: "teachers", room: "rooms" };
const ROW_H = 60;

/** `#/p/<token>` or `#/p/<token>/<kind>/<id>`: the second form is what a student bookmarks. */
export function parsePublicHash(hash: string): { token: string; kind?: Kind; id?: string } | null {
  const m = hash.match(/^#\/p\/([A-Za-z0-9_-]+)(?:\/(batch|faculty|room)\/(.+))?$/);
  if (!m) return null;
  return { token: m[1], kind: m[2] as Kind | undefined, id: m[3] ? decodeURIComponent(m[3]) : undefined };
}

const asPlacement = (s: PublicSession): Placement => ({
  offering_id: s.offering_id, session_index: s.session_index, day: s.day, start: s.start, length: s.length, room_id: s.room_name,
});

const NEW_BADGE = "New or moved";

function lines(kind: Kind, s: PublicSession): string[] {
  return kind === "batch" ? [s.faculty_name, s.room_name] : kind === "faculty" ? [s.batch_ids.join(", "), s.room_name] : [s.faculty_name, s.batch_ids.join(", ")];
}

function Week({ meta, kind, sessions, clock }: { meta: PublicMeta; kind: Kind; sessions: PublicSession[]; clock: string | null }) {
  const cal = meta.calendar;
  const side = sideTimes(cal, clock);
  const items = useMemo(() => sessions.map(asPlacement), [sessions]);
  const lanes = useMemo(() => assignLanes(items), [items]);
  const byKey = new Map(sessions.map((s) => [sessionKey(s), s]));
  const label = (start: number, length: number) =>
    side[start] && side[start + length - 1] ? `${range(side[start]).split("–")[0]}–${range(side[start + length - 1]).split("–")[1]}` : null;
  return (
    <div className="grid" style={{ gridTemplateColumns: `80px repeat(${cal.day_names.length}, minmax(110px, 1fr))` }}>
      <div className="grid-corner" />
      {cal.day_names.map((d) => <div key={d} className="grid-day">{d}</div>)}
      <div className="grid-times">
        {Array.from({ length: cal.lectures_per_day }, (_, p) => (
          <div key={p} className={"grid-time" + (cal.break_after.includes(p) ? " break" : "")} style={{ height: ROW_H }}>
            <b>L{p + 1}</b>
            {side[p] && <small>{range(side[p])}</small>}
          </div>
        ))}
      </div>
      {cal.day_names.map((_, day) => (
        <div key={day} className="grid-col" style={{ height: ROW_H * cal.lectures_per_day }}>
          {Array.from({ length: cal.lectures_per_day }, (_, p) => (
            <div key={p} className={"grid-cell" + (cal.break_after.includes(p) ? " break" : "")} style={{ height: ROW_H }} />
          ))}
          {sessions.filter((s) => s.day === day).map((s) => {
            const key = sessionKey(s);
            const { lane, lanes: n } = lanes.get(key) ?? { lane: 0, lanes: 1 };
            const own = byKey.get(key)?.time;
            return (
              <div key={key} className="card" style={{
                top: s.start * ROW_H, height: s.length * ROW_H - 2, left: `calc(${(lane / n) * 100}% + 2px)`, width: `calc(${100 / n}% - 4px)`,
                background: courseColour(s.course_code), cursor: "default", outline: s.changed ? "3px solid #d9a400" : undefined,
              }} title={`${s.course_name}\n${lines(kind, s).join(" · ")}`}>
                {s.changed && <small className="cardtime">{NEW_BADGE}</small>}
                <strong>{s.course_code}</strong>
                <span>{s.course_name}</span>
                {lines(kind, s).map((l, i) => <em key={i}>{l}</em>)}
                {own && own !== label(s.start, s.length) && <small className="cardtime">{own}</small>}
              </div>
            );
          })}
        </div>
      ))}
    </div>
  );
}

/** The same week as a plain list, day by day: easier to read on a phone. */
function Agenda({ meta, kind, sessions }: { meta: PublicMeta; kind: Kind; sessions: PublicSession[] }) {
  const days = meta.calendar.day_names.map((name, d) => ({ name, rows: sessions.filter((s) => s.day === d).sort((a, b) => a.start - b.start) })).filter((d) => d.rows.length);
  if (!days.length) return <p className="muted">Nothing is scheduled.</p>;
  return (
    <div className="agenda">
      {days.map((d) => (
        <section key={d.name}>
          <h3>{d.name}</h3>
          <ul>
            {d.rows.map((s) => (
              <li key={sessionKey(s)} style={{ borderLeftColor: courseColour(s.course_code) }}>
                <b>{s.time ?? `Lecture ${s.start + 1}${s.length > 1 ? `–${s.start + s.length}` : ""}`}</b>
                {s.changed && <span className="changedtag"> {NEW_BADGE}</span>}
                {s.time && <span className="muted"> · lecture {s.start + 1}{s.length > 1 ? `–${s.start + s.length}` : ""}</span>}
                <div>{s.course_name}</div>
                <div className="muted small">{lines(kind, s).join(" · ")}</div>
              </li>
            ))}
          </ul>
        </section>
      ))}
    </div>
  );
}

export function PublicTimetable({ token }: { token: string }) {
  const start = parsePublicHash(window.location.hash);
  const [meta, setMeta] = useState<PublicMeta | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [kind, setKind] = useState<Kind | null>(start?.kind ?? null);
  const [id, setId] = useState<string | null>(start?.id ?? null);
  const [filter, setFilter] = useState("");
  const [sessions, setSessions] = useState<PublicSession[] | null>(null);
  const [removed, setRemoved] = useState<RemovedSession[]>([]);
  const [copied, setCopied] = useState(false);
  const [layout, setLayout] = useState<"week" | "list">(window.innerWidth < 720 ? "list" : "week");

  useEffect(() => {
    api.publicMeta(token).then((m) => {
      setMeta(m);
      document.title = `${m.institution} timetable`;
      const first = (m.kinds as Kind[])[0];
      setKind((k) => (k && m.kinds.includes(k) ? k : first));
    }).catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, [token]);

  const entities = meta && kind ? meta.entities[kind] ?? [] : [];
  const shown = entities.filter((e) => e.label.toLowerCase().includes(filter.trim().toLowerCase()));
  useEffect(() => {
    if (!meta || !kind) return;
    if (!id || !entities.some((e) => e.id === id)) setId(entities[0]?.id ?? null);
  }, [meta, kind]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!kind || !id) { setSessions(null); return; }
    let alive = true;
    setSessions(null);
    api.publicView(token, kind, id).then((v) => { if (alive) { setSessions(v.sessions); setRemoved(v.removed ?? []); } }).catch((e) => { if (alive) setError(e instanceof Error ? e.message : String(e)); });
    // a link to exactly this class, to bookmark or send on
    window.history.replaceState(null, "", `#/p/${token}/${kind}/${encodeURIComponent(id)}`);
    return () => { alive = false; };
  }, [token, kind, id]);

  if (error) return (
    <div className="authwall"><div className="authcard"><h1>Timetable</h1><h2>This timetable can't be shown</h2><p>{error}</p>
      <p className="muted small">Ask whoever sent you the link for a new one.</p></div></div>
  );
  if (!meta || !kind) return <div className="authwall"><div className="authcard"><p className="muted">Loading the timetable…</p></div></div>;

  const entity = entities.find((e) => e.id === id);
  const clock = kind === "batch" ? entity?.clock ?? null : null;
  return (
    <div className="public">
      <header>
        <h1>{meta.institution}</h1>
        <span className="muted">{meta.label || "Timetable"} · view only</span>
      </header>
      <main>
        <div className="toolbar">
          {meta.kinds.length > 1 && (
            <div className="tabs" role="tablist">
              {(meta.kinds as Kind[]).map((k) => (
                <button key={k} role="tab" aria-selected={kind === k} className={kind === k ? "active" : ""}
                  onClick={() => { setKind(k); setFilter(""); setId(null); }}>{KIND_LABEL[k]}</button>
              ))}
            </div>
          )}
          {entities.length > 12 && (
            <input type="search" className="filter" placeholder={`Find a ${KIND_LABEL[kind].toLowerCase()}…`} aria-label="find" value={filter} onChange={(e) => setFilter(e.target.value)} />
          )}
          <select aria-label={KIND_LABEL[kind]} value={id ?? ""} onChange={(e) => setId(e.target.value)}>
            {(shown.some((e) => e.id === id) ? shown : [...(entity ? [entity] : []), ...shown]).map((e) => <option key={e.id} value={e.id}>{e.label}</option>)}
          </select>
          <span className="spacer" />
          <div className="tabs" role="tablist" aria-label="layout">
            <button role="tab" aria-selected={layout === "week"} className={layout === "week" ? "active" : ""} onClick={() => setLayout("week")}>Week</button>
            <button role="tab" aria-selected={layout === "list"} className={layout === "list" ? "active" : ""} onClick={() => setLayout("list")}>List</button>
          </div>
        </div>

        {meta.update && (
          <div className="alert note" role="status">
            <div>
              <b>Timetable updated {new Date(meta.update.at).toLocaleDateString()}.</b>
              {meta.update.note && <> {meta.update.note}{/[.!?]$/.test(meta.update.note) ? "" : "."}</>}
              {sessions && (sessions.some((s) => s.changed) || removed.length > 0
                ? <> For this {KIND_LABEL[kind].toLowerCase()}: {sessions.filter((s) => s.changed).length} new or moved (marked), {removed.length} no longer scheduled.</>
                : <> Nothing changed for this {KIND_LABEL[kind].toLowerCase()}.</>)}
              {removed.length > 0 && (
                <ul className="removedlist">
                  {removed.map((r, i) => <li key={i}>No longer: {r.course_name}, {r.day_name} {r.time ?? `lecture ${r.start + 1}`}</li>)}
                </ul>
              )}
            </div>
          </div>
        )}
        {sessions === null ? <p className="muted">Loading…</p>
          : layout === "week" ? <Week meta={meta} kind={kind} sessions={sessions} clock={clock} />
          : <Agenda meta={meta} kind={kind} sessions={sessions} />}

        <p className="downloads">
          {id && <a className="btn" href={api.publicPdfUrl(token, kind, id)}>Download PDF: this {KIND_LABEL[kind].toLowerCase()}</a>}
          <a className="btn" href={api.publicPdfUrl(token, kind)}>PDF: all {KIND_PLURAL[kind]}</a>
          {meta.everything && <a className="btn" href={api.publicPdfUrl(token)}>PDF: everything</a>}
          {meta.everything && <a className="btn" href={api.publicXlsxUrl(token)}>Excel workbook</a>}
        </p>
        {meta.calendar_feed && id && (
          <p className="downloads" aria-label="calendar">
            <b>Calendar:</b>
            <a className="btn" href={api.publicCalendarUrl(token, kind, id, true)} title="Opens your calendar app and keeps it up to date">Add to my calendar</a>
            <a className="btn" href={api.publicCalendarUrl(token, kind, id)} download="timetable.ics">Download .ics file</a>
            <button className="btn" title="For Google Calendar: Other calendars, From URL"
              onClick={() => { void navigator.clipboard?.writeText(api.publicCalendarUrl(token, kind, id)).then(() => { setCopied(true); setTimeout(() => setCopied(false), 2000); }); }}>
              {copied ? "Copied" : "Copy calendar address"}
            </button>
          </p>
        )}
        {meta.no_class.length > 0 && (
          <p className="muted small" aria-label="no lectures">
            No lectures: {meta.no_class.map(rangeText).join("; ")}.
          </p>
        )}
        <p className="muted small">
          Published {new Date(meta.published_at).toLocaleDateString()}
          {meta.expires_at ? ` · available until ${new Date(meta.expires_at).toLocaleDateString()}` : ""}. Made with Automated Academics.
        </p>
      </main>
    </div>
  );
}
