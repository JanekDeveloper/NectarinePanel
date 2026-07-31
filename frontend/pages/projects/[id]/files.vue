<script setup lang="ts">
import {
  IconArchive,
  IconArrowRight,
  IconArrowUp,
  IconArrowsMove,
  IconChevronRight,
  IconDownload,
  IconDatabase,
  IconEdit,
  IconFile,
  IconFileZip,
  IconFolder,
  IconFolderPlus,
  IconFolderSymlink,
  IconTrash,
  IconUpload,
  IconX,
} from "@tabler/icons-vue";

import {
  isBrowsableDirectory,
  isBrowsableFile,
  persistentMounts,
} from "~/utils/files";
import type { Project } from "~/types/api";
import { fileMessages } from "~/locales/files";

interface FileEntry {
  name: string;
  path: string;
  kind: string;
  target_kind?: string | null;
  size_bytes: number;
  modified_at: string;
  permissions: string;
}

const route = useRoute();
const projectId = String(route.params.id);
const api = useApi();
const localeController = useLocale();
const { t, dateTime, number } = localeController;
const copy = computed(() => fileMessages[localeController.locale.value]);
const currentPath = ref("");
const includeHidden = ref(false);
const fileInput = ref<HTMLInputElement | null>(null);
const sftp = ref<{
  enabled: boolean;
  username: string | null;
  one_time_password?: string | null;
} | null>(null);
const sftpSecret = ref("");
const directoryModalOpen = ref(false);
const fileModalOpen = ref(false);
const disableSftpOpen = ref(false);
const moveTarget = ref<FileEntry | null>(null);
const deleteTarget = ref<FileEntry | null>(null);
const archiveTarget = ref<FileEntry | null>(null);
const extractTarget = ref<FileEntry | null>(null);
const editorPath = ref("");
const editorContent = ref("");
const editorOpen = ref(false);
const message = ref("");
const selectedPaths = ref<string[]>([]);
const bulkMoveOpen = ref(false);
const bulkDeleteOpen = ref(false);

const { data: project } = await useAsyncData(`files-project-${projectId}`, () =>
  api.request<Project>(`/projects/${projectId}`),
);

const { data, refresh } = await useAsyncData(
  () => `files-${projectId}-${currentPath.value}-${includeHidden.value}`,
  () =>
    api.request<FileEntry[]>(`/projects/${projectId}/files`, {
      query: { path: currentPath.value, include_hidden: includeHidden.value },
    }),
  { watch: [currentPath, includeHidden] },
);
const selectedEntries = computed(() =>
  (data.value || []).filter((entry) =>
    selectedPaths.value.includes(entry.path),
  ),
);
const allSelected = computed(
  () =>
    Boolean(data.value?.length) &&
    selectedEntries.value.length === data.value?.length,
);
const breadcrumbs = computed(() => {
  const result: { label: string; path: string }[] = [
    { label: "project", path: "" },
  ];
  let path = "";
  for (const part of currentPath.value.split("/").filter(Boolean)) {
    path = [path, part].filter(Boolean).join("/");
    result.push({ label: part, path });
  }
  return result;
});
const projectPersistentMounts = computed(() =>
  persistentMounts(project.value?.runtime_config ?? {}),
);
const extractDestination = computed(() => {
  const path = extractTarget.value?.path;
  return path ? parentPath(path) || "." : "";
});

watch([currentPath, includeHidden], () => {
  selectedPaths.value = [];
});

onMounted(async () => {
  try {
    sftp.value = await api.request<{
      enabled: boolean;
      username: string | null;
      one_time_password?: string | null;
    }>(`/projects/${projectId}/sftp`, { silent: true });
  } catch {
    sftp.value = { enabled: false, username: null };
  }
});

function joinPath(name: string): string {
  return [currentPath.value, name].filter(Boolean).join("/");
}

function parentPath(path: string): string {
  return path.split("/").slice(0, -1).join("/");
}

