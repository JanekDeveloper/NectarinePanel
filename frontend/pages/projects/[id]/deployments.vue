<script setup lang="ts">
import {
  acceptsDeploymentArchive,
  canRollbackDeployment,
  deploymentBadgeStatus,
  deploymentDuration as rawDeploymentDuration,
  deploymentTitle as rawDeploymentTitle,
  formatDeployDate as rawFormatDeployDate,
  isTerminalJobStatus,
  jobKindLabel,
} from "~/utils/deployments";
import {
  IconArchive,
  IconChevronDown,
  IconChevronRight,
  IconClock,
  IconCode,
  IconFileZip,
  IconGitBranch,
  IconHistory,
  IconPlayerPlay,
  IconRefresh,
  IconRotateClockwise,
  IconSettings,
  IconTerminal2,
} from "@tabler/icons-vue";
import type { Deployment, Job, Project } from "~/types/api";
import { deploymentMessages } from "~/locales/deployments";

const route = useRoute();
const projectId = String(route.params.id);
const api = useApi();
const auth = useAuthStore();
const localeController = useLocale();
const { t } = localeController;
const copy = computed(() => deploymentMessages[localeController.locale.value]);

const revision = ref("");
const archiveInput = ref<HTMLInputElement | null>(null);
const archiveFile = ref<File | null>(null);
const deployError = ref("");
const archiveError = ref("");
const logError = ref("");
const pendingAction = ref<"git" | "archive" | "rollback" | "">("");
const activeJob = ref<Job | null>(null);
const activeJobState = ref<Record<string, unknown> | null>(null);
const expandedDeploymentId = ref<string | null>(null);
const deploymentLogs = ref<Record<string, string>>({});
const rollbackTarget = ref<Deployment | null>(null);

let socket: WebSocket | null = null;
let pollTimer: ReturnType<typeof setInterval> | null = null;

const {
  data: project,
  pending: projectPending,
  error: projectError,
  refresh: refreshProject,
} = await useAsyncData(`deploy-project-${projectId}`, () =>
  api.request<Project>(`/projects/${projectId}`),
);
const {
  data: deployments,
  pending: deploymentsPending,
  error: deploymentsError,
  refresh: refreshDeployments,
} = await useAsyncData(`deployments-${projectId}`, () =>
  api.request<Deployment[]>(`/projects/${projectId}/deployments`),
);

const canDeploy = computed(() => auth.canWriteProjects);
const loading = computed(
  () => projectPending.value || deploymentsPending.value,
);
const loadError = computed(() => projectError.value || deploymentsError.value);
const latestDeployment = computed(() => deployments.value?.[0] ?? null);
const gitSourceConfigured = computed(() =>
  Boolean(project.value?.repository_url),
);
const hasActiveJob = computed(
  () =>
    activeJob.value !== null && !isTerminalJobStatus(activeJob.value.status),
);
const activeProgress = computed(() => {
  const stateProgress = activeJobState.value?.progress;
  if (typeof stateProgress === "number") return stateProgress;
  return activeJob.value?.progress ?? 0;
});
const activeStatus = computed(() => {
  const state = activeJobState.value?.state;
  if (typeof state === "string") return state.toLowerCase();
  return activeJob.value?.status ?? "queued";
});
const activeStage = computed(() => {
  const stage = activeJobState.value?.stage;
  return typeof stage === "string" ? stage : null;
});

function clearJobWatchers(): void {
  if (socket) {
    socket.close();
    socket = null;
  }
  if (pollTimer) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
}

async function finalizeJobIfDone(): Promise<void> {
  const status = activeStatus.value;
  if (!isTerminalJobStatus(status)) return;
  clearJobWatchers();
  await Promise.all([refreshProject(), refreshDeployments()]);
}

async function refreshActiveJob(): Promise<void> {
  if (!activeJob.value) return;
  activeJob.value = await api.request<Job>(`/jobs/${activeJob.value.id}`, {
    silent: true,
  });
  await finalizeJobIfDone();
}

