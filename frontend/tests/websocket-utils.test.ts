import { describe, expect, it } from "vitest";
import { buildWebSocketUrl } from "../utils/websocket";

describe("WebSocket URL construction", () => {
  it.each([
    ["http://152.89.254.55:9341/projects/1", "ws://152.89.254.55:9341"],
    ["https://panel.example.com/projects/1", "wss://panel.example.com"],
  ])("uses the page protocol and port for a proxied API: %s", (page, base) => {
    expect(
      buildWebSocketUrl(
        "/projects/1/terminal/live",
        "/api/v1/",
        "wss://incorrect.example.com",
        "test-token",
        page,
      ),
    ).toBe(`${base}/api/v1/projects/1/terminal/live?token=test-token`);
  });

  it("preserves a separate WebSocket host for an absolute API", () => {
    expect(
      buildWebSocketUrl(
        "logs/live",
        "https://api.example.com/custom/api/",
        "wss://socket.example.com/",
        "test-token",
        "https://panel.example.com/",
      ),
    ).toBe("wss://socket.example.com/custom/api/logs/live?token=test-token");
  });

  it("supports runtime configuration without a browser location", () => {
    expect(
      buildWebSocketUrl("/monitoring/live", "api/v1", "ws://localhost:8000", ""),
    ).toBe("ws://localhost:8000/api/v1/monitoring/live?token=");
  });

  it("encodes tokens as a single query parameter", () => {
    const url = new URL(
      buildWebSocketUrl("/logs/live", "/api/v1", "ws://localhost", "a+b&c=?"),
    );
    expect(url.searchParams.get("token")).toBe("a+b&c=?");
    expect(Array.from(url.searchParams.keys())).toEqual(["token"]);
  });
});
