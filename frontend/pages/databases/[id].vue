<script setup lang="ts">
import type { DatabaseInstance, DatabaseTable } from "~/types/api";

const route = useRoute();
const api = useApi();
const { t } = useLocale();
const id = String(route.params.id);
const dumpInput = ref<HTMLInputElement | null>(null);
const message = ref("");
const pendingDump = ref<File | null>(null);
const importOpen = ref(false);
const deleteOpen = ref(false);
const { data: databases } = await useAsyncData("databases-detail-list", () =>
  api.request<DatabaseInstance[]>("/databases"),
);
const database = computed(() =>
  databases.value?.find((item) => item.id === id),
);
const {
  data: tables,
  error: tablesError,
  refresh: refreshTables,
} = await useAsyncData(`database-tables-${id}`, async () => {
  if (database.value?.status !== "ready") return [];
  return api.request<DatabaseTable[]>(`/databases/${id}/tables`);
});
const canDelete = computed(() =>
  ["ready", "failed", "delete_failed"].includes(database.value?.status || ""),
);

watch(
  database,
  () => {
    void refreshTables();
  },
  { flush: "post" },
);

function reloadTables(): void {
  void refreshTables();
}

async function exportDump(): Promise<void> {
  await api.request(`/databases/${id}/export`, { method: "POST" });
  message.value = t("database.exportQueued");
}

async function openAdminer(): Promise<void> {
  const popup = window.open("about:blank", "_blank");
  try {
    const result = await api.request<{ url: string }>(
      `/databases/${id}/adminer-session`,
      {
        method: "POST",
      },
    );
    if (popup) {
      popup.opener = null;
      popup.location.assign(result.url);
    } else {
      window.location.assign(result.url);
    }
  } catch {
    popup?.close();
    message.value = t("database.adminerError");
  }
}

function chooseDump(event: Event): void {
  const input = event.target as HTMLInputElement;
  pendingDump.value = input.files?.[0] ?? null;
  importOpen.value = Boolean(pendingDump.value && database.value);
  if (!importOpen.value) input.value = "";
}

function cancelImport(): void {
  importOpen.value = false;
  pendingDump.value = null;
  if (dumpInput.value) dumpInput.value.value = "";
}

async function importDump(confirmation: string): Promise<void> {
  const file = pendingDump.value;
  if (!file || !database.value) return;
  if (confirmation !== database.value.name) {
    pendingDump.value = null;
    if (dumpInput.value) dumpInput.value.value = "";
    return;
  }
  const body = new FormData();
  body.append("file", file);
  body.append("confirm_database_name", confirmation);
  await api.request(`/databases/${id}/import`, { method: "POST", body });
  message.value = t("database.importQueued");
  pendingDump.value = null;
  importOpen.value = false;
  if (dumpInput.value) dumpInput.value.value = "";
}

async function deleteDatabase(confirmation: string): Promise<void> {
  if (!database.value || confirmation !== database.value.name) return;
  await api.request(`/databases/${id}`, {
    method: "DELETE",
    body: { confirm_database_name: confirmation },
  });
  deleteOpen.value = false;
  await navigateTo("/databases");
}
</script>