function formatSize(value: number): string {
  if (value < 1024) return `${number(value)} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let size = value / 1024;
  let unit = units[0];
  for (let index = 1; index < units.length && size >= 1024; index += 1) {
    size /= 1024;
    unit = units[index];
  }
  return `${number(size, { maximumFractionDigits: size >= 10 ? 1 : 2 })} ${unit}`;
}

function formatDate(value: string): string {
  return dateTime(value, {
    dateStyle: "short",
    timeStyle: "short",
  });
}

function toggleSelection(entry: FileEntry): void {
  selectedPaths.value = selectedPaths.value.includes(entry.path)
    ? selectedPaths.value.filter((path) => path !== entry.path)
    : [...selectedPaths.value, entry.path];
}

function toggleAll(): void {
  selectedPaths.value = allSelected.value
    ? []
    : (data.value || []).map((entry) => entry.path);
}

function isEditable(entry: FileEntry): boolean {
  return isBrowsableFile(entry) && entry.size_bytes <= 2 * 1024 * 1024;
}

function isArchive(entry: FileEntry): boolean {
  const name = entry.name.toLowerCase();
  return (
    isBrowsableFile(entry) &&
    (name.endsWith(".zip") || name.endsWith(".tar.gz"))
  );
}

async function open(entry: FileEntry): Promise<void> {
  if (isBrowsableDirectory(entry)) {
    currentPath.value = entry.path;
    return;
  }
  if (isBrowsableFile(entry)) {
    await download(entry);
  }
}

function up(): void {
  currentPath.value = parentPath(currentPath.value);
}

function openPersistentMount(source: string): void {
  currentPath.value = ["shared", source].filter(Boolean).join("/");
}

async function createDirectory(): Promise<void> {
  directoryModalOpen.value = true;
}

async function submitDirectory(name: string): Promise<void> {
  const trimmed = name.trim();
  if (!trimmed) return;
  await api.request(`/projects/${projectId}/files`, {
    method: "POST",
    body: { path: joinPath(trimmed), kind: "directory", content: "" },
  });
  directoryModalOpen.value = false;
  await refresh();
}

async function submitFile(name: string): Promise<void> {
  const trimmed = name.trim();
  if (!trimmed) return;
  await api.request(`/projects/${projectId}/files`, {
    method: "POST",
    body: { path: joinPath(trimmed), kind: "file", content: "" },
  });
  fileModalOpen.value = false;
  await refresh();
}

async function upload(event: Event): Promise<void> {
  const input = event.target as HTMLInputElement;
  const file = input.files?.[0];
  if (!file) return;
  const body = new FormData();
  body.append("file", file);
  await api.request(`/projects/${projectId}/files/upload`, {
    method: "POST",
    query: { path: joinPath(file.name) },
    body,
  });
  input.value = "";
  await refresh();
}

async function download(entry: FileEntry): Promise<void> {
  const auth = useAuthStore();
  const response = await fetch(
    `${useRuntimeConfig().public.apiBase}/projects/${projectId}/files/download?path=${encodeURIComponent(entry.path)}`,
    { headers: { Authorization: `Bearer ${auth.accessToken}` } },
  );
  if (!response.ok) throw new Error("Download failed");
  const url = URL.createObjectURL(await response.blob());
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = entry.name;
  anchor.click();
  URL.revokeObjectURL(url);
}

async function edit(entry: FileEntry): Promise<void> {
  const payload = await api.request<{ path: string; content: string }>(
    `/projects/${projectId}/files/content`,
    { query: { path: entry.path } },
  );
  editorPath.value = payload.path;
  editorContent.value = payload.content;
  editorOpen.value = true;
}

async function saveEditor(): Promise<void> {
  await api.request(`/projects/${projectId}/files/content`, {
    method: "PUT",
    query: { path: editorPath.value },
    body: { content: editorContent.value },
  });
  editorOpen.value = false;
  await refresh();
}

async function moveEntry(destination: string): Promise<void> {
  const target = moveTarget.value;
  const trimmed = destination.trim();
  if (!target || !trimmed) return;
  await api.request(`/projects/${projectId}/files/move`, {
    method: "POST",
    query: { path: target.path },
    body: { destination: trimmed },
  });
  moveTarget.value = null;
  await refresh();
}

async function deleteEntry(): Promise<void> {
  const target = deleteTarget.value;
  if (!target) return;
  await api.request(`/projects/${projectId}/files`, {
    method: "DELETE",
    query: { path: target.path },
    body: { confirm_path: target.path },
  });
  deleteTarget.value = null;
  await refresh();
}

async function deleteSelected(): Promise<void> {
  const entries = [...selectedEntries.value];
  if (!entries.length) return;
  for (const entry of entries) {
    await api.request(`/projects/${projectId}/files`, {
      method: "DELETE",
      query: { path: entry.path },
      body: { confirm_path: entry.path },
    });
  }
  bulkDeleteOpen.value = false;
  selectedPaths.value = [];
  message.value = copy.value.deleted.replace("{count}", String(entries.length));
  await refresh();
}

async function moveSelected(destination: string): Promise<void> {
  const entries = [...selectedEntries.value];
  const directory = destination.trim().replace(/^\/+|\/+$/g, "");
  if (!entries.length || !destination.trim()) return;
  for (const entry of entries) {
    await api.request(`/projects/${projectId}/files/move`, {
      method: "POST",
      query: { path: entry.path },
      body: {
        destination: [directory, entry.name].filter(Boolean).join("/"),
      },
    });
  }
  bulkMoveOpen.value = false;
  selectedPaths.value = [];
  message.value = copy.value.moved.replace("{count}", String(entries.length));
  await refresh();
}

async function archiveEntry(destination: string): Promise<void> {
  const target = archiveTarget.value;
  const trimmed = destination.trim();
  if (!target || !trimmed) return;
  const result = await api.request<{ job_id: string }>(
    `/projects/${projectId}/files/archive`,
    {
      method: "POST",
      body: { source: target.path, destination: trimmed },
    },
  );
  archiveTarget.value = null;
  message.value = copy.value.archiveQueued.replace("{id}", result.job_id);
  await refresh();
}

async function extractEntry(destination: string): Promise<void> {
  const target = extractTarget.value;
  const trimmed = destination.trim();
  if (!target || !trimmed) return;
  const result = await api.request<{ job_id: string }>(
    `/projects/${projectId}/files/extract`,
    {
      method: "POST",
      body: { source: target.path, destination: trimmed },
    },
  );
  extractTarget.value = null;
  message.value = copy.value.extractQueued.replace("{id}", result.job_id);
  await refresh();
}

async function enableSftp(): Promise<void> {
  const result = await api.request<{
    enabled: boolean;
    username: string | null;
    one_time_password: string | null;
  }>(`/projects/${projectId}/sftp`, {
    method: "PUT",
    body: { generate_password: true, public_key: null },
  });
  sftp.value = result;
  sftpSecret.value = result.one_time_password || "";
}

async function disableSftp(): Promise<void> {
  disableSftpOpen.value = false;
  await api.request(`/projects/${projectId}/sftp`, { method: "DELETE" });
  sftp.value = { enabled: false, username: null };
  sftpSecret.value = "";
}
</script>

<template>
  <PageHeader :title="copy.title" :description="copy.description">
    <div class="actions">
      <button
        class="button-secondary"
        type="button"
        @click="fileModalOpen = true"
      >
        <IconFile :size="18" /> {{ copy.file }}
      </button>
      <button class="button-secondary" type="button" @click="createDirectory">
        <IconFolderPlus :size="18" /> {{ copy.folder }}
      </button>
      <button class="button-primary" type="button" @click="fileInput?.click()">
        <IconUpload :size="18" /> {{ copy.upload }}
      </button>
      <input ref="fileInput" class="hidden" type="file" @change="upload" />
    </div>
  </PageHeader>
  <ProjectNav :project-id="projectId" />
  <section class="sftp panel">
    <div>
      <strong>SFTP</strong>
      <p v-if="sftp?.enabled">
        User: <code>{{ sftp.username }}</code
        >, directory: <code>/uploads</code>
      </p>
      <p v-else>{{ copy.sftpDescription }}</p>
      <p v-if="sftpSecret" class="secret">
        {{ copy.oneTimePassword }}: <code>{{ sftpSecret }}</code>
      </p>
    </div>
    <button
      v-if="sftp?.enabled"
      class="button-danger"
      type="button"
      @click="disableSftpOpen = true"
    >
      {{ copy.disable }}
    </button>
    <button v-else class="button-secondary" type="button" @click="enableSftp">
      {{ copy.enableSftp }}
    </button>
  </section>
  <section v-if="projectPersistentMounts.length" class="persistent-data panel">
    <div class="persistent-data__heading">
      <IconDatabase :size="20" />
      <div>
        <strong>{{ copy.persistentTitle }}</strong>
        <p>{{ copy.persistentDescription }}</p>
      </div>
    </div>
    <div class="persistent-data__mounts">
      <button
        v-for="mount in projectPersistentMounts"
        :key="`${mount.source}:${mount.target}`"
        type="button"
        @click="openPersistentMount(mount.source)"
      >
        <code>{{ mount.target }}</code>
        <IconArrowRight :size="16" aria-hidden="true" />
        <code>shared/{{ mount.source }}</code>
        <span v-if="mount.read_only">{{ copy.readOnly }}</span>
      </button>
    </div>
  </section>
  <p v-if="message" class="notice">{{ message }}</p>
  <div class="toolbar">
    <button
      class="icon-button"
      type="button"
      :disabled="!currentPath"
      :aria-label="copy.up"
      :title="copy.up"
      @click="up"
    >
      <IconArrowUp :size="19" />
    </button>
    <nav class="breadcrumbs" :aria-label="copy.folderPath">
      <template v-for="(crumb, index) in breadcrumbs" :key="crumb.path">
        <IconChevronRight v-if="index > 0" :size="15" aria-hidden="true" />
        <button
          type="button"
          :aria-current="index === breadcrumbs.length - 1 ? 'page' : undefined"
          @click="currentPath = crumb.path"
        >
          {{ crumb.label }}
        </button>
      </template>
    </nav>
    <label
      ><input v-model="includeHidden" type="checkbox" />
      {{ copy.hiddenFiles }}</label
    >
  </div>
  <div v-if="selectedEntries.length" class="selection-bar" role="status">
    <strong>{{
      copy.selected.replace("{count}", String(selectedEntries.length))
    }}</strong>
    <div>
      <button
        class="button-secondary"
        type="button"
        @click="bulkMoveOpen = true"
      >
        <IconArrowsMove :size="18" /> {{ copy.move }}
      </button>
      <button
        class="button-danger"
        type="button"
        @click="bulkDeleteOpen = true"
      >
        <IconTrash :size="18" /> {{ t("common.delete") }}
      </button>
      <button
        class="icon-button"
        type="button"
        :aria-label="copy.clearSelection"
        :title="copy.clearSelection"
        @click="selectedPaths = []"
      >
        <IconX :size="18" />
      </button>
    </div>
  </div>
  <div class="file-table">
    <div class="file-head" role="row">
      <label class="select-cell">
        <input
          type="checkbox"
          :checked="allSelected"
          :aria-label="copy.selectAll"
          @change="toggleAll"
        />
      </label>
      <span>{{ copy.name }}</span>
      <span>{{ copy.size }}</span>
      <span>{{ copy.modified }}</span>
      <span class="actions-heading">{{ copy.actions }}</span>
    </div>
    <div
      v-for="entry in data"
      :key="entry.path"
      class="file-row"
      :class="{ selected: selectedPaths.includes(entry.path) }"
    >
      <label class="select-cell">
        <input
          type="checkbox"
          :checked="selectedPaths.includes(entry.path)"
          :aria-label="copy.selectItem.replace('{name}', entry.name)"
          @change="toggleSelection(entry)"
        />
      </label>
      <button class="file-main" type="button" @click="open(entry)">
        <IconFolder v-if="entry.kind === 'directory'" :size="20" />
        <IconFolderSymlink v-else-if="entry.kind === 'symlink'" :size="20" />
        <IconFile v-else :size="20" />
        <span>
          <strong>{{ entry.name }}</strong>
          <code>{{ entry.permissions }}</code>
        </span>
      </button>
      <span class="file-size">{{ formatSize(entry.size_bytes) }}</span>
      <time :datetime="entry.modified_at">{{
        formatDate(entry.modified_at)
      }}</time>
      <div class="file-actions">
        <button
          v-if="isEditable(entry)"
          type="button"
          :aria-label="copy.editItem.replace('{name}', entry.name)"
          :title="copy.edit"
          @click="edit(entry)"
        >
          <IconEdit :size="18" />
        </button>
        <button
          v-if="isBrowsableFile(entry)"
          type="button"
          :aria-label="copy.downloadItem.replace('{name}', entry.name)"
          :title="copy.download"
          @click="download(entry)"
        >
          <IconDownload :size="18" />
        </button>
        <button
          type="button"
          :aria-label="copy.moveItem.replace('{name}', entry.name)"
          :title="copy.move"
          @click="moveTarget = entry"
        >
          <IconArrowsMove :size="18" />
        </button>
        <button
          type="button"
          :aria-label="copy.archiveItem.replace('{name}', entry.name)"
          :title="copy.archive"
          @click="archiveTarget = entry"
        >
          <IconArchive :size="18" />
        </button>
        <button
          v-if="isArchive(entry)"
          type="button"
          :aria-label="copy.extractItem.replace('{name}', entry.name)"
          :title="copy.extract"
          @click="extractTarget = entry"
        >
          <IconFileZip :size="18" />
        </button>
        <button
          class="danger-link"
          type="button"
          :aria-label="copy.deleteItem.replace('{name}', entry.name)"
          :title="t('common.delete')"
          @click="deleteTarget = entry"
        >
          <IconTrash :size="18" />
        </button>
      </div>
    </div>
    <p v-if="!data?.length" class="empty">{{ copy.empty }}</p>
  </div>
  <section v-if="editorOpen" class="editor panel">
    <header>
      <strong>{{ copy.editor.replace("{path}", editorPath) }}</strong>
      <button type="button" @click="editorOpen = false">
        {{ t("common.close") }}
      </button>
    </header>
    <textarea v-model="editorContent" spellcheck="false" />
    <button class="button-primary" type="button" @click="saveEditor">
      {{ t("common.save") }}
    </button>
  </section>
  <ConfirmDialog
    :open="bulkMoveOpen"
    :title="copy.bulkMoveTitle"
    :message="
      copy.bulkMoveMessage.replace('{count}', String(selectedEntries.length))
    "
    :confirm-label="copy.move"
    require-input
    :input-label="copy.destinationFolder"
    @cancel="bulkMoveOpen = false"
    @confirm="moveSelected"
  />
  <ConfirmDialog
    :open="bulkDeleteOpen"
    :title="copy.bulkDeleteTitle"
    :message="
      copy.bulkDeleteMessage.replace('{count}', String(selectedEntries.length))
    "
    :confirm-label="t('common.delete')"
    danger
    @cancel="bulkDeleteOpen = false"
    @confirm="deleteSelected"
  />
  <ConfirmDialog
    :open="fileModalOpen"
    :title="copy.createFile"
    :message="copy.createFileMessage"
    :confirm-label="t('common.create')"
    require-input
    :input-label="copy.fileName"
    @cancel="fileModalOpen = false"
    @confirm="submitFile"
  />
  <ConfirmDialog
    :open="directoryModalOpen"
    :title="copy.createFolder"
    :message="copy.createFolderMessage"
    :confirm-label="t('common.create')"
    require-input
    :input-label="copy.folderName"
    @cancel="directoryModalOpen = false"
    @confirm="submitDirectory"
  />
  <ConfirmDialog
    :open="Boolean(moveTarget)"
    :title="copy.moveRename"
    :message="copy.moveMessage.replace('{path}', moveTarget?.path || '')"
    :confirm-label="copy.move"
    require-input
    :input-label="copy.newPath"
    @cancel="moveTarget = null"
    @confirm="moveEntry"
  />
  <ConfirmDialog
    :open="Boolean(archiveTarget)"
    :title="copy.createZip"
    :message="copy.archiveMessage.replace('{path}', archiveTarget?.path || '')"
    :confirm-label="copy.createZip"
    require-input
    :input-label="copy.archivePath"
    @cancel="archiveTarget = null"
    @confirm="archiveEntry"
  />
  <ConfirmDialog
    :open="Boolean(extractTarget)"
    :title="copy.extractTitle"
    :message="copy.extractMessage.replace('{path}', extractTarget?.path || '')"
    :confirm-label="copy.extract"
    require-input
    :input-label="copy.destinationFolder"
    :initial-value="extractDestination"
    @cancel="extractTarget = null"
    @confirm="extractEntry"
  />
  <ConfirmDialog
    :open="Boolean(deleteTarget)"
    :title="copy.deletePathTitle"
    :message="
      copy.deletePathMessage.replace('{path}', deleteTarget?.path || '')
    "
    :confirm-label="t('common.delete')"
    :expected-text="deleteTarget?.path || ''"
    danger
    @cancel="deleteTarget = null"
    @confirm="deleteEntry"
  />
  <ConfirmDialog
    :open="disableSftpOpen"
    :title="copy.disableSftpTitle"
    :message="copy.disableSftpMessage"
    :confirm-label="copy.disable"
    danger
    @cancel="disableSftpOpen = false"
    @confirm="disableSftp"
  />
</template>

<style scoped>
.actions {
  display: flex;
  gap: 0.6rem;
}
.hidden {
  display: none;
}
.notice {
  margin: 0 0 1rem;
  color: #72d6a3;
  font-size: 0.9rem;
}
.toolbar {
  display: grid;
  grid-template-columns: auto 1fr auto;
  align-items: center;
  gap: 0.8rem;
  border: 1px solid var(--border);
  border-radius: 10px;
  background: var(--surface-subtle);
  padding: 0.55rem;
}
.sftp {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
  margin-bottom: 1rem;
  padding: 1rem;
}
.sftp p {
  margin: 0.35rem 0 0;
  color: var(--text-muted);
  font-size: 0.82rem;
}
.sftp .secret {
  color: #edbb68;
}
.persistent-data {
  display: grid;
  gap: 0.8rem;
  margin-bottom: 1rem;
  padding: 1rem;
}
.persistent-data__heading {
  display: flex;
  align-items: flex-start;
  gap: 0.7rem;
}
.persistent-data__heading > svg {
  flex: 0 0 auto;
  margin-top: 0.05rem;
  color: var(--accent);
}
.persistent-data p {
  margin: 0.3rem 0 0;
  color: var(--text-muted);
  font-size: 0.82rem;
}
.persistent-data__mounts {
  display: flex;
  flex-wrap: wrap;
  gap: 0.55rem;
}
.persistent-data__mounts button {
  display: inline-flex;
  align-items: center;
  gap: 0.5rem;
  min-height: 38px;
  cursor: pointer;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--surface-raised);
  color: var(--text);
  padding: 0.45rem 0.65rem;
}
.persistent-data__mounts button:hover {
  border-color: color-mix(in srgb, var(--accent) 55%, var(--border));
}
.persistent-data__mounts span {
  color: var(--text-muted);
  font-size: 0.72rem;
}
.icon-button {
  display: grid;
  flex: 0 0 auto;
  width: 44px;
  height: 44px;
  cursor: pointer;
  place-items: center;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--surface-raised);
  color: var(--text);
}
.icon-button:disabled {
  cursor: not-allowed;
  opacity: 0.45;
}
.toolbar label {
  color: var(--text-muted);
  font-size: 0.82rem;
}
.breadcrumbs {
  display: flex;
  min-width: 0;
  align-items: center;
  overflow-x: auto;
  scrollbar-width: thin;
}
.breadcrumbs button {
  min-height: 36px;
  cursor: pointer;
  border: 0;
  border-radius: 7px;
  background: transparent;
  color: var(--text-muted);
  font: inherit;
  padding: 0.4rem 0.55rem;
  white-space: nowrap;
}
.breadcrumbs button:hover,
.breadcrumbs button[aria-current="page"] {
  background: var(--surface-raised);
  color: var(--text);
}
.selection-bar {
  display: flex;
  min-height: 60px;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
  margin-top: 0.8rem;
  border: 1px solid #694128;
  border-radius: 10px;
  background: rgb(255 112 61 / 8%);
  padding: 0.45rem 0.55rem 0.45rem 1rem;
}
.selection-bar > div {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}
.file-table {
  display: grid;
  margin-top: 0.8rem;
  overflow-x: auto;
  border: 1px solid var(--border);
  border-radius: 10px;
  background: var(--surface-subtle);
}
.file-head,
.file-row {
  display: grid;
  min-width: 850px;
  grid-template-columns: 44px minmax(220px, 1fr) 120px 145px minmax(220px, auto);
  align-items: center;
  column-gap: 0.65rem;
}
.file-head {
  min-height: 42px;
  border-bottom: 1px solid var(--border);
  color: var(--text-muted);
  font-size: 0.72rem;
  font-weight: 650;
  letter-spacing: 0.03em;
  text-transform: uppercase;
}
.actions-heading {
  padding-right: 0.7rem;
  text-align: right;
}
.file-row {
  min-height: 64px;
  border-bottom: 1px solid var(--border);
  transition: background-color 160ms ease;
}
.file-row:last-of-type {
  border-bottom: 0;
}
.file-row:hover {
  background: rgb(255 255 255 / 2%);
}
.file-row.selected {
  background: rgb(255 112 61 / 7%);
}
.select-cell {
  display: grid;
  min-height: 44px;
  cursor: pointer;
  place-items: center;
}
.select-cell input {
  width: 18px;
  height: 18px;
  accent-color: var(--accent);
}
.file-main {
  display: flex;
  min-width: 0;
  min-height: 44px;
  cursor: pointer;
  align-items: center;
  gap: 0.7rem;
  border: 0;
  background: transparent;
  color: var(--text);
  text-align: left;
}
.file-main span {
  display: grid;
  min-width: 0;
  gap: 0.2rem;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.file-main strong {
  overflow: hidden;
  font-size: 0.88rem;
  text-overflow: ellipsis;
}
.file-main code {
  color: var(--text-muted);
  font-size: 0.68rem;
}
.file-size,
.file-row time {
  color: var(--text-muted);
  font-size: 0.78rem;
  font-variant-numeric: tabular-nums;
}
.file-actions {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 0.3rem;
  padding-right: 0.65rem;
}
.file-actions button {
  display: grid;
  width: 44px;
  height: 44px;
  cursor: pointer;
  place-items: center;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--surface-raised);
  color: var(--text-muted);
  padding: 0;
}
.file-actions button:hover {
  border-color: #4a4d54;
  color: var(--text);
}
.file-actions .danger-link {
  border-color: rgb(255 91 91 / 40%);
  color: #ff8c8c;
}
.editor header button {
  cursor: pointer;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--surface-raised);
  color: var(--text-muted);
  padding: 0.45rem 0.7rem;
}
.empty {
  color: var(--text-muted);
}
.empty {
  padding: 3rem 1rem;
  text-align: center;
}
.editor {
  display: grid;
  gap: 0.8rem;
  margin-top: 1rem;
  padding: 1rem;
}
.editor header {
  display: flex;
  justify-content: space-between;
  gap: 1rem;
}
.editor textarea {
  min-height: 360px;
  resize: vertical;
  border: 1px solid var(--border);
  border-radius: 12px;
  background: #05070d;
  color: var(--text);
  font-family: "JetBrains Mono", "Fira Code", monospace;
  font-size: 0.88rem;
  line-height: 1.5;
  padding: 1rem;
}
@media (max-width: 980px) {
  .actions {
    flex-direction: column;
  }
  .file-head {
    display: none;
  }
  .file-row {
    min-width: 0;
    grid-template-columns: 44px minmax(0, 1fr) auto;
    padding: 0.45rem 0;
  }
  .file-row time {
    display: none;
  }
  .file-actions {
    grid-column: 2 / -1;
    justify-content: flex-end;
    padding: 0 0.65rem 0 0;
  }
}
@media (max-width: 620px) {
  .toolbar {
    grid-template-columns: auto 1fr;
  }
  .toolbar label {
    grid-column: 1 / -1;
    padding: 0 0.45rem 0.35rem;
  }
  .selection-bar {
    align-items: stretch;
    flex-direction: column;
    padding: 0.75rem;
  }
  .selection-bar > div {
    flex-wrap: wrap;
  }
  .selection-bar .button-secondary,
  .selection-bar .button-danger {
    flex: 1;
  }
}
</style>
