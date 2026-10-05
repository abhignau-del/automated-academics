import { useEffect, useMemo, useRef, useState } from "react";
import type { TableKey } from "./dataops";
import { detectColumns, parseTSV, pasteGrid, shownColumns, type PasteResult } from "./sheet";
import type { Institution } from "./types";

interface Props {
  table: TableKey;
  title: string; // e.g. "rooms"
  inst: Institution;
  onApply: (result: PasteResult) => void;
  onClose: () => void;
}

/**
 * Paste rows copied from Excel or Google Sheets. If the first row is a header it is used to match
 * columns (any order; rows whose id already exists are updated); otherwise the cells fill the
 * table's columns left to right and every row is added.
 */
export function PasteDialog({ table, title, inst, onApply, onClose }: Props) {
  const [text, setText] = useState("");
  const box = useRef<HTMLTextAreaElement>(null);
  useEffect(() => box.current?.focus(), []);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const grid = useMemo(() => parseTSV(text), [text]);
  const header = useMemo(() => (grid.length ? detectColumns(table, grid[0]) : null), [grid, table]);
  const rows = header ? grid.slice(1) : grid;
  const widest = rows.reduce((n, r) => Math.max(n, r.length), 0);
  const shown = shownColumns(table);
  const preview = useMemo(
    () => (rows.length ? pasteGrid(inst, table, rows, header ? { columns: header, upsert: true } : {}) : null),
    [rows, header, inst, table],
  );

  return (
    <div className="modal" role="dialog" aria-modal="true" aria-label={`Paste ${title} from a spreadsheet`}
      onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="modal-card">
        <h3>Paste {title} from a spreadsheet</h3>
        <p className="muted small">
          In Excel or Google Sheets, select the cells (include the heading row if you like), copy, and paste below.
          With a heading row, columns are matched by name and any row whose ID already exists is updated.
          Without one, cells fill the columns in the order {shown.map((c) => c.label).join(" → ")}.
        </p>
        <textarea ref={box} value={text} onChange={(e) => setText(e.target.value)} rows={8} spellCheck={false}
          aria-label="pasted rows" placeholder="Paste here (Ctrl+V)" />

        {rows.length > 0 && preview && (
          <div className="pastepreview">
            <p>
              {header
                ? <>Heading row found: <b>{header.map((c, i) => c ? c.label : `(${grid[0][i]} ignored)`).join(" · ")}</b></>
                : <>No heading row: filling <b>{shown.slice(0, Math.max(1, widest)).map((c) => c.label).join(" · ")}</b>{widest > shown.length && ` (${widest - shown.length} extra column(s) ignored)`}</>}
            </p>
            <p><b>{rows.length}</b> row{rows.length === 1 ? "" : "s"}: <b>{preview.added}</b> new{header && <>, <b>{preview.updated}</b> updated</>}</p>
            {preview.problems.length > 0 && (
              <ul className="problems">{preview.problems.slice(0, 6).map((p, i) => <li key={i}>{p}</li>)}
                {preview.problems.length > 6 && <li>…and {preview.problems.length - 6} more</li>}</ul>
            )}
          </div>
        )}

        <div className="modal-actions">
          <button className="btn" onClick={onClose}>Cancel</button>
          <button className="btn primary" disabled={!preview || rows.length === 0} onClick={() => preview && onApply(preview)}>
            {preview ? `Add ${preview.added} new` + (header && preview.updated ? `, update ${preview.updated}` : "") : "Apply"}
          </button>
        </div>
      </div>
    </div>
  );
}
