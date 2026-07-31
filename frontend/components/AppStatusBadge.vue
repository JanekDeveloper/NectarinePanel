<script setup lang="ts">
const props = defineProps<{ status: string }>();
const { t } = useLocale();

const labels = computed<Record<string, string>>(() => ({
  running: t("status.running"),
  stopped: t("status.stopped"),
  failed: t("status.failed"),
  deploying: t("status.deploying"),
  created: t("status.created"),
}));

const tone = computed(() => {
  if (props.status === "running") return "success";
  if (props.status === "failed") return "danger";
  if (props.status === "deploying") return "warning";
  return "neutral";
});
</script>

<template>
  <span class="status-badge" :class="`status-badge--${tone}`">
    <span class="status-badge__dot" aria-hidden="true" />
    {{ labels[status] ?? status }}
  </span>
</template>

<style scoped>
.status-badge {
  display: inline-flex;
  align-items: center;
  gap: 0.45rem;
  border: 1px solid var(--border);
  border-radius: 999px;
  padding: 0.24rem 0.55rem;
  color: var(--text-muted);
  font-size: 0.75rem;
  font-weight: 650;
}
.status-badge__dot {
  width: 0.45rem;
  height: 0.45rem;
  border-radius: 50%;
  background: currentColor;
}
.status-badge--success {
  border-color: #285b42;
  color: #70d49d;
}
.status-badge--danger {
  border-color: #73383d;
  color: #f09a9f;
}
.status-badge--warning {
  border-color: #6b4d25;
  color: #edbb68;
}
</style>
