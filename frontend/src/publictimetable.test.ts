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

import { parseResetHash } from "./auth";

describe("password-reset link addresses", () => {
  it("reads the token and ignores everything else", () => {
    const t = "A1b2C3d4E5f6G7h8I9j0K-L_mnop";
    expect(parseResetHash(`#/reset/${t}`)).toBe(t);
    expect(parseResetHash("#/reset/short")).toBeNull();
    expect(parseResetHash(`#/reset/${t}/extra`)).toBeNull();
    expect(parseResetHash(`#/p/${t}`)).toBeNull();
    expect(parseResetHash("")).toBeNull();
  });
});
