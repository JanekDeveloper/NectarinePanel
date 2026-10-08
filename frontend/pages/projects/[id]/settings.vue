<script setup lang="ts">
import { isMinecraftRuntime } from "~/utils/minecraft";
import {
  IconAlertTriangle,
  IconCheck,
  IconCpu,
  IconGitBranch,
  IconKey,
  IconRefresh,
  IconServer,
  IconSettings,
  IconTrash,
  IconUserPlus,
  IconUsers,
} from "@tabler/icons-vue";
import { onBeforeRouteLeave } from "vue-router";

import {
  buildProjectSettingsPayload,
  normalizeProjectSettingsSection,
  projectSettingsDraft,
  projectSettingsFingerprint,
  type ProjectSettingsDraft,
  type ProjectSettingsSection,
} from "~/utils/project-settings";
import type {
  Project,
  ProjectGitCredential,
  ProjectMembership,
  ProjectResourcePolicy,
  User,
} from "~/types/api";
import { projectSettingsMessages } from "~/locales/project-settings";

const route = useRoute();
const router = useRouter();
const projectId = String(route.params.id);
const api = useApi();
const auth = useAuthStore();
const localeController = useLocale();
const { t } = localeController;
const copy = computed(
  () => projectSettingsMessages[localeController.locale.value],
);

const projectPending = ref(false);
const projectMessage = ref("");
const projectSaveError = ref("");
const savedProjectFingerprint = ref("");
const runtimeAdvancedOpen = ref(false);
const settingsContent = ref<HTMLElement | null>(null);

const credentialMessage = ref("");
const credentialError = ref("");
const credentialPending = ref(false);
const deleteCredentialOpen = ref(false);

const policyMessage = ref("");
const policyError = ref("");
const policyPending = ref(false);
const savedPolicyFingerprint = ref("");

const memberMessage = ref("");
const memberError = ref("");
const memberPending = ref(false);
const memberToRemove = ref<ProjectMembership | null>(null);

const form = reactive<ProjectSettingsDraft>({
  name: "",
  description: "",
  project_type: "web",
  runtime_type: "systemd",
  source_type: "files",
  repository_url: "",
  branch: "main",
  install_command: "",
  build_command: "",
  start_command: "",
  output_directory: "",
  healthcheck_url: "",
  upstream_port: null,
  runtime_config_text: "{}",
});
const policyForm = reactive({
  enabled: false,
  cpu_cores: null as number | null,
  memory_mb: null as number | null,
  disk_mb: null as number | null,
});
const credentialForm = reactive({
  credential_type: "token" as "token" | "deploy_key",
  username: "x-access-token",
  value: "",
});
const memberForm = reactive({
  userId: "",
  role: "viewer" as "maintainer" | "viewer",
});

const {
  data: project,
  pending: pagePending,
  error: pageError,
  refresh,
} = await useAsyncData(`project-settings-${projectId}`, () =>
  api.request<Project>(`/projects/${projectId}`),
);
const {
  data: users,
  error: usersLoadError,
  refresh: refreshUsers,
} = await useAsyncData(`project-settings-users-${projectId}`, () =>
  api.request<User[]>("/users", { silent: true }),
);
const {
  data: members,
  error: membersLoadError,
  refresh: refreshMembers,
} = await useAsyncData(`project-members-${projectId}`, () =>
  api.request<ProjectMembership[]>(`/projects/${projectId}/members`, {
    silent: true,
  }),
);
const {
  data: resourcePolicy,
  error: resourcePolicyLoadError,
  refresh: refreshResourcePolicy,
} = await useAsyncData(`project-resource-policy-${projectId}`, () =>
  api.request<ProjectResourcePolicy>(`/projects/${projectId}/resource-policy`),
);
const {
  data: gitCredential,
  error: credentialLoadError,
  refresh: refreshGitCredential,
} = await useAsyncData(`project-git-credential-${projectId}`, () =>
  api.request<ProjectGitCredential>(
    `/projects/${projectId}/source/credential`,
    { silent: true },
  ),
);

const canEditProject = computed(() => auth.canWriteProjects);
const canManageMembers = computed(
  () => auth.role === "owner" || auth.role === "admin",
);
const canManageGitCredential = computed(() => auth.canWriteProjects);
const canManageResourcePolicy = computed(
  () => auth.role === "owner" || auth.role === "admin",
);

const settingsSections = computed(() => [
  {
    id: "general" as const,
    label: copy.value.sectionGeneral,
    description: copy.value.sectionGeneralDescription,
    icon: IconSettings,
  },
  {
    id: "source" as const,
    label: copy.value.sectionSource,
    description: copy.value.sectionSourceDescription,
    icon: IconGitBranch,
  },
  {
    id: "runtime" as const,
    label: "Runtime",
    description: copy.value.sectionRuntimeDescription,
    icon: IconServer,
  },
  {
    id: "resources" as const,
    label: copy.value.sectionResources,
    description: copy.value.sectionResourcesDescription,
    icon: IconCpu,
  },
  {
    id: "access" as const,
    label: copy.value.sectionAccess,
    description: copy.value.sectionAccessDescription,
    icon: IconUsers,
  },
]);

const visibleSections = computed(() =>
  settingsSections.value.filter(
    (section) => section.id !== "access" || canManageMembers.value,
  ),
);
const activeSection = computed(() =>
  normalizeProjectSettingsSection(route.query.section, canManageMembers.value),
);
const projectFieldsSection = computed(() =>
  ["general", "source", "runtime"].includes(activeSection.value),
);
const projectDirty = computed(
  () =>
    Boolean(project.value) &&
    projectSettingsFingerprint(form) !== savedProjectFingerprint.value,
);
const normalizedPolicy = computed(() => ({
  enabled: policyForm.enabled,
  cpu_cores: finiteNumberOrNull(policyForm.cpu_cores),
  memory_mb: finiteNumberOrNull(policyForm.memory_mb),
  disk_mb: finiteNumberOrNull(policyForm.disk_mb),
}));
const policyFingerprint = computed(() =>
  JSON.stringify(normalizedPolicy.value),
);
const policyDirty = computed(
  () =>
    Boolean(resourcePolicy.value) &&
    policyFingerprint.value !== savedPolicyFingerprint.value,
);
const savedGitSource = computed(() => Boolean(project.value?.repository_url));
const sourceIdentityDirty = computed(() => {
  if (!project.value) return false;
  const savedSource = project.value.repository_url ? "git" : "files";
  return (
    form.source_type !== savedSource ||
    form.repository_url.trim() !== (project.value.repository_url ?? "")
  );
});
const gitCredentialReady = computed(
  () =>
    !credentialLoadError.value &&
    form.source_type === "git" &&
    savedGitSource.value &&
    !sourceIdentityDirty.value,
);
const composeMonitorOnly = computed(
  () => form.runtime_type === "docker_compose",
);
const dockerRuntime = computed(() => form.runtime_type === "docker");
const minecraftRuntime = computed(() => isMinecraftRuntime(form.runtime_type));
const staticRuntime = computed(() => form.runtime_type === "static");
const commandFieldsVisible = computed(
  () => !composeMonitorOnly.value && !minecraftRuntime.value,
);
const startCommandVisible = computed(
  () => !staticRuntime.value && commandFieldsVisible.value,
);
const assignableUsers = computed(() => {
  const memberIds = new Set(
    (members.value ?? []).map((member) => member.user_id),
  );
  return (users.value ?? []).filter(
    (user) =>
      user.is_active &&
      ["maintainer", "viewer"].includes(user.role) &&
      !memberIds.has(user.id),
  );
});
const accessLoadError = computed(() =>
  Boolean(usersLoadError.value || membersLoadError.value),
);

