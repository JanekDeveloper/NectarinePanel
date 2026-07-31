<script setup lang="ts">
import {
  IconAlertTriangle,
  IconCopy,
  IconEye,
  IconHistory,
  IconKey,
  IconLock,
  IconPlus,
  IconRefresh,
  IconTrash,
  IconUpload,
  IconX,
} from "@tabler/icons-vue";
import type {
  EnvironmentImportResult,
  EnvironmentReveal,
  EnvironmentVariable,
  EnvironmentVariableVersion,
} from "~/types/api";
import {
  normalizeEnvironmentKey,
  parseDotenvPreview as rawParseDotenvPreview,
  secretStats,
  validateEnvironmentKey,
  versionActionLabel as rawVersionActionLabel,
} from "~/utils/secrets";
import { environmentMessages } from "~/locales/environment";

const route = useRoute();
const projectId = String(route.params.id);
const api = useApi();
const auth = useAuthStore();
const localeController = useLocale();
const { t, dateTime } = localeController;
const copy = computed(() => environmentMessages[localeController.locale.value]);

const form = reactive({
  key: "",
  value: "",
  isSecret: true,
  reason: "",
});
const importForm = reactive({
  open: false,
  content: "",
  mode: "merge" as "merge" | "replace",
  isSecret: true,
  reason: "",
});

const saving = ref(false);
const importing = ref(false);
const errorMessage = ref("");
const importResult = ref<EnvironmentImportResult | null>(null);
const deleteKey = ref<string | null>(null);
const revealTarget = ref<EnvironmentVariable | null>(null);
const revealConfirm = ref("");
const revealReason = ref("");
const revealPending = ref(false);
const revealed = ref<EnvironmentReveal | null>(null);
const historyTarget = ref<EnvironmentVariable | null>(null);
const versions = ref<EnvironmentVariableVersion[]>([]);
const historyPending = ref(false);
const rollbackVersion = ref<EnvironmentVariableVersion | null>(null);
const rollbackConfirm = ref("");
const rollbackReason = ref("");
const rollbackPending = ref(false);

const {
  data: variables,
  pending: loading,
  error,
  refresh,
} = await useAsyncData(`env-${projectId}`, () =>
  api.request<EnvironmentVariable[]>(`/projects/${projectId}/env`),
);

const stats = computed(() => secretStats(variables.value));
const importPreview = computed(() =>
  rawParseDotenvPreview(importForm.content, localeController.locale.value),
);
const sortedVariables = computed(() => variables.value ?? []);
const canWriteEnv = computed(() => auth.canWriteProjects);
const canRevealEnv = computed(() => auth.canRevealSecrets);

function resetForm(): void {
  form.key = "";
  form.value = "";
  form.isSecret = true;
  form.reason = "";
}

function closeReveal(): void {
  revealTarget.value = null;
  revealConfirm.value = "";
  revealReason.value = "";
  revealed.value = null;
}

function closeHistory(): void {
  historyTarget.value = null;
  versions.value = [];
  rollbackVersion.value = null;
  rollbackConfirm.value = "";
  rollbackReason.value = "";
}

function closeImport(): void {
  importForm.open = false;
  importForm.content = "";
  importForm.mode = "merge";
  importForm.isSecret = true;
  importForm.reason = "";
  importResult.value = null;
}

async function save(): Promise<void> {
  const key = normalizeEnvironmentKey(form.key);
  errorMessage.value = "";
  if (!validateEnvironmentKey(key)) {
    errorMessage.value = copy.value.invalidKey;
    return;
  }
  saving.value = true;
  try {
    await api.request(`/projects/${projectId}/env/${key}`, {
      method: "PUT",
      body: {
        key,
        value: form.value,
        is_secret: form.isSecret,
        reason: form.reason || null,
      },
    });
    resetForm();
    await refresh();
  } catch {
    errorMessage.value = copy.value.saveError;
  } finally {
    saving.value = false;
  }
}

async function remove(): Promise<void> {
  if (!deleteKey.value) return;
  const variableKey = deleteKey.value;
  deleteKey.value = null;
  await api.request(`/projects/${projectId}/env/${variableKey}`, {
    method: "DELETE",
  });
  await refresh();
}

