import { describe, expect, it } from "vitest";
import { canDelete, canEdit, canShare, roleOf } from "./access";

describe("access", () => {
  it("treats no listed role as full access (sign-in off, or an administrator)", () => {
    expect(roleOf(undefined)).toBe("owner");
    expect(roleOf({})).toBe("owner");
    expect(roleOf({ role: "viewer" })).toBe("viewer");
  });

  it("lets viewers look only, editors change, and only owners share or delete", () => {
    expect([canEdit("viewer"), canEdit("editor"), canEdit("owner")]).toEqual([false, true, true]);
    expect([canShare("viewer"), canShare("editor"), canShare("owner")]).toEqual([false, false, true]);
    expect([canDelete("viewer"), canDelete("editor"), canDelete("owner")]).toEqual([false, false, true]);
  });
});
