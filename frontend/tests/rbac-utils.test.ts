import { describe, expect, it } from "vitest";

import {
  canCreateProjects,
  canEditUser,
  canManageSettings,
  canManageUsers,
  canRevealSecrets,
  canWriteProjects,
} from "~/utils/rbac";

describe("rbac utils", () => {
  it("limits project creation to owner and admin", () => {
    expect(canCreateProjects("owner")).toBe(true);
    expect(canCreateProjects("admin")).toBe(true);
    expect(canCreateProjects("maintainer")).toBe(false);
    expect(canCreateProjects("viewer")).toBe(false);
  });

  it("limits global settings to owners", () => {
    expect(canManageSettings("owner")).toBe(true);
    expect(canManageSettings("admin")).toBe(false);
  });

  it("allows user management only for owner and admin", () => {
    expect(canManageUsers("owner")).toBe(true);
    expect(canManageUsers("admin")).toBe(true);
    expect(canManageUsers("maintainer")).toBe(false);
    expect(canManageUsers("viewer")).toBe(false);
  });

  it("limits secret reveal to owner and admin", () => {
    expect(canRevealSecrets("owner")).toBe(true);
    expect(canRevealSecrets("admin")).toBe(true);
    expect(canRevealSecrets("maintainer")).toBe(false);
    expect(canRevealSecrets("viewer")).toBe(false);
  });

  it("allows project writes for maintainers and above", () => {
    expect(canWriteProjects("owner")).toBe(true);
    expect(canWriteProjects("admin")).toBe(true);
    expect(canWriteProjects("maintainer")).toBe(true);
    expect(canWriteProjects("viewer")).toBe(false);
  });

  it("prevents admins from editing owner and admin users", () => {
    expect(canEditUser("admin", { role: "viewer" })).toBe(true);
    expect(canEditUser("admin", { role: "maintainer" })).toBe(true);
    expect(canEditUser("admin", { role: "admin" })).toBe(false);
    expect(canEditUser("admin", { role: "owner" })).toBe(false);
  });
});