async function reveal(): Promise<void> {
  if (!revealTarget.value) return;
  revealPending.value = true;
  revealed.value = null;
  try {
    revealed.value = await api.request<EnvironmentReveal>(
      `/projects/${projectId}/env/${revealTarget.value.key}/reveal`,
      {
        method: "POST",
        body: {
          confirm_key: revealConfirm.value,
          reason: revealReason.value || null,
        },
      },
    );
  } finally {
    revealPending.value = false;
  }
}

async function copyRevealed(): Promise<void> {
  if (!revealed.value || !import.meta.client || !navigator.clipboard) return;
  await navigator.clipboard.writeText(revealed.value.value);
}

async function openHistory(variable: EnvironmentVariable): Promise<void> {
  historyTarget.value = variable;
  historyPending.value = true;
  versions.value = [];
  try {
    versions.value = await api.request<EnvironmentVariableVersion[]>(
      `/projects/${projectId}/env/${variable.key}/versions`,
    );
  } finally {
    historyPending.value = false;
  }
}

async function rollback(): Promise<void> {
  if (!historyTarget.value || !rollbackVersion.value) return;
  rollbackPending.value = true;
  try {
    await api.request(
      `/projects/${projectId}/env/${historyTarget.value.key}/rollback`,
      {
        method: "POST",
        body: {
          version_id: rollbackVersion.value.id,
          confirm_key: rollbackConfirm.value,
          reason: rollbackReason.value || null,
        },
      },
    );
    rollbackVersion.value = null;
    rollbackConfirm.value = "";
    rollbackReason.value = "";
    await refresh();
    await openHistory(historyTarget.value);
  } finally {
    rollbackPending.value = false;
  }
}

async function importDotenv(): Promise<void> {
  if (importPreview.value.error) return;
  importing.value = true;
  importResult.value = null;
  try {
    importResult.value = await api.request<EnvironmentImportResult>(
      `/projects/${projectId}/env/import`,
      {
        method: "POST",
        body: {
          content: importForm.content,
          mode: importForm.mode,
          is_secret: importForm.isSecret,
          reason: importForm.reason || null,
        },
      },
    );
    await refresh();
  } finally {
    importing.value = false;
  }
}

function versionActionLabel(version: EnvironmentVariableVersion): string {
  return rawVersionActionLabel(version, localeController.locale.value);
}
</script>

