import { mountSuspended } from "@nuxt/test-utils/runtime";
import { createPinia, setActivePinia } from "pinia";
import { describe, expect, it } from "vitest";

import AppToastStack from "../components/AppToastStack.vue";
import { useToastStore } from "../stores/toasts";

describe("AppToastStack", () => {
  it("renders and dismisses global notifications", async () => {
    const pinia = createPinia();
    setActivePinia(pinia);
    const store = useToastStore();
    store.add("Настройки сохранены.", "success", 0);
    const wrapper = await mountSuspended(AppToastStack, {
      global: { plugins: [pinia] },
    });

    expect(wrapper.text()).toContain("Настройки сохранены.");
    await wrapper.find("button").trigger("click");
    expect(store.items).toHaveLength(0);
  });
});
