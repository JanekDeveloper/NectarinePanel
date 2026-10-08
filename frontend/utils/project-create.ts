import type { ProjectTemplate } from "~/types/api";
import { translateMessage, type PanelLocale } from "~/locales/messages";

export const PROJECT_CREATE_STEPS = [
  "template",
  "details",
  "runtime",
  "review",
] as const;

export type ProjectCreateStep = (typeof PROJECT_CREATE_STEPS)[number];
export type ProjectSourceType = "files" | "git";

export interface ProjectCreateDraft {
  name: string;
  description: string;
  project_type: string;
  runtime_type: string;
  source_type: ProjectSourceType;
  repository_url: string;
  branch: string;
  install_command: string;
  build_command: string;
  start_command: string;
  output_directory: string;
  healthcheck_url: string;
}

export interface ProjectCreateRequest {
  path: "/projects" | "/projects/from-template";
  body: Record<string, unknown>;
}

export type ProjectCreateErrors = Partial<
  Record<keyof ProjectCreateDraft, string>
>;

/**
 * Creates an empty project draft with safe defaults.
 */
export function emptyProjectCreateDraft(): ProjectCreateDraft {
  return {
    name: "",
    description: "",
    project_type: "docker",
    runtime_type: "docker",
    source_type: "files",
    repository_url: "",
    branch: "main",
    install_command: "",
    build_command: "",
    start_command: "",
    output_directory: "",
    healthcheck_url: "",
  };
}

/**
 * Applies template defaults while preserving user-entered identity and source.
 */
export function applyProjectTemplate(
  draft: ProjectCreateDraft,
  template: ProjectTemplate,
): ProjectCreateDraft {
  return {
    ...draft,
    description: draft.description.trim()
      ? draft.description
      : template.description,
    project_type: template.project_type,
    runtime_type: template.runtime_type,
    install_command: template.install_command ?? "",
    build_command: template.build_command ?? "",
    start_command: template.start_command ?? "",
    output_directory: template.output_directory ?? "",
    healthcheck_url: template.healthcheck_url ?? "",
  };
}

/**
 * Validates the fields owned by one creation step.
 */
export function validateProjectCreateStep(
  draft: ProjectCreateDraft,
  step: ProjectCreateStep,
  usesTemplate: boolean,
  locale: PanelLocale = "ru",
): ProjectCreateErrors {
  const errors: ProjectCreateErrors = {};

  if (step === "details" || step === "review") {
    const name = draft.name.trim();
    if (name.length < 2)
      errors.name = translateMessage(locale, "validation.minTwo");
    else if (name.length > 120)
      errors.name = translateMessage(locale, "validation.nameMax");

    if (draft.description.length > 5000) {
      errors.description = translateMessage(
        locale,
        "validation.descriptionMax",
      );
    }

    if (draft.source_type === "git") {
      if (!draft.repository_url.trim()) {
        errors.repository_url = translateMessage(
          locale,
          "validation.repositoryRequired",
        );
      } else if (!isHttpUrl(draft.repository_url)) {
        errors.repository_url = translateMessage(locale, "validation.httpUrl");
      }

      const branch = draft.branch.trim();
      if (!branch)
        errors.branch = translateMessage(locale, "validation.branchRequired");
      else if (branch.length > 255)
        errors.branch = translateMessage(locale, "validation.branchMax");
      else if (hasControlCharacters(branch))
        errors.branch = translateMessage(
          locale,
          "validation.invalidCharacters",
        );
    }
  }

  if ((step === "runtime" || step === "review") && !usesTemplate) {
    validateLength(
      errors,
      "install_command",
      draft.install_command,
      4000,
      "Install command",
      locale,
    );
    validateLength(
      errors,
      "build_command",
      draft.build_command,
      4000,
      "Build command",
      locale,
    );
    validateLength(
      errors,
      "start_command",
      draft.start_command,
      4000,
      "Start command",
      locale,
    );
    validateLength(
      errors,
      "output_directory",
      draft.output_directory,
      512,
      "Output directory",
      locale,
    );

    if (hasControlCharacters(draft.output_directory)) {
      errors.output_directory = translateMessage(
        locale,
        "validation.outputInvalidCharacters",
      );
    }
    if (draft.healthcheck_url.trim() && !isHttpUrl(draft.healthcheck_url)) {
      errors.healthcheck_url = translateMessage(locale, "validation.httpUrl");
    }
  }

  return errors;
}

