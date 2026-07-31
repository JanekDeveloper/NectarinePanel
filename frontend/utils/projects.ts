import type {
  MetricSample,
  Project,
  ProjectMetricValues,
  ProjectResourcePolicyMetric,
  ProjectResourceViolation,
} from "~/types/api";
import { translateMessage, type PanelLocale } from "~/locales/messages";

export type ProjectStatusFilter = Project["status"] | "all";
export type ProjectSortKey = "updated" | "created" | "name" | "status";

export interface ProjectFilters {
  query: string;
  status: ProjectStatusFilter;
  runtime: string;
  sort: ProjectSortKey;
}

export interface ProjectSummary {
  total: number;
  running: number;
  failed: number;
  deploying: number;
  stopped: number;
}

export function projectInitials(name: string): string {
  return name.trim().slice(0, 2).toUpperCase() || "PR";
}

export function summarizeProjects(projects: Project[]): ProjectSummary {
  return projects.reduce<ProjectSummary>(
    (summary, project) => {
      summary.total += 1;
      if (project.status === "running") summary.running += 1;
      if (project.status === "failed") summary.failed += 1;
      if (project.status === "deploying") summary.deploying += 1;
      if (project.status === "stopped") summary.stopped += 1;
      return summary;
    },
    { total: 0, running: 0, failed: 0, deploying: 0, stopped: 0 },
  );
}

export function runtimeOptions(projects: Project[]): string[] {
  return [...new Set(projects.map((project) => project.runtime_type))].sort(
    (left, right) => left.localeCompare(right),
  );
}

export function filterProjects(
  projects: Project[],
  filters: ProjectFilters,
): Project[] {
  const query = filters.query.trim().toLowerCase();
  return projects.filter((project) => {
    const matchesQuery =
      !query ||
      project.name.toLowerCase().includes(query) ||
      project.slug.toLowerCase().includes(query) ||
      project.runtime_type.toLowerCase().includes(query) ||
      project.branch.toLowerCase().includes(query);
    const matchesStatus =
      filters.status === "all" || project.status === filters.status;
    const matchesRuntime =
      filters.runtime === "all" || project.runtime_type === filters.runtime;
    return matchesQuery && matchesStatus && matchesRuntime;
  });
}

export function sortProjects(
  projects: Project[],
  sort: ProjectSortKey,
): Project[] {
  const sorted = [...projects];
  const statusWeight: Record<Project["status"], number> = {
    failed: 0,
    deploying: 1,
    running: 2,
    stopped: 3,
    created: 4,
  };
  sorted.sort((left, right) => {
    if (sort === "name") return left.name.localeCompare(right.name);
    if (sort === "status")
      return statusWeight[left.status] - statusWeight[right.status];
    const key = sort === "created" ? "created_at" : "updated_at";
    return Date.parse(right[key]) - Date.parse(left[key]);
  });
  return sorted;
}

export function visibleProjects(
  projects: Project[],
  filters: ProjectFilters,
): Project[] {
  return sortProjects(filterProjects(projects, filters), filters.sort);
}

export function metricValues(
  sample: MetricSample | null | undefined,
): ProjectMetricValues | null {
  if (!sample) return null;
  return sample.values as ProjectMetricValues;
}

export function formatBytes(
  value: number | null | undefined,
  locale: PanelLocale = "ru",
): string {
  if (value === null || value === undefined)
    return translateMessage(locale, "common.noData");
  if (value < 1024) return `${value} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let current = value / 1024;
  let unitIndex = 0;
  while (current >= 1024 && unitIndex < units.length - 1) {
    current /= 1024;
    unitIndex += 1;
  }
  return `${current.toFixed(current >= 10 ? 0 : 1)} ${units[unitIndex]}`;
}

export function formatPercent(
  value: number | null | undefined,
  locale: PanelLocale = "ru",
): string {
  if (value === null || value === undefined)
    return translateMessage(locale, "common.noData");
  return `${value.toFixed(1)}%`;
}

export function formatLimitBytes(
  megabytes: number | null | undefined,
  locale: PanelLocale = "ru",
): string {
  if (megabytes === null || megabytes === undefined)
    return translateMessage(locale, "projects.noLimit");
  return formatBytes(megabytes * 1024 * 1024, locale);
}

export function resourcePolicySummary(
  policy: ProjectResourcePolicyMetric | null | undefined,
  locale: PanelLocale = "ru",
): string {
  if (!policy?.enabled)
    return translateMessage(locale, "projects.policyDisabled");
  const limits = [
    policy.cpu_cores === null
      ? null
      : `CPU ${policy.cpu_cores} ${translateMessage(locale, "projects.cores")}`,
    policy.memory_mb === null
      ? null
      : `RAM ${formatLimitBytes(policy.memory_mb, locale)}`,
    policy.disk_mb === null
      ? null
      : `${translateMessage(locale, "projects.disk")} ${formatLimitBytes(policy.disk_mb, locale)}`,
  ].filter(Boolean);
  return limits.length
    ? limits.join(" · ")
    : translateMessage(locale, "projects.policyEnabled");
}

export function violationLabel(
  violation: ProjectResourceViolation,
  locale: PanelLocale = "ru",
): string {
  const resource =
    violation.resource === "memory"
      ? "RAM"
      : violation.resource === "disk"
        ? translateMessage(locale, "projects.disk")
        : violation.resource.toUpperCase();
  if (violation.unit === "bytes") {
    return `${resource}: ${formatBytes(violation.current, locale)} / ${formatBytes(violation.limit, locale)}`;
  }
  return `${resource}: ${violation.current.toFixed(1)}% / ${violation.limit.toFixed(1)}%`;
}

export function policyUsageLabel(
  values: ProjectMetricValues | null,
  resource: "cpu" | "memory" | "disk",
  locale: PanelLocale = "ru",
): string {
  const policy = values?.resource_policy;
  if (!policy?.enabled) return translateMessage(locale, "projects.noLimit");
  if (resource === "cpu") {
    if (policy.cpu_cores === null)
      return translateMessage(locale, "projects.noLimit");
    return `${formatPercent(values?.cpu_percent, locale)} / ${(policy.cpu_cores * 100).toFixed(0)}%`;
  }
  if (resource === "memory") {
    if (policy.memory_mb === null)
      return translateMessage(locale, "projects.noLimit");
    return `${formatBytes(values?.memory_used, locale)} / ${formatLimitBytes(policy.memory_mb, locale)}`;
  }
  if (policy.disk_mb === null)
    return translateMessage(locale, "projects.noLimit");
  return `${formatBytes(values?.disk_bytes, locale)} / ${formatLimitBytes(policy.disk_mb, locale)}`;
}
