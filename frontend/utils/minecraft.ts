/** Supported managed Minecraft runtimes. */
export const MINECRAFT_RUNTIMES = [
  "minecraft_forge",
  "minecraft_paper",
  "minecraft_purpur",
  "minecraft_spigot",
] as const;

/** Return whether a runtime provides Minecraft lifecycle controls. */
export function isMinecraftRuntime(
  runtime: string | null | undefined,
): boolean {
  return (MINECRAFT_RUNTIMES as readonly string[]).includes(runtime ?? "");
}

/** Build an installation request without mixing Forge and plugin builds. */
export function minecraftInstallPayload(
  engine: string,
  version: string,
  build: string,
  uploadedJar = "",
): Record<string, string> {
  if (uploadedJar)
    return {
      uploaded_jar: uploadedJar,
      ...(version ? { minecraft_version: version } : {}),
    };
  return {
    minecraft_version: version,
    [engine === "forge" ? "forge_version" : "build_id"]: build,
  };
}

/** Return core-specific configuration editor paths. */
export function minecraftConfigPaths(engine: string): string[] {
  const files = [
    "server.properties",
    "whitelist.json",
    "ops.json",
    "banned-players.json",
  ];
  if (engine !== "forge")
    files.push("bukkit.yml", "spigot.yml", "commands.yml", "permissions.yml");
  if (["paper", "purpur"].includes(engine))
    files.push(
      "paper.yml",
      "config/paper-global.yml",
      "config/paper-world-defaults.yml",
    );
  if (engine === "purpur") files.push("purpur.yml");
  return files;
}

/** Render a bounded Minecraft chat component as safe plain text. */
export function minecraftMotd(value: unknown, depth = 0): string {
  if (depth > 8) return "";
  if (typeof value === "string") return value.slice(0, 4096);
  if (Array.isArray(value))
    return value
      .slice(0, 100)
      .map((part) => minecraftMotd(part, depth + 1))
      .join("")
      .slice(0, 4096);
  if (value && typeof value === "object") {
    const component = value as { text?: unknown; extra?: unknown };
    return (
      minecraftMotd(component.text, depth + 1) +
      minecraftMotd(component.extra, depth + 1)
    ).slice(0, 4096);
  }
  return "";
}
