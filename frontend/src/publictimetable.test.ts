import { describe, expect, it } from "vitest";
import { parsePublicHash } from "./PublicTimetable";

describe("published link addresses", () => {
  it("reads the token, and a bookmarked class if there is one", () => {
    expect(parsePublicHash("#/p/abc_DEF-123")).toEqual({ token: "abc_DEF-123", kind: undefined, id: undefined });
    expect(parsePublicHash("#/p/abc/batch/CS-UG1")).toEqual({ token: "abc", kind: "batch", id: "CS-UG1" });
    expect(parsePublicHash("#/p/abc/faculty/Dr%20Rao%20%2F%20X")).toEqual({ token: "abc", kind: "faculty", id: "Dr Rao / X" });
    expect(parsePublicHash("#/p/abc/room/R-1")?.kind).toBe("room");
  });

  it("ignores everything that is not a published link", () => {
    for (const h of ["", "#", "#/", "#/p/", "#/p", "#/q/abc", "#/p/abc/teacher/x", "#/p/abc/batch/", "#/p/a b", "#/p/abc/extra"]) {
      expect(parsePublicHash(h)).toBeNull();
    }
  });
});