<template>
  <PageHeader :title="copy.title" :description="copy.description" />
  <ProjectNav :project-id="projectId" />

  <section class="summary panel">
    <div>
      <span>{{ copy.total }}</span>
      <strong>{{ stats.total }}</strong>
    </div>
    <div>
      <span>{{ copy.secrets }}</span>
      <strong>{{ stats.secret }}</strong>
    </div>
    <div>
      <span>{{ copy.publicValues }}</span>
      <strong>{{ stats.public }}</strong>
    </div>
    <p>
      <IconAlertTriangle :size="18" />
      {{ copy.restartWarning }}
    </p>
  </section>

  <section class="manager-grid">
    <form v-if="canWriteEnv" class="secret-form panel" @submit.prevent="save">
      <header>
        <IconKey :size="20" />
        <div>
          <strong>{{ copy.addOrUpdate }}</strong>
          <span>{{ copy.newVersion }}</span>
        </div>
      </header>
      <label>
        <span>{{ copy.name }}</span>
        <input
          v-model="form.key"
          class="control"
          pattern="[A-Z_][A-Z0-9_]*"
          placeholder="API_TOKEN"
          required
        />
      </label>
      <label>
        <span>{{ copy.value }}</span>
        <textarea
          v-model="form.value"
          class="control secret-value"
          required
          autocomplete="off"
          spellcheck="false"
        />
      </label>
      <label>
        <span>{{ copy.reason }}</span>
        <input
          v-model="form.reason"
          class="control"
          maxlength="1000"
          :placeholder="copy.reasonPlaceholder"
        />
      </label>
      <label class="checkbox">
        <input v-model="form.isSecret" type="checkbox" />
        {{ copy.secretValue }}
      </label>
      <button class="button-primary" type="submit" :disabled="saving">
        <IconPlus :size="18" />
        {{ saving ? t("common.saving") : t("common.save") }}
      </button>
      <p v-if="errorMessage" class="error" role="alert">{{ errorMessage }}</p>
    </form>

    <section v-if="canWriteEnv" class="import-panel panel">
      <header>
        <IconUpload :size="20" />
        <div>
          <strong>{{ copy.bulkImport }}</strong>
          <span>{{ copy.noShellExpansion }}</span>
        </div>
      </header>
      <button
        class="button-secondary"
        type="button"
        @click="importForm.open = true"
      >
        <IconUpload :size="18" /> {{ copy.openImport }}
      </button>
    </section>
  </section>

  <section class="variables panel">
    <header class="table-header">
      <div>
        <strong>{{ copy.variables }}</strong>
        <span>{{ copy.maskedDescription }}</span>
      </div>
      <button class="button-secondary" type="button" @click="() => refresh()">
        <IconRefresh :size="18" /> {{ t("common.refresh") }}
      </button>
    </header>

    <div v-if="loading" class="state">{{ copy.loading }}</div>
    <div v-else-if="error" class="state error">
      {{ copy.loadError }}
    </div>
    <div v-else-if="!sortedVariables.length" class="state">
      {{ copy.empty }}
    </div>
    <article
      v-for="variable in sortedVariables"
      v-else
      :key="variable.id"
      class="variable-row"
    >
      <div class="variable-main">
        <span class="secret-icon">
          <IconLock v-if="variable.is_secret" :size="18" />
          <IconKey v-else :size="18" />
        </span>
        <div>
          <strong>{{ variable.key }}</strong>
          <code>{{ variable.value }}</code>
        </div>
      </div>
      <div class="variable-meta">
        <span>{{ variable.is_secret ? copy.secretKind : copy.plainKind }}</span>
        <time :datetime="variable.updated_at">
          {{ dateTime(variable.updated_at) }}
        </time>
      </div>
      <div class="actions" :aria-label="copy.actions">
        <button
          v-if="canRevealEnv"
          type="button"
          :aria-label="`${copy.reveal} ${variable.key}`"
          @click="
            revealTarget = variable;
            revealConfirm = '';
            revealed = null;
          "
        >
          <IconEye :size="18" />
        </button>
        <button
          type="button"
          :aria-label="copy.historyAria.replace('{key}', variable.key)"
          @click="openHistory(variable)"
        >
          <IconHistory :size="18" />
        </button>
        <button
          v-if="canWriteEnv"
          type="button"
          :aria-label="copy.deleteAria.replace('{key}', variable.key)"
          @click="deleteKey = variable.key"
        >
          <IconTrash :size="18" />
        </button>
      </div>
    </article>
  </section>

  <div v-if="revealTarget" class="modal-backdrop" role="presentation">
    <section
      class="modal panel"
      role="dialog"
      aria-modal="true"
      aria-labelledby="reveal-title"
    >
      <header>
        <div>
          <strong id="reveal-title"
            >{{ copy.reveal }} {{ revealTarget.key }}</strong
          >
          <span>{{ copy.plaintextOnce }}</span>
        </div>
        <button
          type="button"
          :aria-label="copy.closeReveal"
          @click="closeReveal"
        >
          <IconX :size="20" />
        </button>
      </header>
      <label>
        <span>{{ copy.confirmKey }}</span>
        <input v-model="revealConfirm" class="control" autocomplete="off" />
      </label>
      <label>
        <span>{{ copy.reason }}</span>
        <input v-model="revealReason" class="control" maxlength="1000" />
      </label>
      <button
        class="button-primary"
        type="button"
        :disabled="revealPending || revealConfirm !== revealTarget.key"
        @click="reveal"
      >
        <IconEye :size="18" />
        {{ revealPending ? copy.revealing : copy.reveal }}
      </button>
      <div v-if="revealed" class="revealed" aria-live="polite">
        <span>{{ revealed.key }}</span>
        <code>{{ revealed.value }}</code>
        <button class="button-secondary" type="button" @click="copyRevealed">
          <IconCopy :size="18" /> {{ copy.copy }}
        </button>
      </div>
    </section>
  </div>

  <div v-if="historyTarget" class="modal-backdrop" role="presentation">
    <section
      class="modal panel wide"
      role="dialog"
      aria-modal="true"
      aria-labelledby="history-title"
    >
      <header>
        <div>
          <strong id="history-title"
            >{{ copy.history }} {{ historyTarget.key }}</strong
          >
          <span>{{ copy.hashesOnly }}</span>
        </div>
        <button
          type="button"
          :aria-label="copy.closeHistory"
          @click="closeHistory"
        >
          <IconX :size="20" />
        </button>
      </header>
      <div v-if="historyPending" class="state">{{ copy.loadingHistory }}</div>
      <div v-else-if="!versions.length" class="state">
        {{ copy.emptyHistory }}
      </div>
      <article
        v-for="version in versions"
        v-else
        :key="version.id"
        class="version-row"
      >
        <div>
          <strong>{{ versionActionLabel(version) }}</strong>
          <span>{{ dateTime(version.created_at) }}</span>
          <code>{{ version.value_sha256 || "tombstone" }}</code>
          <small v-if="version.reason">{{ version.reason }}</small>
        </div>
        <button
          class="button-secondary"
          type="button"
          :disabled="version.action === 'delete'"
          @click="
            rollbackVersion = version;
            rollbackConfirm = '';
          "
        >
          <IconRefresh :size="18" /> {{ copy.rollback }}
        </button>
      </article>
      <div v-if="rollbackVersion" class="rollback-box">
        <strong>{{ copy.rollback }} {{ historyTarget.key }}</strong>
        <label>
          <span>{{ copy.confirmKey }}</span>
          <input v-model="rollbackConfirm" class="control" autocomplete="off" />
        </label>
        <label>
          <span>{{ copy.reason }}</span>
          <input v-model="rollbackReason" class="control" maxlength="1000" />
        </label>
        <div class="modal-actions">
          <button
            class="button-secondary"
            type="button"
            @click="rollbackVersion = null"
          >
            {{ t("common.cancel") }}
          </button>
          <button
            class="button-primary"
            type="button"
            :disabled="rollbackPending || rollbackConfirm !== historyTarget.key"
            @click="rollback"
          >
            {{ rollbackPending ? copy.rollingBack : copy.rollback }}
          </button>
        </div>
      </div>
    </section>
  </div>

  <div v-if="importForm.open" class="modal-backdrop" role="presentation">
    <section
      class="modal panel wide"
      role="dialog"
      aria-modal="true"
      aria-labelledby="import-title"
    >
      <header>
        <div>
          <strong id="import-title">{{ copy.import }} .env</strong>
          <span>{{ copy.dotenvSupport }}</span>
        </div>
        <button
          type="button"
          :aria-label="copy.closeImport"
          @click="closeImport"
        >
          <IconX :size="20" />
        </button>
      </header>
      <textarea
        v-model="importForm.content"
        class="control dotenv"
        placeholder="DATABASE_URL=postgresql://..."
        spellcheck="false"
      />
      <div class="import-options">
        <label>
          <span>{{ copy.mode }}</span>
          <select v-model="importForm.mode" class="control">
            <option value="merge">{{ copy.merge }}</option>
            <option value="replace">{{ copy.replace }}</option>
          </select>
        </label>
        <label class="checkbox">
          <input v-model="importForm.isSecret" type="checkbox" />
          {{ copy.importAsSecret }}
        </label>
      </div>
      <label>
        <span>{{ copy.reason }}</span>
        <input v-model="importForm.reason" class="control" maxlength="1000" />
      </label>
      <p v-if="importPreview.error" class="error" role="alert">
        {{ importPreview.error }}
      </p>
      <p v-else class="preview">
        {{ copy.willImport.replace("{keys}", importPreview.keys.join(", ")) }}
      </p>
      <p v-if="importForm.mode === 'replace'" class="warning">
        {{ copy.replaceWarning }}
      </p>
      <button
        class="button-primary"
        type="button"
        :disabled="importing || Boolean(importPreview.error)"
        @click="importDotenv"
      >
        <IconUpload :size="18" />
        {{ importing ? copy.importing : copy.import }}
      </button>
      <p v-if="importResult" class="success" aria-live="polite">
        {{
          copy.result
            .replace("{created}", String(importResult.created))
            .replace("{updated}", String(importResult.updated))
            .replace("{deleted}", String(importResult.deleted))
        }}
      </p>
    </section>
  </div>

  <ConfirmDialog
    :open="Boolean(deleteKey)"
    :title="copy.deleteTitle"
    :message="copy.deleteMessage.replace('{key}', deleteKey || '')"
    :confirm-label="t('common.delete')"
    danger
    @cancel="deleteKey = null"
    @confirm="remove"
  />