watch(
  project,
  (value) => {
    if (!value) return;
    const draft = projectSettingsDraft(value);
    Object.assign(form, draft);
    savedProjectFingerprint.value = projectSettingsFingerprint(draft);
  },
  { immediate: true },
);

watch(
  resourcePolicy,
  (value) => {
    if (!value) return;
    policyForm.enabled = value.enabled;
    policyForm.cpu_cores = value.cpu_cores;
    policyForm.memory_mb = value.memory_mb;
    policyForm.disk_mb = value.disk_mb;
    savedPolicyFingerprint.value = JSON.stringify({
      enabled: value.enabled,
      cpu_cores: value.cpu_cores,
      memory_mb: value.memory_mb,
      disk_mb: value.disk_mb,
    });
  },
  { immediate: true },
);

watch(
  gitCredential,
  (value) => {
    if (!value?.configured) return;
    if (value.credential_type === "deploy_key") {
      credentialForm.credential_type = "deploy_key";
      credentialForm.username = "";
    } else {
      credentialForm.credential_type = "token";
      credentialForm.username = value.username || "x-access-token";
    }
  },
  { immediate: true },
);

watch(
  [() => route.query.section, canManageMembers],
  ([value]) => {
    const normalized = normalizeProjectSettingsSection(
      value,
      canManageMembers.value,
    );
    if (value === "access" && normalized !== "access") {
      void selectSection("general");
    }
  },
  { immediate: true },
);

onBeforeRouteLeave(() => {
  if (!projectDirty.value || !canEditProject.value || !import.meta.client) {
    return true;
  }
  return window.confirm(copy.value.leaveConfirm);
});

/**
 * Changes the local settings section and preserves unrelated query values.
 */
async function selectSection(section: ProjectSettingsSection): Promise<void> {
  const query = { ...route.query };
  if (section === "general") delete query.section;
  else query.section = section;
  await router.replace({ query });
  await nextTick();
  settingsContent.value?.focus({ preventScroll: true });
}

/**
 * Restores project fields from the last persisted response.
 */
function resetProjectForm(): void {
  if (!project.value) return;
  const draft = projectSettingsDraft(project.value);
  Object.assign(form, draft);
  projectMessage.value = "";
  projectSaveError.value = "";
}

/**
 * Validates and saves all project fields through the existing PATCH API.
 */
async function saveProject(): Promise<void> {
  if (!canEditProject.value || !projectDirty.value) return;
  projectPending.value = true;
  projectMessage.value = "";
  projectSaveError.value = "";
  try {
    if (form.name.trim().length < 2) {
      await selectSection("general");
      throw new Error("PROJECT_NAME");
    }
    if (form.source_type === "git" && !form.repository_url.trim()) {
      await selectSection("source");
      throw new Error("REPOSITORY_URL");
    }
    const payload = buildProjectSettingsPayload(form);
    await api.request<Project>(`/projects/${projectId}`, {
      method: "PATCH",
      body: payload,
    });
    await refresh();
    projectMessage.value = copy.value.saved;
  } catch (error) {
    const code = error instanceof Error ? error.message : "";
    if (code === "PROJECT_NAME") {
      projectSaveError.value = copy.value.nameMin;
    } else if (code === "REPOSITORY_URL") {
      projectSaveError.value = copy.value.repositoryRequired;
    } else if (error instanceof SyntaxError) {
      runtimeAdvancedOpen.value = true;
      await selectSection("runtime");
      projectSaveError.value = copy.value.invalidJson;
    } else if (error instanceof RangeError) {
      await selectSection("runtime");
      projectSaveError.value = copy.value.invalidPort;
    } else {
      projectSaveError.value = copy.value.saveError;
    }
  } finally {
    projectPending.value = false;
  }
}

/**
 * Persists the resource policy after local validation.
 */
async function saveResourcePolicy(): Promise<void> {
  if (!canManageResourcePolicy.value || !policyDirty.value) return;
  policyMessage.value = "";
  policyError.value = "";
  if (
    policyForm.enabled &&
    normalizedPolicy.value.cpu_cores === null &&
    normalizedPolicy.value.memory_mb === null &&
    normalizedPolicy.value.disk_mb === null
  ) {
    policyError.value = copy.value.policyRequired;
    return;
  }
  policyPending.value = true;
  try {
    await api.request<ProjectResourcePolicy>(
      `/projects/${projectId}/resource-policy`,
      {
        method: "PUT",
        body: {
          ...normalizedPolicy.value,
        },
      },
    );
    await refreshResourcePolicy();
    policyMessage.value = copy.value.policySaved;
  } catch {
    policyError.value = copy.value.policySaveError;
  } finally {
    policyPending.value = false;
  }
}

/**
 * Restores resource policy fields from the last persisted response.
 */
function resetResourcePolicy(): void {
  if (!resourcePolicy.value) return;
  policyForm.enabled = resourcePolicy.value.enabled;
  policyForm.cpu_cores = resourcePolicy.value.cpu_cores;
  policyForm.memory_mb = resourcePolicy.value.memory_mb;
  policyForm.disk_mb = resourcePolicy.value.disk_mb;
  policyMessage.value = "";
  policyError.value = "";
}

/**
 * Converts cleared numeric controls to API-compatible null values.
 */
function finiteNumberOrNull(value: number | null): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

/**
 * Saves or replaces the encrypted Git credential.
 */
async function saveGitCredential(): Promise<void> {
  if (!canManageGitCredential.value || !gitCredentialReady.value) return;
  credentialPending.value = true;
  credentialMessage.value = "";
  credentialError.value = "";
  try {
    await api.request<ProjectGitCredential>(
      `/projects/${projectId}/source/credential`,
      {
        method: "PUT",
        body: {
          credential_type: credentialForm.credential_type,
          username:
            credentialForm.credential_type === "token"
              ? credentialForm.username || "x-access-token"
              : null,
          value: credentialForm.value,
        },
      },
    );
    credentialForm.value = "";
    await refreshGitCredential();
    credentialMessage.value = copy.value.credentialSaved;
  } catch {
    credentialError.value = copy.value.credentialSaveError;
  } finally {
    credentialPending.value = false;
  }
}

