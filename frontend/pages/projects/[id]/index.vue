<script setup lang="ts">
import {
  IconActivity,
  IconArchive,
  IconBrandDocker,
  IconFile,
  IconPlayerPlay,
  IconPlayerStop,
  IconRefresh,
  IconReload,
  IconTerminal2,
  IconTrash,
} from "@tabler/icons-vue";
import type { MetricSample, Project, ProjectMetricValues } from "~/types/api";
import { projectOverviewMessages } from "~/locales/project-overview";

const route = useRoute();
const router = useRouter();
const api = useApi();
const auth = useAuthStore();
const config = useRuntimeConfig();
const localeController = useLocale();
const { t, number } = localeController;
const copy = computed(
  () => projectOverviewMessages[localeController.locale.value],
);
const id = computed(() => String(route.params.id));
const pendingAction = ref("");
const actionMessage = ref("");
const stopOpen = ref(false);
const deleteOpen = ref(false);
const deleteError = ref("");
const {
  data: project,
  pending,
  error,
  refresh,
} = await useAsyncData(
  () => `project-${id.value}`,
  () => api.request<Project>(`/projects/${id.value}`),
);
const { data: latestMetrics, refresh: refreshMetrics } = await useAsyncData(
  () => `project-metrics-${id.value}`,
  async () => {
    auth.restore();
    try {
      return await $fetch<MetricSample>(
        `/monitoring/projects/${id.value}/latest`,
        {
          baseURL: config.public.apiBase,
          headers: auth.accessToken
            ? { Authorization: `Bearer ${auth.accessToken}` }
            : {},
        },
      );
    } catch {
      return null;
    }
  },
);
let metricsTimer: ReturnType<typeof setInterval> | null = null;
onMounted(() => {
  metricsTimer = setInterval(() => {
    void refreshMetrics();
  }, 15_000);
});
onBeforeUnmount(() => {
  if (metricsTimer) clearInterval(metricsTimer);
});
const projectMetrics = computed(
  () => latestMetrics.value?.values as ProjectMetricValues | undefined,
);
const isRunning = computed(() => project.value?.status === "running");
const resourcePolicy = computed(() => projectMetrics.value?.resource_policy);
const resourceViolations = computed(
  () => projectMetrics.value?.resource_violations ?? [],
);

type ProjectAction = "start" | "stop" | "restart" | "redeploy";

const actionMessages = computed<Record<ProjectAction, string>>(() => ({
  start: copy.value.started,
  stop: copy.value.stopped,
  restart: copy.value.restarted,
  redeploy: copy.value.redeployQueued,
}));

async function action(kind: ProjectAction): Promise<void> {
  if (!project.value) return;
  pendingAction.value = kind;
  actionMessage.value = "";
  try {
    if (kind === "redeploy") {
      await api.request(`/projects/${project.value.id}/deploy`, {
        method: "POST",
        body: {},
      });
    } else {
      await api.request(`/projects/${project.value.id}/runtime/${kind}`, {
        method: "POST",
      });
    }
    actionMessage.value = actionMessages.value[kind];
    await refresh();
  } finally {
    pendingAction.value = "";
  }
}

async function stopProject(): Promise<void> {
  stopOpen.value = false;
  await action("stop");
}

async function deleteProject(confirmation: string): Promise<void> {
  if (!project.value || pendingAction.value) return;
  deleteError.value = "";
  pendingAction.value = "delete";
  deleteOpen.value = false;
  try {
    await api.request(`/projects/${project.value.id}`, {
      method: "DELETE",
      body: { confirm_project_name: confirmation },
    });
    await router.push("/projects");
  } catch {
    deleteError.value = copy.value.deleteError;
    deleteOpen.value = true;
  } finally {
    pendingAction.value = "";
  }
}

function bytes(value?: number): string {
  if (value === undefined) return "—";
  if (value === 0) return "0 MB";
  if (value >= 1024 ** 3)
    return `${number(value / 1024 ** 3, { maximumFractionDigits: 1 })} GB`;
  return `${number(value / 1024 ** 2, { maximumFractionDigits: 1 })} MB`;
}

function percent(value?: number): string {
  return value === undefined ? "—" : `${value.toFixed(1)}%`;
}

