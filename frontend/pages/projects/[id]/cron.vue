<script setup lang="ts">
import { IconClock, IconPlus, IconTrash } from "@tabler/icons-vue";

interface CronJob {
  id: string;
  name: string;
  expression: string;
  command: string;
  enabled: boolean;
  last_run_at: string | null;
  last_status: string | null;
}

const route = useRoute();
const projectId = String(route.params.id);
const api = useApi();
const { t } = useLocale();
const form = reactive({
  name: "",
  expression: "0 * * * *",
  command: "",
  enabled: true,
});
const errorMessage = ref("");
const { data, refresh } = await useAsyncData(`cron-${projectId}`, () =>
  api.request<CronJob[]>(`/projects/${projectId}/cron`),
);

async function create(): Promise<void> {
  errorMessage.value = "";
  try {
    await api.request(`/projects/${projectId}/cron`, {
      method: "POST",
      body: form,
    });
    form.name = "";
    form.command = "";
    await refresh();
  } catch {
    errorMessage.value = t("cron.validationError");
  }
}

async function remove(id: string): Promise<void> {
  await api.request(`/projects/${projectId}/cron/${id}`, { method: "DELETE" });
  await refresh();
}
</script>

<template>
  <PageHeader :title="t('cron.title')" :description="t('cron.description')" />
  <ProjectNav :project-id="projectId" />
  <form class="form panel" @submit.prevent="create">
    <label
      >{{ t("cron.name") }}<input v-model="form.name" class="control" required
    /></label>
    <label
      >{{ t("cron.expression")
      }}<input
        v-model="form.expression"
        class="control"
        required
        placeholder="0 * * * *"
    /></label>
    <label
      >{{ t("cron.command")
      }}<input v-model="form.command" class="control" required
    /></label>
    <button class="button-primary" type="submit">
      <IconPlus :size="18" /> {{ t("cron.add") }}
    </button>
    <p v-if="errorMessage" class="error" role="alert">{{ errorMessage }}</p>
  </form>
  <div class="list">
    <article v-for="job in data" :key="job.id">
      <IconClock :size="20" />
      <div>
        <strong>{{ job.name }}</strong
        ><code>{{ job.expression }} / {{ job.command }}</code>
      </div>
      <span>{{ job.last_status || t("cron.neverRun") }}</span>
      <button
        type="button"
        :aria-label="t('cron.deleteAria', { name: job.name })"
        @click="remove(job.id)"
      >
        <IconTrash :size="19" />
      </button>
    </article>
    <p v-if="!data?.length" class="empty">{{ t("cron.empty") }}</p>
  </div>
</template>

<style scoped>
.form {
  display: grid;
  grid-template-columns: 1fr 1fr 2fr auto;
  align-items: end;
  gap: 1rem;
  padding: 1rem;
}
label {
  display: grid;
  gap: 0.4rem;
  font-size: 0.82rem;
  font-weight: 600;
}
.error {
  grid-column: 1 / -1;
  color: #f3a1a6;
}
.list article {
  display: grid;
  min-height: 70px;
  grid-template-columns: auto 1fr auto auto;
  align-items: center;
  gap: 0.8rem;
  border-bottom: 1px solid var(--border);
}
.list article div {
  display: grid;
  gap: 0.3rem;
}
.list code,
.list span,
.empty {
  color: var(--text-muted);
  font-size: 0.78rem;
}
.list button {
  display: grid;
  width: 44px;
  height: 44px;
  place-items: center;
  border: 0;
  background: transparent;
  color: #f09a9f;
}
.empty {
  padding: 3rem 0;
}
@media (max-width: 800px) {
  .form {
    grid-template-columns: 1fr;
  }
}
</style>
