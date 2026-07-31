import { describe, expect, it } from "vitest";

import type { Project } from "../types/api";
import {
  buildProjectSettingsPayload,
  normalizeProjectSettingsSection,
  parseProjectRuntimeConfig,
  projectSettingsDraft,
  projectSettingsFingerprint,
} from "../utils/project-settings";

const project: Project = {
  id: "project-id",
  name: "Jannet API",
  slug: "jannet-api",
  description: "Backend",
  project_type: "backend",
  runtime_type: "systemd",
  status: "running",
  repository_url: "https://github.com/example/api.git",
  branch: "main",
  install_command: "npm ci",
  build_command: "npm run build",
  start_command: "node dist/server.js",
  output_directory: "dist",
  healthcheck_url: "https://api.example.com/health",
  notes: null,
  runtime_config: {
    systemd_unit: "jannet-api.service",
    upstream_port: 8090,
  },
  created_at: "2026-07-01T10:00:00.000Z",
  updated_at: "2026-07-01T12:00:00.000Z",
};

describe("project settings utilities", () => {
  it("normalizes section query values and protects unavailable access", () => {
    expect(normalizeProjectSettingsSection("source")).toBe("source");
    expect(normalizeProjectSettingsSection(["runtime"])).toBe("runtime");
    expect(normalizeProjectSettingsSection("unknown")).toBe("general");
    expect(normalizeProjectSettingsSection("access", false)).toBe("general");
  });

  it("separates upstream port from advanced runtime JSON", () => {
    const draft = projectSettingsDraft(project);

    expect(draft.upstream_port).toBe(8090);
    expect(JSON.parse(draft.runtime_config_text)).toEqual({
      systemd_unit: "jannet-api.service",
    });
  });

  it("normalizes the legacy Docker host port field", () => {
    const draft = projectSettingsDraft({
      ...project,
      runtime_type: "docker",
      runtime_config: {
        internal_port: 8000,
        host_port: 18000,
      },
    });

    expect(draft.upstream_port).toBe(18000);
    expect(JSON.parse(draft.runtime_config_text)).toEqual({
      internal_port: 8000,
    });
    expect(buildProjectSettingsPayload(draft).runtime_config).toEqual({
      internal_port: 8000,
      upstream_port: 18000,
    });
  });

  it("builds a compatible PATCH payload and clears file source fields", () => {
    const draft = projectSettingsDraft(project);
    draft.source_type = "files";
    draft.description = " ";

    expect(buildProjectSettingsPayload(draft)).toMatchObject({
      name: "Jannet API",
      description: null,
      repository_url: null,
      branch: "main",
      runtime_config: {
        systemd_unit: "jannet-api.service",
        upstream_port: 8090,
      },
    });
  });

  it("rejects non-object runtime JSON", () => {
    expect(() => parseProjectRuntimeConfig("[]", null)).toThrow(
      "Runtime config must be a JSON object.",
    );
    expect(() => parseProjectRuntimeConfig("null", null)).toThrow(
      "Runtime config must be a JSON object.",
    );
  });

  it("validates the dedicated upstream port", () => {
    expect(() => parseProjectRuntimeConfig("{}", 80)).toThrow(
      "Upstream port must be between 1024 and 65535.",
    );
    expect(() => parseProjectRuntimeConfig("{}", 65536)).toThrow(
      "Upstream port must be between 1024 and 65535.",
    );
  });

  it("treats JSON whitespace and key order as the same settings state", () => {
    const first = projectSettingsDraft(project);
    const second = projectSettingsDraft(project);
    second.runtime_config_text =
      '{ "z": 1, "systemd_unit": "jannet-api.service" }';
    first.runtime_config_text =
      '{ "systemd_unit": "jannet-api.service", "z": 1 }';

    expect(projectSettingsFingerprint(first)).toBe(
      projectSettingsFingerprint(second),
    );
  });
});
