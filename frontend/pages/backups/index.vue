<script setup lang="ts">
import { IconArchive, IconDownload, IconTrash } from "@tabler/icons-vue";
import type { Backup } from "~/types/api";

const api = useApi();
const { t, dateTime } = useLocale();
const pending = ref(false);
const encrypted = ref(false);
const selectedItems = ref<string[]>(["all"]);
const deleteTarget = ref<Backup | null>(null);
const fullBackupItems = computed(() => [
  { value: "all", label: t("backups.everything") },
  { value: "panel_db", label: "Panel DB" },
  { value: "panel_config", label: "Panel config" },
  { value: "nginx_configs", label: "Nginx configs" },
  { value: "project_files", label: "Project files" },
  { value: "minecraft_files", label: "Minecraft files" },
  { value: "sqlite_databases", label: "SQLite databases" },
  { value: "state", label: "Scheduler state" },
]);
const { data, refresh } = await useAsyncData("all-backups", () =>
  api.request<Backup[]>("/backups"),
);

watch(selectedItems, (items) => {
  if (items.includes("all") && items.length > 1) {
    selectedItems.value = ["all"];
  }
});

async function download(backup: Backup): Promise<void> {
  const result = await api.request<{ url: string }>(
    `/backups/${backup.id}/download-link`,
    { method: "POST" },
  );
  window.location.assign(result.url);
}

async function createFullBackup(): Promise<void> {
  if (selectedItems.value.length === 0) return;
  pending.value = true;
  try {
    await api.request("/backups/full", {
      method: "POST",
      body: {
        include_items: selectedItems.value,
        encryption_enabled: encrypted.value,
      },
    });
    await refresh();
  } finally {
    pending.value = false;
  }
}

async function deleteBackup(): Promise<void> {
  if (!deleteTarget.value) return;
  const backup = deleteTarget.value;
  deleteTarget.value = null;
  await api.request(`/backups/${backup.id}`, {
    method: "DELETE",
    body: { confirm_backup_id: backup.id },
  });
  await refresh();
}
</script>

<template>
  <PageHeader
    :title="t('backups.title')"
    :description="t('backups.description')"
  >
    <button
      class="button-primary"
      type="button"
      :disabled="pending || !selectedItems.length"
      @click="createFullBackup"
    >
      <IconArchive :size="18" />
      {{ pending ? t("backups.creating") : t("backups.fullTitle") }}
    </button>
  </PageHeader>
  <section class="panel full-backup">
    <div>
      <strong>{{ t("backups.fullTitle") }}</strong>
      <span>{{ t("backups.fullDescription") }}</span>
    </div>
    <div class="item-grid">
      <label v-for="item in fullBackupItems" :key="item.value" class="check">
        <input v-model="selectedItems" type="checkbox" :value="item.value" />
        {{ item.label }}
      </label>
    </div>
    <label class="check"
      ><input v-model="encrypted" type="checkbox" /> AES-256-GCM</label
    >
  </section>
  <div class="list">
    <article v-for="backup in data" :key="backup.id">
      <IconArchive :size="20" />
      <div>
        <strong>{{ backup.backup_type }}</strong
        ><span>{{ dateTime(backup.created_at) }} / {{ backup.status }}</span>
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
        @click="deleteTarget = backup"
      >
        <IconTrash :size="17" /> {{ t("common.delete") }}
      </button>
    </article>
    <p v-if="!data?.length" class="empty">{{ t("backups.empty") }}</p>
  </div>
  <ConfirmDialog
    :open="Boolean(deleteTarget)"
    :title="t('backups.deleteTitle')"
    :message="t('backups.deleteMessage')"
    :confirm-label="t('common.delete')"
    danger
    :expected-text="deleteTarget?.id || null"
    :input-label="t('backups.enterId', { id: deleteTarget?.id || '' })"
    @cancel="deleteTarget = null"
    @confirm="deleteBackup"
  />
</template>

<style scoped>
.full-backup {
  display: grid;
  gap: 0.8rem;
  margin-top: 1.25rem;
  padding: 1rem;
}
.full-backup > div:first-child {
  display: grid;
  gap: 0.25rem;
}
.full-backup span {
  color: var(--text-muted);
  font-size: 0.8rem;
}
.item-grid {
  display: flex;
  flex-wrap: wrap;
  gap: 0.55rem 0.9rem;
}
.check {
  display: flex;
  align-items: center;
  gap: 0.45rem;
  color: var(--text-muted);
  font-size: 0.82rem;
}
.list {
  margin-top: 2rem;
}
article {
  display: grid;
  min-height: 70px;
  grid-template-columns: auto 1fr auto auto;
  align-items: center;
  gap: 0.8rem;
  border-bottom: 1px solid var(--border);
}
article div {
  display: grid;
  gap: 0.25rem;
}
article span,
.empty {
  color: var(--text-muted);
  font-size: 0.8rem;
}
.empty {
  padding: 3rem 0;
}
@media (max-width: 720px) {
  article {
    grid-template-columns: auto 1fr;
  }
}
</style>
