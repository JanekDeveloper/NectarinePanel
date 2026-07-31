<script setup lang="ts">
import { IconCheck, IconDownload, IconPlayerPlay } from "@tabler/icons-vue";
import type {
  Job,
  MinecraftVersionCatalog,
  MinecraftVersionOption,
} from "~/types/api";
import { minecraftMessages } from "~/locales/minecraft";

const route = useRoute();
const projectId = String(route.params.id);
const api = useApi();
const { locale, t } = useLocale();
const copy = computed(() => minecraftMessages[locale.value]);
const message = ref("");
const pending = ref(false);
const editorFile = ref("");
const editorContent = ref("");
const editorOpen = ref(false);
const selectedMinecraftVersion = ref("");
const selectedForgeVersion = ref("");
const installJob = ref<Job | null>(null);
let installPollTimer: ReturnType<typeof setInterval> | null = null;
const form = reactive({
  java_version: 21,
  minecraft_version: null as string | null,
  forge_version: null as string | null,
  xms: "1G",
  xmx: "4G",
  server_jar: "forge-server.jar",
  launch_mode: "jar",
  game_port: 25565,
  eula_accepted: false,
  rcon_enabled: true,
  rcon_port: 25575,
  rcon_host_port: 25575,
  rcon_password: "",
});
const { data, refresh: refreshConfig } = await useAsyncData(
  `minecraft-${projectId}`,
  () =>
    api.request<{
      configuration: Record<string, unknown>;
      rcon_configured: boolean;
    }>(`/projects/${projectId}/minecraft`),
);
const {
  data: versionCatalog,
  pending: versionsPending,
  error: versionsError,
  refresh: refreshVersions,
} = await useAsyncData(`minecraft-versions-${projectId}`, () =>
  api.request<MinecraftVersionCatalog>(
    `/projects/${projectId}/minecraft/versions`,
  ),
);
const { data: mods, refresh: refreshMods } = await useAsyncData(
  `minecraft-mods-${projectId}`,
  () =>
    api.request<{ name: string; path: string; size_bytes: number }[]>(
      `/projects/${projectId}/minecraft/mods`,
    ),
);
watchEffect(() => {
  if (data.value?.configuration) Object.assign(form, data.value.configuration);
});
const minecraftVersions = computed(() => versionCatalog.value?.versions ?? []);
const selectedVersion = computed<MinecraftVersionOption | undefined>(() =>
  minecraftVersions.value.find(
    (item) => item.minecraft_version === selectedMinecraftVersion.value,
  ),
);
const forgeVersions = computed(
  () => selectedVersion.value?.forge_versions ?? [],
);
const installActive = computed(
  () =>
    installJob.value !== null &&
    !["success", "failure", "revoked"].includes(installJob.value.status),
);

watchEffect(() => {
  const firstVersion = minecraftVersions.value.at(0);
  if (!firstVersion || selectedMinecraftVersion.value) return;
  const installed = form.minecraft_version;
  selectedMinecraftVersion.value = minecraftVersions.value.some(
    (item) => item.minecraft_version === installed,
  )
    ? String(installed)
    : firstVersion.minecraft_version;
});

watch(
  selectedMinecraftVersion,
  () => {
    const option = selectedVersion.value;
    if (!option) {
      selectedForgeVersion.value = "";
      return;
    }
    if (!option.forge_versions.includes(selectedForgeVersion.value)) {
      const firstForgeVersion = option.forge_versions.at(0);
      if (!firstForgeVersion) return;
      const installedForge =
        form.minecraft_version === option.minecraft_version
          ? form.forge_version
          : null;
      selectedForgeVersion.value =
        installedForge && option.forge_versions.includes(installedForge)
          ? installedForge
          : firstForgeVersion;
    }
    form.java_version = option.java_version;
  },
  { immediate: true },
);

onBeforeUnmount(() => {
  if (installPollTimer) clearInterval(installPollTimer);
});

