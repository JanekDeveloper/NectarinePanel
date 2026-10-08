<script setup lang="ts">
import { isMinecraftRuntime } from "~/utils/minecraft";
import type { Project } from "~/types/api";

const props = defineProps<{ projectId: string }>();
const api = useApi();
const route = useRoute();
const { t } = useLocale();
const projectType = ref<string | null>(null);
try {
  const project = await api.request<Project>(`/projects/${props.projectId}`, {
    silent: true,
  });
  projectType.value = project.project_type;
} catch {
  projectType.value = null;
}
const showMinecraft = computed(() => isMinecraftRuntime(projectType.value));

const tabs = computed(() => [
  { label: t("project.nav.overview"), suffix: "" },
  { label: t("project.nav.deployments"), suffix: "/deployments" },
  { label: t("project.nav.logs"), suffix: "/logs" },
  { label: t("project.nav.console"), suffix: "/console" },
  { label: t("project.nav.environment"), suffix: "/env" },
  { label: t("project.nav.files"), suffix: "/files" },
  { label: t("project.nav.domains"), suffix: "/domains" },
  { label: t("project.nav.backups"), suffix: "/backups" },
  { label: t("project.nav.cron"), suffix: "/cron" },
  { label: t("project.nav.settings"), suffix: "/settings" },
  ...(showMinecraft.value
    ? [{ label: t("project.nav.minecraft"), suffix: "/minecraft" }]
    : []),
]);
</script>

<template>
  <nav class="project-tabs" :aria-label="t('project.nav.label')">
    <NuxtLink
      v-for="tab in tabs"
      :key="tab.suffix"
      :to="`/projects/${props.projectId}${tab.suffix}`"
      :class="{
        active: route.path === `/projects/${props.projectId}${tab.suffix}`,
      }"
    >
      {{ tab.label }}
    </NuxtLink>
  </nav>
</template>

<style scoped>
.project-tabs {
  display: flex;
  gap: 1.3rem;
  margin: 1.5rem 0 2rem;
  overflow-x: auto;
  border-bottom: 1px solid var(--border);
}
.project-tabs a {
  min-height: 44px;
  flex: none;
  border-bottom: 2px solid transparent;
  color: var(--text-muted);
  font-size: 0.85rem;
  text-decoration: none;
}
.project-tabs a.active {
  border-color: var(--accent);
  color: var(--text);
}
</style>