/**
 * Deletes the configured Git credential after confirmation.
 */
async function deleteGitCredential(): Promise<void> {
  if (!canManageGitCredential.value) return;
  deleteCredentialOpen.value = false;
  credentialPending.value = true;
  credentialMessage.value = "";
  credentialError.value = "";
  try {
    await api.request<ProjectGitCredential>(
      `/projects/${projectId}/source/credential`,
      { method: "DELETE" },
    );
    credentialForm.value = "";
    credentialForm.username = "x-access-token";
    credentialForm.credential_type = "token";
    await refreshGitCredential();
    credentialMessage.value = copy.value.credentialDeleted;
  } catch {
    credentialError.value = copy.value.credentialDeleteError;
  } finally {
    credentialPending.value = false;
  }
}

/**
 * Adds a project-level maintainer or viewer membership.
 */
async function addMember(): Promise<void> {
  if (!memberForm.userId || !canManageMembers.value) return;
  memberPending.value = true;
  memberMessage.value = "";
  memberError.value = "";
  try {
    await api.request(`/projects/${projectId}/members/${memberForm.userId}`, {
      method: "PUT",
      body: { role: memberForm.role },
    });
    memberForm.userId = "";
    memberForm.role = "viewer";
    await Promise.all([refreshMembers(), refreshUsers()]);
    memberMessage.value = copy.value.memberAdded;
  } catch {
    memberError.value = copy.value.memberAddError;
  } finally {
    memberPending.value = false;
  }
}

/**
 * Refreshes the user directory and project membership list together.
 */
async function refreshAccess(): Promise<void> {
  await Promise.all([refreshMembers(), refreshUsers()]);
}

/**
 * Updates an existing project membership role.
 */
async function updateMember(
  member: ProjectMembership,
  role: "maintainer" | "viewer",
): Promise<void> {
  memberPending.value = true;
  memberMessage.value = "";
  memberError.value = "";
  try {
    await api.request(`/projects/${projectId}/members/${member.user_id}`, {
      method: "PUT",
      body: { role },
    });
    await refreshMembers();
    memberMessage.value = copy.value.memberUpdated;
  } catch {
    memberError.value = copy.value.memberUpdateError;
  } finally {
    memberPending.value = false;
  }
}

/**
 * Deletes the membership selected in the confirmation dialog.
 */
async function removeMember(): Promise<void> {
  if (!memberToRemove.value) return;
  memberPending.value = true;
  memberMessage.value = "";
  memberError.value = "";
  try {
    await api.request(
      `/projects/${projectId}/members/${memberToRemove.value.user_id}`,
      { method: "DELETE" },
    );
    memberToRemove.value = null;
    await Promise.all([refreshMembers(), refreshUsers()]);
    memberMessage.value = copy.value.memberDeleted;
  } catch {
    memberError.value = copy.value.memberDeleteError;
  } finally {
    memberPending.value = false;
  }
}
</script>

