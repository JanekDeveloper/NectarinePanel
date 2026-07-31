<script setup lang="ts">
import {
  IconArchive,
  IconDownload,
  IconRefresh,
  IconUpload,
} from "@tabler/icons-vue";
import type { Backup, BackupPolicy, Project } from "~/types/api";

const route = useRoute();
const projectId = String(route.params.id);
const api = useApi();
const { t, number, dateTime } = useLocale();
const pending = ref(false);
const uploadInput = ref<HTMLInputElement | null>(null);
const importOpen = ref(false);
const importFile = ref<File | null>(null);
const confirmation = ref("");
const restoreBackupItem = ref<Backup | null>(null);
const manualEncrypted = ref(false);
const manualAll = ref(true);
const manualItems = ref<string[]>([".env"]);
const backupItems = [
  { value: ".env", label: ".env" },
  { value: "current", label: "current" },
  { value: "shared", label: "shared" },
  { value: "uploads", label: "uploads" },
  { value: "logs", label: "logs" },
  { value: "world", label: "world" },
  { value: "world_nether", label: "world_nether" },
  { value: "world_the_end", label: "world_the_end" },
  { value: "mods", label: "mods" },
  { value: "config", label: "config" },
  { value: "server.properties", label: "server.properties" },
  { value: "whitelist.json", label: "whitelist.json" },
  { value: "ops.json", label: "ops.json" },
];
const policyForm = reactive({
  enabled: false,
  schedule: "0 3 * * *",
  retention_count: 7,
  include_items: [".env"],
  include_databases: true,
  encryption_enabled: false,
});
const { data, refresh } = await useAsyncData(`backups-${projectId}`, () =>
  api.request<Backup[]>(`/projects/${projectId}/backups`),
);
const { data: project } = await useAsyncData(
  `backup-project-${projectId}`,
  () => api.request<Project>(`/projects/${projectId}`),
);
const { data: policy } = await useAsyncData(`backup-policy-${projectId}`, () =>
  api.request<BackupPolicy | null>(`/projects/${projectId}/backup-policy`),
);
watch(
  policy,
  (value) => {
    if (value) {
      policyForm.enabled = value.enabled;
      policyForm.schedule = value.schedule;
      policyForm.retention_count = value.retention_count;
      policyForm.include_items = [...value.include_items];
      policyForm.include_databases = value.include_databases;
      policyForm.encryption_enabled = value.encryption_enabled;
    }
  },
  { immediate: true },
);

function size(bytes: number): string {
  return `${number(bytes / 1024 / 1024, { maximumFractionDigits: 1 })} MB`;
}

async function createBackup(): Promise<void> {
  if (!manualAll.value && manualItems.value.length === 0) return;
  pending.value = true;
  try {
    await api.request(`/projects/${projectId}/backups`, {
      method: "POST",
      body: {
        include_items: manualAll.value ? ["all"] : manualItems.value,
        encryption_enabled: manualEncrypted.value,
      },
    });
    await refresh();
  } finally {
    pending.value = false;
  }
}

async function savePolicy(): Promise<void> {
  if (policyForm.include_items.length === 0) return;
  await api.request(`/projects/${projectId}/backup-policy`, {
    method: "PUT",
    body: { ...policyForm },
  });
}

function chooseImport(event: Event): void {
  const input = event.target as HTMLInputElement;
  importFile.value = input.files?.[0] ?? null;
  confirmation.value = "";
  importOpen.value = Boolean(importFile.value);
}

async function importBackup(): Promise<void> {
  if (
    !importFile.value ||
    !project.value ||
    confirmation.value !== project.value.name
  )
    return;
  const body = new FormData();
  body.append("file", importFile.value);
  body.append("confirm_project_name", confirmation.value);
  await api.request(`/projects/${projectId}/backups/import`, {
    method: "POST",
    body,
  });
  importOpen.value = false;
  importFile.value = null;
  if (uploadInput.value) uploadInput.value.value = "";
  await refresh();
}