<template>
  <PageHeader
    :title="database?.name || t('database.detailFallback')"
    :description="t('database.detailDescription')"
  >
    <div class="actions">
      <button
        class="button-secondary"
        type="button"
        :disabled="database?.status !== 'ready'"
        @click="openAdminer"
      >
        Adminer
      </button>
      <button
        class="button-secondary"
        type="button"
        :disabled="database?.status !== 'ready'"
        @click="dumpInput?.click()"
      >
        {{ t("common.import") }}
      </button>
      <button
        class="button-primary"
        type="button"
        :disabled="database?.status !== 'ready'"
        @click="exportDump"
      >
        {{ t("common.export") }}
      </button>
      <button
        class="button-danger"
        type="button"
        :disabled="!canDelete"
        @click="deleteOpen = true"
      >
        {{ t("common.delete") }}
      </button>
      <input
        ref="dumpInput"
        class="hidden"
        type="file"
        accept=".dump,.sql,.db,.sqlite,.sqlite3"
        @change="chooseDump"
      />
    </div>
  </PageHeader>
  <section v-if="database" class="details panel">
    <dl>
      <div>
        <dt>{{ t("database.engine") }}</dt>
        <dd>{{ database.engine }}</dd>
      </div>
      <div>
        <dt>{{ t("database.username") }}</dt>
        <dd>{{ database.username }}</dd>
      </div>
      <div>
        <dt>{{ t("database.status") }}</dt>
        <dd>{{ database.status }}</dd>
      </div>
      <div>
        <dt>{{ t("database.project") }}</dt>
        <dd>{{ database.project_id || t("database.notAttached") }}</dd>
      </div>
    </dl>
  </section>
  <section v-if="database" class="tables panel">
    <div class="section-heading">
      <div>
        <h2>{{ t("database.tables") }}</h2>
        <p>{{ t("database.tablesDescription") }}</p>
      </div>
      <button
        class="button-secondary"
        type="button"
        :disabled="database.status !== 'ready'"
        @click="reloadTables"
      >
        {{ t("common.refresh") }}
      </button>
    </div>
    <p v-if="database.status !== 'ready'" class="empty">
      {{ t("database.tablesProvisioning") }}
    </p>
    <p v-else-if="tablesError" class="empty">
      {{ t("database.tablesUnavailable") }}
    </p>
    <ul v-else-if="tables?.length" class="table-list">
      <li v-for="table in tables" :key="`${table.schema || ''}.${table.name}`">
        <span
          >{{ table.schema ? `${table.schema}.` : "" }}{{ table.name }}</span
        >
        <small>{{ table.table_type }}</small>
      </li>
    </ul>
    <p v-else class="empty">{{ t("database.tablesEmpty") }}</p>
  </section>
  <p v-if="message" class="message" aria-live="polite">{{ message }}</p>
  <p v-else class="empty">{{ t("database.notFound") }}</p>
  <ConfirmDialog
    :open="importOpen"
    :title="t('database.importTitle')"
    :message="t('database.importMessage', { name: database?.name || '' })"
    :confirm-label="t('common.import')"
    danger
    :expected-text="database?.name || null"
    :input-label="t('database.enterName', { name: database?.name || '' })"
    @cancel="cancelImport"
    @confirm="importDump"
  />
  <ConfirmDialog
    :open="deleteOpen"
    :title="t('database.deleteTitle')"
    :message="t('database.deleteMessage', { name: database?.name || '' })"
    :confirm-label="t('common.delete')"
    danger
    :expected-text="database?.name || null"
    :input-label="t('database.enterName', { name: database?.name || '' })"
    @cancel="deleteOpen = false"
    @confirm="deleteDatabase"
  />
</template>

<style scoped>
.details {
  margin-top: 2rem;
  padding: 1.2rem;
}
.tables {
  margin-top: 1rem;
  padding: 1.2rem;
}
.section-heading {
  align-items: center;
  display: flex;
  justify-content: space-between;
  gap: 1rem;
}
.section-heading h2 {
  margin: 0;
}
.section-heading p {
  color: var(--text-muted);
  margin: 0.35rem 0 0;
}
.actions {
  display: flex;
  gap: 0.6rem;
}
.hidden {
  display: none;
}
.message {
  color: #70d49d;
}
dl {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 1.5rem;
  margin: 0;
}
dt {
  color: var(--text-muted);
  font-size: 0.75rem;
}
dd {
  margin: 0.35rem 0 0;
  font-family: ui-monospace, monospace;
}
.table-list {
  display: grid;
  gap: 0.6rem;
  list-style: none;
  margin: 1rem 0 0;
  padding: 0;
}
.table-list li {
  align-items: center;
  background: rgb(255 255 255 / 4%);
  border: 1px solid var(--border-subtle);
  border-radius: 0.75rem;
  display: flex;
  justify-content: space-between;
  padding: 0.75rem 0.9rem;
}
.table-list span {
  font-family: ui-monospace, monospace;
}
.table-list small {
  color: var(--text-muted);
}
.empty {
  color: var(--text-muted);
}
</style>
