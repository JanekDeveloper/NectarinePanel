<script setup lang="ts">
const route = useRoute();
const projectId = String(route.params.id);
const { t } = useLocale();
</script>

<template>
  <PageHeader
    :title="t('project.nav.console')"
    :description="t('console.terminalAria')"
  />
  <ProjectNav :project-id="projectId" />
  <ProjectConsole :project-id="projectId" />
</template>
