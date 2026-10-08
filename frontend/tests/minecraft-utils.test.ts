import { describe, expect, it } from "vitest";
import {
  isMinecraftRuntime,
  minecraftConfigPaths,
  minecraftInstallPayload,
  minecraftMotd,
} from "../utils/minecraft";

describe("Minecraft runtime management", () => {
  it("recognizes each core without accepting arbitrary prefixes", () => {
    for (const engine of ["forge", "paper", "purpur", "spigot"])
      expect(isMinecraftRuntime(`minecraft_${engine}`)).toBe(true);
    expect(isMinecraftRuntime("minecraft_custom")).toBe(false);
    expect(isMinecraftRuntime(null)).toBe(false);
  });
  it("keeps Forge coordinates separate from selected plugin builds", () => {
    expect(minecraftInstallPayload("forge", "1.20.1", "47.4.0")).toEqual({
      minecraft_version: "1.20.1",
      forge_version: "47.4.0",
    });
    expect(minecraftInstallPayload("paper", "1.21.1", "133")).toEqual({
      minecraft_version: "1.21.1",
      build_id: "133",
    });
    expect(
      minecraftInstallPayload("spigot", "1.21.1", "", "uploaded.jar"),
    ).toEqual({ minecraft_version: "1.21.1", uploaded_jar: "uploaded.jar" });
  });
  it("offers only configuration files belonging to the selected core", () => {
    expect(minecraftConfigPaths("forge")).not.toContain("bukkit.yml");
    expect(minecraftConfigPaths("spigot")).not.toContain("purpur.yml");
    expect(minecraftConfigPaths("purpur")).toContain("config/paper-global.yml");
    expect(minecraftConfigPaths("purpur")).toContain("purpur.yml");
  });
  it("renders nested MOTD components as bounded text without interpreting markup", () => {
    expect(
      minecraftMotd({ text: "Welcome", extra: [{ text: " <script>" }] }),
    ).toBe("Welcome <script>");
    expect(minecraftMotd(null)).toBe("");
    expect(minecraftMotd("x".repeat(10000))).toHaveLength(4096);
    const component: { extra?: unknown[] } = {};
    component.extra = [component];
    expect(minecraftMotd(component)).toBe("");
  });
});
