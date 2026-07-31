<script setup lang="ts">
import { IconArrowRight, IconPlus } from "@tabler/icons-vue";
import type { HostMetrics, Project } from "~/types/api";

const api = useApi();
const { t, number } = useLocale();
const { data: projects, pending: projectsPending } = await useAsyncData(
  "dashboard-projects",
  () => api.request<Project[]>("/projects"),
);
const {
  data: metrics,
  pending: metricsPending,
  refresh: refreshMetrics,
} = await useAsyncData("host-metrics", () =>
  api.request<HostMetrics>("/monitoring"),
);

let timer: ReturnType<typeof setInterval> | undefined;
onMounted(() => {
  timer = setInterval(refreshMetrics, 10_000);
});
onBeforeUnmount(() => {
  if (timer) clearInterval(timer);
});

function bytes(value?: number): string {
  if (value === undefined) return "0 GB";
  return `${number(value / 1024 ** 3, { maximumFractionDigits: 1 })} GB`;
}

function uptime(value?: number): string {
  const days = Math.floor((value ?? 0) / 86400);
  const hours = Math.floor(((value ?? 0) % 86400) / 3600);
  return t("dashboard.uptimeValue", { days, hours });
}
</script>

<template>
  <header class="page-header">
    <div>
      <h1>{{ t("dashboard.title") }}</h1>
      <p>{{ t("dashboard.description") }}</p>
    </div>
    <NuxtLink class="button-primary" to="/projects/create">
      <IconPlus :size="18" :stroke-width="1.8" />
      {{ t("dashboard.newProject") }}
    </NuxtLink>
  </header>

  <div
    v-if="metricsPending"
    class="metrics-grid"
    :aria-label="t('dashboard.loadingMetrics')"
  >
    <div v-for="index in 4" :key="index" class="metric-skeleton" />
  </div>
  <section
    v-else
    class="metrics-grid"
    :aria-label="t('dashboard.serverMetrics')"
  >
    <MetricCard
      label="CPU"
      :value="`${metrics?.cpu_percent.toFixed(1) ?? '0.0'}%`"
      :detail="t('dashboard.currentLoad')"
      :percent="metrics?.cpu_percent"
    />
    <MetricCard
      :label="t('dashboard.memory')"
      :value="`${metrics?.memory_percent.toFixed(1) ?? '0.0'}%`"
      :detail="
        t('dashboard.usedOf', {
          used: bytes(metrics?.memory_used),
          total: bytes(metrics?.memory_total),
        })
      "
      :percent="metrics?.memory_percent"
    />
    <MetricCard
      :label="t('dashboard.disk')"
      :value="`${metrics?.disk_percent.toFixed(1) ?? '0.0'}%`"
      :detail="
        t('dashboard.usedOf', {
          used: bytes(metrics?.disk_used),
          total: bytes(metrics?.disk_total),
        })
      "
      :percent="metrics?.disk_percent"
    />
    <MetricCard
      :label="t('dashboard.uptime')"
      :value="uptime(metrics?.uptime_seconds)"
      :detail="t('dashboard.sinceBoot')"
    />
  </section>

  <section class="projects-section">
    <div class="section-header">
      <div>
        <h2>{{ t("nav.projects") }}</h2>
        <p>
          {{ t("dashboard.projectsCount", { count: projects?.length ?? 0 }) }}
        </p>
      </div>
      <NuxtLink to="/projects">
        {{ t("dashboard.allProjects") }}
        <IconArrowRight :size="17" :stroke-width="1.7" />
      </NuxtLink>
    </div>
    <div v-if="projectsPending" class="project-list">
      <div v-for="index in 3" :key="index" class="project-skeleton" />
    </div>
    <div v-else-if="projects?.length" class="project-list">
      <NuxtLink
        v-for="project in projects.slice(0, 6)"
        :key="project.id"
        class="project-row"
        :to="`/projects/${project.id}`"
      >
        <div class="project-mark">
          {{ project.name.slice(0, 2).toUpperCase() }}
        </div>
        <div class="project-copy">
          <strong>{{ project.name }}</strong>
          <span>{{ project.runtime_type }} / {{ project.branch }}</span>
        </div>
        <AppStatusBadge :status="project.status" />
        <IconArrowRight class="project-arrow" :size="18" :stroke-width="1.7" />
      </NuxtLink>
    </div>
    <div v-else class="empty-state">
      <h3>{{ t("dashboard.emptyTitle") }}</h3>
      <p>{{ t("dashboard.emptyDescription") }}</p>
      <NuxtLink class="button-primary" to="/projects/create">{{
        t("dashboard.createProject")
      }}</NuxtLink>
    </div>
  </section>
</template>

<style scoped>
.page-header,
.section-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 1rem;
}
h1,
h2,
h3,
p {
  margin: 0;
}
h1 {
  font-size: clamp(1.7rem, 4vw, 2.35rem);
  letter-spacing: -0.035em;
}
.page-header p,
.section-header p,
.empty-state p {
  margin-top: 0.35rem;
  color: var(--text-muted);
}
.metrics-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: clamp(1rem, 3vw, 2.5rem);
  margin-top: 2.5rem;
}
.metric-skeleton,
.project-skeleton {
  border-radius: 8px;
  background: #202226;
  animation: pulse 1.3s ease-in-out infinite;
}
.metric-skeleton {
  height: 112px;
}
.projects-section {
  margin-top: 3rem;
}
.section-header {
  align-items: end;
  border-bottom: 1px solid var(--border);
  padding-bottom: 1rem;
}
.section-header a {
  display: inline-flex;
  min-height: 44px;
  align-items: center;
  gap: 0.35rem;
  color: #ff9b70;
  text-decoration: none;
}
.project-list {
  display: grid;
}
.project-row {
  display: grid;
  min-height: 76px;
  grid-template-columns: auto 1fr auto auto;
  align-items: center;
  gap: 1rem;
  border-bottom: 1px solid var(--border);
  color: var(--text);
  text-decoration: none;
  transition: background-color 180ms ease;
}
.project-row:hover {
  background: rgb(255 255 255 / 2%);
}
.project-mark {
  display: grid;
  width: 40px;
  height: 40px;
  place-items: center;
  border-radius: 9px;
  background: #2f211c;
  color: #ff9b70;
  font-family: ui-monospace, monospace;
  font-size: 0.76rem;
  font-weight: 700;
}
.project-copy {
  display: grid;
  gap: 0.25rem;
}
.project-copy span {
  color: var(--text-muted);
  font-family: ui-monospace, monospace;
  font-size: 0.75rem;
}
.project-arrow {
  color: var(--text-muted);
}
.project-skeleton {
  height: 58px;
  margin-top: 1rem;
}
.empty-state {
  display: grid;
  justify-items: start;
  padding: 4rem 0;
}
.empty-state .button-primary {
  margin-top: 1.2rem;
}
@keyframes pulse {
  50% {
    opacity: 0.55;
  }
}
@media (max-width: 850px) {
  .metrics-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
@media (max-width: 560px) {
  .page-header {
    align-items: stretch;
    flex-direction: column;
  }
  .metrics-grid {
    grid-template-columns: 1fr;
  }
  .project-row {
    grid-template-columns: auto 1fr auto;
  }
  .project-row .status-badge {
    grid-column: 2;
    justify-self: start;
  }
  .project-arrow {
    grid-column: 3;
    grid-row: 1 / span 2;
  }
}
</style>
