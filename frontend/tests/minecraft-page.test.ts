import { mountSuspended, mockNuxtImport } from "@nuxt/test-utils/runtime";
import { flushPromises, type VueWrapper } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import MinecraftPage from "../pages/projects/[id]/minecraft.vue";

const state = vi.hoisted(() => ({
  writable: true,
  configured: true,
  request: vi.fn(),
}));
mockNuxtImport("useApi", () => () => ({ request: state.request }));
mockNuxtImport("useAuthStore", () => () => ({
  canWriteProjects: state.writable,
  authenticated: true,
  user: { role: "owner" },
  restore: () => {},
}));

describe("Minecraft management page", () => {
  let wrapper: VueWrapper | undefined;
  beforeEach(() => {
    state.writable = true;
    state.configured = true;
    state.request.mockReset();
    state.request.mockImplementation(
      async (path: string, options?: { method?: string }) => {
        if (path.endsWith("/minecraft/install"))
          return { id: "job", status: "queued", progress: 0 };
        if (options?.method) return { message: "done" };
        if (path.endsWith("/minecraft"))
          return {
            engine: "paper",
            status: "stopped",
            active_job_id: null,
            configuration: {
              server_jar: "server.jar",
              java_version: 21,
              eula_accepted: state.configured,
              rcon_enabled: true,
            },
            rcon_configured: true,
          };
        if (path.endsWith("/versions"))
          return {
            versions: [
              {
                minecraft_version: "1.21.1",
                forge_versions: [],
                java_version: 21,
              },
            ],
          };
        if (path.endsWith("/builds"))
          return {
            builds: [{ build_id: "133", channel: "stable", java_version: 21 }],
          };
        if (path.endsWith("/plugins"))
          return [
            {
              name: "test.jar",
              path: "plugins/test.jar",
              size_bytes: 100,
              enabled: true,
            },
          ];
        if (path.endsWith("/latest"))
          return {
            values: {
              minecraft: {
                available: false,
                running: false,
                online_players: null,
                max_players: null,
              },
            },
          };
        return [];
      },
    );
  });
  afterEach(() => {
    wrapper?.unmount();
    wrapper = undefined;
  });
  it("requires saved EULA acceptance before installing or starting a new server", async () => {
    state.configured = false;
    wrapper = await mountSuspended(MinecraftPage, {
      route: "/projects/test/minecraft",
    });
    await flushPromises();
    expect(
      wrapper.find("form.installer button[type=submit]").attributes("disabled"),
    ).toBeDefined();
    const eula = wrapper.find("label.critical input[type=checkbox]");
    await eula.setValue(true);
    expect(
      wrapper.find("form.installer button[type=submit]").attributes("disabled"),
    ).toBeDefined();
    await wrapper.find("form.installer").trigger("submit");
    expect(state.request).not.toHaveBeenCalledWith(
      "/projects/test/minecraft/install",
      expect.anything(),
    );
  });
  it("selects an official build and queues the installation with visible progress", async () => {
    wrapper = await mountSuspended(MinecraftPage, {
      route: "/projects/test/minecraft",
    });
    await flushPromises();
    expect(wrapper.text()).toContain("Paper");
    expect(
      wrapper.findAll("select").some((select) => select.text().includes("133")),
    ).toBe(true);
    await wrapper.find("form.installer").trigger("submit");
    await flushPromises();
    expect(state.request).toHaveBeenCalledWith(
      "/projects/test/minecraft/install",
      expect.objectContaining({
        method: "POST",
        body: { minecraft_version: "1.21.1", build_id: "133" },
      }),
    );
    expect(wrapper.find(".install-progress").exists()).toBe(true);
  });
  it("disables plugin mutations and player commands for a viewer", async () => {
    state.writable = false;
    wrapper = await mountSuspended(MinecraftPage, {
      route: "/projects/test/minecraft",
    });
    await flushPromises();
    expect(
      wrapper.find(".management-panel input[type=file]").attributes("disabled"),
    ).toBeDefined();
    for (const button of wrapper.findAll(".management-panel button").slice(1))
      expect(button.attributes("disabled")).toBeDefined();
    expect(
      wrapper
        .findAll("form")
        .at(-1)
        ?.find("button[type=submit]")
        .attributes("disabled"),
    ).toBeDefined();
  });
  it("sends stopped-server plugin changes through the dedicated API", async () => {
    wrapper = await mountSuspended(MinecraftPage, {
      route: "/projects/test/minecraft",
    });
    await flushPromises();
    await wrapper.find(".management-panel li button").trigger("click");
    await flushPromises();
    expect(state.request).toHaveBeenCalledWith(
      "/projects/test/minecraft/plugins/test.jar",
      { method: "POST", body: { action: "disable" } },
    );
  });
});
