import { describe, expect, it } from "vitest";

import {
  acceptsDeploymentArchive,
  canRollbackDeployment,
  deploymentBadgeStatus,
  deploymentDuration,
  deploymentTitle,
  isTerminalJobStatus,
  jobKindLabel,
} from "~/utils/deployments";

describe("deployment utils", () => {
  it("maps deployment statuses to badge statuses", () => {
    expect(deploymentBadgeStatus("success")).toBe("running");
    expect(deploymentBadgeStatus("failed")).toBe("failed");
    expect(deploymentBadgeStatus("queued")).toBe("created");
    expect(deploymentBadgeStatus("running")).toBe("deploying");
  });

  it("formats deployment labels and durations", () => {
    expect(deploymentTitle({ source_revision: null })).toBe("Без ревизии");
    expect(
      deploymentDuration({
        started_at: "2026-07-01T10:00:00Z",
        finished_at: "2026-07-01T10:01:05Z",
      }),
    ).toBe("1 мин 5 сек");
  });

  it("detects terminal jobs and accepted archive names", () => {
    expect(isTerminalJobStatus("SUCCESS")).toBe(true);
    expect(isTerminalJobStatus("progress")).toBe(false);
    expect(acceptsDeploymentArchive("release.zip")).toBe(true);
    expect(acceptsDeploymentArchive("release.tar.gz")).toBe(false);
  });

  it("allows rollback only for writable roles and successful deployments", () => {
    expect(canRollbackDeployment("owner", { status: "success" })).toBe(true);
    expect(canRollbackDeployment("maintainer", { status: "success" })).toBe(
      true,
    );
    expect(canRollbackDeployment("viewer", { status: "success" })).toBe(false);
    expect(canRollbackDeployment("admin", { status: "failed" })).toBe(false);
  });

  it("labels job kinds", () => {
    expect(jobKindLabel("project.archive_deploy")).toBe("ZIP deploy");
    expect(jobKindLabel("project.rollback")).toBe("Rollback");
    expect(jobKindLabel("project.git_deploy")).toBe("Git deploy");
  });
});
