<script setup lang="ts">
import {
  IconAdjustmentsHorizontal,
  IconClock,
  IconExternalLink,
  IconFile,
  IconLogs,
  IconPlus,
  IconSearch,
  IconSettings,
  IconTerminal2,
} from "@tabler/icons-vue";
import type {
  Project,
  ProjectLatestMetrics,
  ProjectMetricValues,
} from "~/types/api";
import {
  formatBytes,
  formatPercent,
  metricValues,
  policyUsageLabel,
  projectInitials,
  resourcePolicySummary,
  runtimeOptions,
  summarizeProjects,
  violationLabel,
  visibleProjects,
  type ProjectSortKey,
  type ProjectStatusFilter,
} from "~/utils/projects";
import { canCreateProjects } from "~/utils/rbac";

const api = useApi();
const auth = useAuthStore();
const localeController = useLocale();
const { t, dateTime } = localeController;
const query = ref("");
const statusFilter = ref<ProjectStatusFilter>("all");
const runtimeFilter = ref("all");
const sortKey = ref<ProjectSortKey>("updated");

const {
  data: projects,
  pending,
  error,
  refresh,
} = await useAsyncData("projects", () => api.request<Project[]>("/projects"));

const { data: latestMetrics, pending: metricsPending } = await useAsyncData(
  "project-latest-metrics",
  async () => {
    try {
      return await api.request<ProjectLatestMetrics>(
        "/monitoring/projects/latest",
        {
          silent: true,
        },
      );
    } catch {
      return {};
    }
  },
);

const allProjects = computed(() => projects.value ?? []);
const summary = computed(() => summarizeProjects(allProjects.value));
const runtimes = computed(() => runtimeOptions(allProjects.value));
const filtered = computed(() =>
  visibleProjects(allProjects.value, {
    query: query.value,
    status: statusFilter.value,
    runtime: runtimeFilter.value,
    sort: sortKey.value,
  }),
);

function projectMetrics(projectId: string): ProjectMetricValues | null {
  return metricValues(latestMetrics.value?.[projectId]);
}

