import type { Deployment, UserRole } from "~/types/api";
import { canWriteProjects } from "~/utils/rbac";
import {
  localeTags,
  translateMessage,
  type PanelLocale,
} from "~/locales/messages";

export function deploymentBadgeStatus(status: string): string {
  if (status === "success") return "running";
  if (status === "failed") return "failed";
  if (status === "queued") return "created";
  return "deploying";
}

export function deploymentTitle(
  deployment: Pick<Deployment, "source_revision">,
  locale: PanelLocale = "ru",
): string {
  return (
    deployment.source_revision ||
    translateMessage(locale, "projects.noRevision")
  );
}

export function formatDeployDate(
  value: string | null,
  locale: PanelLocale = "ru",
): string {
  if (!value) return "—";
  return new Date(value).toLocaleString(localeTags[locale]);
}

export function deploymentDuration(
  deployment: Pick<Deployment, "started_at" | "finished_at">,
  locale: PanelLocale = "ru",
): string {
  if (!deployment.started_at || !deployment.finished_at) return "—";
  const started = new Date(deployment.started_at).getTime();
  const finished = new Date(deployment.finished_at).getTime();
  const seconds = Math.max(0, Math.round((finished - started) / 1000));
  if (seconds < 60)
    return translateMessage(locale, "projects.seconds", { count: seconds });
  const minutes = Math.floor(seconds / 60);
  return translateMessage(locale, "projects.minutesSeconds", {
    minutes,
    seconds: seconds % 60,
  });
}

export function jobKindLabel(kind: string): string {
  if (kind === "project.archive_deploy") return "ZIP deploy";
  if (kind === "project.rollback") return "Rollback";
  return "Git deploy";
}

export function isTerminalJobStatus(status: string): boolean {
  return ["success", "failure", "failed", "revoked"].includes(
    status.toLowerCase(),
  );
}

export function acceptsDeploymentArchive(filename: string): boolean {
  return filename.toLowerCase().endsWith(".zip");
}

export function canRollbackDeployment(
  role: UserRole | null | undefined,
  deployment: Pick<Deployment, "status">,
): boolean {
  return deployment.status === "success" && canWriteProjects(role);
}