<template>
  <PageHeader :title="copy.title" :description="copy.description">
    <AppStatusBadge v-if="project" :status="project.status" />
  </PageHeader>
  <ProjectNav :project-id="projectId" />

  <section v-if="pagePending" class="page-state panel" aria-live="polite">
    <IconRefresh class="state-icon state-icon--loading" :size="24" />
    <div>
      <h2>{{ copy.loading }}</h2>
      <p>{{ copy.loadingDescription }}</p>
    </div>
  </section>

  <section
    v-else-if="pageError || !project"
    class="page-state panel"
    role="alert"
  >
    <IconAlertTriangle class="state-icon state-icon--error" :size="24" />
    <div>
      <h2>{{ copy.unavailable }}</h2>
      <p>{{ copy.unavailableDescription }}</p>
    </div>
    <button class="button-secondary" type="button" @click="refresh()">
      <IconRefresh :size="18" />
      {{ t("projects.retry") }}
    </button>
  </section>

  <div v-else class="settings-layout">
    <aside class="settings-sidebar panel">
      <div class="project-context">
        <span class="project-context__label">{{ copy.project }}</span>
        <strong>{{ project.name }}</strong>
        <code>{{ project.slug }}</code>
        <div class="project-context__meta">
          <span>{{ form.runtime_type }}</span>
          <span>{{ form.source_type === "git" ? "Git" : copy.files }}</span>
        </div>
      </div>
      <nav class="settings-nav" :aria-label="copy.sections">
        <button
          v-for="section in visibleSections"
          :key="section.id"
          type="button"
          :class="{ active: activeSection === section.id }"
          :aria-current="activeSection === section.id ? 'page' : undefined"
          @click="selectSection(section.id)"
        >
          <component :is="section.icon" :size="20" :stroke-width="1.7" />
          <span>
            <strong>{{ section.label }}</strong>
            <small>{{ section.description }}</small>
          </span>
        </button>
      </nav>
    </aside>

    <div class="settings-workspace">
      <div class="mobile-section-switch panel">
        <label for="settings-section">{{ copy.section }}</label>
        <select
          id="settings-section"
          class="control"
          :value="activeSection"
          @change="
            selectSection(
              ($event.target as HTMLSelectElement)
                .value as ProjectSettingsSection,
            )
          "
        >
          <option
            v-for="section in visibleSections"
            :key="section.id"
            :value="section.id"
          >
            {{ section.label }} — {{ section.description }}
          </option>
        </select>
      </div>

      <main
        id="settings-content"
        ref="settingsContent"
        class="settings-content"
        tabindex="-1"
      >
        <section
          v-show="activeSection === 'general'"
          class="settings-section panel"
          aria-labelledby="general-title"
        >
          <header class="section-header">
            <div>
              <span class="section-kicker">{{ copy.project }}</span>
              <h2 id="general-title">{{ copy.generalTitle }}</h2>
              <p>{{ copy.generalDescription }}</p>
            </div>
            <span v-if="!canEditProject" class="readonly-badge">
              {{ copy.readOnly }}
            </span>
          </header>
          <fieldset class="settings-grid" :disabled="!canEditProject">
            <label>
              <span>{{ copy.name }} *</span>
              <input
                v-model="form.name"
                class="control"
                required
                minlength="2"
                maxlength="120"
              />
              <small>{{ copy.nameHint }}</small>
            </label>
            <label>
              <span>{{ copy.projectType }} *</span>
              <select v-model="form.project_type" class="control">
                <option value="web">Frontend</option>
                <option value="backend">Backend</option>
                <option value="bot">{{ copy.bot }}</option>
                <option value="docker">Docker</option>
                <option value="static">{{ copy.staticSite }}</option>
                <option value="minecraft_forge">Minecraft Forge</option>
                <option value="minecraft_paper">Minecraft Paper</option>
                <option value="minecraft_purpur">Minecraft Purpur</option>
                <option value="minecraft_spigot">Minecraft Spigot</option>
              </select>
              <small>{{ copy.projectTypeHint }}</small>
            </label>
            <label class="field-wide">
              <span>{{ copy.fieldDescription }}</span>
              <textarea
                v-model="form.description"
                class="control"
                rows="4"
                maxlength="5000"
                :placeholder="copy.descriptionPlaceholder"
              />
              <small>{{ copy.descriptionHint }}</small>
            </label>
          </fieldset>
        </section>

        <section
          v-show="activeSection === 'source'"
          class="settings-section panel"
          aria-labelledby="source-title"
        >
          <header class="section-header">
            <div>
              <span class="section-kicker">{{ copy.deployKicker }}</span>
              <h2 id="source-title">{{ copy.sourceTitle }}</h2>
              <p>{{ copy.sourceTitleDescription }}</p>
            </div>
            <span v-if="!canEditProject" class="readonly-badge">
              {{ copy.readOnly }}
            </span>
          </header>

          <fieldset class="settings-stack" :disabled="!canEditProject">
            <div class="field-group">
              <div class="field-group__header">
                <h3>{{ copy.projectSource }}</h3>
                <p>{{ copy.projectSourceDescription }}</p>
              </div>
              <div class="source-options">
                <label class="source-option">
                  <input
                    v-model="form.source_type"
                    type="radio"
                    value="files"
                  />
                  <span>
                    <strong>{{ copy.projectFiles }}</strong>
                    <small>{{ copy.projectFilesDescription }}</small>
                  </span>
                </label>
                <label class="source-option">
                  <input v-model="form.source_type" type="radio" value="git" />
                  <span>
                    <strong>{{ copy.gitRepository }}</strong>
                    <small>{{ copy.gitDescription }}</small>
                  </span>
                </label>
              </div>
              <div v-if="form.source_type === 'git'" class="settings-grid">
                <label>
                  <span>Repository URL *</span>
                  <input
                    v-model="form.repository_url"
                    class="control"
                    type="url"
                    inputmode="url"
                    maxlength="2048"
                    placeholder="https://github.com/owner/repository.git"
                    required
                  />
                  <small>{{ copy.repositoryHint }}</small>
                </label>
                <label>
                  <span>{{ copy.branch }}</span>
                  <input
                    v-model="form.branch"
                    class="control monospace"
                    maxlength="255"
                    placeholder="main"
                  />
                  <small>{{ copy.branchHint }}</small>
                </label>
              </div>
            </div>

            <div class="field-group">
              <div class="field-group__header">
                <h3>
                  {{ dockerRuntime ? copy.dockerBuild : copy.buildCommands }}
                </h3>
                <p v-if="dockerRuntime">{{ copy.dockerBuildDescription }}</p>
                <p v-else>{{ copy.emptyStepsSkipped }}</p>
              </div>
              <div
                v-if="composeMonitorOnly"
                class="notice notice--warning"
                role="status"
              >
                <IconAlertTriangle :size="18" />
                <span>{{ copy.composeCommands }}</span>
              </div>
              <div v-else-if="minecraftRuntime" class="notice" role="status">
                <IconAlertTriangle :size="18" />
                <span>{{ copy.minecraftCommands }}</span>
              </div>
              <div v-else class="settings-grid">
                <label>
                  <span>
                    {{
                      dockerRuntime
                        ? "Host pre-build: install"
                        : "Install command"
                    }}
                  </span>
                  <input
                    v-model="form.install_command"
                    class="control monospace"
                    maxlength="4000"
                    :placeholder="dockerRuntime ? copy.usuallyEmpty : 'npm ci'"
                  />
                  <small v-if="dockerRuntime">{{ copy.hostInstallHint }}</small>
                </label>
                <label>
                  <span>
                    {{
                      dockerRuntime ? "Host pre-build: build" : "Build command"
                    }}
                  </span>
                  <input
                    v-model="form.build_command"
                    class="control monospace"
                    maxlength="4000"
                    :placeholder="
                      dockerRuntime ? copy.usuallyEmpty : 'npm run build'
                    "
                  />
                  <small v-if="dockerRuntime">{{ copy.hostBuildHint }}</small>
                </label>
                <label v-if="startCommandVisible">
                  <span>
                    {{
                      dockerRuntime
                        ? "Container command override"
                        : "Start command"
                    }}
                  </span>
                  <input
                    v-model="form.start_command"
                    class="control monospace"
                    maxlength="4000"
                    :placeholder="
                      dockerRuntime
                        ? copy.dockerCommandPlaceholder
                        : 'node dist/server.js'
                    "
                  />
                  <small v-if="dockerRuntime">{{
                    copy.dockerCommandHint
                  }}</small>
                </label>
                <label v-if="staticRuntime">
                  <span>{{ copy.outputDirectory }}</span>
                  <input
                    v-model="form.output_directory"
                    class="control monospace"
                    maxlength="512"
                    placeholder="dist"
                  />
                </label>
              </div>
            </div>
          </fieldset>

          <div class="subsection">
            <header class="subsection-header">
              <div class="subsection-title">
                <span class="subsection-icon"><IconKey :size="19" /></span>
                <div>
                  <h3>{{ copy.privateGit }}</h3>
                  <p>{{ copy.privateGitDescription }}</p>
                </div>
              </div>
              <span
                :class="
                  !credentialLoadError && gitCredential?.configured
                    ? 'configured-badge'
                    : 'readonly-badge'
                "
              >
                {{
                  credentialLoadError
                    ? copy.unavailableStatus
                    : gitCredential?.configured
                      ? copy.configured.replace(
                          "{type}",
                          gitCredential.credential_type ?? "—",
                        )
                      : copy.notConfigured
                }}
              </span>
            </header>

            <div
              v-if="credentialLoadError"
              class="notice notice--danger"
              role="alert"
            >
              <IconAlertTriangle :size="18" />
              <span>{{ copy.credentialLoadError }}</span>
              <button
                class="notice-action"
                type="button"
                @click="refreshGitCredential()"
              >
                {{ t("projects.retry") }}
              </button>
            </div>
            <div
              v-else-if="!gitCredentialReady"
              class="notice notice--warning"
              role="status"
            >
              <IconAlertTriangle :size="18" />
              <span>{{ copy.saveSourceFirst }}</span>
            </div>

            <form class="settings-stack" @submit.prevent="saveGitCredential">
              <fieldset
                class="settings-grid"
                :disabled="
                  !canManageGitCredential ||
                  !gitCredentialReady ||
                  credentialPending
                "
              >
                <label>
                  <span>{{ copy.credentialType }}</span>
                  <select
                    v-model="credentialForm.credential_type"
                    class="control"
                  >
                    <option value="token">{{ copy.tokenCredential }}</option>
                    <option value="deploy_key">
                      {{ copy.deployKeyCredential }}
                    </option>
                  </select>
                </label>
                <label v-if="credentialForm.credential_type === 'token'">
                  <span>{{ copy.username }}</span>
                  <input
                    v-model="credentialForm.username"
                    class="control"
                    maxlength="255"
                    placeholder="x-access-token"
                  />
                  <small>{{ copy.githubUsernameHint }}</small>
                </label>
                <label class="field-wide">
                  <span>
                    {{
                      credentialForm.credential_type === "token"
                        ? copy.token
                        : copy.privateDeployKey
                    }}
                  </span>
                  <textarea
                    v-if="credentialForm.credential_type === 'deploy_key'"
                    v-model="credentialForm.value"
                    class="control monospace"
                    rows="7"
                    minlength="8"
                    maxlength="20000"
                    spellcheck="false"
                    placeholder="-----BEGIN OPENSSH PRIVATE KEY-----"
                  />
                  <input
                    v-else
                    v-model="credentialForm.value"
                    class="control"
                    type="password"
                    minlength="8"
                    maxlength="20000"
                    autocomplete="new-password"
                    placeholder="github_pat_..."
                  />
                  <small>{{ copy.credentialValueHint }}</small>
                </label>
              </fieldset>

              <div class="feedback" aria-live="polite">
                <p v-if="credentialMessage" class="success">
                  <IconCheck :size="17" />
                  {{ credentialMessage }}
                </p>
                <p v-if="credentialError" class="error" role="alert">
                  {{ credentialError }}
                </p>
              </div>

              <div v-if="canManageGitCredential" class="section-actions">
                <button
                  class="button-danger"
                  type="button"
                  :disabled="
                    credentialPending ||
                    Boolean(credentialLoadError) ||
                    !gitCredential?.configured
                  "
                  @click="deleteCredentialOpen = true"
                >
                  <IconTrash :size="18" />
                  {{ copy.deleteCredential }}
                </button>
                <button
                  class="button-primary"
                  type="submit"
                  :disabled="
                    credentialPending ||
                    !gitCredentialReady ||
                    credentialForm.value.length < 8
                  "
                >
                  <IconKey :size="18" />
                  {{
                    credentialPending
                      ? t("common.saving")
                      : gitCredential?.configured
                        ? copy.replaceCredential
                        : copy.saveCredential
                  }}
                </button>
              </div>
            </form>
          </div>
        </section>

        <section
          v-show="activeSection === 'runtime'"
          class="settings-section panel"
          aria-labelledby="runtime-title"
        >
          <header class="section-header">
            <div>
              <span class="section-kicker">{{ copy.executionKicker }}</span>
              <h2 id="runtime-title">{{ copy.runtimeTitle }}</h2>
              <p>{{ copy.runtimeDescription }}</p>
            </div>
            <span v-if="!canEditProject" class="readonly-badge">
              {{ copy.readOnly }}
            </span>
          </header>

          <fieldset class="settings-stack" :disabled="!canEditProject">
            <div class="settings-grid">
              <label>
                <span>Runtime *</span>
                <select v-model="form.runtime_type" class="control">
                  <option value="docker">Docker container</option>
                  <option value="docker_compose">Docker Compose</option>
                  <option value="systemd">systemd</option>
                  <option value="pm2">PM2</option>
                  <option value="static">Nginx static</option>
                  <option value="minecraft_forge">Minecraft Forge</option>
                  <option value="minecraft_paper">Minecraft Paper</option>
                  <option value="minecraft_purpur">Minecraft Purpur</option>
                  <option value="minecraft_spigot">Minecraft Spigot</option>
                </select>
                <small>{{ copy.runtimeHint }}</small>
              </label>
              <label>
                <span>{{ copy.upstreamPort }}</span>
                <input
                  v-model.number="form.upstream_port"
                  class="control"
                  type="number"
                  min="1024"
                  max="65535"
                  :placeholder="dockerRuntime ? copy.automatic : '8090'"
                />
                <small v-if="dockerRuntime">{{ copy.dockerPortHint }}</small>
                <small v-else>{{ copy.processPortHint }}</small>
              </label>
              <label class="field-wide">
                <span>{{ copy.healthcheckUrl }}</span>
                <input
                  v-model="form.healthcheck_url"
                  class="control"
                  type="url"
                  inputmode="url"
                  maxlength="2048"
                  placeholder="https://example.com/health"
                />
                <small>{{ copy.healthcheckHint }}</small>
              </label>
            </div>

            <details
              class="advanced-settings"
              :open="runtimeAdvancedOpen"
              @toggle="
                runtimeAdvancedOpen = (
                  $event.currentTarget as HTMLDetailsElement
                ).open
              "
            >
              <summary>
                <span>
                  <strong>{{ copy.advanced }}</strong>
                  <small>{{ copy.runtimeConfigJson }}</small>
                </span>
              </summary>
              <div class="advanced-settings__body">
                <div class="notice">
                  <IconAlertTriangle :size="18" />
                  <span>{{ copy.advancedHint }}</span>
                </div>
                <label>
                  <span>{{ copy.runtimeConfigJson }}</span>
                  <textarea
                    v-model="form.runtime_config_text"
                    class="control monospace"
                    rows="10"
                    spellcheck="false"
                  />
                </label>
              </div>
            </details>
          </fieldset>
        </section>

        <section
          v-show="activeSection === 'resources'"
          class="settings-section panel"
          aria-labelledby="resources-title"
        >
          <header class="section-header">
            <div>
              <span class="section-kicker">{{ copy.guardrailsKicker }}</span>
              <h2 id="resources-title">{{ copy.resourcesTitle }}</h2>
              <p>{{ copy.resourcesDescription }}</p>
            </div>
            <span v-if="!canManageResourcePolicy" class="readonly-badge">
              {{ copy.readOnly }}
            </span>
          </header>

          <div
            v-if="resourcePolicyLoadError"
            class="notice notice--danger"
            role="alert"
          >
            <IconAlertTriangle :size="18" />
            <span>{{ copy.resourceLoadError }}</span>
            <button
              class="notice-action"
              type="button"
              @click="refreshResourcePolicy()"
            >
              {{ t("projects.retry") }}
            </button>
          </div>
          <div
            v-else-if="composeMonitorOnly"
            class="notice notice--warning"
            role="status"
          >
            <IconAlertTriangle :size="18" />
            <span>{{ copy.composeMonitorOnly }}</span>
          </div>

          <form class="settings-stack" @submit.prevent="saveResourcePolicy">
            <fieldset
              class="settings-stack"
              :disabled="
                !canManageResourcePolicy ||
                policyPending ||
                Boolean(resourcePolicyLoadError)
              "
            >
              <label class="policy-toggle">
                <input v-model="policyForm.enabled" type="checkbox" />
                <span>
                  <strong>{{ copy.resourcePolicy }}</strong>
                  <small>
                    {{
                      policyForm.enabled
                        ? copy.limitsNextDeploy
                        : copy.limitsDisabled
                    }}
                  </small>
                </span>
              </label>
              <div class="settings-grid settings-grid--three">
                <label>
                  <span>{{ copy.cpuCores }}</span>
                  <input
                    v-model.number="policyForm.cpu_cores"
                    class="control"
                    type="number"
                    min="0.1"
                    max="128"
                    step="0.1"
                    placeholder="1.0"
                  />
                  <small>{{ copy.cpuRange }}</small>
                </label>
                <label>
                  <span>RAM</span>
                  <div class="input-with-unit">
                    <input
                      v-model.number="policyForm.memory_mb"
                      class="control"
                      type="number"
                      min="64"
                      max="1048576"
                      step="1"
                      placeholder="1024"
                    />
                    <span>MB</span>
                  </div>
                  <small>{{ copy.memoryMin }}</small>
                </label>
                <label>
                  <span>{{ copy.disk }}</span>
                  <div class="input-with-unit">
                    <input
                      v-model.number="policyForm.disk_mb"
                      class="control"
                      type="number"
                      min="64"
                      max="10485760"
                      step="1"
                      placeholder="4096"
                    />
                    <span>MB</span>
                  </div>
                  <small>{{ copy.diskMonitoring }}</small>
                </label>
              </div>
              <div class="notice">
                <IconAlertTriangle :size="18" />
                <span>{{ copy.policyHint }}</span>
              </div>
            </fieldset>

            <div class="feedback" aria-live="polite">
              <p v-if="policyMessage" class="success">
                <IconCheck :size="17" />
                {{ policyMessage }}
              </p>
              <p v-if="policyError" class="error" role="alert">
                {{ policyError }}
              </p>
            </div>

            <div v-if="canManageResourcePolicy" class="section-actions">
              <button
                class="button-secondary"
                type="button"
                :disabled="
                  policyPending ||
                  !policyDirty ||
                  Boolean(resourcePolicyLoadError)
                "
                @click="resetResourcePolicy"
              >
                {{ copy.discardChanges }}
              </button>
              <button
                class="button-primary"
                type="submit"
                :disabled="
                  policyPending ||
                  !policyDirty ||
                  Boolean(resourcePolicyLoadError)
                "
              >
                <IconCheck :size="18" />
                {{ policyPending ? t("common.saving") : copy.saveLimits }}
              </button>
            </div>
          </form>
        </section>

        <section
          v-if="canManageMembers"
          v-show="activeSection === 'access'"
          class="settings-section panel"
          aria-labelledby="access-title"
        >
          <header class="section-header">
            <div>
              <span class="section-kicker">{{ copy.rbacKicker }}</span>
              <h2 id="access-title">{{ copy.accessTitle }}</h2>
              <p>{{ copy.accessDescription }}</p>
            </div>
            <span class="member-count">
              {{
                accessLoadError
                  ? copy.unavailableStatus
                  : copy.members.replace(
                      "{count}",
                      String(members?.length ?? 0),
                    )
              }}
            </span>
          </header>

          <div
            v-if="accessLoadError"
            class="notice notice--danger"
            role="alert"
          >
            <IconAlertTriangle :size="18" />
            <span>{{ copy.accessLoadError }}</span>
            <button class="notice-action" type="button" @click="refreshAccess">
              {{ t("projects.retry") }}
            </button>
          </div>

          <form v-else class="member-form" @submit.prevent="addMember">
            <label>
              <span>{{ copy.user }}</span>
              <select v-model="memberForm.userId" class="control" required>
                <option value="" disabled>
                  {{ assignableUsers.length ? copy.selectUser : copy.noUsers }}
                </option>
                <option
                  v-for="user in assignableUsers"
                  :key="user.id"
                  :value="user.id"
                >
                  {{ user.display_name || user.username }} — {{ user.role }}
                </option>
              </select>
            </label>
            <label>
              <span>{{ copy.projectRole }}</span>
              <select v-model="memberForm.role" class="control">
                <option value="maintainer">Maintainer</option>
                <option value="viewer">Viewer</option>
              </select>
            </label>
            <button
              class="button-primary"
              type="submit"
              :disabled="memberPending || !memberForm.userId"
            >
              <IconUserPlus :size="18" />
              {{ copy.add }}
            </button>
          </form>

          <div class="feedback" aria-live="polite">
            <p v-if="memberMessage" class="success">
              <IconCheck :size="17" />
              {{ memberMessage }}
            </p>
            <p v-if="memberError" class="error" role="alert">
              {{ memberError }}
            </p>
          </div>

          <div
            v-if="!accessLoadError && !members?.length"
            class="empty-members"
          >
            <IconUsers :size="24" />
            <div>
              <strong>{{ copy.noMembers }}</strong>
              <p>{{ copy.noMembersDescription }}</p>
            </div>
          </div>
          <div v-else-if="!accessLoadError" class="member-list">
            <article
              v-for="member in members"
              :key="member.id"
              class="member-row"
            >
              <div class="member-identity">
                <span class="member-avatar" aria-hidden="true">
                  {{
                    (member.user.display_name || member.user.username)
                      .slice(0, 2)
                      .toUpperCase()
                  }}
                </span>
                <span>
                  <strong>
                    {{ member.user.display_name || member.user.username }}
                  </strong>
                  <small>@{{ member.user.username }}</small>
                </span>
              </div>
              <label>
                <span class="sr-only">
                  {{
                    copy.userRole.replace("{username}", member.user.username)
                  }}
                </span>
                <select
                  class="control"
                  :value="member.role"
                  :disabled="memberPending"
                  @change="
                    updateMember(
                      member,
                      ($event.target as HTMLSelectElement).value as
                        | 'maintainer'
                        | 'viewer',
                    )
                  "
                >
                  <option value="maintainer">Maintainer</option>
                  <option value="viewer">Viewer</option>
                </select>
              </label>
              <button
                class="icon-button icon-button--danger"
                type="button"
                :disabled="memberPending"
                :aria-label="
                  copy.removeUser.replace('{username}', member.user.username)
                "
                :title="
                  copy.removeUser.replace('{username}', member.user.username)
                "
                @click="memberToRemove = member"
              >
                <IconTrash :size="19" />
              </button>
            </article>
          </div>
        </section>

        <footer
          v-if="projectFieldsSection && canEditProject"
          class="save-bar panel"
        >
          <div class="save-state" aria-live="polite">
            <p v-if="projectSaveError" class="error" role="alert">
              {{ projectSaveError }}
            </p>
            <p v-else-if="projectMessage" class="success">
              <IconCheck :size="17" />
              {{ projectMessage }}
            </p>
            <p v-else-if="projectDirty">{{ copy.unsaved }}</p>
            <p v-else>{{ copy.allSaved }}</p>
          </div>
          <div class="save-actions">
            <button
              class="button-secondary"
              type="button"
              :disabled="projectPending || !projectDirty"
              @click="resetProjectForm"
            >
              {{ copy.discard }}
            </button>
            <button
              class="button-primary"
              type="button"
              :disabled="projectPending || !projectDirty"
              @click="saveProject"
            >
              <IconCheck :size="18" />
              {{ projectPending ? t("common.saving") : t("common.save") }}
            </button>
          </div>
        </footer>
      </main>
    </div>
  </div>

  <ConfirmDialog
    :open="deleteCredentialOpen"
    :title="copy.deleteCredentialTitle"
    :message="copy.deleteCredentialMessage"
    :confirm-label="copy.deleteCredential"
    danger
    @cancel="deleteCredentialOpen = false"
    @confirm="deleteGitCredential"
  />
  <ConfirmDialog
    :open="Boolean(memberToRemove)"
    :title="copy.removeMemberTitle"
    :message="
      memberToRemove
        ? copy.removeMemberMessage.replace(
            '{name}',
            memberToRemove.user.display_name || memberToRemove.user.username,
          )
        : ''
    "
    :confirm-label="copy.removeMember"
    danger
    @cancel="memberToRemove = null"
    @confirm="removeMember"
  />