function limitBytes(megabytes?: number | null): string {
  if (megabytes === undefined || megabytes === null) return copy.value.noLimit;
  return bytes(megabytes * 1024 * 1024);
}

function resourceLimit(resource: "cpu" | "memory" | "disk"): string {
  const policy = resourcePolicy.value;
  if (!policy?.enabled) return copy.value.noPolicy;
  if (resource === "cpu") {
    return policy.cpu_cores === null
      ? copy.value.noLimit
      : copy.value.limit.replace(
          "{value}",
          `${(policy.cpu_cores * 100).toFixed(0)}%`,
        );
  }
  if (resource === "memory")
    return copy.value.limit.replace("{value}", limitBytes(policy.memory_mb));
  return copy.value.limit.replace("{value}", limitBytes(policy.disk_mb));
}

function violationText(resource: string): string {
  if (resource === "memory") return "RAM";
  if (resource === "disk") return "Disk";
  return resource.toUpperCase();
}
</script>

<template>
  <div v-if="pending" class="loading" :aria-label="copy.loading" />
  <section v-else-if="error || !project" class="error-state" role="alert">
    <h1>{{ copy.unavailable }}</h1>
    <p>{{ copy.unavailableDescription }}</p>
    <button class="button-secondary" type="button" @click="() => refresh()">
      {{ t("projects.retry") }}
    </button>
  </section>
  <template v-else>
    <header class="project-header">
      <div>
        <div class="title-row">
          <h1>{{ project.name }}</h1>
          <AppStatusBadge :status="project.status" />
        </div>
        <p>{{ project.description || copy.noDescription }}</p>
      </div>
      <div class="header-controls">
        <div class="actions">
          <button
            class="button-secondary"
            type="button"
            :disabled="!!pendingAction"
            @click="action('redeploy')"
          >
            <IconRefresh :size="18" :stroke-width="1.8" />
            {{ pendingAction === "redeploy" ? copy.starting : "Redeploy" }}
          </button>
          <button
            v-if="isRunning"
            class="button-secondary"
            type="button"
            :disabled="!!pendingAction"
            @click="action('restart')"
          >
            <IconReload :size="18" :stroke-width="1.8" />
            {{ pendingAction === "restart" ? copy.restarting : copy.restart }}
          </button>
          <button
            v-if="isRunning"
            class="button-danger"
            type="button"
            :disabled="!!pendingAction"
            @click="stopOpen = true"
          >
            <IconPlayerStop :size="18" :stroke-width="1.8" />
            {{ pendingAction === "stop" ? copy.stopping : copy.stop }}
          </button>
          <button
            v-else-if="project.status !== 'deploying'"
            class="button-primary"
            type="button"
            :disabled="!!pendingAction"
            @click="action('start')"
          >
            <IconPlayerPlay :size="18" :stroke-width="1.8" />
            {{ pendingAction === "start" ? copy.starting : copy.start }}
          </button>
        </div>
        <a
          v-if="project.healthcheck_url"
          class="healthcheck"
          :href="project.healthcheck_url"
          target="_blank"
          rel="noreferrer"
        >
          <span>{{ copy.healthcheck }}</span>
          <strong>{{ project.healthcheck_url }}</strong>
        </a>
      </div>
    </header>
    <p v-if="actionMessage" class="action-message" aria-live="polite">
      {{ actionMessage }}
    </p>

    <ProjectNav :project-id="project.id" />

    <section class="resource-grid">
      <div class="panel resource-card">
        <IconActivity :size="20" />
        <span>CPU</span>
        <strong>{{ percent(projectMetrics?.cpu_percent) }}</strong>
        <small v-if="resourcePolicy?.enabled">{{ resourceLimit("cpu") }}</small>
      </div>
      <div class="panel resource-card">
        <IconActivity :size="20" />
        <span>RAM</span>
        <strong>{{ percent(projectMetrics?.memory_percent) }}</strong>
        <small
          >{{ bytes(projectMetrics?.memory_used) }} /
          {{ bytes(projectMetrics?.memory_total) }}</small
        >
        <small v-if="resourcePolicy?.enabled">{{
          resourceLimit("memory")
        }}</small>
      </div>
      <div class="panel resource-card">
        <IconActivity :size="20" />
        <span>{{ copy.disk }}</span>
        <strong>{{ bytes(projectMetrics?.disk_bytes) }}</strong>
        <small v-if="resourcePolicy?.enabled">{{
          resourceLimit("disk")
        }}</small>
      </div>
      <div class="panel resource-card">
        <IconActivity :size="20" />
        <span>{{ copy.network }}</span>
        <strong>{{ bytes(projectMetrics?.network_bytes_received) }} ↓</strong>
        <small>{{ bytes(projectMetrics?.network_bytes_sent) }} ↑</small>
      </div>
    </section>

    <section
      v-if="resourcePolicy?.enabled || resourceViolations.length"
      class="policy-state"
      :class="{ 'policy-state--warning': resourceViolations.length }"
    >
      <strong>
        {{
          resourceViolations.length ? copy.policyExceeded : copy.policyActive
        }}
      </strong>
      <span v-if="resourceViolations.length">
        {{
          resourceViolations
            .map((item) => violationText(item.resource))
            .join(", ")
        }}
      </span>
      <span v-else>
        Enforcement:
        {{
          resourcePolicy?.enforcement === "monitor_only"
            ? "monitor-only"
            : "enforced"
        }}
      </span>
    </section>

    <section class="overview-console">
      <div class="section-heading">
        <div>
          <h2>{{ t("project.nav.console") }}</h2>
          <p>{{ copy.consoleDescription }}</p>
        </div>
        <NuxtLink
          class="button-secondary"
          :to="`/projects/${project.id}/console`"
        >
          {{ copy.fullScreen }}
        </NuxtLink>
      </div>
      <ProjectConsole :project-id="project.id" compact />
    </section>

    <div class="workspace">
      <section class="panel activity">
        <h2>{{ copy.recentOperations }}</h2>
        <div class="empty">
          <IconBrandDocker :size="28" :stroke-width="1.5" />
          <strong>{{ copy.noDeployments }}</strong>
          <p>{{ copy.noDeploymentsDescription }}</p>
        </div>
      </section>
      <aside class="quick-actions">
        <h2>{{ copy.quickActions }}</h2>
        <NuxtLink :to="`/projects/${project.id}/console`">
          <IconTerminal2 :size="19" :stroke-width="1.7" />
          {{ copy.openConsole }}
        </NuxtLink>
        <NuxtLink :to="`/projects/${project.id}/files`">
          <IconFile :size="19" :stroke-width="1.7" /> {{ copy.projectFiles }}
        </NuxtLink>
        <NuxtLink :to="`/projects/${project.id}/backups`">
          <IconArchive :size="19" :stroke-width="1.7" /> {{ copy.createBackup }}
        </NuxtLink>
        <NuxtLink
          v-if="project.project_type === 'minecraft_forge'"
          :to="`/projects/${project.id}/minecraft`"
        >
          <IconBrandDocker :size="19" :stroke-width="1.7" />
          {{ copy.configureMinecraft }}
        </NuxtLink>
        <button class="delete-project" type="button" @click="deleteOpen = true">
          <IconTrash :size="19" :stroke-width="1.7" /> {{ copy.deleteProject }}
        </button>
        <p v-if="deleteError" class="delete-error" role="alert">
          {{ deleteError }}
        </p>
      </aside>
    </div>
    <ConfirmDialog
      :open="stopOpen"
      :title="copy.stopTitle"
      :message="copy.stopMessage"
      :confirm-label="copy.stop"
      danger
      @cancel="stopOpen = false"
      @confirm="stopProject"
    />
    <ConfirmDialog
      :open="deleteOpen"
      :title="copy.deleteTitle"
      :message="copy.deleteMessage"
      :confirm-label="t('common.delete')"
      danger
      :expected-text="project.name"
      :input-label="copy.enterName.replace('{name}', project.name)"
      @cancel="deleteOpen = false"
      @confirm="deleteProject"
    />
  </template>
