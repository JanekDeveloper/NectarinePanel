import type { Project } from "~/types/api";

export const PROJECT_SETTINGS_SECTIONS = [
  "general",
  "source",
  "runtime",
  "resources",
  "access",
] as const;

export type ProjectSettingsSection = (typeof PROJECT_SETTINGS_SECTIONS)[number];

export interface ProjectSettingsDraft {
  name: string;
  description: string;
  project_type: string;
  runtime_type: string;
  source_type: "files" | "git";
  repository_url: string;
  branch: string;
  install_command: string;
  build_command: string;
  start_command: string;
  output_directory: string;
  healthcheck_url: string;
  upstream_port: number | null;
  runtime_config_text: string;
}

export interface ProjectSettingsPayload {
  name: string;
  description: string | null;
  project_type: string;
  runtime_type: string;
  repository_url: string | null;
  branch: string;
  install_command: string | null;
  build_command: string | null;
  start_command: string | null;
  output_directory: string | null;
  healthcheck_url: string | null;
  runtime_config: Record<string, unknown>;
}

/**
 * Returns a supported settings section or the default section.
 */
export function normalizeProjectSettingsSection(
  value: unknown,
  allowAccess = true,
): ProjectSettingsSection {
  const candidate = Array.isArray(value) ? value[0] : value;
  if (
    typeof candidate === "string" &&
    PROJECT_SETTINGS_SECTIONS.includes(candidate as ProjectSettingsSection) &&
    (candidate !== "access" || allowAccess)
  ) {
    return candidate as ProjectSettingsSection;
  }
  return "general";
}

/**
 * Creates an editable settings draft from the persisted project model.
 */
export function projectSettingsDraft(project: Project): ProjectSettingsDraft {
  const runtimeConfig = { ...(project.runtime_config ?? {}) };
  const upstreamPort = runtimeConfig.upstream_port ?? runtimeConfig.host_port;
  delete runtimeConfig.upstream_port;
  delete runtimeConfig.host_port;
  return {
    name: project.name,
    description: project.description ?? "",
    project_type: project.project_type,
    runtime_type: project.runtime_type,
    source_type: project.repository_url ? "git" : "files",
    repository_url: project.repository_url ?? "",
    branch: project.branch,
    install_command: project.install_command ?? "",
    build_command: project.build_command ?? "",
    start_command: project.start_command ?? "",
    output_directory: project.output_directory ?? "",
    healthcheck_url: project.healthcheck_url ?? "",
    upstream_port: typeof upstreamPort === "number" ? upstreamPort : null,
    runtime_config_text: JSON.stringify(runtimeConfig, null, 2),
  };
}

/**
 * Parses advanced runtime JSON and applies the dedicated upstream port field.
 */
export function parseProjectRuntimeConfig(
  text: string,
  upstreamPort: number | null,
): Record<string, unknown> {
  const parsed = JSON.parse(text || "{}") as unknown;
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new Error("Runtime config must be a JSON object.");
  }
  const config = { ...(parsed as Record<string, unknown>) };
  if (upstreamPort !== null) {
    if (
      typeof upstreamPort !== "number" ||
      !Number.isInteger(upstreamPort) ||
      upstreamPort < 1024 ||
      upstreamPort > 65535
    ) {
      throw new RangeError("Upstream port must be between 1024 and 65535.");
    }
    config.upstream_port = upstreamPort;
  } else {
    delete config.upstream_port;
  }
  delete config.host_port;
  return config;
}

/**
 * Builds the existing project PATCH payload from a settings draft.
 */
export function buildProjectSettingsPayload(
  draft: ProjectSettingsDraft,
): ProjectSettingsPayload {
  return {
    name: draft.name.trim(),
    description: draft.description.trim() || null,
    project_type: draft.project_type,
    runtime_type: draft.runtime_type,
    repository_url:
      draft.source_type === "git" ? draft.repository_url.trim() || null : null,
    branch: draft.branch.trim() || "main",
    install_command: draft.install_command.trim() || null,
    build_command: draft.build_command.trim() || null,
    start_command: draft.start_command.trim() || null,
    output_directory: draft.output_directory.trim() || null,
    healthcheck_url: draft.healthcheck_url.trim() || null,
    runtime_config: parseProjectRuntimeConfig(
      draft.runtime_config_text,
      draft.upstream_port,
    ),
  };
}

/**
 * Produces a stable value representation for semantic dirty-state checks.
 */
export function projectSettingsFingerprint(
  draft: ProjectSettingsDraft,
): string {
  try {
    return JSON.stringify(canonicalize(buildProjectSettingsPayload(draft)));
  } catch {
    return JSON.stringify(draft);
  }
}

/**
 * Recursively sorts object keys to make JSON comparisons order-independent.
 */
function canonicalize(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(canonicalize);
  if (!value || typeof value !== "object") return value;
  return Object.fromEntries(
    Object.entries(value as Record<string, unknown>)
      .sort(([left], [right]) => left.localeCompare(right))
      .map(([key, nestedValue]) => [key, canonicalize(nestedValue)]),
  );
}