</template>

<style scoped>
.settings-layout {
  display: grid;
  grid-template-columns: minmax(230px, 260px) minmax(0, 920px);
  gap: 1.25rem;
  align-items: start;
}

.settings-sidebar {
  position: sticky;
  top: 1rem;
  overflow: hidden;
}

.project-context {
  display: grid;
  gap: 0.35rem;
  border-bottom: 1px solid var(--border);
  padding: 1rem;
}

.project-context__label,
.section-kicker {
  color: var(--text-muted);
  font-size: 0.7rem;
  font-weight: 700;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.project-context strong {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.project-context code {
  overflow: hidden;
  color: var(--text-muted);
  font-size: 0.75rem;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.project-context__meta {
  display: flex;
  flex-wrap: wrap;
  gap: 0.4rem;
  margin-top: 0.35rem;
}

.project-context__meta span,
.member-count {
  border: 1px solid var(--border);
  border-radius: 999px;
  padding: 0.2rem 0.5rem;
  color: var(--text-muted);
  font-size: 0.72rem;
}

.settings-nav {
  display: grid;
  gap: 0.25rem;
  padding: 0.45rem;
}

.settings-nav button {
  display: grid;
  min-height: 62px;
  width: 100%;
  cursor: pointer;
  grid-template-columns: 24px minmax(0, 1fr);
  gap: 0.7rem;
  align-items: center;
  border: 1px solid transparent;
  border-radius: 8px;
  background: transparent;
  padding: 0.6rem 0.7rem;
  color: var(--text-muted);
  text-align: left;
  transition:
    border-color 180ms ease,
    background-color 180ms ease,
    color 180ms ease;
}

.settings-nav button:hover {
  background: var(--surface-subtle);
  color: var(--text);
}

.settings-nav button.active {
  border-color: var(--border);
  background: var(--surface-subtle);
  color: var(--text);
}

.settings-nav button > span {
  display: grid;
  min-width: 0;
  gap: 0.15rem;
}

.settings-nav strong,
.settings-nav small {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.settings-nav strong {
  font-size: 0.85rem;
}

.settings-nav small {
  color: var(--text-muted);
  font-size: 0.72rem;
  font-weight: 400;
}

.settings-workspace,
.settings-content {
  min-width: 0;
}

.settings-content:focus {
  outline: none;
}

.mobile-section-switch {
  display: none;
}

.settings-section {
  display: grid;
  gap: 1.4rem;
  padding: clamp(1rem, 3vw, 1.5rem);
}

.section-header,
.subsection-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 1rem;
}

.section-header h2,
.section-header p,
.subsection-header h3,
.subsection-header p,
.field-group__header h3,
.field-group__header p,
.empty-members p {
  margin: 0;
}

.section-header h2 {
  margin-top: 0.25rem;
  font-size: 1.3rem;
  letter-spacing: -0.02em;
}

.section-header p,
.subsection-header p,
.field-group__header p,
.empty-members p {
  margin-top: 0.3rem;
  color: var(--text-muted);
  font-size: 0.86rem;
  line-height: 1.5;
}

.readonly-badge,
.configured-badge {
  flex: none;
  border: 1px solid var(--border);
  border-radius: 999px;
  padding: 0.28rem 0.6rem;
  color: var(--text-muted);
  font-size: 0.72rem;
  font-weight: 650;
}

.configured-badge {
  border-color: #285b42;
  color: #70d49d;
}

.settings-grid {
  display: grid;
  min-width: 0;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 1.1rem;
  border: 0;
  margin: 0;
  padding: 0;
}

.settings-grid--three {
  grid-template-columns: repeat(3, minmax(0, 1fr));
}

.settings-stack {
  display: grid;
  min-width: 0;
  gap: 1.25rem;
  border: 0;
  margin: 0;
  padding: 0;
}

.field-wide {
  grid-column: 1 / -1;
}

label {
  display: grid;
  min-width: 0;
  gap: 0.45rem;
  color: #d8d9dc;
  font-size: 0.86rem;
  font-weight: 650;
}

label small {
  color: var(--text-muted);
  font-size: 0.74rem;
  font-weight: 400;
  line-height: 1.45;
}

textarea {
  resize: vertical;
}

fieldset:disabled .control,
fieldset:disabled .source-option,
fieldset:disabled .policy-toggle {
  cursor: not-allowed;
  opacity: 0.65;
}

.monospace {
  font-family: ui-monospace, "IBM Plex Mono", monospace;
}

.field-group {
  display: grid;
  gap: 1rem;
}

.field-group + .field-group {
  border-top: 1px solid var(--border);
  padding-top: 1.25rem;
}

.source-options {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0.75rem;
}

.source-option {
  min-height: 76px;
  cursor: pointer;
  grid-template-columns: auto minmax(0, 1fr);
  gap: 0.75rem;
  align-items: flex-start;
  border: 1px solid var(--border);
  border-radius: 10px;
  background: var(--surface-base);
  padding: 0.9rem;
  transition:
    border-color 180ms ease,
    background-color 180ms ease;
}

.source-option:hover,
.source-option:has(input:checked) {
  border-color: var(--accent);
  background: var(--surface-subtle);
}

.source-option input {
  width: 18px;
  height: 18px;
  margin: 0.1rem 0 0;
  accent-color: var(--accent);
}

.source-option > span,
.policy-toggle > span {
  display: grid;
  gap: 0.25rem;
}

.subsection {
  display: grid;
  gap: 1.1rem;
  border-top: 1px solid var(--border);
  padding-top: 1.4rem;
}

.subsection-title {
  display: flex;
  gap: 0.75rem;
  align-items: flex-start;
}

.subsection-icon {
  display: inline-grid;
  width: 36px;
  height: 36px;
  flex: none;
  place-items: center;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--surface-subtle);
  color: var(--accent);
}

.notice {
  display: flex;
  gap: 0.65rem;
  align-items: flex-start;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--surface-base);
  padding: 0.75rem 0.85rem;
  color: var(--text-muted);
  font-size: 0.8rem;
  line-height: 1.45;
}

.notice svg {
  flex: none;
  margin-top: 0.05rem;
}

.notice--warning {
  border-color: #6f5524;
  background: #241f15;
  color: #edbb68;
}

.notice--danger {
  border-color: #73383d;
  background: #27191b;
  color: #f3a1a6;
}

.notice-action {
  min-height: 44px;
  cursor: pointer;
  border: 0;
  border-radius: 6px;
  background: transparent;
  padding: 0.25rem 0.5rem;
  color: inherit;
  font: inherit;
  font-weight: 700;
  text-decoration: underline;
  text-underline-offset: 3px;
}

.notice-action:hover {
  background: rgb(255 255 255 / 7%);
}

.section-actions {
  display: flex;
  justify-content: flex-end;
  gap: 0.7rem;
  border-top: 1px solid var(--border);
  padding-top: 1rem;
}

.advanced-settings {
  overflow: hidden;
  border: 1px solid var(--border);
  border-radius: 10px;
  background: var(--surface-base);
}

.advanced-settings summary {
  display: flex;
  min-height: 58px;
  cursor: pointer;
  align-items: center;
  justify-content: space-between;
  padding: 0.8rem 1rem;
  list-style: none;
}

.advanced-settings summary::-webkit-details-marker {
  display: none;
}

.advanced-settings summary::after {
  width: 8px;
  height: 8px;
  border-right: 2px solid var(--text-muted);
  border-bottom: 2px solid var(--text-muted);
  content: "";
  transform: rotate(45deg);
  transition: transform 180ms ease;
}

.advanced-settings[open] summary::after {
  transform: rotate(225deg);
}

.advanced-settings summary > span {
  display: grid;
  gap: 0.2rem;
}

.advanced-settings summary small {
  color: var(--text-muted);
  font-size: 0.74rem;
}

.advanced-settings__body {
  display: grid;
  gap: 1rem;
  border-top: 1px solid var(--border);
  padding: 1rem;
}

.policy-toggle {
  min-height: 72px;
  cursor: pointer;
  grid-template-columns: auto minmax(0, 1fr);
  gap: 0.75rem;
  align-items: center;
  border: 1px solid var(--border);
  border-radius: 10px;
  background: var(--surface-base);
  padding: 0.85rem 1rem;
}

.policy-toggle input {
  width: 20px;
  height: 20px;
  accent-color: var(--accent);
}

.input-with-unit {
  position: relative;
}

.input-with-unit .control {
  padding-right: 3.2rem;
}

.input-with-unit > span {
  position: absolute;
  top: 50%;
  right: 0.8rem;
  color: var(--text-muted);
  font-size: 0.72rem;
  transform: translateY(-50%);
}

.member-form {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(150px, 190px) auto;
  gap: 0.8rem;
  align-items: end;
}

.member-list {
  display: grid;
  border: 1px solid var(--border);
  border-radius: 10px;
}

.member-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 170px 44px;
  gap: 0.8rem;
  align-items: center;
  padding: 0.8rem;
}

