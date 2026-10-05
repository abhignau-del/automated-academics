import { describe, expect, it } from "vitest";
import { lock, pinnedKeys, unlock } from "./pins";
import type { Pin, Placement } from "./types";

const pl = (o: string, i: number, day: number, start: number, room = "R1"): Placement =>
  ({ offering_id: o, session_index: i, day, start, length: 1, room_id: room });

describe("pins", () => {
  it("locks sessions where they are, room included", () => {
    expect(lock([], [pl("O1", 0, 2, 3, "R2")])).toEqual([{ offering_id: "O1", session_index: 0, day: 2, start: 3, room_id: "R2" }]);
  });

  it("moves an existing pin instead of duplicating it", () => {
    const pins: Pin[] = [{ offering_id: "O1", session_index: 0, day: 0, start: 0, room_id: "R1" }, { offering_id: "O2", session_index: 0, day: 1, start: 1, room_id: null }];
    const out = lock(pins, [pl("O1", 0, 4, 2)]);
    expect(out).toHaveLength(2);
    expect(out.find((p) => p.offering_id === "O1")).toMatchObject({ day: 4, start: 2 });
    expect(out.find((p) => p.offering_id === "O2")).toEqual(pins[1]);
  });

  it("unlocks only the named sessions", () => {
    const pins = lock([], [pl("O1", 0, 0, 0), pl("O1", 1, 1, 0), pl("O2", 0, 2, 0)]);
    const out = unlock(pins, new Set(["O1#1"]));
    expect([...pinnedKeys(out)].sort()).toEqual(["O1#0", "O2#0"]);
  });
});