function watchJob(job: Job): void {
  activeJob.value = job;
  activeJobState.value = null;
  clearJobWatchers();
  if (import.meta.client) {
    socket = new WebSocket(useWebSocketUrl(`/ws/jobs/${job.id}`));
    socket.addEventListener("message", (event) => {
      activeJobState.value = JSON.parse(event.data) as Record<string, unknown>;
      void refreshActiveJob();
    });
    socket.addEventListener("close", () => {
      if (hasActiveJob.value && !pollTimer) {
        pollTimer = setInterval(() => void refreshActiveJob(), 2000);
      }
    });
    socket.addEventListener("error", () => {
      socket?.close();
    });
  }
  pollTimer = setInterval(() => void refreshActiveJob(), 5000);
}

async function startGitDeploy(): Promise<void> {
  if (!project.value || !canDeploy.value || !gitSourceConfigured.value) return;
  pendingAction.value = "git";
  deployError.value = "";
  try {
    const job = await api.request<Job>(`/projects/${project.value.id}/deploy`, {
      method: "POST",
      body: { revision: revision.value.trim() || null },
    });
    revision.value = "";
    watchJob(job);
    await Promise.all([refreshProject(), refreshDeployments()]);
  } catch {
    deployError.value = copy.value.gitQueueError;
  } finally {
    pendingAction.value = "";
  }
}

function onArchiveChange(event: Event): void {
  archiveError.value = "";
  const input = event.target as HTMLInputElement;
  archiveFile.value = input.files?.[0] ?? null;
  if (archiveFile.value && !acceptsDeploymentArchive(archiveFile.value.name)) {
    archiveError.value = copy.value.zipOnly;
    archiveFile.value = null;
    input.value = "";
  }
}

async function startArchiveDeploy(): Promise<void> {
  if (!project.value || !canDeploy.value || !archiveFile.value) return;
  pendingAction.value = "archive";
  archiveError.value = "";
  try {
    const form = new FormData();
    form.append("file", archiveFile.value);
    const job = await api.request<Job>(
      `/projects/${project.value.id}/deploy/archive`,
      {
        method: "POST",
        body: form,
      },
    );
    archiveFile.value = null;
    if (archiveInput.value) archiveInput.value.value = "";
    watchJob(job);
    await Promise.all([refreshProject(), refreshDeployments()]);
  } catch {
    archiveError.value = copy.value.zipUploadError;
  } finally {
    pendingAction.value = "";
  }
}

async function toggleLog(deployment: Deployment): Promise<void> {
  logError.value = "";
  if (expandedDeploymentId.value === deployment.id) {
    expandedDeploymentId.value = null;
    return;
  }
  expandedDeploymentId.value = deployment.id;
  if (deploymentLogs.value[deployment.id] !== undefined) return;
  try {
    const response = await api.request<{ deployment_id: string; log: string }>(
      `/projects/${projectId}/deployments/${deployment.id}/log`,
    );
    deploymentLogs.value[deployment.id] = response.log || "";
  } catch {
    logError.value = copy.value.logError;
  }
}

async function rollback(_: string): Promise<void> {
  if (!rollbackTarget.value || !project.value || !canDeploy.value) return;
  pendingAction.value = "rollback";
  try {
    const job = await api.request<Job>(
      `/projects/${project.value.id}/rollback/${rollbackTarget.value.id}`,
      { method: "POST" },
    );
    rollbackTarget.value = null;
    watchJob(job);
    await Promise.all([refreshProject(), refreshDeployments()]);
  } finally {
    pendingAction.value = "";
  }
}

onBeforeUnmount(() => {
  clearJobWatchers();
});

function deploymentTitle(
  deployment: Pick<Deployment, "source_revision">,
): string {
  return rawDeploymentTitle(deployment, localeController.locale.value);
}