.member-row + .member-row {
  border-top: 1px solid var(--border);
}

.member-identity {
  display: flex;
  min-width: 0;
  gap: 0.75rem;
  align-items: center;
}

.member-avatar {
  display: inline-grid;
  width: 38px;
  height: 38px;
  flex: none;
  place-items: center;
  border: 1px solid var(--border);
  border-radius: 9px;
  background: var(--surface-subtle);
  color: var(--text-muted);
  font-size: 0.75rem;
  font-weight: 750;
}

.member-identity > span:last-child {
  display: grid;
  min-width: 0;
  gap: 0.15rem;
}

.member-identity strong,
.member-identity small {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.member-identity small {
  color: var(--text-muted);
}

.icon-button {
  display: inline-grid;
  width: 44px;
  height: 44px;
  cursor: pointer;
  place-items: center;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--surface-subtle);
  color: var(--text-muted);
}

.icon-button--danger:hover {
  border-color: #77363b;
  background: #3c2023;
  color: #ffdadd;
}

.icon-button:disabled {
  cursor: not-allowed;
  opacity: 0.45;
}

.empty-members {
  display: flex;
  gap: 0.8rem;
  align-items: flex-start;
  border: 1px dashed var(--border);
  border-radius: 10px;
  padding: 1rem;
  color: var(--text-muted);
}