async function download(backup: Backup): Promise<void> {
  const result = await api.request<{ url: string }>(
    `/backups/${backup.id}/download-link`,
    { method: "POST" },
  );
  window.location.assign(result.url);
}

async function restore(): Promise<void> {
  if (!project.value || !restoreBackupItem.value) return;
  const backup = restoreBackupItem.value;
  restoreBackupItem.value = null;
  await api.request(`/backups/${backup.id}/restore`, {
    method: "POST",
    body: { confirm_project_name: project.value.name },
  });
  await refresh();
}
</script>

<template>
  <PageHeader
    :title="t('backups.projectTitle')"
    :description="t('backups.projectDescription')"
  >
    <div class="header-actions">
      <label class="encrypt-toggle"
        ><input v-model="manualEncrypted" type="checkbox" />
        {{ t("backups.encrypt") }}</label
      >
      <button
        class="button-secondary"
        type="button"
        @click="uploadInput?.click()"
      >
        <IconUpload :size="18" /> {{ t("backups.import") }}
      </button>
      <input
        ref="uploadInput"
        class="hidden"
        type="file"
        accept=".tar.gz,.enc"
        @change="chooseImport"
      />
      <button
        class="button-primary"
        type="button"
        :disabled="pending || (!manualAll && manualItems.length === 0)"
        @click="createBackup"
      >
        <IconArchive :size="18" />
        {{ pending ? t("backups.creating") : t("backups.create") }}
      </button>
    </div>
  </PageHeader>
  <ProjectNav :project-id="projectId" />
  <section class="selection panel">
    <div>
      <strong>{{ t("backups.manual") }}</strong
      ><span>{{ t("backups.manualDescription") }}</span>
    </div>
    <label class="check"
      ><input v-model="manualAll" type="checkbox" />
      {{ t("backups.entireRoot") }}</label
    >
    <div class="item-grid" :class="{ disabled: manualAll }">
      <label v-for="item in backupItems" :key="item.value" class="check">
        <input
          v-model="manualItems"
          type="checkbox"
          :value="item.value"
          :disabled="manualAll"
        />
        {{ item.label }}
      </label>
    </div>
  </section>
  <form class="policy panel" @submit.prevent="savePolicy">
    <div>
      <strong>{{ t("backups.automatic") }}</strong
      ><span>{{ t("backups.offByDefault") }}</span>
    </div>
    <label
      ><span>Cron (UTC)</span
      ><input v-model="policyForm.schedule" class="control" required
    /></label>
    <label
      ><span>{{ t("backups.keep") }}</span
      ><input
        v-model.number="policyForm.retention_count"
        class="control"
        type="number"
        min="1"
        max="100"
    /></label>
    <div class="policy-items">
      <span>{{ t("backups.files") }}</span>
      <div class="item-grid">
        <label v-for="item in backupItems" :key="item.value" class="check">
          <input
            v-model="policyForm.include_items"
            type="checkbox"
            :value="item.value"
          />
          {{ item.label }}
        </label>
      </div>
    </div>
    <label class="check"
      ><input v-model="policyForm.include_databases" type="checkbox" />
      {{ t("backups.attachedDatabases") }}</label
    >
    <label class="check"
      ><input v-model="policyForm.encryption_enabled" type="checkbox" />
      AES-256-GCM</label
    >
    <label class="check"
      ><input v-model="policyForm.enabled" type="checkbox" />
      {{ t("common.enabled") }}</label
    >
    <button
      class="button-secondary"
      type="submit"
      :disabled="policyForm.include_items.length === 0"
    >
      {{ t("common.save") }}
    </button>
  </form>
  <div class="backup-list">
    <article v-for="backup in data" :key="backup.id">
      <IconArchive :size="21" />
      <div>
        <strong>{{ dateTime(backup.created_at) }}</strong
        ><span>{{ size(backup.size_bytes) }} / {{ backup.status }}</span>
      </div>
      <button
        class="button-secondary"
        type="button"
        :disabled="backup.status !== 'ready'"
        @click="download(backup)"
      >
        <IconDownload :size="17" /> {{ t("backups.download") }}
      </button>
      <button
        class="button-secondary"
        type="button"
        :disabled="backup.status !== 'ready'"
        @click="restoreBackupItem = backup"
      >
        <IconRefresh :size="17" /> {{ t("common.restore") }}
      </button>
    </article>
    <p v-if="!data?.length" class="empty">
      {{ t("backups.projectEmpty") }}
    </p>
  </div>
  <div
    v-if="importOpen"
    class="modal-backdrop"
    role="presentation"
    @click.self="importOpen = false"
  >
    <section
      class="modal panel"
      role="dialog"
      aria-modal="true"
      aria-labelledby="import-title"
    >
      <h2 id="import-title">{{ t("backups.importTitle") }}</h2>
      <p>{{ t("backups.importDescription") }}</p>
      <label
        ><span>{{
          t("backups.enterProjectName", { name: project?.name || "" })
        }}</span
        ><input v-model="confirmation" class="control"
      /></label>
      <div class="modal-actions">
        <button
          class="button-secondary"
          type="button"
          @click="importOpen = false"
        >
          {{ t("common.cancel") }}
        </button>
        <button
          class="button-primary"
          type="button"
          :disabled="confirmation !== project?.name"
          @click="importBackup"
        >
          {{ t("common.import") }}
        </button>
      </div>
    </section>
  </div>
  <ConfirmDialog
    :open="Boolean(restoreBackupItem)"
    :title="t('backups.restoreTitle')"
    :message="t('backups.restoreMessage', { name: project?.name || '' })"
    :confirm-label="t('common.restore')"
    danger
    :expected-text="project?.name || null"
    :input-label="t('backups.enterProjectName', { name: project?.name || '' })"
    @cancel="restoreBackupItem = null"
    @confirm="restore"
  />
