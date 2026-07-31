import { mountSuspended } from "@nuxt/test-utils/runtime";
import { nextTick } from "vue";
import { describe, expect, it } from "vitest";

import ConfirmDialog from "../components/ConfirmDialog.vue";

describe("ConfirmDialog", () => {
  it("requires exact text before confirming destructive actions", async () => {
    const wrapper = await mountSuspended(ConfirmDialog, {
      attachTo: document.body,
      global: {
        stubs: {
          teleport: true,
        },
      },
      props: {
        open: true,
        title: "Удалить?",
        message: "Введите имя для подтверждения.",
        expectedText: "project",
      },
    });

    expect(wrapper.text()).toContain("Удалить?");
    expect(
      wrapper.find<HTMLButtonElement>(".button-primary").element.disabled,
    ).toBe(true);

    await wrapper.find("input").setValue("project");
    await nextTick();
    expect(
      wrapper.find<HTMLButtonElement>(".button-primary").element.disabled,
    ).toBe(false);
    await wrapper.find(".button-primary").trigger("click");

    expect(wrapper.emitted("confirm")).toEqual([["project"]]);
    wrapper.unmount();
  });
});