function formatDeployDate(value: string | null): string {
  return rawFormatDeployDate(value, localeController.locale.value);
}

function deploymentDuration(
  deployment: Pick<Deployment, "started_at" | "finished_at">,
): string {
  return rawDeploymentDuration(deployment, localeController.locale.value);
}
</script>

<template>
  <PageHeader :title="copy.title" :description="copy.description">
    <button
      class="button-secondary"
      type="button"
      @click="() => refreshDeployments()"
    >
      <IconRefresh :size="18" :stroke-width="1.8" /> {{ t("common.refresh") }}
    </button>
  </PageHeader>
  <ProjectNav :project-id="projectId" />

  <section v-if="loading" class="skeleton-grid" :aria-label="copy.loading">
    <div class="skeleton panel" />
    <div class="skeleton panel" />
    <div class="skeleton skeleton--wide panel" />
  </section>

  <section
    v-else-if="loadError || !project"
    class="state state--error"
    role="alert"
  >
    <IconTerminal2 :size="24" :stroke-width="1.8" />
    <h2>{{ copy.unavailable }}</h2>
    <p>{{ copy.unavailableDescription }}</p>
    <button
      class="button-secondary"
      type="button"
      @click="() => refreshProject()"
    >
      {{ t("projects.retry") }}
    </button>
  </section>

  <template v-else>
    <section class="summary-grid">
      <article class="panel summary-card summary-card--main">
        <span>{{ copy.project }}</span>
        <div>
          <strong>{{ project.name }}</strong>
          <AppStatusBadge :status="project.status" />
        </div>
        <p>{{ project.description || copy.noDescription }}</p>
      </article>
      <article class="panel summary-card">
        <IconGitBranch :size="19" :stroke-width="1.8" />
        <span>{{ copy.source }}</span>
        <strong>{{ project.repository_url ? "Git" : copy.files }}</strong>
        <small>{{ project.repository_url || copy.fileDeploy }}</small>
      </article>
      <article class="panel summary-card">
        <IconCode :size="19" :stroke-width="1.8" />
        <span>{{ copy.runtime }}</span>
        <strong>{{ project.runtime_type }}</strong>
        <small>{{ copy.branch }}: {{ project.branch || "—" }}</small>
      </article>
      <article class="panel summary-card">
        <IconClock :size="19" :stroke-width="1.8" />
        <span>{{ copy.lastDeploy }}</span>
        <strong>{{
          latestDeployment ? deploymentTitle(latestDeployment) : "—"
        }}</strong>
        <small>{{
          latestDeployment
            ? formatDeployDate(latestDeployment.created_at)
            : copy.noHistory
        }}</small>
      </article>
    </section>

    <section v-if="!canDeploy" class="readonly panel">
      <IconSettings :size="20" :stroke-width="1.8" />
      <div>
        <strong>{{ copy.readOnly }}</strong>
        <p>{{ copy.readOnlyDescription }}</p>
      </div>
    </section>

    <section class="deploy-layout">
      <form class="deploy-card panel" @submit.prevent="startGitDeploy">
        <header>
          <div>
            <IconGitBranch :size="21" :stroke-width="1.8" />
            <strong>{{ copy.gitDeploy }}</strong>
          </div>
          <AppStatusBadge
            :status="gitSourceConfigured ? 'created' : 'stopped'"
          />
        </header>
        <p>
          {{ copy.gitDescription }}
        </p>
        <label>
          <span>{{ copy.revision }}</span>
          <input
            v-model="revision"
            class="control"
            maxlength="255"
            placeholder="main"
            :disabled="
              !canDeploy || !gitSourceConfigured || pendingAction !== ''
            "
          />
        </label>
        <div v-if="!gitSourceConfigured" class="inline-state">
          {{ copy.gitNotConfigured }}
          <NuxtLink :to="`/projects/${project.id}/settings`">{{
            copy.openSettings
          }}</NuxtLink>
        </div>
        <p v-if="deployError" class="form-error">{{ deployError }}</p>
        <button
          class="button-primary"
          type="submit"
          :disabled="!canDeploy || !gitSourceConfigured || pendingAction !== ''"
        >
          <IconPlayerPlay :size="18" :stroke-width="1.8" />
          {{ pendingAction === "git" ? copy.starting : copy.startGit }}
        </button>
      </form>

      <form class="deploy-card panel" @submit.prevent="startArchiveDeploy">
        <header>
          <div>
            <IconFileZip :size="21" :stroke-width="1.8" />
            <strong>{{ copy.zipDeploy }}</strong>
          </div>
          <span class="pill">{{ copy.zipOnlyLabel }}</span>
        </header>
        <p>
          {{ copy.zipDescription }}
        </p>
        <input
          ref="archiveInput"
          class="file-input"
          type="file"
          accept=".zip,application/zip"
          :disabled="!canDeploy || pendingAction !== ''"
          @change="onArchiveChange"
        />
        <div class="archive-selected">
          <IconArchive :size="18" :stroke-width="1.8" />
          <span>{{ archiveFile?.name || copy.noArchive }}</span>
        </div>
        <p v-if="archiveError" class="form-error">{{ archiveError }}</p>
        <button
          class="button-secondary"
          type="button"
          :disabled="!canDeploy || pendingAction !== ''"
          @click="archiveInput?.click()"
        >
          {{ copy.selectArchive }}
        </button>
        <button
          class="button-primary"
          type="submit"
          :disabled="!canDeploy || !archiveFile || pendingAction !== ''"
        >
          <IconPlayerPlay :size="18" :stroke-width="1.8" />
          {{ pendingAction === "archive" ? copy.uploading : copy.startZip }}
        </button>
      </form>
    </section>

    <section v-if="activeJob" class="job-card panel" aria-live="polite">
      <header>
        <div>
          <strong>{{ jobKindLabel(activeJob.kind) }}</strong>
          <span>{{ activeStage || activeStatus }}</span>
        </div>
        <AppStatusBadge
          :status="activeStatus === 'failure' ? 'failed' : 'deploying'"
        />
      </header>
      <progress :value="activeProgress" max="100" />
      <div class="job-meta">
        <span>{{ activeProgress }}%</span>
        <code>{{ activeJob.id }}</code>
      </div>
      <p v-if="activeJob.error" class="form-error">{{ activeJob.error }}</p>
    </section>

    <section class="history panel">
      <header class="history-header">
        <div>
          <IconHistory :size="21" :stroke-width="1.8" />
          <strong>{{ copy.history }}</strong>
        </div>
        <span>{{
          copy.records.replace("{count}", String(deployments?.length || 0))
        }}</span>
      </header>

      <div v-if="!deployments?.length" class="state state--compact">
        <IconHistory :size="24" :stroke-width="1.8" />
        <h2>{{ copy.empty }}</h2>
        <p>{{ copy.emptyDescription }}</p>
      </div>

      <div v-else class="deployment-list">
        <article
          v-for="deployment in deployments"
          :key="deployment.id"
          class="deployment-row"
        >
          <button
            class="log-toggle"
            type="button"
            @click="toggleLog(deployment)"
          >
            <IconChevronDown
              v-if="expandedDeploymentId === deployment.id"
              :size="18"
              :stroke-width="1.8"
            />
            <IconChevronRight v-else :size="18" :stroke-width="1.8" />
          </button>
          <AppStatusBadge :status="deploymentBadgeStatus(deployment.status)" />
          <div class="deployment-main">
            <strong>{{ deploymentTitle(deployment) }}</strong>
            <span>{{
              formatDeployDate(deployment.started_at || deployment.created_at)
            }}</span>
          </div>
          <div class="deployment-meta">
            <span>{{ copy.duration }}</span>
            <strong>{{ deploymentDuration(deployment) }}</strong>
          </div>
          <div class="deployment-path">
            <span>{{ copy.release }}</span>
            <code>{{ deployment.release_path || "—" }}</code>
          </div>
          <button
            v-if="canRollbackDeployment(auth.role, deployment)"
            class="icon-button"
            type="button"
            :title="copy.rollback"
            :aria-label="copy.rollback"
            :disabled="pendingAction !== ''"
            @click="rollbackTarget = deployment"
          >
            <IconRotateClockwise :size="18" :stroke-width="1.8" />
          </button>
          <div
            v-if="expandedDeploymentId === deployment.id"
            class="deployment-log"
          >
            <p v-if="logError" class="form-error">{{ logError }}</p>
            <pre v-else>{{
              deploymentLogs[deployment.id] || copy.emptyLog
            }}</pre>
          </div>
        </article>
      </div>
    </section>

    <ConfirmDialog
      :open="rollbackTarget !== null"
      :title="copy.rollbackTitle"
      :message="
        copy.rollbackMessage.replace(
          '{revision}',
          rollbackTarget ? deploymentTitle(rollbackTarget) : '',
        )
      "
      :confirm-label="copy.rollback"
      :cancel-label="t('common.cancel')"
      :expected-text="rollbackTarget ? deploymentTitle(rollbackTarget) : null"
      :input-label="copy.revision"
      danger
      @cancel="rollbackTarget = null"
      @confirm="rollback"
    />
  </template>
