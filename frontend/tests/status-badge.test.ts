import { mountSuspended } from "@nuxt/test-utils/runtime";
import { describe, expect, it } from "vitest";

import AppStatusBadge from "../components/AppStatusBadge.vue";

describe("AppStatusBadge", () => {
  it("renders a localized running state", async () => {
    const wrapper = await mountSuspended(AppStatusBadge, {
      props: { status: "running" },
    });
    expect(wrapper.text()).toContain("Работает");
    expect(wrapper.classes()).toContain("status-badge--success");
  });
});
