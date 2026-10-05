// Pins: sessions the user has fixed in place. The solver keeps them where they are and plans the rest around them.

import { sessionKey } from "./timetable";
import type { Pin, Placement } from "./types";

export const pinKey = (p: Pick<Pin, "offering_id" | "session_index">) => `${p.offering_id}#${p.session_index}`;

export const pinnedKeys = (pins: Pin[]): Set<string> => new Set(pins.map(pinKey));

const fromPlacement = (p: Placement): Pin => ({
  offering_id: p.offering_id, session_index: p.session_index, day: p.day, start: p.start, room_id: p.room_id,
});

/** Fix these sessions where they are now. A session that was already pinned has its pin moved to the new place. */
export function lock(pins: Pin[], placements: Placement[]): Pin[] {
  const keys = new Set(placements.map(sessionKey));
  return [...pins.filter((p) => !keys.has(pinKey(p))), ...placements.map(fromPlacement)];
}

export const unlock = (pins: Pin[], keys: Set<string>): Pin[] => pins.filter((p) => !keys.has(pinKey(p)));
