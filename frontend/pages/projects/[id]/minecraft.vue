<script setup lang="ts">
import { IconCheck, IconDownload, IconPlayerPlay } from "@tabler/icons-vue";
import type {
  Job,
  MinecraftBuildOption,
  MinecraftPluginEntry,
  MinecraftStatus,
  ProjectMetricValues,
  MetricSample,
  MinecraftVersionCatalog,
  MinecraftVersionOption,
} from "~/types/api";
import { minecraftManagementMessages } from "~/locales/minecraft-management";
import {
  minecraftConfigPaths,
  minecraftInstallPayload,
  minecraftMotd,
} from "~/utils/minecraft";
import { minecraftMessages } from "~/locales/minecraft";

const route = useRoute();
const projectId = String(route.params.id);
const api = useApi();
const { locale, t } = useLocale();
const copy = computed(
  () =>
    Object.fromEntries(
      Object.entries(minecraftMessages[locale.value]).map(([key, value]) => [
        key,
        isForge.value ? value : value.replaceAll("Forge", coreName.value),
      ]),
    ) as typeof minecraftMessages.ru,
);
const manageCopy = computed(() => minecraftManagementMessages[locale.value]);
const auth = useAuthStore();
const canWrite = computed(() => auth.canWriteProjects);
const builds = ref<MinecraftBuildOption[]>([]);
const buildsPending = ref(false);
const buildsError = ref(false);
const uploadedServerFile = ref<File | null>(null);
const playerForm = reactive({
  action: "whitelist_add" as
    | "whitelist_add"
    | "whitelist_remove"
    | "op"
    | "deop"
    | "ban"
    | "pardon"
    | "kick",
  player: "",
  reason: "",
});
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
  build_id: null as string | null,
  sha256: "",
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
      engine: string;
      status: string;
      active_job_id: string | null;
    }>(`/projects/${projectId}/minecraft`),
);
const core = computed(() => data.value?.engine ?? "forge");
const isForge = computed(() => core.value === "forge");
const coreName = computed(
  () => core.value.charAt(0).toUpperCase() + core.value.slice(1),
);
const configPaths = computed(() => minecraftConfigPaths(core.value));
const installed = computed(() => Boolean(form.sha256));
const serverRunning = computed(
  () => status.value?.running ?? data.value?.status === "running",
);
const { data: metric, refresh: refreshMetric } = await useAsyncData(
  `minecraft-status-${projectId}`,
  async () => {
    try {
      return await api.request<MetricSample>(
        `/monitoring/projects/${projectId}/latest`,
        { silent: true },
      );
    } catch {
      return null;
    }
  },
);
const status = computed<MinecraftStatus | undefined>(
  () => (metric.value?.values as ProjectMetricValues | undefined)?.minecraft,
);
const { data: plugins, refresh: refreshPlugins } = await useAsyncData(
  `minecraft-plugins-${projectId}`,
  () =>
    isForge.value
      ? Promise.resolve([])
      : api.request<MinecraftPluginEntry[]>(
          `/projects/${projectId}/minecraft/plugins`,
        ),
);
let metricsTimer: ReturnType<typeof setInterval> | null = null;
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
    !isForge.value
      ? Promise.resolve([])
      : api.request<{ name: string; path: string; size_bytes: number }[]>(
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
const forgeVersions = computed(() =>
  isForge.value
    ? (selectedVersion.value?.forge_versions ?? [])
    : builds.value.map((build) => build.build_id),
);
const installActive = computed(
  () =>
    Boolean(data.value?.active_job_id) ||
    (installJob.value !== null &&
      !["success", "failure", "revoked"].includes(installJob.value.status)),
);
const installationConfigured = computed(
  () =>
    isForge.value ||
    (data.value?.configuration.eula_accepted === true &&
      (!data.value.configuration.rcon_enabled || data.value.rcon_configured)),
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
  async () => {
    if (!isForge.value) {
      buildsPending.value = true;
      buildsError.value = false;
      builds.value = [];
      selectedForgeVersion.value = "";
      const version = selectedMinecraftVersion.value;
      if (!version) {
        buildsPending.value = false;
        return;
      }
      try {
        const result = await api.request<{ builds: MinecraftBuildOption[] }>(
          `/projects/${projectId}/minecraft/versions/${version}/builds`,
        );
        if (version !== selectedMinecraftVersion.value) return;
        builds.value = result.builds;
        selectedForgeVersion.value =
          form.minecraft_version === version &&
          result.builds.some((build) => build.build_id === form.build_id)
            ? String(form.build_id)
            : (result.builds[0]?.build_id ?? "");
      } catch {
        buildsError.value = true;
      } finally {
        buildsPending.value = false;
      }
      return;
    }
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

watch(selectedForgeVersion, (value) => {
  if (!isForge.value) {
    const build = builds.value.find((item) => item.build_id === value);
    if (build) form.java_version = build.java_version;
  }
});
onMounted(() => {
  metricsTimer = setInterval(() => {
    void refreshMetric();
  }, 15000);
  if (data.value?.active_job_id) {
    void api.request<Job>(`/jobs/${data.value.active_job_id}`).then((job) => {
      installJob.value = job;
      startInstallPolling();
    });
  }
});
onBeforeUnmount(() => {
  if (metricsTimer) clearInterval(metricsTimer);
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
    await refreshConfig();
    await refreshMetric();
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
    await refreshPlugins();
  } else if (["failure", "revoked"].includes(job.status)) {
    message.value = copy.value.installError.replace(
      "{error}",
      job.error ? `: ${job.error}` : "",
    );
    await refreshConfig();
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

/** Retry recovery while retaining the original operation reservation. */
async function retryRecovery(): Promise<void> {
  pending.value = true;
  try {
    installJob.value = await api.request<Job>(
      `/projects/${projectId}/minecraft/recovery`,
      { method: "POST" },
    );
    startInstallPolling();
  } finally {
    pending.value = false;
  }
}

/** Queue an official server install or a backed-up update. */
async function installForge(): Promise<void> {
  if (
    !installationConfigured.value ||
    !selectedMinecraftVersion.value ||
    !selectedForgeVersion.value
  )
    return;
  if (
    !isForge.value &&
    installed.value &&
    !window.confirm(manageCopy.value.updateConfirm)
  )
    return;
  pending.value = true;
  try {
    const result = await api.request<Job>(
      `/projects/${projectId}/minecraft/${!isForge.value && installed.value ? "update" : "install"}`,
      {
        method: "POST",
        body: minecraftInstallPayload(
          core.value,
          selectedMinecraftVersion.value,
          selectedForgeVersion.value,
        ),
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
/** Apply a shared runtime action and refresh server state. */
async function control(action: "stop" | "restart"): Promise<void> {
  pending.value = true;
  try {
    await api.request(`/projects/${projectId}/runtime/${action}`, {
      method: "POST",
    });
    await refreshConfig();
    await refreshMetric();
  } finally {
    pending.value = false;
  }
}

/** Upload a plugin and refresh its stopped-server activation state. */
async function uploadPlugin(event: Event): Promise<void> {
  const input = event.target as HTMLInputElement;
  const file = input.files?.[0];
  if (!file) return;
  pending.value = true;
  try {
    const body = new FormData();
    body.append("file", file);
    await api.request(`/projects/${projectId}/minecraft/plugins`, {
      method: "POST",
      body,
    });
    await refreshPlugins();
  } finally {
    pending.value = false;
    input.value = "";
  }
}

/** Change only a plugin JAR, retaining its persistent data directory. */
async function changePlugin(
  plugin: MinecraftPluginEntry,
  action: "enable" | "disable" | "delete",
): Promise<void> {
  if (action === "delete" && !window.confirm(manageCopy.value.deleteConfirm))
    return;
  pending.value = true;
  try {
    await api.request(
      `/projects/${projectId}/minecraft/plugins/${encodeURIComponent(plugin.name)}`,
      {
        method: action === "delete" ? "DELETE" : "POST",
        ...(action !== "delete" ? { body: { action } } : {}),
      },
    );
    await refreshPlugins();
  } finally {
    pending.value = false;
  }
}

/** Send a validated player administration command over private RCON. */
async function managePlayer(): Promise<void> {
  pending.value = true;
  try {
    const result = await api.request<{ message: string }>(
      `/projects/${projectId}/minecraft/players/actions`,
      { method: "POST", body: { ...playerForm } },
    );
    message.value = result.message;
  } finally {
    pending.value = false;
  }
}

/** Select a browser-uploaded server artifact. */
function selectServerJar(event: Event): void {
  uploadedServerFile.value =
    (event.target as HTMLInputElement).files?.[0] ?? null;
}

/** Import a server JAR through the existing bounded file upload service. */
async function importServer(): Promise<void> {
  if (
    !installationConfigured.value ||
    !uploadedServerFile.value ||
    !selectedMinecraftVersion.value
  )
    return;
  if (installed.value && !window.confirm(manageCopy.value.updateConfirm))
    return;
  pending.value = true;
  try {
    const name = `${core.value}-upload-${Date.now()}.jar`;
    const body = new FormData();
    body.append("file", uploadedServerFile.value);
    await api.request(
      `/projects/${projectId}/files/upload?path=${encodeURIComponent(name)}`,
      { method: "POST", body },
    );
    installJob.value = await api.request<Job>(
      `/projects/${projectId}/minecraft/${installed.value ? "update" : "install"}`,
      {
        method: "POST",
        body: minecraftInstallPayload(
          core.value,
          selectedMinecraftVersion.value,
          "",
          name,
        ),
      },
    );
    startInstallPolling();
  } finally {
    pending.value = false;
  }
}
</script>

<template>
  <PageHeader :title="`Minecraft ${coreName}`" :description="copy.description">
    <button
      class="button-primary"
      type="button"
      :disabled="
        pending ||
        installActive ||
        !canWrite ||
        !form.eula_accepted ||
        (!isForge && !installed)
      "
      @click="start"
    >
      <IconPlayerPlay :size="18" /> {{ copy.start }}
    </button>
    <button
      class="button-secondary"
      type="button"
      :disabled="pending || installActive || !canWrite"
      @click="control('stop')"
    >
      {{ manageCopy.stop }}
    </button>
    <button
      class="button-secondary"
      type="button"
      :disabled="pending || installActive || !canWrite"
      @click="control('restart')"
    >
      {{ manageCopy.restart }}
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
      }}<input
        v-model="form.server_jar"
        class="control"
        :readonly="!isForge"
        required
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
    <button
      class="button-primary"
      type="submit"
      :disabled="pending || installActive || !canWrite"
    >
      {{ copy.saveConfiguration }}
    </button>
  </form>
  <form class="installer panel" @submit.prevent="installForge">
    <p v-if="!installationConfigured" role="status">
      {{ manageCopy.configurationRequired }}
    </p>
    <div class="installer__heading">
      <strong>{{ copy.installTitle }}</strong>
      <p>{{ copy.installDescription }}</p>
      <span
        v-if="form.minecraft_version && (form.forge_version || form.build_id)"
        class="installed-version"
      >
        <IconCheck :size="16" :stroke-width="2" />
        {{
          copy.installedVersion
            .replace("{minecraft}", form.minecraft_version)
            .replace("{forge}", form.forge_version ?? form.build_id ?? "")
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
      >{{ isForge ? "Forge" : manageCopy.build }}
      <select
        v-model="selectedForgeVersion"
        class="control"
        required
        :disabled="!forgeVersions.length || buildsPending || installActive"
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
    <p
      v-if="
        !isForge &&
        builds.find((item) => item.build_id === selectedForgeVersion)
          ?.channel === 'unknown'
      "
      class="hint"
    >
      {{ manageCopy.unknownChannel }}
    </p>
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
          !installationConfigured ||
          !canWrite ||
          !selectedMinecraftVersion ||
          !selectedForgeVersion
        "
      >
        <IconDownload :size="18" :stroke-width="1.8" />
        {{
          installActive
            ? copy.installing
            : !isForge && installed
              ? manageCopy.update
              : copy.installForge
        }}
      </button>
    </div>
    <div v-if="installJob" class="install-progress" aria-live="polite">
      <div>
        <span>{{ copy.installProgress }}</span>
        <strong>{{ installJob.progress }}%</strong>
      </div>
      <progress :value="installJob.progress" max="100" />
    </div>
    <div v-if="versionsError || buildsError" class="catalog-error" role="alert">
      <span>{{ copy.catalogError }}</span>
      <button type="button" @click="() => refreshVersions()">
        {{ t("projects.retry") }}
      </button>
    </div>
    <div v-else-if="versionsPending" class="catalog-loading">
      {{ copy.catalogLoading }}
    </div>
  </form>
  <form v-if="!isForge" class="panel config" @submit.prevent="importServer">
    <h2>{{ manageCopy.importJar }}</h2>
    <p class="hint">{{ manageCopy.importHint }}</p>
    <input
      type="file"
      accept=".jar"
      :disabled="pending || installActive || !canWrite || serverRunning"
      @change="selectServerJar"
    />
    <button
      class="button-primary"
      type="submit"
      :disabled="
        pending ||
        installActive ||
        !canWrite ||
        !uploadedServerFile ||
        !installationConfigured ||
        !selectedMinecraftVersion
      "
    >
      {{ manageCopy.upload }}
    </button>
  </form>
  <section v-if="!isForge" class="panel config">
    <strong
      >{{ manageCopy.players }}: {{ status?.online_players ?? "—" }} /
      {{ status?.max_players ?? "—" }}</strong
    >
    <span>{{
      status?.available ? manageCopy.available : manageCopy.unavailable
    }}</span>
    <span>{{ status?.version }}</span>
    <span v-if="status?.motd">MOTD: {{ minecraftMotd(status.motd) }}</span>
    <span v-if="installed"
      >{{ manageCopy.installed }}: {{ form.minecraft_version }} /
      {{ form.build_id ?? "JAR" }}</span
    >
  </section>
  <p v-if="message" class="message" aria-live="polite">{{ message }}</p>
  <button
    v-if="data?.active_job_id && installJob?.status === 'failure'"
    type="button"
    :disabled="pending || !canWrite"
    @click="retryRecovery"
  >
    {{ manageCopy.recovery }}
  </button>
  <section class="tools">
    <div class="panel editors">
      <h2>{{ copy.serverConfigs }}</h2>
      <button
        v-for="file in configPaths"
        :key="file"
        type="button"
        @click="openEditor(file)"
      >
        {{ file }}
      </button>
    </div>
    <div v-if="isForge" class="panel mods">
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
  <section v-if="!isForge" class="panel mods management-panel">
    <div class="mods-header">
      <h2>{{ manageCopy.plugins }}</h2>
      <button type="button" @click="() => refreshPlugins()">
        {{ t("common.refresh") }}
      </button>
    </div>
    <p class="hint">{{ manageCopy.pluginHint }}</p>
    <input
      type="file"
      accept=".jar"
      :disabled="pending || installActive || !canWrite || serverRunning"
      @change="uploadPlugin"
    />
    <ul v-if="plugins?.length">
      <li v-for="plugin in plugins" :key="plugin.path">
        <span
          >{{ plugin.name }} ·
          {{ plugin.enabled ? manageCopy.enabled : manageCopy.disabled }}</span
        ><code>{{ bytes(plugin.size_bytes) }}</code
        ><button
          type="button"
          :disabled="pending || installActive || !canWrite || serverRunning"
          @click="changePlugin(plugin, plugin.enabled ? 'disable' : 'enable')"
        >
          {{ plugin.enabled ? manageCopy.disable : manageCopy.enable }}</button
        ><button
          type="button"
          :disabled="pending || installActive || !canWrite || serverRunning"
          @click="changePlugin(plugin, 'delete')"
        >
          {{ manageCopy.remove }}
        </button>
      </li>
    </ul>
    <p v-else>{{ manageCopy.emptyPlugins }}</p>
  </section>
  <form v-if="!isForge" class="panel config" @submit.prevent="managePlayer">
    <h2>{{ manageCopy.players }}</h2>
    <label
      >{{ manageCopy.player
      }}<input
        v-model="playerForm.player"
        class="control"
        required
        pattern="[A-Za-z0-9_]{1,16}"
        maxlength="16"
    /></label>
    <select v-model="playerForm.action" class="control">
      <option
        v-for="action in [
          'whitelist_add',
          'whitelist_remove',
          'op',
          'deop',
          'ban',
          'pardon',
          'kick',
        ] as const"
        :key="action"
        :value="action"
      >
        {{ manageCopy[action] }}
      </option>
    </select>
    <label
      >{{ manageCopy.reason
      }}<input v-model="playerForm.reason" class="control" maxlength="256"
    /></label>
    <button
      class="button-primary"
      type="submit"
      :disabled="
        pending ||
        installActive ||
        !canWrite ||
        !form.rcon_enabled ||
        !serverRunning
      "
    >
      {{ manageCopy.send }}
    </button>
  </form>
  <section v-if="editorOpen" class="editor panel">
    <header>
      <strong>{{ editorFile }}</strong>
      <button type="button" @click="editorOpen = false">
        {{ t("common.close") }}
      </button>
    </header>
    <textarea v-model="editorContent" spellcheck="false" />
    <button
      class="button-primary"
      type="button"
      :disabled="pending || installActive || !canWrite"
      @click="saveEditor"
    >
      {{ t("common.save") }}
    </button>
  </section>
  <p v-if="isForge" class="hint">
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
