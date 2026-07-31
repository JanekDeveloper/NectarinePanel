<script setup lang="ts">
import type { HostMetrics, MetricSample, Notification } from "~/types/api";

const api = useApi();
const auth = useAuthStore();
const config = useRuntimeConfig();
const { t } = useLocale();
const { data: metrics, refresh } = await useAsyncData(
  "monitoring-current",
  () => api.request<HostMetrics>("/monitoring"),
);
const { data: history } = await useAsyncData("monitoring-history", () =>
  api.request<MetricSample[]>("/monitoring/history?limit=60"),
);
const { data: notifications, refresh: refreshNotifications } =
  await useAsyncData("monitoring-alerts", () =>
    api.request<Notification[]>("/monitoring/notifications?unread_only=true"),
  );

let timer: ReturnType<typeof setInterval> | undefined;
let socket: WebSocket | undefined;

function monitoringWebSocketUrl(): string {
  const base = String(config.public.wsBase).replace(/\/$/, "");
  const url = new URL(`${base}/api/v1/monitoring/live`);
  url.searchParams.set("token", auth.accessToken || "");
  return url.toString();
}

async function markRead(id: string): Promise<void> {
  await api.request(`/monitoring/notifications/${id}/read`, { method: "POST" });
  await refreshNotifications();
}

onMounted(() => {
  auth.restore();
  if (auth.accessToken) {
    socket = new WebSocket(monitoringWebSocketUrl());
    socket.onmessage = (event) => {
      metrics.value = JSON.parse(event.data) as HostMetrics;
    };
  }
  timer = setInterval(refresh, 10_000);
});

onBeforeUnmount(() => {
  if (timer) clearInterval(timer);
  socket?.close();
});
</script>

<template>
  <PageHeader
    :title="t('monitoring.title')"
    :description="t('monitoring.description')"
  />
  <section class="metrics">
    <MetricCard
      label="CPU"
      :value="`${metrics?.cpu_percent.toFixed(1) || '0.0'}%`"
      detail="Realtime / fallback polling"
      :percent="metrics?.cpu_percent"
    />
    <MetricCard
      label="RAM"
      :value="`${metrics?.memory_percent.toFixed(1) || '0.0'}%`"
      :detail="t('monitoring.memoryUsage')"
      :percent="metrics?.memory_percent"
    />
    <MetricCard
      label="Disk"
      :value="`${metrics?.disk_percent.toFixed(1) || '0.0'}%`"
      :detail="t('monitoring.diskUsage')"
      :percent="metrics?.disk_percent"
    />
  </section>
  <section class="alerts panel">
    <h2>{{ t("monitoring.activeAlerts") }}</h2>
    <div v-if="notifications?.length" class="alert-list">
      <article v-for="item in notifications" :key="item.id" class="alert-item">
        <div>
          <strong>{{ item.title }}</strong>
          <p>{{ item.message }}</p>
          <small>{{ item.severity }} / {{ item.notification_type }}</small>
        </div>
        <button type="button" @click="markRead(item.id)">
          {{ t("monitoring.markRead") }}
        </button>
      </article>
    </div>
    <p v-else>{{ t("monitoring.noAlerts") }}</p>
  </section>
  <section class="history panel">
    <h2>{{ t("monitoring.latestSamples") }}</h2>
    <div
      v-if="history?.length"
      class="samples"
      :aria-label="t('monitoring.cpuHistory')"
    >
      <i
        v-for="sample in history"
        :key="sample.id"
        :style="{
          height: `${Math.max(2, Number(sample.values.cpu_percent || 0))}%`,
        }"
        :title="`${sample.values.cpu_percent}%`"
      />
    </div>
    <p v-else>{{ t("monitoring.noHistory") }}</p>
  </section>
</template>

<style scoped>
.metrics {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 2rem;
  margin-top: 2rem;
}
.alerts,
.history {
  margin-top: 2rem;
  padding: 1rem;
}
.alerts h2,
.history h2 {
  margin: 0;
  font-size: 1rem;
}
.alerts p,
.history p {
  color: var(--text-muted);
}
.alert-list {
  display: grid;
  gap: 0.75rem;
  margin-top: 1rem;
}
.alert-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 0.85rem;
}
.alert-item p {
  margin: 0.25rem 0;
}
.alert-item small {
  color: var(--text-muted);
}
.alert-item button {
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--surface-raised);
  color: var(--text);
  padding: 0.45rem 0.7rem;
}
.samples {
  display: flex;
  height: 220px;
  align-items: end;
  gap: 3px;
  margin-top: 1rem;
  border-bottom: 1px solid var(--border);
}
.samples i {
  min-width: 3px;
  flex: 1;
  background: var(--accent);
}
@media (max-width: 700px) {
  .metrics {
    grid-template-columns: 1fr;
  }
  .alert-item {
    align-items: flex-start;
    flex-direction: column;
  }
}
</style>
