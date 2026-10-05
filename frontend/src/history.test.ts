import { describe, expect, it } from "vitest";
import { canRedo, canUndo, COALESCE_MS, initHistory, MAX_STEPS, pushHistory, redo, undo } from "./history";

describe("history", () => {
  it("starts with nothing to undo or redo", () => {
    const h = initHistory("a");
    expect([canUndo(h), canRedo(h), h.present]).toEqual([false, false, "a"]);
  });

  it("each unkeyed change is its own step, and undo / redo walk through them", () => {
    let h = initHistory("a");
    h = pushHistory(h, "b", undefined, 0);
    h = pushHistory(h, "c", undefined, 10);
    expect(h.present).toBe("c");
    h = undo(h); expect(h.present).toBe("b");
    h = undo(h); expect(h.present).toBe("a");
    expect(canUndo(h)).toBe(false);
    h = redo(h); expect(h.present).toBe("b");
    h = redo(h); expect(h.present).toBe("c");
    expect(canRedo(h)).toBe(false);
  });

  it("a new change after undoing discards the redo branch", () => {
    let h = pushHistory(pushHistory(initHistory("a"), "b", undefined, 0), "c", undefined, 10);
    h = undo(h);
    h = pushHistory(h, "x", undefined, 20);
    expect([h.present, canRedo(h)]).toEqual(["x", false]);
    expect(undo(h).present).toBe("b");
  });

  it("typing in one cell is a single step, so one undo reverts the whole word", () => {
    let h = initHistory("");
    for (const [i, text] of ["h", "he", "hel", "hell", "hello"].entries()) h = pushHistory(h, text, "rooms.0.name", i * 100);
    expect(h.past).toEqual([""]);
    expect(undo(h).present).toBe("");
  });

  it("a pause, or a different cell, starts a new step", () => {
    let h = initHistory("");
    h = pushHistory(h, "a", "cell1", 0);
    h = pushHistory(h, "ab", "cell1", COALESCE_MS + 50); // too long a pause
    h = pushHistory(h, "abc", "cell2", COALESCE_MS + 100); // another cell
    expect(h.past).toEqual(["", "a", "ab"]);
  });

  it("a structural change between edits to the same cell is never folded into them", () => {
    let h = initHistory("");
    h = pushHistory(h, "a", "cell1", 0);
    h = pushHistory(h, "a!", undefined, 100); // e.g. a delete or a paste
    h = pushHistory(h, "a!b", "cell1", 150);
    expect(h.past).toEqual(["", "a", "a!"]);
  });

  it("undoing ends the folding, so typing again afterwards is a new step", () => {
    let h = pushHistory(initHistory(""), "a", "c", 0);
    h = undo(h);
    h = pushHistory(h, "z", "c", 50);
    expect(h.past).toEqual([""]);
    expect(h.present).toBe("z");
    expect(canRedo(h)).toBe(false);
  });

  it("ignores a change that changes nothing", () => {
    const h = initHistory({ n: 1 });
    expect(pushHistory(h, { n: 1 }, undefined, 0)).toBe(h);
  });

  it("keeps only the most recent steps", () => {
    // 130 changes leave the states 0..129 as "past" and 130 as the present; only the newest 100 of
    // those past states are kept, so the oldest one still reachable by Undo is 30
    let h = initHistory(0);
    for (let i = 1; i <= MAX_STEPS + 30; i++) h = pushHistory(h, i, undefined, i);
    expect(h.present).toBe(MAX_STEPS + 30);
    expect(h.past).toHaveLength(MAX_STEPS);
    expect(h.past[0]).toBe(30);
    expect(h.past[MAX_STEPS - 1]).toBe(MAX_STEPS + 29);
  });

  it("undo and redo with nothing to do change nothing", () => {
    const h = initHistory("a");
    expect(undo(h)).toBe(h);
    expect(redo(h)).toBe(h);
  });

  it("does not mutate earlier states", () => {
    const a = { list: [1] };
    let h = initHistory(a);
    h = pushHistory(h, { list: [1, 2] }, undefined, 0);
    expect(a.list).toEqual([1]);
    expect(undo(h).present).toBe(a);
  });
});