</template>

<style scoped>
.project-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 1.5rem;
}
.title-row,
.actions {
  display: flex;
  align-items: center;
  gap: 0.7rem;
}
.actions {
  flex-wrap: wrap;
  justify-content: flex-end;
}
.header-controls {
  display: grid;
  justify-items: end;
  gap: 0.65rem;
}
.healthcheck {
  display: grid;
  max-width: 520px;
  justify-items: end;
  gap: 0.2rem;
  color: var(--text-muted);
  text-decoration: none;
}
.healthcheck span {
  font-size: 0.72rem;
}
.healthcheck strong {
  overflow: hidden;
  max-width: 100%;
  color: var(--text);
  font-family: ui-monospace, monospace;
  font-size: 0.78rem;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.healthcheck:hover strong {
  color: var(--accent);
}
h1,
h2,
p {
  margin: 0;
}
h1 {
  font-size: clamp(1.7rem, 4vw, 2.35rem);
  letter-spacing: -0.035em;
}
.project-header p {
  margin-top: 0.4rem;
  color: var(--text-muted);
}
.action-message {
  margin-top: 0.8rem;
  color: #70d49d;
}
.resource-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 1rem;
  margin-top: 1.5rem;
}
.resource-card {
  display: grid;
  gap: 0.35rem;
  padding: 1rem;
}
.resource-card svg {
  color: #ff9b70;
}
.resource-card span,
.resource-card small {
  color: var(--text-muted);
  font-size: 0.75rem;
}
.resource-card strong {
  font-size: 1.2rem;
}
.policy-state {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
  margin-top: 1rem;
  border: 1px solid var(--border);
  border-radius: 10px;
  background: var(--surface-raised);
  padding: 0.9rem 1rem;
}
.policy-state span {
  color: var(--text-muted);
  font-family: ui-monospace, monospace;
  font-size: 0.78rem;
}
.policy-state--warning {
  border-color: #6f5524;
  background: #241f15;
}
.policy-state--warning strong {
  color: #edbb68;
}
.overview-console {
  margin-top: 2rem;
}
.section-heading {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 1rem;
  margin-bottom: 1rem;
}
.section-heading p {
  margin-top: 0.25rem;
  color: var(--text-muted);
}
.workspace {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 280px;
  gap: 1rem;
  margin-top: 2rem;
}
.activity {
  padding: 1.25rem;
}
.activity h2,
.quick-actions h2 {
  font-size: 1rem;
}
.empty {
  display: grid;
  justify-items: center;
  gap: 0.55rem;
  padding: 5rem 1rem;
  color: var(--text-muted);
  text-align: center;
}
.empty strong {
  color: var(--text);
}
.quick-actions {
  align-self: start;
  border-top: 1px solid var(--border);
  padding-top: 1rem;
}
.quick-actions a {
  display: flex;
  min-height: 48px;
  align-items: center;
  gap: 0.6rem;
  border-bottom: 1px solid var(--border);
  color: var(--text-muted);
  text-decoration: none;
}
.delete-project {
  display: flex;
  width: 100%;
  min-height: 48px;
  align-items: center;
  gap: 0.6rem;
  border: 0;
  border-bottom: 1px solid var(--border);
  background: transparent;
  color: #f3a1a6;
  cursor: pointer;
  font: inherit;
  text-align: left;
}
.delete-error {
  margin-top: 0.75rem;
  color: #f3a1a6;
  font-size: 0.78rem;
}
.quick-actions a:hover {
  color: var(--text);
}
.loading {
  height: 280px;
  border-radius: 12px;
  background: var(--surface-raised);
  animation: pulse 1.3s ease-in-out infinite;
}
.error-state {
  display: grid;
  justify-items: start;
  gap: 0.7rem;
}
.error-state p {
  color: var(--text-muted);
}
@keyframes pulse {
  50% {
    opacity: 0.55;
  }
}
@media (max-width: 850px) {
  .project-header,
  .actions {
    align-items: stretch;
    flex-direction: column;
  }
  .header-controls,
  .healthcheck {
    width: 100%;
    justify-items: stretch;
  }
  .healthcheck strong {
    max-width: calc(100vw - 2rem);
  }
  .resource-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .workspace {
    grid-template-columns: 1fr;
  }
}
@media (max-width: 560px) {
  .resource-grid {
    grid-template-columns: 1fr;
  }
}
</style>