function formatDate(value: string): string {
  return dateTime(value, {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function healthLabel(values: ProjectMetricValues | null): string {
  if (!values?.health_status) return t("common.noData");
  if (values.health_status === "healthy") return "Healthy";
  if (values.health_status === "unhealthy") return "Unhealthy";
  return values.health_status;
}

function healthTone(values: ProjectMetricValues | null): string {
  if (!values?.health_status) return "muted";
  if (values.health_status === "healthy") return "success";
  if (values.health_status === "unhealthy") return "danger";
  return "warning";
}
</script>

<template>
  <PageHeader
    :title="t('nav.projects')"
    :description="t('projects.description')"
  >
    <NuxtLink
      v-if="!auth.user || canCreateProjects(auth.role)"
      class="button-primary"
      to="/projects/create"
    >
      <IconPlus :size="18" :stroke-width="1.8" />
      {{ t("dashboard.newProject") }}
    </NuxtLink>
  </PageHeader>

  <section class="summary-grid" :aria-label="t('projects.summary')">
    <article class="summary-card summary-card--primary">
      <span>{{ t("projects.total") }}</span>
      <strong>{{ summary.total }}</strong>
      <small>{{ t("projects.inPanel") }}</small>
    </article>
    <article class="summary-card">
      <span>{{ t("projects.running") }}</span>
      <strong>{{ summary.running }}</strong>
      <small>{{ t("projects.activeRuntime") }}</small>
    </article>
    <article class="summary-card">
      <span>{{ t("status.deploying") }}</span>
      <strong>{{ summary.deploying }}</strong>
      <small>{{ t("projects.operationInProgress") }}</small>
    </article>
    <article class="summary-card summary-card--danger">
      <span>{{ t("projects.errors") }}</span>
      <strong>{{ summary.failed }}</strong>
      <small>{{ t("projects.needAttention") }}</small>
    </article>
    <article class="summary-card">
      <span>{{ t("projects.stopped") }}</span>
      <strong>{{ summary.stopped }}</strong>
      <small>{{ t("projects.runtimeOff") }}</small>
    </article>
  </section>

  <section class="toolbar panel" :aria-label="t('projects.filters')">
    <label class="search">
      <span class="sr-only">{{ t("projects.search") }}</span>
      <IconSearch :size="19" :stroke-width="1.7" />
      <input
        v-model="query"
        type="search"
        :placeholder="t('projects.searchPlaceholder')"
      />
    </label>
    <label class="filter">
      <span>{{ t("common.status") }}</span>
      <select v-model="statusFilter">
        <option value="all">{{ t("projects.all") }}</option>
        <option value="running">{{ t("status.running") }}</option>
        <option value="deploying">{{ t("status.deploying") }}</option>
        <option value="failed">{{ t("status.failed") }}</option>
        <option value="stopped">{{ t("status.stopped") }}</option>
        <option value="created">{{ t("status.created") }}</option>
      </select>
    </label>
    <label class="filter">
      <span>{{ t("projects.runtime") }}</span>
      <select v-model="runtimeFilter">
        <option value="all">{{ t("projects.all") }}</option>
        <option v-for="runtime in runtimes" :key="runtime" :value="runtime">
          {{ runtime }}
        </option>
      </select>
    </label>
    <label class="filter">
      <span>{{ t("projects.sort") }}</span>
      <select v-model="sortKey">
        <option value="updated">{{ t("projects.sortUpdated") }}</option>
        <option value="created">{{ t("projects.sortCreated") }}</option>
        <option value="name">{{ t("projects.sortName") }}</option>
        <option value="status">{{ t("projects.sortStatus") }}</option>
      </select>
    </label>
  </section>

  <div v-if="pending" class="project-list" :aria-label="t('projects.loading')">
    <div v-for="index in 5" :key="index" class="project-skeleton panel" />
  </div>

  <section v-else-if="error" class="state state--error" role="alert">
    <IconAdjustmentsHorizontal :size="28" :stroke-width="1.6" />
    <h2>{{ t("projects.loadError") }}</h2>
    <p>{{ t("projects.loadErrorDescription") }}</p>
    <button class="button-secondary" type="button" @click="() => refresh()">
      {{ t("projects.retry") }}
    </button>
  </section>

  <section v-else-if="filtered.length" class="project-list">
    <article
      v-for="project in filtered"
      :key="project.id"
      class="project-row panel"
    >
      <NuxtLink class="project-main" :to="`/projects/${project.id}`">
        <span class="project-mark">{{ projectInitials(project.name) }}</span>
        <span class="project-copy">
          <span class="project-title">
            <strong>{{ project.name }}</strong>
            <AppStatusBadge :status="project.status" />
          </span>
          <span class="project-description">
            {{ project.description || project.slug }}
          </span>
        </span>
      </NuxtLink>

      <dl class="project-meta">
        <div>
          <dt>{{ t("projects.runtime") }}</dt>
          <dd>{{ project.runtime_type }}</dd>
        </div>
        <div>
          <dt>{{ t("projects.branch") }}</dt>
          <dd>{{ project.branch }}</dd>
        </div>
        <div>
          <dt>{{ t("projects.health") }}</dt>
          <dd
            :class="`health health--${healthTone(projectMetrics(project.id))}`"
          >
            {{ healthLabel(projectMetrics(project.id)) }}
          </dd>
        </div>
        <div>
          <dt>{{ t("projects.updated") }}</dt>
          <dd>
            <IconClock :size="15" :stroke-width="1.8" />
            {{ formatDate(project.updated_at) }}
          </dd>
        </div>
        <div>
          <dt>{{ t("projects.created") }}</dt>
          <dd>
            <IconClock :size="15" :stroke-width="1.8" />
            {{ formatDate(project.created_at) }}
          </dd>
        </div>
      </dl>

      <div class="resource-strip" :aria-busy="metricsPending">
        <span>
          CPU
          <strong>{{
            projectMetrics(project.id)?.resource_policy?.enabled
              ? policyUsageLabel(
                  projectMetrics(project.id),
                  "cpu",
                  localeController.locale.value,
                )
              : formatPercent(
                  projectMetrics(project.id)?.cpu_percent,
                  localeController.locale.value,
                )
          }}</strong>
        </span>
        <span>
          RAM
          <strong>
            {{
              projectMetrics(project.id)?.resource_policy?.enabled
                ? policyUsageLabel(
                    projectMetrics(project.id),
                    "memory",
                    localeController.locale.value,
                  )
                : formatBytes(
                    projectMetrics(project.id)?.memory_used,
                    localeController.locale.value,
                  )
            }}
          </strong>
        </span>
        <span>
          Disk
          <strong>{{
            projectMetrics(project.id)?.resource_policy?.enabled
              ? policyUsageLabel(
                  projectMetrics(project.id),
                  "disk",
                  localeController.locale.value,
                )
              : formatBytes(
                  projectMetrics(project.id)?.disk_bytes,
                  localeController.locale.value,
                )
          }}</strong>
        </span>
      </div>

      <div
        v-if="projectMetrics(project.id)?.resource_policy?.enabled"
        class="policy-strip"
        :class="{
          'policy-strip--warning':
            (projectMetrics(project.id)?.resource_violations ?? []).length > 0,
        }"
      >
        <strong>
          {{
            (projectMetrics(project.id)?.resource_violations ?? []).length > 0
              ? t("projects.limitExceeded")
              : resourcePolicySummary(
                  projectMetrics(project.id)?.resource_policy,
                  localeController.locale.value,
                )
          }}
        </strong>
        <span
          v-if="(projectMetrics(project.id)?.resource_violations ?? []).length"
        >
          {{
            (projectMetrics(project.id)?.resource_violations ?? [])
              .map((violation) =>
                violationLabel(violation, localeController.locale.value),
              )
              .join(" · ")
          }}
        </span>
      </div>

      <div class="project-links" :aria-label="t('projects.quickActions')">
        <NuxtLink
          class="icon-link"
          :to="`/projects/${project.id}`"
          :aria-label="t('projects.openOverview')"
        >
          <IconExternalLink :size="18" :stroke-width="1.7" />
        </NuxtLink>
        <NuxtLink
          class="icon-link"
          :to="`/projects/${project.id}/logs`"
          :aria-label="t('projects.openLogs')"
        >
          <IconLogs :size="18" :stroke-width="1.7" />
        </NuxtLink>
        <NuxtLink
          class="icon-link"
          :to="`/projects/${project.id}/console`"
          :aria-label="t('projects.openConsole')"
        >
          <IconTerminal2 :size="18" :stroke-width="1.7" />
        </NuxtLink>
        <NuxtLink
          class="icon-link"
          :to="`/projects/${project.id}/files`"
          :aria-label="t('projects.openFiles')"
        >
          <IconFile :size="18" :stroke-width="1.7" />
        </NuxtLink>
        <NuxtLink
          class="icon-link"
          :to="`/projects/${project.id}/settings`"
          :aria-label="t('projects.openSettings')"
        >
          <IconSettings :size="18" :stroke-width="1.7" />
        </NuxtLink>
      </div>

      <a
        v-if="project.healthcheck_url"
        class="healthcheck"
        :href="project.healthcheck_url"
        target="_blank"
        rel="noreferrer"
      >
        {{ project.healthcheck_url }}
      </a>
    </article>
  </section>

  <section v-else class="state">
    <IconSearch :size="28" :stroke-width="1.6" />
    <h2>{{ query ? t("projects.noResults") : t("projects.empty") }}</h2>
    <p>
      {{
        query || statusFilter !== "all" || runtimeFilter !== "all"
          ? t("projects.adjustFilters")
          : t("projects.createFirst")
      }}
    </p>
    <NuxtLink
      v-if="!query && statusFilter === 'all' && runtimeFilter === 'all'"
      class="button-primary"
      to="/projects/create"
    >
      {{ t("dashboard.createProject") }}
    </NuxtLink>
  </section>
</template>

<style scoped>
h2,
p,
dl {
  margin: 0;
}
.summary-grid {
  display: grid;
  grid-template-columns: 1.3fr repeat(4, minmax(0, 1fr));
  gap: 0.85rem;
  margin-top: 2rem;
}
.summary-card {
  display: grid;
  min-height: 118px;
  align-content: space-between;
  border: 1px solid var(--border);
  border-radius: 12px;
  background: var(--surface-raised);
  padding: 1rem;
}
.summary-card span,
.summary-card small {
  color: var(--text-muted);
  font-size: 0.78rem;
}
.summary-card strong {
  font-family: "IBM Plex Mono", ui-monospace, monospace;
  font-size: clamp(1.6rem, 4vw, 2.35rem);
  font-variant-numeric: tabular-nums;
  letter-spacing: -0.05em;
}
.summary-card--primary {
  border-color: #5b392c;
  background: #211915;
}
.summary-card--danger strong {
  color: #f09a9f;
}
.toolbar {
  display: grid;
  grid-template-columns: minmax(260px, 1fr) repeat(3, minmax(150px, 210px));
  gap: 0.8rem;
  margin-top: 1rem;
  padding: 0.8rem;
}
.search,
.filter {
  display: grid;
  gap: 0.35rem;
}
.search {
  position: relative;
}
.search svg {
  position: absolute;
  top: 50%;
  left: 0.85rem;
  transform: translateY(-50%);
  color: var(--text-muted);
}
.search input,
.filter select {
  min-height: 44px;
  width: 100%;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--surface-base);
  color: var(--text);
  font: inherit;
  outline: 0;
}
.search input {
  padding: 0 0.85rem 0 2.55rem;
}
.filter span {
  color: var(--text-muted);
  font-size: 0.72rem;
}
.filter select {
  padding: 0 0.75rem;
}
.search input:focus,
.filter select:focus {
  border-color: var(--accent);
}
.sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
}
.project-list {
  display: grid;
  gap: 0.75rem;
  margin-top: 1rem;
}
.project-row {
  display: grid;
  grid-template-columns:
    minmax(260px, 1.2fr) minmax(360px, 1.4fr) minmax(260px, 0.9fr)
    auto;
  gap: 1rem;
  align-items: center;
  padding: 0.9rem;
  transition:
    border-color 180ms ease,
    background-color 180ms ease,
    transform 120ms ease;
}
.project-row:hover {
  border-color: #4a4d54;
  background: #1b1d20;
}
.project-row:active {
  transform: scale(0.995);
}
.project-main {
  display: grid;
  min-width: 0;
  grid-template-columns: auto minmax(0, 1fr);
  gap: 0.8rem;
  align-items: center;
  color: var(--text);
  text-decoration: none;
}
.project-mark {
  display: grid;
  width: 44px;
  height: 44px;
  place-items: center;
  border-radius: 10px;
  background: #2f211c;
  color: #ff9b70;
  font-family: "IBM Plex Mono", ui-monospace, monospace;
  font-size: 0.78rem;
  font-weight: 700;
}
.project-copy {
  display: grid;
  min-width: 0;
  gap: 0.35rem;
}
.project-title {
  display: flex;
  min-width: 0;
  align-items: center;
  gap: 0.55rem;
}
.project-title strong,
.project-description,
.healthcheck {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.project-title strong {
  min-width: 0;
}
.project-description,
.healthcheck {
  color: var(--text-muted);
  font-size: 0.82rem;
}
.project-meta {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: 0.75rem;
}
.project-meta dt {
  color: var(--text-muted);
  font-size: 0.7rem;
}
.project-meta dd {
  display: flex;
  min-width: 0;
  align-items: center;
  gap: 0.3rem;
  margin: 0.25rem 0 0;
  overflow: hidden;
  color: var(--text);
  font-family: "IBM Plex Mono", ui-monospace, monospace;
  font-size: 0.77rem;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.health--success {
  color: #70d49d !important;
}
.health--danger {
  color: #f09a9f !important;
}
.health--warning {
  color: #edbb68 !important;
}
.health--muted {
  color: var(--text-muted) !important;
}
.resource-strip {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 0.5rem;
}
.resource-strip span {
  display: grid;
  gap: 0.25rem;
  border-radius: 8px;
  background: var(--surface-base);
  padding: 0.55rem;
  color: var(--text-muted);
  font-size: 0.68rem;
}
.resource-strip strong {
  overflow: hidden;
  color: var(--text);
  font-family: "IBM Plex Mono", ui-monospace, monospace;
  font-size: 0.78rem;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.policy-strip {
  display: flex;
  grid-column: 1 / -1;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--surface-base);
  padding: 0.65rem 0.75rem;
}
.policy-strip strong {
  color: var(--text);
  font-size: 0.78rem;
}
.policy-strip span {
  overflow: hidden;
  color: var(--text-muted);
  font-family: "IBM Plex Mono", ui-monospace, monospace;
  font-size: 0.72rem;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.policy-strip--warning {
  border-color: #6f5524;
  background: #241f15;
}
.policy-strip--warning strong {
  color: #edbb68;
}
.project-links {
  display: flex;
  justify-content: flex-end;
  gap: 0.35rem;
}
.icon-link {
  display: grid;
  width: 44px;
  height: 44px;
  place-items: center;
  border: 1px solid var(--border);
  border-radius: 8px;
  color: var(--text-muted);
  text-decoration: none;
  transition:
    background-color 180ms ease,
    border-color 180ms ease,
    color 180ms ease,
    transform 120ms ease;
}
.icon-link:hover {
  border-color: #4a4d54;
  background: var(--surface-subtle);
  color: var(--text);
}
.icon-link:active {
  transform: scale(0.96);
}
.healthcheck {
  grid-column: 1 / -1;
  min-width: 0;
  border-top: 1px solid var(--border);
  padding-top: 0.75rem;
  text-decoration: none;
}
.healthcheck:hover {
  color: #ff9b70;
}
.state {
  display: grid;
  justify-items: start;
  gap: 0.7rem;
  margin-top: 1rem;
  border-top: 1px solid var(--border);
  padding: 4rem 0;
}
.state svg,
.state p {
  color: var(--text-muted);
}
.state .button-primary,
.state .button-secondary {
  margin-top: 0.5rem;
}
.state--error h2 {
  color: #f3a1a6;
}
.project-skeleton {
  height: 142px;
  background: var(--surface-raised);
  animation: pulse 1.3s ease-in-out infinite;
}
@keyframes pulse {
  50% {
    opacity: 0.55;
  }
}
@media (max-width: 1280px) {
  .summary-grid {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }
  .toolbar {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .project-row {
    grid-template-columns: 1fr;
  }
  .project-links {
    justify-content: flex-start;
  }
}
@media (max-width: 720px) {
  .summary-grid,
  .toolbar,
  .project-meta,
  .resource-strip {
    grid-template-columns: 1fr;
  }
  .summary-card {
    min-height: 96px;
  }
  .project-title {
    align-items: flex-start;
    flex-direction: column;
  }
  .project-links {
    display: grid;
    grid-template-columns: repeat(5, minmax(44px, 1fr));
  }
  .icon-link {
    width: auto;
  }
}
</style>
