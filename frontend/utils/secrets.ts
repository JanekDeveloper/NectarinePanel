import type {
  EnvironmentVariable,
  EnvironmentVariableVersion,
} from "~/types/api";
import { translateMessage, type PanelLocale } from "~/locales/messages";

export interface DotenvPreview {
  keys: string[];
  error: string;
}

const ENV_KEY_PATTERN = /^[A-Z_][A-Z0-9_]{0,254}$/;

export function validateEnvironmentKey(value: string): boolean {
  return ENV_KEY_PATTERN.test(value.trim().toUpperCase());
}

export function normalizeEnvironmentKey(value: string): string {
  return value.trim().toUpperCase();
}

export function parseDotenvPreview(
  content: string,
  locale: PanelLocale = "ru",
): DotenvPreview {
  const keys: string[] = [];
  const seen = new Set<string>();
  const lines = content.split(/\r?\n/);
  for (let index = 0; index < lines.length; index += 1) {
    let line = lines[index]?.trim() ?? "";
    if (!line || line.startsWith("#")) continue;
    if (line.startsWith("export ")) line = line.slice(7).trimStart();
    if (!line.includes("=")) {
      return {
        keys,
        error: translateMessage(locale, "dotenv.missingEquals", {
          line: index + 1,
        }),
      };
    }
    const key = line.split("=", 1)[0]?.trim() ?? "";
    if (!ENV_KEY_PATTERN.test(key)) {
      return {
        keys,
        error: translateMessage(locale, "dotenv.invalidName", {
          line: index + 1,
        }),
      };
    }
    if (seen.has(key)) {
      return {
        keys,
        error: translateMessage(locale, "dotenv.duplicate", {
          line: index + 1,
          key,
        }),
      };
    }
    seen.add(key);
    keys.push(key);
  }
  return {
    keys,
    error: keys.length ? "" : translateMessage(locale, "dotenv.empty"),
  };
}

export function secretStats(
  variables: EnvironmentVariable[] | null | undefined,
) {
  const items = variables ?? [];
  return {
    total: items.length,
    secret: items.filter((item) => item.is_secret).length,
    public: items.filter((item) => !item.is_secret).length,
  };
}

export function versionActionLabel(
  version: EnvironmentVariableVersion,
  locale: PanelLocale = "ru",
): string {
  const labels: Record<string, string> = {
    create: translateMessage(locale, "version.create"),
    update: translateMessage(locale, "version.update"),
    delete: translateMessage(locale, "version.delete"),
    reveal: "Reveal",
    rollback: "Rollback",
    import: "Import",
  };
  return labels[version.action] ?? version.action;
}
