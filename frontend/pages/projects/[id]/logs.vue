<script setup lang="ts">
import { IconRefresh } from "@tabler/icons-vue";

const route = useRoute();
const projectId = String(route.params.id);
const api = useApi();
const { t } = useLocale();
const auth = useAuthStore();
const connected = ref(false);
const socket = shallowRef<WebSocket | null>(null);
let active = false;
let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
let pollTimer: ReturnType<typeof setInterval> | null = null;
const { data, pending, error, refresh } = await useAsyncData(
  `logs-${projectId}`,
  () =>
    api.request<{ content: string }>(`/projects/${projectId}/logs?lines=1000`),
);

function connect(): void {
  if (!active || !auth.accessToken) return;
  socket.value?.close();
  const connection = new WebSocket(
    useWebSocketUrl(`/projects/${projectId}/logs/live`),
  );
  socket.value = connection;
  connection.onopen = () => {
    connected.value = true;
  };
  connection.onmessage = (event) => {
    try {
      const payload = JSON.parse(String(event.data)) as { content?: string };
      if (typeof payload.content === "string")
        data.value = { content: payload.content };
    } catch {
      connection.close();
    }
  };
  connection.onclose = () => {
    connected.value = false;
    if (active) reconnectTimer = setTimeout(connect, 2000);
  };
  connection.onerror = () => connection.close();
}

onMounted(() => {
  active = true;
  connect();
  pollTimer = setInterval(() => {
    if (!connected.value) void refresh();
  }, 5000);
});

onUnmounted(() => {
  active = false;
  if (reconnectTimer) clearTimeout(reconnectTimer);
  if (pollTimer) clearInterval(pollTimer);
  socket.value?.close();
});
</script>

<template>
  <PageHeader :title="t('logs.title')" :description="t('logs.description')">
    <div class="actions">
      <span :class="{ online: connected }">{{
        connected ? "Live" : "Polling"
      }}</span>
      <button class="button-secondary" type="button" @click="() => refresh()">
        <IconRefresh :size="18" :stroke-width="1.8" /> {{ t("common.refresh") }}
      </button>
    </div>
  </PageHeader>
  <ProjectNav :project-id="projectId" />
  <pre v-if="pending" class="terminal">{{ t("common.loading") }}</pre>
  <div v-else-if="error" class="error" role="alert">
    {{ t("logs.loadError") }}
  </div>
  <pre v-else class="terminal">{{
    data?.content || t("console.emptyLogs")
  }}</pre>
</template>

<style scoped>
.terminal {
  min-height: 420px;
  overflow: auto;
  border: 1px solid var(--border);
  border-radius: 12px;
  background: #0c0d0f;
  padding: 1rem;
  color: #d7d9dc;
  font:
    0.8rem/1.65 "IBM Plex Mono",
    ui-monospace,
    monospace;
  white-space: pre-wrap;
}
.error {
  color: #f3a1a6;
}
.actions {
  display: flex;
  align-items: center;
  gap: 0.7rem;
}
.actions span {
  color: var(--text-muted);
  font-size: 0.75rem;
}
.actions span.online {
  color: #70d49d;
}
</style>