.empty-members strong {
  color: var(--text);
}

.feedback:empty {
  display: none;
}

.feedback p,
.save-state p {
  margin: 0;
}

.success {
  display: flex;
  gap: 0.4rem;
  align-items: center;
  color: #70d49d;
}

.error {
  color: #f3a1a6;
}

.save-bar {
  position: sticky;
  z-index: 5;
  bottom: 1rem;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
  margin-top: 1rem;
  padding: 0.75rem 0.8rem 0.75rem 1rem;
  box-shadow: 0 12px 30px rgb(0 0 0 / 28%);
}

.save-state {
  color: var(--text-muted);
  font-size: 0.82rem;
}

.save-actions {
  display: flex;
  gap: 0.6rem;
}

.page-state {
  display: flex;
  max-width: 920px;
  min-height: 120px;
  gap: 0.9rem;
  align-items: center;
  padding: 1.2rem;
}

.page-state h2,
.page-state p {
  margin: 0;
}

.page-state h2 {
  font-size: 1rem;
}

.page-state p {
  margin-top: 0.25rem;
  color: var(--text-muted);
}

.page-state .button-secondary {
  margin-left: auto;
}

.state-icon {
  flex: none;
  color: var(--text-muted);
}

.state-icon--loading {
  animation: spin 1s linear infinite;
}

