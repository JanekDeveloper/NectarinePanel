import { describe, expect, it } from "vitest";

import type { ProjectTemplate } from "../types/api";
import {
  applyProjectTemplate,
  buildProjectCreateRequest,
  emptyProjectCreateDraft,
  validateProjectCreateStep,
} from "../utils/project-create";

const template: ProjectTemplate = {
  id: "fastapi",
  name: "FastAPI app",
  description: "Python API",
  project_type: "backend",
  runtime_type: "docker",
  install_command: null,
  build_command: null,
  start_command: "uvicorn app.main:app",
  output_directory: null,
  healthcheck_url: null,
  runtime_config: { internal_port: 8000 },
  suggested_env: ["DATABASE_URL"],
};

describe("project creation utilities", () => {
  it("applies runtime defaults without replacing entered identity or source", () => {
    const draft = emptyProjectCreateDraft();
    draft.name = "Billing API";
    draft.description = "Custom description";
    draft.source_type = "git";
    draft.repository_url = "https://github.com/example/billing.git";

    const result = applyProjectTemplate(draft, template);

    expect(result).toMatchObject({
      name: "Billing API",
      description: "Custom description",
      source_type: "git",
      repository_url: "https://github.com/example/billing.git",
      project_type: "backend",
      runtime_type: "docker",
      start_command: "uvicorn app.main:app",
    });
  });

  it("validates identity, Git source and URLs on the details step", () => {
    const draft = emptyProjectCreateDraft();
    draft.name = "x";
    draft.source_type = "git";
    draft.repository_url = "ssh://git@example.com/repository.git";
    draft.branch = "";

    expect(validateProjectCreateStep(draft, "details", false)).toEqual({
      name: "Введите минимум 2 символа.",
      repository_url: "Используйте корректный HTTP(S) URL.",
      branch: "Укажите ветку.",
    });
  });

  it("does not validate template-owned runtime fields", () => {
    const draft = emptyProjectCreateDraft();
    draft.healthcheck_url = "invalid";

    expect(validateProjectCreateStep(draft, "runtime", true)).toEqual({});
    expect(validateProjectCreateStep(draft, "runtime", false)).toEqual({
      healthcheck_url: "Используйте корректный HTTP(S) URL.",
    });
  });

  it("uses the default branch when the source is uploaded files", () => {
    const draft = emptyProjectCreateDraft();
    draft.name = "Uploaded app";
    draft.branch = "";

    expect(validateProjectCreateStep(draft, "details", false)).toEqual({});
    expect(buildProjectCreateRequest(draft, "custom").body.branch).toBe("main");
  });

  it("builds the template endpoint payload without ignored runtime overrides", () => {
    const draft = applyProjectTemplate(emptyProjectCreateDraft(), template);
    draft.name = "Billing API";

    expect(buildProjectCreateRequest(draft, "fastapi")).toEqual({
      path: "/projects/from-template",
      body: {
        template_id: "fastapi",
        name: "Billing API",
        description: "Python API",
        repository_url: null,
        branch: "main",
      },
    });
  });

  it("builds a compatible custom payload and clears repository for files", () => {
    const draft = emptyProjectCreateDraft();
    draft.name = "Static site";
    draft.project_type = "static";
    draft.runtime_type = "static";
    draft.source_type = "files";
    draft.repository_url = "https://github.com/ignored/repository.git";
    draft.output_directory = " public ";

    expect(buildProjectCreateRequest(draft, "custom")).toMatchObject({
      path: "/projects",
      body: {
        name: "Static site",
        project_type: "static",
        runtime_type: "static",
        repository_url: null,
        output_directory: "public",
      },
    });
  });
});