</template>

<style scoped>
.summary {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr)) minmax(260px, 1.4fr);
  gap: 1rem;
  padding: 1rem;
}

.summary div {
  display: grid;
  gap: 0.25rem;
}

.summary span,
.summary p,
.secret-form header span,
.import-panel header span,
.table-header span,
.modal header span,
.version-row span,
.version-row small {
  color: var(--text-muted);
}

.summary strong {
  font-size: 1.6rem;
}

.summary p {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  margin: 0;
}

.manager-grid {
  display: grid;
  grid-template-columns: minmax(0, 2fr) minmax(280px, 1fr);
  gap: 1rem;
  margin-top: 1rem;
}

.secret-form,
.import-panel {
  display: grid;
  gap: 1rem;
  padding: 1rem;
}

.secret-form header,
.import-panel header,
.table-header,
.modal header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
}

.secret-form header > div,
.import-panel header > div,
.table-header > div,
.modal header > div {
  display: grid;
  gap: 0.25rem;
}

label {
  display: grid;
  gap: 0.4rem;
  font-size: 0.85rem;
  font-weight: 650;
}

.secret-value,
.dotenv {
  min-height: 132px;
  resize: vertical;
  font-family: "IBM Plex Mono", "JetBrains Mono", monospace;
}

.checkbox {
  display: flex;
  min-height: 44px;
  align-items: center;
  gap: 0.55rem;
}

