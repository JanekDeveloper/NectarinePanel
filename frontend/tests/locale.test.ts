import { describe, expect, it } from "vitest";
import { mountSuspended } from "@nuxt/test-utils/runtime";

import LocaleSwitcher from "../components/LocaleSwitcher.vue";
import { resolvePanelLocale } from "../composables/useLocale";
import { deploymentMessages } from "../locales/deployments";
import { environmentMessages } from "../locales/environment";
import { fileMessages } from "../locales/files";
import {
  localeOptions,
  messages,
  panelLocales,
  translateMessage,
} from "../locales/messages";
import { minecraftMessages } from "../locales/minecraft";
import { projectCreateMessages } from "../locales/project-create";
import { projectOverviewMessages } from "../locales/project-overview";
import { projectSettingsMessages } from "../locales/project-settings";

describe("panel localization", () => {
  it("exposes every supported locale in the language switcher", () => {
    expect(panelLocales).toEqual(["ru", "en", "uk", "pl"]);
    expect(localeOptions.map(({ value }) => value)).toEqual(panelLocales);
  });

  it("keeps message catalogs complete and non-empty", () => {
    const referenceKeys = Object.keys(messages.en).sort();

    for (const locale of panelLocales) {
      expect(Object.keys(messages[locale]).sort()).toEqual(referenceKeys);
      expect(Object.values(messages[locale]).every(Boolean)).toBe(true);
    }
  });

  it("translates and interpolates messages", () => {
    expect(translateMessage("en", "console.sessionEnded", { code: 0 })).toBe(
      "Session ended (code 0).",
    );
    expect(translateMessage("ru", "common.save")).toBe("Сохранить");
    expect(translateMessage("uk", "common.save")).toBe("Зберегти");
    expect(translateMessage("pl", "common.save")).toBe("Zapisz");
  });

  it("keeps untranslated English prose out of Russian catalogs", () => {
    const catalogs = [
      messages.ru,
      deploymentMessages.ru,
      environmentMessages.ru,
      fileMessages.ru,
      minecraftMessages.ru,
      projectCreateMessages.ru,
      projectOverviewMessages.ru,
      projectSettingsMessages.ru,
    ];
    const copy = catalogs
      .flatMap((catalog) => Object.values(catalog))
      .join("\n")
      .replaceAll(/\{[^}]+\}/g, "");
    const untranslated =
      /\b(?:backend|runtime|immutable|release|plaintext|payload|tombstone|symlink|chroot|uploads|destination|credentials?|provisioning|healthcheck|enforcement|cores|maintainer|owner|admin)\b|shell expansion|starter files|health status|output directory|cron (?:jobs|expression)|reverse proxy|docker (?:build|run)|host artifacts|monitor-only|read-only viewer/i;

    expect(copy).not.toMatch(untranslated);
  });

  it("prefers a stored locale and otherwise detects the browser language", () => {
    expect(resolvePanelLocale("pl", ["uk-UA"])).toBe("pl");
    expect(resolvePanelLocale(null, ["fr-FR", "uk-UA"])).toBe("uk");
    expect(resolvePanelLocale("invalid", ["en-GB"])).toBe("en");
    expect(resolvePanelLocale(null, ["fr-FR"])).toBe("ru");
  });

  it("persists a locale change and updates the document language", async () => {
    const values = new Map<string, string>();
    Object.defineProperty(window, "localStorage", {
      configurable: true,
      value: {
        getItem: (key: string) => values.get(key) ?? null,
        setItem: (key: string, value: string) => values.set(key, value),
      },
    });
    const wrapper = await mountSuspended(LocaleSwitcher);
    await wrapper.find("select").setValue("pl");

    expect(window.localStorage.getItem("nectarine.locale")).toBe("pl");
    expect(document.documentElement.lang).toBe("pl");

    await wrapper.find("select").setValue("ru");
  });
});