async function save(): Promise<void> {
  pending.value = true;
  try {
    const result = await api.request<{ one_time_rcon_password: string | null }>(
      `/projects/${projectId}/minecraft`,
      {
        method: "PUT",
        body: { ...form, rcon_password: form.rcon_password || null },
      },
    );
    message.value = result.one_time_rcon_password
      ? copy.value.savedPassword.replace(
          "{password}",
          result.one_time_rcon_password,
        )
      : copy.value.saved;
    form.rcon_password = "";
  } finally {
    pending.value = false;
  }
}

async function start(): Promise<void> {
  pending.value = true;
  try {
    await api.request(`/projects/${projectId}/minecraft/start`, {
      method: "POST",
    });
    message.value = copy.value.started;
  } finally {
    pending.value = false;
  }
}

async function refreshInstallJob(): Promise<void> {
  if (!installJob.value) return;
  const job = await api.request<Job>(`/jobs/${installJob.value.id}`, {
    silent: true,
  });
  installJob.value = job;
  if (job.status === "success") {
    message.value = copy.value.installed;
    await refreshConfig();
  } else if (["failure", "revoked"].includes(job.status)) {
    message.value = copy.value.installError.replace(
      "{error}",
      job.error ? `: ${job.error}` : "",
    );
  } else {
    return;
  }
  if (installPollTimer) clearInterval(installPollTimer);
  installPollTimer = null;
}

function startInstallPolling(): void {
  if (installPollTimer) clearInterval(installPollTimer);
  installPollTimer = setInterval(() => {
    void refreshInstallJob();
  }, 1500);
}

async function installForge(): Promise<void> {
  if (!selectedMinecraftVersion.value || !selectedForgeVersion.value) return;
  pending.value = true;
  try {
    const result = await api.request<Job>(
      `/projects/${projectId}/minecraft/install`,
      {
        method: "POST",
        body: {
          minecraft_version: selectedMinecraftVersion.value,
          forge_version: selectedForgeVersion.value,
        },
      },
    );
    installJob.value = result;
    message.value = copy.value.installStarted;
    startInstallPolling();
  } finally {
    pending.value = false;
  }
}

async function openEditor(filename: string): Promise<void> {
  const result = await api.request<{ path: string; content: string }>(
    `/projects/${projectId}/minecraft/files/${filename}`,
  );
  editorFile.value = result.path;
  editorContent.value = result.content;
  editorOpen.value = true;
}

async function saveEditor(): Promise<void> {
  await api.request(
    `/projects/${projectId}/minecraft/files/${editorFile.value}`,
    {
      method: "PUT",
      body: { content: editorContent.value },
    },
  );
  message.value = copy.value.fileSaved.replace("{file}", editorFile.value);
}