.variables {
  margin-top: 1rem;
  overflow: hidden;
}

.table-header {
  padding: 1rem;
  border-bottom: 1px solid var(--border);
}

.variable-row {
  display: grid;
  grid-template-columns: minmax(0, 1.5fr) minmax(220px, 0.8fr) auto;
  align-items: center;
  gap: 1rem;
  padding: 0.9rem 1rem;
  border-bottom: 1px solid var(--border);
}

.variable-row:last-child {
  border-bottom: 0;
}

.variable-main {
  display: flex;
  min-width: 0;
  align-items: center;
  gap: 0.75rem;
}

.secret-icon {
  display: grid;
  width: 36px;
  height: 36px;
  flex: 0 0 auto;
  place-items: center;
  border-radius: 10px;
  background: var(--surface-subtle);
  color: var(--accent);
}

.variable-main div,
.variable-meta {
  display: grid;
  min-width: 0;
  gap: 0.25rem;
}

code {
  overflow: hidden;
  color: var(--text-muted);
  font-family: "IBM Plex Mono", "JetBrains Mono", monospace;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.variable-meta {
  color: var(--text-muted);
  font-size: 0.82rem;
}

.actions {
  display: flex;
  gap: 0.35rem;
}

.actions button,
.modal header button {
  display: grid;
  width: 44px;
  height: 44px;
  cursor: pointer;
  place-items: center;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--surface-subtle);
  color: var(--text);
}

.actions button:hover,
.modal header button:hover {
  border-color: #4a4d54;
}

.state {
  padding: 3rem 1rem;
  color: var(--text-muted);
  text-align: center;
}

.error {
  color: #f3a1a6;
}

.warning {
  margin: 0;
  color: #f3c47f;
}

.success {
  margin: 0;
  color: #8be0b1;
}

.preview {
  margin: 0;
  color: var(--text-muted);
}

.modal-backdrop {
  position: fixed;
  z-index: 100;
  inset: 0;
  display: grid;
  place-items: center;
  background: rgb(0 0 0 / 64%);
  padding: 1rem;
}

.modal {
  display: grid;
  width: min(560px, 100%);
  max-height: calc(100dvh - 2rem);
  gap: 1rem;
  overflow: auto;
  padding: 1rem;
}

.modal.wide {
  width: min(840px, 100%);
}

.revealed,
.rollback-box {
  display: grid;
  gap: 0.75rem;
  border: 1px solid var(--border);
  border-radius: 10px;
  background: var(--surface-base);
  padding: 1rem;
}

.revealed code {
  white-space: pre-wrap;
  word-break: break-word;
}

.version-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: center;
  gap: 1rem;
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 0.85rem;
}

.version-row div {
  display: grid;
  min-width: 0;
  gap: 0.25rem;
}

.version-row code {
  max-width: 100%;
}

.import-options,
.modal-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 0.75rem;
  align-items: end;
}

.import-options > label {
  min-width: 180px;
}

@media (max-width: 920px) {
  .summary,
  .manager-grid,
  .variable-row {
    grid-template-columns: 1fr;
  }

  .variable-row {
    align-items: stretch;
  }

  .actions {
    justify-content: flex-start;
  }

  .table-header {
    align-items: flex-start;
    flex-direction: column;
  }
}
</style>