</template>

<style scoped>
.backup-list article {
  display: grid;
  min-height: 78px;
  grid-template-columns: auto 1fr auto auto;
  align-items: center;
  gap: 0.8rem;
  border-bottom: 1px solid var(--border);
}
.header-actions,
.modal-actions {
  display: flex;
  align-items: center;
  gap: 0.6rem;
}
.encrypt-toggle,
.check {
  display: flex;
  align-items: center;
  gap: 0.45rem;
  color: var(--text-muted);
  font-size: 0.82rem;
}
.hidden {
  display: none;
}
.policy {
  display: grid;
  grid-template-columns: minmax(180px, 1fr) minmax(180px, 1fr) 110px;
  align-items: end;
  gap: 0.8rem;
  margin-top: 1.25rem;
  padding: 1rem;
}
.selection {
  display: grid;
  gap: 0.8rem;
  margin-top: 1.25rem;
  padding: 1rem;
}
.policy-items {
  display: grid;
  grid-column: 1 / -1;
  gap: 0.45rem;
}
.item-grid {
  display: flex;
  flex-wrap: wrap;
  gap: 0.55rem 0.9rem;
}
.item-grid.disabled {
  opacity: 0.5;
}
.policy > div,
.selection > div,
.policy label:not(.check),
.modal label {
  display: grid;
  gap: 0.35rem;
}
.policy span,
.selection span,
.modal p {
  color: var(--text-muted);
  font-size: 0.8rem;
}
.modal-backdrop {
  position: fixed;
  z-index: 50;
  display: grid;
  inset: 0;
  place-items: center;
  background: rgb(0 0 0 / 68%);
  padding: 1rem;
}
.modal {
  display: grid;
  width: min(460px, 100%);
  gap: 1rem;
  padding: 1.25rem;
}
.modal h2,
.modal p {
  margin: 0;
}
.modal-actions {
  justify-content: flex-end;
}
.backup-list article div {
  display: grid;
  gap: 0.25rem;
}
.backup-list span,
.empty {
  color: var(--text-muted);
  font-size: 0.8rem;
}
.empty {
  padding: 3rem 0;
}
@media (max-width: 720px) {
  .header-actions,
  .policy {
    align-items: stretch;
    grid-template-columns: 1fr;
  }
  .backup-list article {
    grid-template-columns: auto 1fr;
  }
}
</style>
