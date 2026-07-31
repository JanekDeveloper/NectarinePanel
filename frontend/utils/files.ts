export interface BrowsableFileEntry {
  kind: string;
  target_kind?: string | null;
}

export interface PersistentMount {
  source: string;
  target: string;
  read_only: boolean;
}

/**
 * Returns whether an entry can be opened as a directory.
 */
export function isBrowsableDirectory(entry: BrowsableFileEntry): boolean {
  return (
    entry.kind === "directory" ||
    (entry.kind === "symlink" && entry.target_kind === "directory")
  );
}

/**
 * Returns whether an entry resolves to a regular file.
 */
export function isBrowsableFile(entry: BrowsableFileEntry): boolean {
  return (
    entry.kind === "file" ||
    (entry.kind === "symlink" && entry.target_kind === "file")
  );
}

/**
 * Returns validated persistent mount mappings from a project runtime config.
 */
export function persistentMounts(
  runtimeConfig: Record<string, unknown>,
): PersistentMount[] {
  const value = runtimeConfig.persistent_mounts;
  if (!Array.isArray(value)) return [];
  return value.flatMap((entry) => {
    if (!entry || typeof entry !== "object") return [];
    const mount = entry as Record<string, unknown>;
    if (
      typeof mount.source !== "string" ||
      !mount.source ||
      typeof mount.target !== "string" ||
      !mount.target.startsWith("/")
    ) {
      return [];
    }
    return [
      {
        source: mount.source,
        target: mount.target,
        read_only: mount.read_only === true,
      },
    ];
  });
}
