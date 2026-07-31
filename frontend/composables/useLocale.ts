import {
  isPanelLocale,
  localeTags,
  translateMessage,
  type LocaleMessageKey,
  type PanelLocale,
} from "~/locales/messages";

export type { LocaleMessageKey, PanelLocale } from "~/locales/messages";

const STORAGE_KEY = "nectarine.locale";

export function resolvePanelLocale(
  stored: unknown,
  preferredLanguages: readonly string[] = [],
): PanelLocale {
  if (isPanelLocale(stored)) return stored;
  for (const language of preferredLanguages) {
    const candidate = language.toLowerCase().split("-")[0];
    if (isPanelLocale(candidate)) return candidate;
  }
  return "ru";
}

export function useLocale() {
  const localeCookie = useCookie<PanelLocale | null>(STORAGE_KEY, {
    default: () => null,
    maxAge: 60 * 60 * 24 * 365,
    sameSite: "lax",
  });
  const locale = useState<PanelLocale>("panel-locale", () =>
    resolvePanelLocale(localeCookie.value),
  );

  function apply(value: PanelLocale, persist = true): void {
    locale.value = value;
    if (persist) localeCookie.value = value;
    if (!import.meta.client) return;
    document.documentElement.lang = value;
    if (!persist) return;
    try {
      window.localStorage?.setItem?.(STORAGE_KEY, value);
    } catch {
      // Storage can be unavailable in privacy-restricted browser contexts.
    }
  }

  function restore(): void {
    if (!import.meta.client) return;
    let stored: string | null = null;
    try {
      stored = window.localStorage?.getItem?.(STORAGE_KEY) ?? null;
    } catch {
      // Browser language remains a safe fallback when storage is unavailable.
    }
    apply(
      isPanelLocale(stored)
        ? stored
        : isPanelLocale(localeCookie.value)
          ? localeCookie.value
          : resolvePanelLocale(
              null,
              navigator.languages ?? [navigator.language],
            ),
    );
  }

  function t(
    key: LocaleMessageKey,
    parameters: Record<string, string | number> = {},
  ): string {
    return translateMessage(locale.value, key, parameters);
  }

  function dateTime(
    value: string | number | Date,
    options: Intl.DateTimeFormatOptions = {
      dateStyle: "medium",
      timeStyle: "short",
    },
  ): string {
    const date = value instanceof Date ? value : new Date(value);
    if (Number.isNaN(date.getTime())) return "—";
    return new Intl.DateTimeFormat(localeTags[locale.value], options).format(
      date,
    );
  }

  function number(value: number, options?: Intl.NumberFormatOptions): string {
    return new Intl.NumberFormat(localeTags[locale.value], options).format(
      value,
    );
  }

  return { locale: readonly(locale), apply, restore, t, dateTime, number };
}
