import type { User, UserRole } from "~/types/api";

/**
 * Returns whether a role can create new projects.
 */
export function canCreateProjects(role: UserRole | null | undefined): boolean {
  return role === "owner" || role === "admin";
}

export function canManageUsers(role: UserRole | null | undefined): boolean {
  return role === "owner" || role === "admin";
}

export function canManageSettings(role: UserRole | null | undefined): boolean {
  return role === "owner";
}

export function canRevealSecrets(role: UserRole | null | undefined): boolean {
  return role === "owner" || role === "admin";
}

export function canWriteProjects(role: UserRole | null | undefined): boolean {
  return role === "owner" || role === "admin" || role === "maintainer";
}

export function canEditUser(
  actorRole: UserRole | null | undefined,
  target: Pick<User, "role">,
): boolean {
  if (actorRole === "owner") return true;
  return (
    actorRole === "admin" && ["maintainer", "viewer"].includes(target.role)
  );
}
