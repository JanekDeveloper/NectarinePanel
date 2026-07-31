import { describe, expect, it } from "vitest";

import type { MetricSample, Project } from "../types/api";
import {
  filterProjects,
  formatBytes,
  formatLimitBytes,
  formatPercent,
  metricValues,
  policyUsageLabel,
  projectInitials,
  resourcePolicySummary,
  runtimeOptions,
  sortProjects,
  summarizeProjects,
  violationLabel,
  visibleProjects,
} from "../utils/projects";

function project(overrides: Partial<Project>): Project {
  return {
    id: overrides.id ?? "project-id",
    name: overrides.name ?? "Project",
    slug: overrides.slug ?? "project",
    description: overrides.description ?? null,
    project_type: overrides.project_type ?? "backend",
    runtime_type: overrides.runtime_type ?? "systemd",
    status: overrides.status ?? "running",
    repository_url: overrides.repository_url ?? null,
    branch: overrides.branch ?? "main",
    install_command: overrides.install_command ?? null,
    build_command: overrides.build_command ?? null,
    start_command: overrides.start_command ?? null,
    output_directory: overrides.output_directory ?? null,
    healthcheck_url: overrides.healthcheck_url ?? null,
    notes: overrides.notes ?? null,
    runtime_config: overrides.runtime_config ?? {},
    created_at: overrides.created_at ?? "2026-07-01T10:00:00.000Z",
    updated_at: overrides.updated_at ?? "2026-07-01T10:00:00.000Z",
  };
}

describe("project utilities", () => {
  const projects = [
    project({
      id: "api",
      name: "Jannet API",
      slug: "jannet-api",
      runtime_type: "systemd",
      status: "running",
      branch: "main",
      created_at: "2026-07-01T10:00:00.000Z",
      updated_at: "2026-07-01T12:00:00.000Z",
    }),
    project({
      id: "web",
      name: "Panel Web",
      slug: "panel-web",
      runtime_type: "docker",
      status: "failed",
      branch: "release",
      created_at: "2026-07-01T11:00:00.000Z",
      updated_at: "2026-07-01T11:30:00.000Z",
    }),
    project({
      id: "bot",
      name: "Telegram Bot",
      slug: "telegram-bot",
      runtime_type: "systemd",
      status: "stopped",
      branch: "main",
      created_at: "2026-07-01T09:00:00.000Z",
      updated_at: "2026-07-01T09:30:00.000Z",
    }),
  ];

  it("summarizes project statuses", () => {
    expect(summarizeProjects(projects)).toEqual({
      total: 3,
      running: 1,
      failed: 1,
      deploying: 0,
      stopped: 1,
    });
  });

  it("filters by query, status and runtime", () => {
    expect(
      filterProjects(projects, {
        query: "release",
        status: "failed",
        runtime: "docker",
        sort: "updated",
      }).map((item) => item.id),
    ).toEqual(["web"]);
  });

  it("sorts projects by status priority and updated time", () => {
    expect(sortProjects(projects, "status").map((item) => item.id)).toEqual([
      "web",
      "api",
      "bot",
    ]);
    expect(sortProjects(projects, "updated").map((item) => item.id)).toEqual([
      "api",
      "web",
      "bot",
    ]);
  });

  it("combines filtering and sorting for visible projects", () => {
    expect(
      visibleProjects(projects, {
        query: "main",
        status: "all",
        runtime: "systemd",
        sort: "name",
      }).map((item) => item.id),
    ).toEqual(["api", "bot"]);
  });

  it("returns runtime options in deterministic order", () => {
    expect(runtimeOptions(projects)).toEqual(["docker", "systemd"]);
  });

  it("formats empty and present metrics", () => {
    const sample: MetricSample = {
      id: "sample",
      project_id: "api",
      captured_at: "2026-07-01T12:00:00.000Z",
      values: {
        runtime_type: "systemd",
        status: "running",
        disk_bytes: 1024 ** 3,
        health_status: "healthy",
        health_status_code: 200,
        cpu_percent: 12.345,
      },
    };
    expect(metricValues(sample)?.health_status).toBe("healthy");
    expect(metricValues(null)).toBeNull();
    expect(formatPercent(12.345)).toBe("12.3%");
    expect(formatPercent(undefined)).toBe("Нет данных");
    expect(formatBytes(1024 ** 3)).toBe("1.0 GB");
    expect(formatBytes(undefined)).toBe("Нет данных");
  });

  it("formats resource policies and violations", () => {
    expect(formatLimitBytes(2048)).toBe("2.0 GB");
    expect(
      resourcePolicySummary({
        enabled: true,
        cpu_cores: 1.5,
        memory_mb: 1024,
        disk_mb: null,
        enforcement: "enforced",
      }),
    ).toBe("CPU 1.5 ядер · RAM 1.0 GB");
    expect(
      policyUsageLabel(
        {
          runtime_type: "docker",
          status: "running",
          disk_bytes: 2 * 1024 ** 3,
          health_status: "ok",
          health_status_code: 200,
          memory_used: 512 * 1024 ** 2,
          cpu_percent: 40,
          resource_policy: {
            enabled: true,
            cpu_cores: 1,
            memory_mb: 1024,
            disk_mb: 4096,
            enforcement: "enforced",
          },
        },
        "memory",
      ),
    ).toBe("512 MB / 1.0 GB");
    expect(
      violationLabel({
        resource: "disk",
        current: 5 * 1024 ** 3,
        limit: 4 * 1024 ** 3,
        unit: "bytes",
      }),
    ).toBe("Диск: 5.0 GB / 4.0 GB");
  });

  it("builds stable project initials", () => {
    expect(projectInitials("Jannet API")).toBe("JA");
    expect(projectInitials(" ")).toBe("PR");
  });
});