.state-icon--error {
  color: var(--danger);
}

.sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
  clip-path: inset(50%);
}

@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}

@media (max-width: 980px) {
  .settings-layout {
    grid-template-columns: minmax(0, 920px);
  }

  .settings-sidebar {
    display: none;
  }

  .mobile-section-switch {
    display: grid;
    gap: 0.45rem;
    margin-bottom: 1rem;
    padding: 0.8rem;
  }

  .mobile-section-switch label {
    font-size: 0.75rem;
  }
}

@media (max-width: 700px) {
  .settings-grid,
  .settings-grid--three,
  .source-options,
  .member-form,
  .member-row {
    grid-template-columns: 1fr;
  }

  .member-form {
    align-items: stretch;
  }

  .member-row {
    position: relative;
    padding-right: 3.8rem;
  }

  .member-row .icon-button {
    position: absolute;
    top: 0.8rem;
    right: 0.8rem;
  }

  .section-header,
  .subsection-header {
    align-items: flex-start;
    flex-direction: column;
  }

  .section-actions {
    align-items: stretch;
    flex-direction: column-reverse;
  }

  .save-bar {
    position: static;
    align-items: stretch;
    flex-direction: column;
    padding: 0.85rem;
    box-shadow: none;
  }

  .save-actions {
    display: grid;
    grid-template-columns: 1fr 1fr;
  }

  .page-state {
    align-items: flex-start;
    flex-wrap: wrap;
  }

  .page-state .button-secondary {
    width: 100%;
    margin-left: 0;
  }
}
</style>