function bytes(value: number): string {
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / 1024 / 1024).toFixed(1)} MB`;
}
</script>

<template>
  <PageHeader title="Minecraft Forge" :description="copy.description">
    <button
      class="button-primary"
      type="button"
      :disabled="pending || !form.eula_accepted"
      @click="start"
    >
      <IconPlayerPlay :size="18" /> {{ copy.start }}
    </button>
  </PageHeader>
  <ProjectNav :project-id="projectId" />
  <form class="config panel" @submit.prevent="save">
    <label
      >Java<select v-model.number="form.java_version" class="control">
        <option :value="8">Java 8</option>
        <option :value="11">Java 11</option>
        <option :value="16">Java 16</option>
        <option :value="17">Java 17</option>
        <option :value="21">Java 21</option>
        <option :value="25">Java 25</option>
      </select></label
    >
    <label
      >Xms<input
        v-model="form.xms"
        class="control"
        pattern="[1-9][0-9]*[MGmg]"
        required
    /></label>
    <label
      >Xmx<input
        v-model="form.xmx"
        class="control"
        pattern="[1-9][0-9]*[MGmg]"
        required
    /></label>
    <label
      >{{ copy.serverJar
      }}<input v-model="form.server_jar" class="control" required
    /></label>
    <label
      >{{ copy.gamePort
      }}<input
        v-model.number="form.game_port"
        class="control"
        type="number"
        min="1024"
        max="65535"
    /></label>
    <label
      >{{ copy.rconPort
      }}<input
        v-model.number="form.rcon_host_port"
        class="control"
        type="number"
        min="1024"
        max="65535"
    /></label>
    <label
      >{{ copy.newRconPassword
      }}<input
        v-model="form.rcon_password"
        class="control"
        type="password"
        minlength="16"
        autocomplete="new-password"
        :placeholder="copy.generateAutomatically"
    /></label>
    <label class="checkbox"
      ><input v-model="form.rcon_enabled" type="checkbox" />
      {{ copy.enable }} RCON</label
    >
    <label class="checkbox critical"
      ><input v-model="form.eula_accepted" type="checkbox" required />
      {{ copy.acceptEula }}</label
    >
    <button class="button-primary" type="submit" :disabled="pending">
      {{ copy.saveConfiguration }}
    </button>
  </form>
  <form class="installer panel" @submit.prevent="installForge">
    <div class="installer__heading">
      <strong>{{ copy.installTitle }}</strong>
      <p>{{ copy.installDescription }}</p>
      <span
        v-if="form.minecraft_version && form.forge_version"
        class="installed-version"
      >
        <IconCheck :size="16" :stroke-width="2" />
        {{
          copy.installedVersion
            .replace("{minecraft}", form.minecraft_version)
            .replace("{forge}", form.forge_version)
        }}
      </span>
    </div>
    <label
      >Minecraft
      <select
        v-model="selectedMinecraftVersion"
        class="control"
        required
        :disabled="versionsPending || installActive"
      >
        <option
          v-for="version in minecraftVersions"
          :key="version.minecraft_version"
          :value="version.minecraft_version"
        >
          {{ version.minecraft_version }}
        </option>
      </select>
    </label>
    <label
      >Forge
      <select
        v-model="selectedForgeVersion"
        class="control"
        required
        :disabled="!forgeVersions.length || installActive"
      >
        <option
          v-for="version in forgeVersions"
          :key="version"
          :value="version"
        >
          {{ version }}
        </option>
      </select>
    </label>
    <div class="installer__action">
      <span v-if="selectedVersion">
        {{
          copy.javaAutomatic.replace(
            "{version}",
            String(selectedVersion.java_version),
          )
        }}
      </span>
      <button
        class="button-primary"
        type="submit"
        :disabled="
          pending ||
          installActive ||
          !selectedMinecraftVersion ||
          !selectedForgeVersion
        "
      >
        <IconDownload :size="18" :stroke-width="1.8" />
        {{ installActive ? copy.installing : copy.installForge }}
      </button>
    </div>
    <div v-if="installJob" class="install-progress" aria-live="polite">
      <div>
        <span>{{ copy.installProgress }}</span>
        <strong>{{ installJob.progress }}%</strong>
      </div>
      <progress :value="installJob.progress" max="100" />
    </div>
    <div v-if="versionsError" class="catalog-error" role="alert">
      <span>{{ copy.catalogError }}</span>
      <button type="button" @click="() => refreshVersions()">
        {{ t("projects.retry") }}
      </button>
    </div>
    <div v-else-if="versionsPending" class="catalog-loading">
      {{ copy.catalogLoading }}
    </div>
  </form>
  <p v-if="message" class="message" aria-live="polite">{{ message }}</p>
  <section class="tools">
    <div class="panel editors">
      <h2>{{ copy.serverConfigs }}</h2>
      <button type="button" @click="openEditor('server.properties')">
        server.properties
      </button>
      <button type="button" @click="openEditor('whitelist.json')">
        whitelist.json
      </button>
      <button type="button" @click="openEditor('ops.json')">ops.json</button>
    </div>
    <div class="panel mods">
      <div class="mods-header">
        <h2>{{ copy.mods }}</h2>
        <button type="button" @click="() => refreshMods()">
          {{ t("common.refresh") }}
        </button>
      </div>
      <ul v-if="mods?.length">
        <li v-for="mod in mods" :key="mod.path">
          <span>{{ mod.name }}</span>
          <code>{{ bytes(mod.size_bytes) }}</code>
        </li>
      </ul>
      <p v-else>
        {{ copy.noMods }}
      </p>
    </div>
  </section>
  <section v-if="editorOpen" class="editor panel">
    <header>
      <strong>{{ editorFile }}</strong>
      <button type="button" @click="editorOpen = false">
        {{ t("common.close") }}
      </button>
    </header>
    <textarea v-model="editorContent" spellcheck="false" />
    <button class="button-primary" type="button" @click="saveEditor">
      {{ t("common.save") }}
    </button>
  </section>
  <p class="hint">
    {{
      copy.modsHint.replace("{path}", `/srv/vps-panel/minecraft/${projectId}`)
    }}
  </p>
</template>

<style scoped>
.config {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
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
.checkbox {
  display: flex;
  min-height: 44px;
  align-items: center;
}
.critical {
  color: #edbb68;
}
.message {
  overflow-wrap: anywhere;
  color: #70d49d;
}
.tools {
  display: grid;
  grid-template-columns: minmax(0, 0.9fr) minmax(0, 1.1fr);
  gap: 1rem;
  margin-top: 1rem;
}
.installer {
  display: grid;
  grid-template-columns: minmax(240px, 1.2fr) repeat(2, minmax(180px, 0.7fr));
  align-items: end;
  gap: 1rem;
  margin-top: 1rem;
  padding: 1rem;
}
.installer p {
  margin: 0.35rem 0 0;
  color: var(--text-muted);
  font-size: 0.82rem;
}
.installer__heading {
  align-self: stretch;
}
.installed-version {
  display: flex;
  align-items: center;
  gap: 0.35rem;
  margin-top: 0.75rem;
  color: #70d49d;
  font-size: 0.78rem;
}
.installer__action {
  grid-column: 1 / -1;
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 1rem;
}
.installer__action > span {
  margin-right: auto;
  color: var(--text-muted);
  font-size: 0.78rem;
}
.install-progress,
.catalog-error,
.catalog-loading {
  grid-column: 1 / -1;
}
.install-progress {
  display: grid;
  gap: 0.45rem;
}
.install-progress div,
.catalog-error {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
  font-size: 0.82rem;
}
.install-progress progress {
  width: 100%;
  height: 8px;
  accent-color: var(--accent);
}
.catalog-error {
  color: #ef9b95;
}
.catalog-error button {
  border: 0;
  background: transparent;
  color: currentColor;
  text-decoration: underline;
}
.catalog-loading {
  color: var(--text-muted);
  font-size: 0.82rem;
}
.editors,
.mods,
.editor {
  padding: 1rem;
}
.editors {
  display: flex;
  align-items: flex-start;
  flex-direction: column;
  gap: 0.6rem;
}
.editors h2,
.mods h2 {
  margin: 0 0 0.4rem;
  font-size: 1rem;
}
.editors button,
.mods button,
.editor header button {
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--surface-raised);
  color: var(--text);
  padding: 0.5rem 0.7rem;
}
.mods-header,
.editor header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
}
.mods ul {
  display: grid;
  gap: 0.45rem;
  margin: 0;
  padding: 0;
  list-style: none;
}
.mods li {
  display: flex;
  justify-content: space-between;
  gap: 1rem;
  border-bottom: 1px solid var(--border);
  padding: 0.45rem 0;
}
.mods p,
.hint {
  color: var(--text-muted);
  line-height: 1.6;
}
.editor {
  display: grid;
  gap: 0.8rem;
  margin-top: 1rem;
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
@media (max-width: 800px) {
  .config,
  .installer,
  .tools {
    grid-template-columns: 1fr;
  }
}
</style>