</template>

<style scoped>
.skeleton-grid,
.summary-grid,
.deploy-layout {
  display: grid;
  gap: 1rem;
}

.skeleton-grid {
  grid-template-columns: repeat(2, minmax(0, 1fr));
}

.skeleton {
  min-height: 220px;
  animation: pulse 1.2s ease-in-out infinite;
}

.skeleton--wide {
  grid-column: 1 / -1;
  min-height: 320px;
}

@keyframes pulse {
  50% {
    opacity: 0.55;
  }
}

.state {
  display: grid;
  justify-items: center;
  gap: 0.7rem;
  border-top: 1px solid var(--border);
  padding: 3rem 1rem;
  color: var(--text-muted);
  text-align: center;
}

.state h2,
.state p {
  margin: 0;
}

.state--error h2,
.form-error {
  color: #f3a1a6;
}

.state--compact {
  border-top: 0;
}

.summary-grid {
  grid-template-columns: 1.4fr repeat(3, minmax(0, 1fr));
  margin-top: 1rem;
}

.summary-card {
  display: grid;
  align-content: start;
  gap: 0.45rem;
  min-width: 0;
  padding: 1rem;
}

.summary-card--main div {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.7rem;
}

.summary-card span,
.summary-card small,
.history-header span,
.deployment-main span,
.deployment-meta span,
.deployment-path span {
  color: var(--text-muted);
  font-size: 0.78rem;
}

