import { describe, expect, it } from "vitest";

import {
  normalizeEnvironmentKey,
  parseDotenvPreview,
  secretStats,
  validateEnvironmentKey,
  versionActionLabel,
} from "../utils/secrets";

describe("secrets utils", () => {
  it("validates and normalizes environment keys", () => {
    expect(normalizeEnvironmentKey(" api_token ")).toBe("API_TOKEN");
    expect(validateEnvironmentKey("API_TOKEN")).toBe(true);
    expect(validateEnvironmentKey("1BAD")).toBe(false);
  });

  it("previews dotenv keys and rejects malformed lines", () => {
    expect(
      parseDotenvPreview("DATABASE_URL=db\nexport API_TOKEN='token'"),
    ).toEqual({
      keys: ["DATABASE_URL", "API_TOKEN"],
      error: "",
    });
    expect(parseDotenvPreview("NO_EQUALS").error).toContain("нет символа");
    expect(parseDotenvPreview("API_TOKEN=1\nAPI_TOKEN=2").error).toContain(
      "повторяется",
    );
  });

  it("summarizes secret and public variables", () => {
    expect(
      secretStats([
        {
          id: "1",
          key: "API_TOKEN",
          value: "••••••••",
          is_secret: true,
          updated_at: "2026-01-01T00:00:00Z",
        },
        {
          id: "2",
          key: "PORT",
          value: "3000",
          is_secret: false,
          updated_at: "2026-01-01T00:00:00Z",
        },
      ]),
    ).toEqual({ total: 2, secret: 1, public: 1 });
  });

  it("localizes version actions", () => {
    expect(
      versionActionLabel({
        id: "1",
        key: "API_TOKEN",
        is_secret: true,
        value_sha256: "abc",
        action: "rollback",
        actor_id: null,
        reason: null,
        created_at: "2026-01-01T00:00:00Z",
      }),
    ).toBe("Rollback");
  });
});