/**
 * Builds a request compatible with the existing project creation endpoints.
 */
export function buildProjectCreateRequest(
  draft: ProjectCreateDraft,
  selectedTemplateId: string,
): ProjectCreateRequest {
  const repositoryUrl =
    draft.source_type === "git" ? optionalValue(draft.repository_url) : null;
  const branch = draft.branch.trim() || "main";

  if (selectedTemplateId !== "custom") {
    return {
      path: "/projects/from-template",
      body: {
        template_id: selectedTemplateId,
        name: draft.name.trim(),
        description: optionalValue(draft.description),
        repository_url: repositoryUrl,
        branch,
      },
    };
  }

  return {
    path: "/projects",
    body: {
      name: draft.name.trim(),
      description: optionalValue(draft.description),
      project_type: draft.project_type,
      runtime_type: draft.runtime_type,
      repository_url: repositoryUrl,
      branch,
      install_command: optionalValue(draft.install_command),
      build_command: optionalValue(draft.build_command),
      start_command: optionalValue(draft.start_command),
      output_directory: optionalValue(draft.output_directory),
      healthcheck_url: optionalValue(draft.healthcheck_url),
    },
  };
}

/**
 * Returns a localized label for a project type.
 */
export function projectTypeLabel(
  value: string,
  locale: PanelLocale = "ru",
): string {
  return (
    {
      web: "Frontend",
      backend: "Backend",
      bot: translateMessage(locale, "projects.bot"),
      docker: "Docker",
      static: translateMessage(locale, "projects.staticSite"),
      minecraft_forge: "Minecraft Forge",
      minecraft_paper: "Minecraft Paper",
      minecraft_purpur: "Minecraft Purpur",
      minecraft_spigot: "Minecraft Spigot",
    }[value] ?? value
  );
}

/**
 * Returns a human-readable label for a runtime.
 */
export function runtimeTypeLabel(value: string): string {
  return (
    {
      docker: "Docker container",
      docker_compose: "Docker Compose",
      systemd: "systemd",
      pm2: "PM2",
      static: "Nginx static",
      minecraft_forge: "Minecraft Forge",
      minecraft_paper: "Minecraft Paper",
      minecraft_purpur: "Minecraft Purpur",
      minecraft_spigot: "Minecraft Spigot",
    }[value] ?? value
  );
}

/**
 * Returns true when a value is a valid HTTP or HTTPS URL.
 */
function isHttpUrl(value: string): boolean {
  try {
    const url = new URL(value.trim());
    return url.protocol === "http:" || url.protocol === "https:";
  } catch {
    return false;
  }
}

/**
 * Adds a maximum-length error for an optional field.
 */
function validateLength(
  errors: ProjectCreateErrors,
  field: keyof ProjectCreateDraft,
  value: string,
  maximum: number,
  label: string,
  locale: PanelLocale,
): void {
  if (value.length > maximum) {
    errors[field] = translateMessage(locale, "validation.maxCharacters", {
      label,
      maximum,
    });
  }
}

/**
 * Returns a trimmed string or null for optional API fields.
 */
function optionalValue(value: string): string | null {
  return value.trim() || null;
}

/**
 * Detects ASCII control characters rejected by the backend.
 */
function hasControlCharacters(value: string): boolean {
  return [...value].some((character) => character.charCodeAt(0) < 32);
}