.summary-card strong,
.deployment-main strong,
.deployment-meta strong {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.summary-card p {
  margin: 0;
  color: var(--text-muted);
}

.summary-card small {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.readonly,
.job-card {
  display: flex;
  gap: 0.9rem;
  align-items: flex-start;
  margin-top: 1rem;
  padding: 1rem;
}

.readonly p {
  margin: 0.25rem 0 0;
  color: var(--text-muted);
}

.deploy-layout {
  grid-template-columns: repeat(2, minmax(0, 1fr));
  margin-top: 1rem;
}

.deploy-card {
  display: grid;
  gap: 1rem;
  padding: 1rem;
}

.deploy-card header,
.history-header,
.job-card header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
}

.deploy-card header div,
.history-header div {
  display: inline-flex;
  align-items: center;
  gap: 0.55rem;
}

.deploy-card p,
.form-error {
  margin: 0;
  color: var(--text-muted);
}

.deploy-card label {
  display: grid;
  gap: 0.4rem;
  color: var(--text-muted);
  font-size: 0.82rem;
  font-weight: 650;
}

.inline-state {
  border: 1px solid #5c4424;
  border-radius: 8px;
  background: rgb(217 154 55 / 10%);
  padding: 0.75rem;
  color: #edbb68;
  font-size: 0.86rem;
}

.inline-state a {
  color: var(--accent-hover);
  font-weight: 700;
}

.pill {
  border: 1px solid var(--border);
  border-radius: 999px;
  padding: 0.25rem 0.55rem;
  color: var(--text-muted);
  font-size: 0.75rem;
  font-weight: 700;
}

.file-input {
  display: none;
}

.archive-selected {
  display: flex;
  align-items: center;
  gap: 0.55rem;
  min-height: 44px;
  border: 1px dashed var(--border);
  border-radius: 8px;
  padding: 0.65rem 0.8rem;
  color: var(--text-muted);
}

.job-card {
  display: grid;
}

.job-card progress {
  width: 100%;
  height: 10px;
  overflow: hidden;
  border: 0;
  border-radius: 999px;
  background: var(--surface-base);
}

.job-card progress::-webkit-progress-bar {
  background: var(--surface-base);
}

.job-card progress::-webkit-progress-value {
  background: var(--accent);
}

.job-card progress::-moz-progress-bar {
  background: var(--accent);
}

.job-card header div,
.job-meta {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
}

.job-card header span,
.job-meta {
  color: var(--text-muted);
  font-size: 0.82rem;
}

.history {
  margin-top: 1rem;
  overflow: hidden;
}

.history-header {
  border-bottom: 1px solid var(--border);
  padding: 1rem;
}

.deployment-list {
  display: grid;
}

.deployment-row {
  display: grid;
  grid-template-columns: auto auto minmax(150px, 1.2fr) minmax(
      90px,
      0.5fr
    ) minmax(160px, 1fr) auto;
  align-items: center;
  gap: 0.85rem;
  border-bottom: 1px solid var(--border);
  padding: 0.85rem 1rem;
}

.deployment-row:last-child {
  border-bottom: 0;
}

.deployment-main,
.deployment-meta,
.deployment-path {
  display: grid;
  min-width: 0;
  gap: 0.2rem;
}

code,
pre {
  font-family: "JetBrains Mono", "SFMono-Regular", Consolas, monospace;
}

code {
  overflow: hidden;
  color: #c7c9cf;
  font-size: 0.74rem;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.log-toggle,
.icon-button {
  display: inline-grid;
  width: 36px;
  height: 36px;
  cursor: pointer;
  place-items: center;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--surface-subtle);
  color: var(--text);
}

.icon-button:disabled {
  cursor: not-allowed;
  opacity: 0.45;
}

.deployment-log {
  grid-column: 1 / -1;
  min-width: 0;
}

.deployment-log pre {
  max-height: 360px;
  overflow: auto;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: #0d0e10;
  margin: 0.25rem 0 0;
  padding: 1rem;
  color: #d6d8dd;
  font-size: 0.78rem;
  line-height: 1.55;
  white-space: pre-wrap;
}

@media (max-width: 1100px) {
  .summary-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .deployment-row {
    grid-template-columns: auto auto minmax(160px, 1fr) auto;
  }

  .deployment-meta,
  .deployment-path {
    grid-column: 3 / -1;
  }
}

@media (max-width: 760px) {
  .skeleton-grid,
  .summary-grid,
  .deploy-layout {
    grid-template-columns: 1fr;
  }

  .deployment-row {
    grid-template-columns: auto 1fr auto;
  }

  .deployment-row > .status-badge,
  .deployment-main,
  .deployment-meta,
  .deployment-path {
    grid-column: 2 / -1;
  }

  .deployment-row .icon-button {
    grid-column: 3;
    grid-row: 1;
  }
}
</style>
