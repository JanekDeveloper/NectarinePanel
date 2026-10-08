<script setup lang="ts">
import { isMinecraftRuntime } from "~/utils/minecraft";
import {
  IconAlertTriangle,
  IconArrowLeft,
  IconBox,
  IconBrandDocker,
  IconCheck,
  IconChevronLeft,
  IconChevronRight,
  IconCode,
  IconFileCode,
  IconFiles,
  IconGitBranch,
  IconInfoCircle,
  IconPackage,
  IconRefresh,
  IconRocket,
  IconSearch,
  IconServer,
  IconSettings,
  IconShieldCheck,
} from "@tabler/icons-vue";
import type { Component } from "vue";

import {
  PROJECT_CREATE_STEPS,
  applyProjectTemplate,
  buildProjectCreateRequest,
  emptyProjectCreateDraft,
  projectTypeLabel as rawProjectTypeLabel,
  runtimeTypeLabel,
  validateProjectCreateStep as rawValidateProjectCreateStep,
  type ProjectCreateDraft,
  type ProjectCreateErrors,
  type ProjectCreateStep,
  type ProjectSourceType,
} from "~/utils/project-create";
import { canCreateProjects } from "~/utils/rbac";
import type { Project, ProjectTemplate } from "~/types/api";
import { projectCreateMessages } from "~/locales/project-create";

const DRAFT_STORAGE_KEY = "nectarine.project-create-draft.v2";

const api = useApi();
const auth = useAuthStore();
const localeController = useLocale();
const copy = computed(
  () => projectCreateMessages[localeController.locale.value],
);
const selectedTemplateId = ref("custom");
const templateQuery = ref("");
const currentStepIndex = ref(0);
const maximumVisitedStep = ref(0);
const lastTemplateDescription = ref("");
const pending = ref(false);
const serverError = ref("");
const draftReady = ref(false);
const stepContent = ref<HTMLElement | null>(null);
const form = reactive<ProjectCreateDraft>(emptyProjectCreateDraft());
const errors = reactive<ProjectCreateErrors>({});

const {
  data: templates,
  pending: templatesPending,
  error: templatesError,
  refresh: refreshTemplates,
} = await useAsyncData("project-templates", () =>
  api.request<ProjectTemplate[]>("/projects/templates", { silent: true }),
);

const steps = computed<
  Array<{
    id: ProjectCreateStep;
    label: string;
    description: string;
    icon: Component;
  }>
>(() => [
  {
    id: "template",
    label: copy.value.stepBase,
    description: copy.value.stepBaseDescription,
    icon: IconPackage,
  },
  {
    id: "details",
    label: copy.value.stepProject,
    description: copy.value.stepProjectDescription,
    icon: IconFileCode,
  },
  {
    id: "runtime",
    label: "Runtime",
    description: copy.value.stepRuntimeDescription,
    icon: IconServer,
  },
  {
    id: "review",
    label: copy.value.stepReview,
    description: copy.value.stepReviewDescription,
    icon: IconShieldCheck,
  },
]);

const currentStep = computed(
  () => PROJECT_CREATE_STEPS[currentStepIndex.value] ?? "template",
);
const selectedTemplate = computed(
  () =>
    templates.value?.find(
      (template) => template.id === selectedTemplateId.value,
    ) ?? null,
);
const usesTemplate = computed(() => selectedTemplateId.value !== "custom");
const canCreateProject = computed(
  () => !auth.user || canCreateProjects(auth.role),
);
const filteredTemplates = computed(() => {
  const query = templateQuery.value.trim().toLocaleLowerCase("ru");
  if (!query) return templates.value ?? [];
  return (templates.value ?? []).filter((template) =>
    [
      template.name,
      template.description,
      template.project_type,
      template.runtime_type,
    ]
      .join(" ")
      .toLocaleLowerCase("ru")
      .includes(query),
  );
});
const hasDraft = computed(
  () =>
    Boolean(form.name.trim()) ||
    Boolean(form.description.trim()) ||
    selectedTemplateId.value !== "custom" ||
    form.source_type === "git",
);
const sourceLabel = computed(() =>
  form.source_type === "git" ? copy.value.sourceGit : copy.value.sourceFiles,
);
const currentStepMeta = computed(
  () => steps.value[currentStepIndex.value] ?? steps.value[0]!,
);
const dockerRuntime = computed(() => form.runtime_type === "docker");
const composeRuntime = computed(() => form.runtime_type === "docker_compose");
const minecraftRuntime = computed(() => isMinecraftRuntime(form.runtime_type));
watch(
  () => form.project_type,
  (value) => {
    if (isMinecraftRuntime(value) && value !== "minecraft_forge") {
      form.runtime_type = value;
      form.source_type = "files";
      form.repository_url = "";
    }
  },
);
const hostBuildFieldsVisible = computed(
  () => !composeRuntime.value && !minecraftRuntime.value,
);
const startCommandVisible = computed(() =>
  ["docker", "systemd", "pm2"].includes(form.runtime_type),
);
const outputDirectoryVisible = computed(() => form.runtime_type === "static");

onMounted(() => {
  restoreDraft();
  draftReady.value = true;
});

watch(
  [form, selectedTemplateId],
  () => {
    if (!draftReady.value || !import.meta.client) return;
    sessionStorage.setItem(
      DRAFT_STORAGE_KEY,
      JSON.stringify({
        selectedTemplateId: selectedTemplateId.value,
        form: { ...form },
      }),
    );
  },
  { deep: true },
);

/**
 * Restores a safe session-scoped draft after a page reload.
 */
function restoreDraft(): void {
  if (!import.meta.client) return;
  const stored = sessionStorage.getItem(DRAFT_STORAGE_KEY);
  if (!stored) return;
  try {
    const parsed = JSON.parse(stored) as {
      selectedTemplateId?: unknown;
      form?: Record<string, unknown>;
    };
    const defaults = emptyProjectCreateDraft();
    if (parsed.form && typeof parsed.form === "object") {
      for (const key of Object.keys(defaults) as Array<
        keyof ProjectCreateDraft
      >) {
        const value = parsed.form[key];
        if (key === "source_type" && (value === "files" || value === "git")) {
          form.source_type = value;
        } else if (key !== "source_type" && typeof value === "string") {
          (form as unknown as Record<string, string>)[key] = value;
        }
      }
    }
    if (
      typeof parsed.selectedTemplateId === "string" &&
      (parsed.selectedTemplateId === "custom" ||
        templates.value?.some(
          (template) => template.id === parsed.selectedTemplateId,
        ))
    ) {
      selectedTemplateId.value = parsed.selectedTemplateId;
    }
  } catch {
    sessionStorage.removeItem(DRAFT_STORAGE_KEY);
  }
}

/**
 * Selects a built-in template and applies its visible defaults.
 */
function chooseTemplate(template: ProjectTemplate | null): void {
  selectedTemplateId.value = template?.id ?? "custom";
  if (template) {
    const replaceDescription =
      !form.description.trim() ||
      form.description === lastTemplateDescription.value;
    const nextDraft = applyProjectTemplate(form, template);
    if (replaceDescription) nextDraft.description = template.description;
    Object.assign(form, nextDraft);
    lastTemplateDescription.value = template.description;
  }
  clearErrors();
  serverError.value = "";
}

/**
 * Converts the selected template defaults into editable custom settings.
 */
function useManualConfiguration(): void {
  selectedTemplateId.value = "custom";
  clearErrors();
}

/**
 * Selects the project source mode and removes stale field errors.
 */
function chooseSource(sourceType: ProjectSourceType): void {
  form.source_type = sourceType;
  delete errors.repository_url;
}

/**
 * Navigates to a previously reached wizard step.
 */
async function goToStep(index: number): Promise<void> {
  if (index > maximumVisitedStep.value || index === currentStepIndex.value)
    return;
  currentStepIndex.value = index;
  serverError.value = "";
  clearErrors();
  await focusStep();
}

/**
 * Validates the current step and advances the wizard.
 */
async function nextStep(): Promise<void> {
  if (!validateStep(currentStep.value)) {
    await focusFirstError();
    return;
  }
  if (currentStepIndex.value >= steps.value.length - 1) {
    await submit();
    return;
  }
  currentStepIndex.value += 1;
  maximumVisitedStep.value = Math.max(
    maximumVisitedStep.value,
    currentStepIndex.value,
  );
  clearErrors();
  await focusStep();
}

/**
 * Moves the wizard back one step.
 */
async function previousStep(): Promise<void> {
  if (currentStepIndex.value === 0) return;
  currentStepIndex.value -= 1;
  serverError.value = "";
  clearErrors();
  await focusStep();
}

/**
 * Opens an editable step from the review screen.
 */
async function editStep(index: number): Promise<void> {
  maximumVisitedStep.value = Math.max(maximumVisitedStep.value, index);
  currentStepIndex.value = index;
  clearErrors();
  await focusStep();
}

/**
 * Validates a wizard step and replaces visible inline errors.
 */
function validateStep(step: ProjectCreateStep): boolean {
  clearErrors();
  const result =
    step === "review"
      ? {
          ...validateProjectCreateStep(form, "details", usesTemplate.value),
          ...validateProjectCreateStep(form, "runtime", usesTemplate.value),
        }
      : validateProjectCreateStep(form, step, usesTemplate.value);
  Object.assign(errors, result);
  return Object.keys(result).length === 0;
}

/**
 * Removes one field error when the user starts correcting it.
 */
function clearFieldError(field: keyof ProjectCreateDraft): void {
  errors[field] = undefined;
  serverError.value = "";
}

/**
 * Removes all visible field errors.
 */
function clearErrors(): void {
  Object.assign(errors, {
    name: undefined,
    description: undefined,
    project_type: undefined,
    runtime_type: undefined,
    source_type: undefined,
    repository_url: undefined,
    branch: undefined,
    install_command: undefined,
    build_command: undefined,
    start_command: undefined,
    output_directory: undefined,
    healthcheck_url: undefined,
  });
}

/**
 * Focuses the current step heading for predictable keyboard navigation.
 */
async function focusStep(): Promise<void> {
  await nextTick();
  stepContent.value?.focus({ preventScroll: true });
  if (import.meta.client && window.scrollY > 160) {
    window.scrollTo({ top: 0, behavior: "smooth" });
  }
}

/**
 * Focuses the first invalid field after failed validation.
 */
async function focusFirstError(): Promise<void> {
  await nextTick();
  const firstField = Object.entries(errors).find(([, message]) =>
    Boolean(message),
  )?.[0];
  if (!firstField || !import.meta.client) return;
  document.querySelector<HTMLElement>(`[data-field="${firstField}"]`)?.focus();
}

/**
 * Creates the project through the existing API and clears the local draft.
 */
async function submit(): Promise<void> {
  if (pending.value || !canCreateProject.value) return;
  if (!validateStep("review")) {
    const detailsErrors = validateProjectCreateStep(
      form,
      "details",
      usesTemplate.value,
    );
    currentStepIndex.value = Object.keys(detailsErrors).length ? 1 : 2;
    await focusFirstError();
    return;
  }

  pending.value = true;
  serverError.value = "";
  try {
    const request = buildProjectCreateRequest(form, selectedTemplateId.value);
    const project = await api.request<Project>(request.path, {
      method: "POST",
      body: request.body,
      silent: true,
    });
    if (import.meta.client) sessionStorage.removeItem(DRAFT_STORAGE_KEY);
    await navigateTo(`/projects/${project.id}`);
  } catch (error: unknown) {
    serverError.value = requestErrorMessage(error);
  } finally {
    pending.value = false;
  }
}

/**
 * Clears the draft and returns to the projects list.
 */
async function cancelCreation(): Promise<void> {
  if (import.meta.client) sessionStorage.removeItem(DRAFT_STORAGE_KEY);
  await navigateTo("/projects");
}

/**
 * Extracts a safe backend error message for inline recovery.
 */
function requestErrorMessage(error: unknown): string {
  const payload = error as {
    data?: { detail?: unknown };
    response?: { _data?: { detail?: unknown }; status?: number };
  };
  const detail = payload.data?.detail ?? payload.response?._data?.detail;
  if (typeof detail === "string") {
    if (detail === "Project name already exists") {
      return copy.value.duplicateName;
    }
    return detail;
  }
  if (payload.response?.status === 403) {
    return copy.value.forbidden;
  }
  return copy.value.createError;
}

/**
 * Returns a consistent icon for a project template.
 */
function templateIcon(template: ProjectTemplate): Component {
  if (template.runtime_type === "docker_compose") return IconBrandDocker;
  if (isMinecraftRuntime(template.runtime_type)) return IconBox;
  if (template.project_type === "bot") return IconCode;
  if (template.runtime_type === "static") return IconFiles;
  return IconServer;
}

/**
 * Returns a concise localized explanation for known template presets.
 */
function templateDescription(template: ProjectTemplate): string {
  return (
    {
      fastapi: copy.value.templateFastapi,
      nuxt: copy.value.templateNuxt,
      "vue-vite": copy.value.templateVue,
      "react-vite": copy.value.templateReact,
      next: copy.value.templateNext,
      "node-api": copy.value.templateNode,
      "telegram-bot-python": copy.value.templateTelegram,
      "discord-bot-python": copy.value.templateDiscord,
      "static-site": copy.value.templateStatic,
      "docker-compose": copy.value.templateCompose,
      "minecraft-forge": copy.value.templateMinecraft,
    }[template.id] ?? template.description
  );
}

/**
 * Returns contextual guidance for the selected runtime.
 */
function runtimeHint(runtimeType: string): string {
  return (
    {
      docker: copy.value.hintDocker,
      docker_compose: copy.value.hintCompose,
      systemd: copy.value.hintSystemd,
      pm2: copy.value.hintPm2,
      static: copy.value.hintStatic,
      minecraft_forge: copy.value.hintMinecraft,
      minecraft_paper: copy.value.hintMinecraft,
      minecraft_purpur: copy.value.hintMinecraft,
      minecraft_spigot: copy.value.hintMinecraft,
    }[runtimeType] ?? copy.value.hintDefault
  );
}

/**
 * Returns a compact placeholder when an optional value is empty.
 */
function displayValue(value: string | null | undefined): string {
  return value?.trim() || copy.value.notSet;
}

function validateProjectCreateStep(
  draft: ProjectCreateDraft,
  step: ProjectCreateStep,
  usesTemplateValue: boolean,
): ProjectCreateErrors {
  return rawValidateProjectCreateStep(
    draft,
    step,
    usesTemplateValue,
    localeController.locale.value,
  );
}

function projectTypeLabel(value: string): string {
  return rawProjectTypeLabel(value, localeController.locale.value);
}
</script>

<template>
  <div class="create-page">
    <NuxtLink class="back-link" to="/projects">
      <IconArrowLeft :size="18" :stroke-width="1.8" />
      {{ copy.allProjects }}
    </NuxtLink>

    <PageHeader :title="copy.newProject" :description="copy.newDescription">
      <span v-if="hasDraft" class="draft-status">
        <IconCheck :size="16" :stroke-width="2" />
        {{ copy.draftSaved }}
      </span>
    </PageHeader>

    <section v-if="!canCreateProject" class="access-state panel" role="alert">
      <IconShieldCheck :size="30" :stroke-width="1.6" />
      <div>
        <h2>{{ copy.accessTitle }}</h2>
        <p>{{ copy.accessDescription }}</p>
      </div>
      <NuxtLink class="button-secondary" to="/projects">{{
        copy.backToProjects
      }}</NuxtLink>
    </section>

    <form v-else class="creation-layout" novalidate @submit.prevent="nextStep">
      <aside class="steps-panel panel" :aria-label="copy.creationSteps">
        <div class="steps-heading">
          <span>{{ copy.projectSetup }}</span>
          <strong>{{
            copy.progressOf
              .replace("{current}", String(currentStepIndex + 1))
              .replace("{total}", String(steps.length))
          }}</strong>
        </div>
        <nav>
          <button
            v-for="(step, index) in steps"
            :key="step.id"
            type="button"
            class="step-button"
            :class="{
              'step-button--active': index === currentStepIndex,
              'step-button--complete': index < currentStepIndex,
            }"
            :disabled="index > maximumVisitedStep"
            :aria-current="index === currentStepIndex ? 'step' : undefined"
            @click="goToStep(index)"
          >
            <span class="step-marker" aria-hidden="true">
              <IconCheck
                v-if="index < currentStepIndex"
                :size="16"
                :stroke-width="2.2"
              />
              <component
                :is="step.icon"
                v-else
                :size="17"
                :stroke-width="1.8"
              />
            </span>
            <span class="step-copy">
              <strong>{{ step.label }}</strong>
              <small>{{ step.description }}</small>
            </span>
          </button>
        </nav>

        <div class="steps-note">
          <IconInfoCircle :size="18" :stroke-width="1.7" />
          <p>{{ copy.noAutoDeploy }}</p>
        </div>
      </aside>

      <section class="form-panel panel">
        <div class="mobile-progress" aria-hidden="true">
          <span>{{ currentStepMeta.label }}</span>
          <strong>{{ currentStepIndex + 1 }} / {{ steps.length }}</strong>
          <div class="progress-track">
            <i
              :style="{
                width: `${((currentStepIndex + 1) / steps.length) * 100}%`,
              }"
            />
          </div>
        </div>

        <div
          ref="stepContent"
          class="step-content"
          tabindex="-1"
          :aria-labelledby="`step-title-${currentStep}`"
        >
          <template v-if="currentStep === 'template'">
            <header class="step-header">
              <span class="step-kicker">{{ copy.stepOne }}</span>
              <h2 :id="`step-title-${currentStep}`">
                {{ copy.chooseFoundation }}
              </h2>
              <p>{{ copy.chooseFoundationDescription }}</p>
            </header>

            <label v-if="(templates?.length ?? 0) > 6" class="template-search">
              <span class="sr-only">{{ copy.searchTemplates }}</span>
              <IconSearch :size="19" :stroke-width="1.7" />
              <input
                v-model="templateQuery"
                type="search"
                :placeholder="copy.searchPlaceholder"
              />
            </label>

            <div class="template-grid" :aria-label="copy.availableFoundations">
              <button
                type="button"
                class="template-card template-card--custom"
                :class="{
                  'template-card--selected': selectedTemplateId === 'custom',
                }"
                :aria-pressed="selectedTemplateId === 'custom'"
                @click="chooseTemplate(null)"
              >
                <span class="template-icon">
                  <IconSettings :size="23" :stroke-width="1.6" />
                </span>
                <span class="template-copy">
                  <strong>{{ copy.manualSetup }}</strong>
                  <small>{{ copy.manualSetupDescription }}</small>
                </span>
                <span class="template-badges">
                  <i>{{ copy.flexible }}</i>
                  <i>{{ copy.noStarterFiles }}</i>
                </span>
                <IconCheck
                  v-if="selectedTemplateId === 'custom'"
                  class="selected-check"
                  :size="18"
                  :stroke-width="2.2"
                />
              </button>

              <template v-if="templatesPending">
                <div
                  v-for="index in 5"
                  :key="`skeleton-${index}`"
                  class="template-skeleton"
                  aria-hidden="true"
                />
              </template>

              <template v-else>
                <button
                  v-for="template in filteredTemplates"
                  :key="template.id"
                  type="button"
                  class="template-card"
                  :class="{
                    'template-card--selected':
                      selectedTemplateId === template.id,
                  }"
                  :aria-pressed="selectedTemplateId === template.id"
                  @click="chooseTemplate(template)"
                >
                  <span class="template-icon">
                    <component
                      :is="templateIcon(template)"
                      :size="23"
                      :stroke-width="1.6"
                    />
                  </span>
                  <span class="template-copy">
                    <strong>{{ template.name }}</strong>
                    <small>{{ templateDescription(template) }}</small>
                  </span>
                  <span class="template-badges">
                    <i>{{ projectTypeLabel(template.project_type) }}</i>
                    <i>{{ runtimeTypeLabel(template.runtime_type) }}</i>
                  </span>
                  <IconCheck
                    v-if="selectedTemplateId === template.id"
                    class="selected-check"
                    :size="18"
                    :stroke-width="2.2"
                  />
                </button>
              </template>
            </div>

            <div
              v-if="templatesError"
              class="inline-state inline-state--warning"
              role="alert"
            >
              <IconAlertTriangle :size="20" :stroke-width="1.7" />
              <div>
                <strong>{{ copy.templatesError }}</strong>
                <p>{{ copy.templatesErrorDescription }}</p>
              </div>
              <button
                class="button-secondary button-compact"
                type="button"
                @click="() => refreshTemplates()"
              >
                <IconRefresh :size="17" :stroke-width="1.8" />
                {{ copy.retry }}
              </button>
            </div>

            <div
              v-else-if="
                !templatesPending && templateQuery && !filteredTemplates.length
              "
              class="inline-state"
            >
              <IconSearch :size="20" :stroke-width="1.7" />
              <div>
                <strong>{{ copy.templatesEmpty }}</strong>
                <p>{{ copy.templatesEmptyDescription }}</p>
              </div>
            </div>
          </template>

          <template v-else-if="currentStep === 'details'">
            <header class="step-header">
              <span class="step-kicker">{{ copy.stepTwo }}</span>
              <h2 :id="`step-title-${currentStep}`">
                {{ copy.projectAndSource }}
              </h2>
              <p>{{ copy.projectAndSourceDescription }}</p>
            </header>

            <div class="field-section">
              <div class="section-heading">
                <div>
                  <h3>{{ copy.basics }}</h3>
                  <p>{{ copy.basicsDescription }}</p>
                </div>
              </div>
              <div class="field-grid">
                <label class="field field--wide">
                  <span>{{ copy.name }} <b aria-hidden="true">*</b></span>
                  <input
                    id="project-name"
                    v-model="form.name"
                    data-field="name"
                    class="control"
                    :class="{ 'control--error': errors.name }"
                    required
                    minlength="2"
                    maxlength="120"
                    autocomplete="off"
                    :placeholder="copy.namePlaceholder"
                    :aria-invalid="Boolean(errors.name)"
                    :aria-describedby="
                      errors.name ? 'project-name-error' : 'project-name-help'
                    "
                    @input="clearFieldError('name')"
                  />
                  <small id="project-name-help">{{ copy.nameHint }}</small>
                  <small
                    v-if="errors.name"
                    id="project-name-error"
                    class="field-error"
                    role="alert"
                    >{{ errors.name }}</small
                  >
                </label>
                <label class="field field--wide">
                  <span>{{ copy.description }}</span>
                  <textarea
                    v-model="form.description"
                    data-field="description"
                    class="control"
                    :class="{ 'control--error': errors.description }"
                    rows="3"
                    maxlength="5000"
                    :placeholder="copy.descriptionPlaceholder"
                    :aria-invalid="Boolean(errors.description)"
                    @input="clearFieldError('description')"
                  />
                  <small>{{ copy.descriptionHint }}</small>
                  <small
                    v-if="errors.description"
                    class="field-error"
                    role="alert"
                    >{{ errors.description }}</small
                  >
                </label>
              </div>
            </div>

            <div class="field-section">
              <div class="section-heading">
                <div>
                  <h3>{{ copy.source }}</h3>
                  <p>{{ copy.sourceDescription }}</p>
                </div>
              </div>
              <div class="choice-grid">
                <label
                  class="choice-card"
                  :class="{
                    'choice-card--selected': form.source_type === 'git',
                  }"
                >
                  <input
                    v-model="form.source_type"
                    type="radio"
                    value="git"
                    @change="chooseSource('git')"
                  />
                  <IconGitBranch :size="22" :stroke-width="1.7" />
                  <span>
                    <strong>{{ copy.sourceGit }}</strong>
                    <small>{{ copy.sourceGitDescription }}</small>
                  </span>
                  <IconCheck
                    v-if="form.source_type === 'git'"
                    class="choice-check"
                    :size="17"
                    :stroke-width="2.2"
                  />
                </label>
                <label
                  class="choice-card"
                  :class="{
                    'choice-card--selected': form.source_type === 'files',
                  }"
                >
                  <input
                    v-model="form.source_type"
                    type="radio"
                    value="files"
                    @change="chooseSource('files')"
                  />
                  <IconFiles :size="22" :stroke-width="1.7" />
                  <span>
                    <strong>{{ copy.sourceFiles }}</strong>
                    <small>{{ copy.sourceFilesDescription }}</small>
                  </span>
                  <IconCheck
                    v-if="form.source_type === 'files'"
                    class="choice-check"
                    :size="17"
                    :stroke-width="2.2"
                  />
                </label>
              </div>

              <div v-if="form.source_type === 'git'" class="source-fields">
                <label class="field field--wide">
                  <span
                    >{{ copy.repositoryUrl }} <b aria-hidden="true">*</b></span
                  >
                  <input
                    v-model="form.repository_url"
                    data-field="repository_url"
                    class="control"
                    :class="{ 'control--error': errors.repository_url }"
                    type="url"
                    inputmode="url"
                    required
                    autocomplete="url"
                    placeholder="https://github.com/owner/repository.git"
                    :aria-invalid="Boolean(errors.repository_url)"
                    @input="clearFieldError('repository_url')"
                  />
                  <small>{{ copy.privateCredentialHint }}</small>
                  <small
                    v-if="errors.repository_url"
                    class="field-error"
                    role="alert"
                    >{{ errors.repository_url }}</small
                  >
                </label>
                <label class="field">
                  <span>{{ copy.branch }} <b aria-hidden="true">*</b></span>
                  <div class="input-with-icon">
                    <IconGitBranch :size="18" :stroke-width="1.7" />
                    <input
                      v-model="form.branch"
                      data-field="branch"
                      class="control"
                      :class="{ 'control--error': errors.branch }"
                      required
                      maxlength="255"
                      autocomplete="off"
                      placeholder="main"
                      :aria-invalid="Boolean(errors.branch)"
                      @input="clearFieldError('branch')"
                    />
                  </div>
                  <small
                    v-if="errors.branch"
                    class="field-error"
                    role="alert"
                    >{{ errors.branch }}</small
                  >
                </label>
              </div>

              <div v-else class="source-notice">
                <IconInfoCircle :size="20" :stroke-width="1.7" />
                <p>{{ copy.zipAfterCreation }}</p>
              </div>
            </div>
          </template>

          <template v-else-if="currentStep === 'runtime'">
            <header class="step-header">
              <span class="step-kicker">{{ copy.stepThree }}</span>
              <h2 :id="`step-title-${currentStep}`">{{ copy.runtimeBuild }}</h2>
              <p v-if="usesTemplate">
                {{ copy.runtimeTemplateDescription }}
              </p>
              <p v-else>
                {{ copy.runtimeManualDescription }}
              </p>
            </header>

            <template v-if="usesTemplate && selectedTemplate">
              <div class="selected-template-summary">
                <span class="template-icon template-icon--large">
                  <component
                    :is="templateIcon(selectedTemplate)"
                    :size="26"
                    :stroke-width="1.6"
                  />
                </span>
                <div>
                  <span>{{ copy.selectedTemplate }}</span>
                  <h3>{{ selectedTemplate.name }}</h3>
                  <p>{{ templateDescription(selectedTemplate) }}</p>
                </div>
                <button
                  class="button-secondary button-compact"
                  type="button"
                  @click="useManualConfiguration"
                >
                  <IconSettings :size="17" :stroke-width="1.8" />
                  {{ copy.configureManually }}
                </button>
              </div>

              <dl class="runtime-overview">
                <div>
                  <dt>{{ copy.projectType }}</dt>
                  <dd>{{ projectTypeLabel(selectedTemplate.project_type) }}</dd>
                </div>
                <div>
                  <dt>{{ copy.runtime }}</dt>
                  <dd>{{ runtimeTypeLabel(selectedTemplate.runtime_type) }}</dd>
                </div>
                <div>
                  <dt>{{ copy.install }}</dt>
                  <dd>
                    <code>{{
                      displayValue(selectedTemplate.install_command)
                    }}</code>
                  </dd>
                </div>
                <div>
                  <dt>{{ copy.build }}</dt>
                  <dd>
                    <code>{{
                      displayValue(selectedTemplate.build_command)
                    }}</code>
                  </dd>
                </div>
                <div>
                  <dt>{{ copy.startCommand }}</dt>
                  <dd>
                    <code>{{
                      displayValue(selectedTemplate.start_command)
                    }}</code>
                  </dd>
                </div>
                <div>
                  <dt>{{ copy.outputDirectory }}</dt>
                  <dd>
                    <code>{{
                      displayValue(selectedTemplate.output_directory)
                    }}</code>
                  </dd>
                </div>
              </dl>

              <div
                v-if="selectedTemplate.suggested_env.length"
                class="suggested-env"
              >
                <div>
                  <strong>{{ copy.variablesAfterCreation }}</strong>
                  <p>{{ copy.variablesDescription }}</p>
                </div>
                <span
                  v-for="key in selectedTemplate.suggested_env"
                  :key="key"
                  >{{ key }}</span
                >
              </div>
            </template>

            <template v-else>
              <div class="field-section">
                <div class="section-heading">
                  <div>
                    <h3>{{ copy.launchMethod }}</h3>
                    <p>{{ copy.launchMethodDescription }}</p>
                  </div>
                </div>
                <div class="field-grid">
                  <label class="field">
                    <span
                      >{{ copy.projectType }} <b aria-hidden="true">*</b></span
                    >
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
                  </label>
                  <label class="field">
                    <span>{{ copy.runtime }} <b aria-hidden="true">*</b></span>
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
                  </label>
                </div>
                <div class="runtime-hint">
                  <IconInfoCircle :size="19" :stroke-width="1.7" />
                  <p>{{ runtimeHint(form.runtime_type) }}</p>
                </div>
              </div>

              <div class="field-section">
                <div class="section-heading">
                  <div>
                    <h3>{{ copy.deployCommands }}</h3>
                    <p v-if="dockerRuntime">
                      {{ copy.dockerfileBuilds }}
                    </p>
                    <p v-else-if="composeRuntime">
                      {{ copy.composeBuilds }}
                    </p>
                    <p v-else-if="minecraftRuntime">
                      {{ copy.minecraftBuilds }}
                    </p>
                    <p v-else>
                      {{ copy.panelBuilds }}
                    </p>
                  </div>
                  <span v-if="hostBuildFieldsVisible" class="optional-badge">{{
                    copy.optional
                  }}</span>
                </div>
                <div
                  v-if="composeRuntime || minecraftRuntime"
                  class="runtime-hint"
                >
                  <IconInfoCircle :size="19" :stroke-width="1.7" />
                  <p v-if="composeRuntime">{{ copy.composeCommands }}</p>
                  <p v-else>{{ copy.minecraftCommands }}</p>
                </div>
                <div v-if="hostBuildFieldsVisible" class="field-grid">
                  <label class="field">
                    <span>{{
                      dockerRuntime
                        ? "Host pre-build: install"
                        : "Install command"
                    }}</span>
                    <input
                      v-model="form.install_command"
                      data-field="install_command"
                      class="control control--code"
                      :class="{ 'control--error': errors.install_command }"
                      maxlength="4000"
                      autocomplete="off"
                      placeholder="npm ci"
                      :aria-invalid="Boolean(errors.install_command)"
                      @input="clearFieldError('install_command')"
                    />
                    <small v-if="dockerRuntime">{{
                      copy.dockerInstallHint
                    }}</small>
                    <small
                      v-if="errors.install_command"
                      class="field-error"
                      role="alert"
                      >{{ errors.install_command }}</small
                    >
                  </label>
                  <label class="field">
                    <span>{{
                      dockerRuntime ? "Host pre-build: build" : "Build command"
                    }}</span>
                    <input
                      v-model="form.build_command"
                      data-field="build_command"
                      class="control control--code"
                      :class="{ 'control--error': errors.build_command }"
                      maxlength="4000"
                      autocomplete="off"
                      placeholder="npm run build"
                      :aria-invalid="Boolean(errors.build_command)"
                      @input="clearFieldError('build_command')"
                    />
                    <small v-if="dockerRuntime">{{
                      copy.dockerBuildHint
                    }}</small>
                    <small
                      v-if="errors.build_command"
                      class="field-error"
                      role="alert"
                      >{{ errors.build_command }}</small
                    >
                  </label>
                  <label v-if="startCommandVisible" class="field">
                    <span>{{
                      dockerRuntime
                        ? "Container command override"
                        : "Start command"
                    }}</span>
                    <input
                      v-model="form.start_command"
                      data-field="start_command"
                      class="control control--code"
                      :class="{ 'control--error': errors.start_command }"
                      maxlength="4000"
                      autocomplete="off"
                      placeholder="npm run start"
                      :aria-invalid="Boolean(errors.start_command)"
                      @input="clearFieldError('start_command')"
                    />
                    <small v-if="dockerRuntime">{{
                      copy.dockerStartHint
                    }}</small>
                    <small
                      v-if="errors.start_command"
                      class="field-error"
                      role="alert"
                      >{{ errors.start_command }}</small
                    >
                  </label>
                  <label v-if="outputDirectoryVisible" class="field">
                    <span>{{ copy.outputDirectory }}</span>
                    <input
                      v-model="form.output_directory"
                      data-field="output_directory"
                      class="control control--code"
                      :class="{ 'control--error': errors.output_directory }"
                      maxlength="512"
                      autocomplete="off"
                      :placeholder="copy.outputPlaceholder"
                      :aria-invalid="Boolean(errors.output_directory)"
                      @input="clearFieldError('output_directory')"
                    />
                    <small
                      v-if="errors.output_directory"
                      class="field-error"
                      role="alert"
                      >{{ errors.output_directory }}</small
                    >
                  </label>
                </div>
              </div>

              <div class="field-section">
                <div class="section-heading">
                  <div>
                    <h3>{{ copy.availability }}</h3>
                    <p>{{ copy.availabilityDescription }}</p>
                  </div>
                  <span class="optional-badge">{{ copy.optional }}</span>
                </div>
                <label class="field field--wide">
                  <span>{{ copy.healthcheck }} URL</span>
                  <input
                    v-model="form.healthcheck_url"
                    data-field="healthcheck_url"
                    class="control"
                    :class="{ 'control--error': errors.healthcheck_url }"
                    type="url"
                    inputmode="url"
                    autocomplete="url"
                    placeholder="https://api.example.com/health"
                    :aria-invalid="Boolean(errors.healthcheck_url)"
                    @input="clearFieldError('healthcheck_url')"
                  />
                  <small>{{ copy.healthHint }}</small>
                  <small
                    v-if="errors.healthcheck_url"
                    class="field-error"
                    role="alert"
                    >{{ errors.healthcheck_url }}</small
                  >
                </label>
              </div>
            </template>
          </template>

          <template v-else>
            <header class="step-header">
              <span class="step-kicker">{{ copy.stepFour }}</span>
              <h2 :id="`step-title-${currentStep}`">
                {{ copy.reviewConfiguration }}
              </h2>
              <p>{{ copy.reviewDescription }}</p>
            </header>

            <div class="review-grid">
              <article class="review-card">
                <header>
                  <span class="review-icon">
                    <IconFileCode :size="20" :stroke-width="1.7" />
                  </span>
                  <div>
                    <h3>{{ copy.stepProject }}</h3>
                    <p>{{ copy.mainInformation }}</p>
                  </div>
                  <button type="button" @click="editStep(1)">
                    {{ copy.edit }}
                  </button>
                </header>
                <dl>
                  <div>
                    <dt>{{ copy.name }}</dt>
                    <dd>{{ displayValue(form.name) }}</dd>
                  </div>
                  <div>
                    <dt>{{ copy.description }}</dt>
                    <dd>{{ displayValue(form.description) }}</dd>
                  </div>
                </dl>
              </article>

              <article class="review-card">
                <header>
                  <span class="review-icon">
                    <IconGitBranch :size="20" :stroke-width="1.7" />
                  </span>
                  <div>
                    <h3>{{ copy.source }}</h3>
                    <p>{{ sourceLabel }}</p>
                  </div>
                  <button type="button" @click="editStep(1)">
                    {{ copy.edit }}
                  </button>
                </header>
                <dl>
                  <div>
                    <dt>{{ copy.type }}</dt>
                    <dd>{{ sourceLabel }}</dd>
                  </div>
                  <template v-if="form.source_type === 'git'">
                    <div>
                      <dt>{{ copy.repository }}</dt>
                      <dd class="break-value">
                        {{ displayValue(form.repository_url) }}
                      </dd>
                    </div>
                    <div>
                      <dt>{{ copy.branch }}</dt>
                      <dd>{{ displayValue(form.branch) }}</dd>
                    </div>
                  </template>
                </dl>
              </article>

              <article class="review-card review-card--wide">
                <header>
                  <span class="review-icon">
                    <IconServer :size="20" :stroke-width="1.7" />
                  </span>
                  <div>
                    <h3>{{ copy.runtime }}</h3>
                    <p>
                      {{
                        selectedTemplate
                          ? selectedTemplate.name
                          : copy.manualSetup
                      }}
                    </p>
                  </div>
                  <button type="button" @click="editStep(2)">
                    {{ copy.edit }}
                  </button>
                </header>
                <dl class="review-runtime">
                  <div>
                    <dt>{{ copy.projectType }}</dt>
                    <dd>{{ projectTypeLabel(form.project_type) }}</dd>
                  </div>
                  <div>
                    <dt>{{ copy.runtime }}</dt>
                    <dd>{{ runtimeTypeLabel(form.runtime_type) }}</dd>
                  </div>
                  <div>
                    <dt>{{ copy.install }}</dt>
                    <dd>
                      <code>{{ displayValue(form.install_command) }}</code>
                    </dd>
                  </div>
                  <div>
                    <dt>{{ copy.build }}</dt>
                    <dd>
                      <code>{{ displayValue(form.build_command) }}</code>
                    </dd>
                  </div>
                  <div>
                    <dt>{{ copy.startCommand }}</dt>
                    <dd>
                      <code>{{ displayValue(form.start_command) }}</code>
                    </dd>
                  </div>
                  <div>
                    <dt>{{ copy.healthcheck }}</dt>
                    <dd class="break-value">
                      {{ displayValue(form.healthcheck_url) }}
                    </dd>
                  </div>
                </dl>
              </article>
            </div>

            <div class="creation-note">
              <IconRocket :size="21" :stroke-width="1.7" />
              <div>
                <strong>{{ copy.whatNext }}</strong>
                <p>{{ copy.whatNextDescription }}</p>
              </div>
            </div>

            <div v-if="serverError" class="submit-error" role="alert">
              <IconAlertTriangle :size="20" :stroke-width="1.8" />
              <div>
                <strong>{{ copy.notCreated }}</strong>
                <p>{{ serverError }}</p>
              </div>
            </div>
          </template>
        </div>

        <footer class="form-actions">
          <button
            v-if="currentStepIndex === 0"
            class="button-secondary"
            type="button"
            :disabled="pending"
            @click="cancelCreation"
          >
            {{ copy.cancel }}
          </button>
          <button
            v-else
            class="button-secondary"
            type="button"
            :disabled="pending"
            @click="previousStep"
          >
            <IconChevronLeft :size="18" :stroke-width="1.8" />
            {{ copy.back }}
          </button>

          <div class="action-context">
            <span v-if="currentStepIndex < steps.length - 1">
              {{
                copy.next.replace(
                  "{step}",
                  steps[currentStepIndex + 1]?.label || "",
                )
              }}
            </span>
            <button class="button-primary" type="submit" :disabled="pending">
              <template v-if="currentStepIndex === steps.length - 1">
                <IconRocket :size="18" :stroke-width="1.8" />
                {{ pending ? copy.creating : copy.createProject }}
              </template>
              <template v-else>
                {{ copy.continue }}
                <IconChevronRight :size="18" :stroke-width="1.8" />
              </template>
            </button>
          </div>
        </footer>
      </section>
    </form>
  </div>
</template>

<style scoped>
.create-page {
  width: min(1180px, 100%);
}

.back-link {
  display: inline-flex;
  min-height: 44px;
  align-items: center;
  gap: 0.45rem;
  margin-bottom: 0.65rem;
  color: var(--text-muted);
  font-size: 0.9rem;
  font-weight: 600;
  text-decoration: none;
  transition: color 180ms ease;
}

.back-link:hover {
  color: var(--text);
}

.draft-status {
  display: inline-flex;
  min-height: 36px;
  align-items: center;
  gap: 0.4rem;
  border: 1px solid #315c47;
  border-radius: 999px;
  background: #182b22;
  padding: 0.4rem 0.75rem;
  color: #8bd3a9;
  font-size: 0.8rem;
  font-weight: 650;
}

.creation-layout {
  display: grid;
  grid-template-columns: 248px minmax(0, 1fr);
  align-items: start;
  gap: 1rem;
  margin-top: 1.75rem;
}

.steps-panel {
  position: sticky;
  top: 1rem;
  padding: 0.75rem;
}

.steps-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0.45rem 0.55rem 0.75rem;
  color: var(--text-muted);
  font-size: 0.75rem;
}

.steps-heading strong {
  color: #d2d4d8;
  font-weight: 650;
}

.steps-panel nav {
  display: grid;
  gap: 0.2rem;
}

.step-button {
  display: grid;
  min-height: 64px;
  width: 100%;
  cursor: pointer;
  grid-template-columns: 32px minmax(0, 1fr);
  align-items: center;
  gap: 0.7rem;
  border: 1px solid transparent;
  border-radius: 8px;
  background: transparent;
  padding: 0.55rem;
  color: var(--text-muted);
  text-align: left;
  transition:
    border-color 180ms ease,
    background-color 180ms ease,
    color 180ms ease;
}

.step-button:hover:not(:disabled) {
  background: var(--surface-subtle);
  color: var(--text);
}

.step-button:disabled {
  cursor: default;
  opacity: 0.48;
}

.step-button--active {
  border-color: #65402f;
  background: #2b211d;
  color: var(--text);
}

.step-button--complete {
  color: #c9cbd0;
}

.step-marker {
  display: inline-flex;
  width: 32px;
  height: 32px;
  align-items: center;
  justify-content: center;
  border: 1px solid #3b3e44;
  border-radius: 8px;
  background: var(--surface-base);
}

.step-button--active .step-marker {
  border-color: var(--accent);
  background: #3a241b;
  color: #ff9d73;
}

.step-button--complete .step-marker {
  border-color: #315c47;
  background: #182b22;
  color: #8bd3a9;
}

.step-copy {
  display: grid;
  min-width: 0;
  gap: 0.15rem;
}

.step-copy strong {
  color: inherit;
  font-size: 0.88rem;
}

.step-copy small {
  overflow: hidden;
  color: var(--text-muted);
  font-size: 0.72rem;
  font-weight: 400;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.steps-note {
  display: grid;
  grid-template-columns: 18px minmax(0, 1fr);
  gap: 0.55rem;
  margin-top: 0.75rem;
  border-top: 1px solid var(--border);
  padding: 1rem 0.55rem 0.45rem;
  color: var(--text-muted);
}

.steps-note p {
  margin: 0;
  font-size: 0.75rem;
  line-height: 1.55;
}

.form-panel {
  min-width: 0;
}

.mobile-progress {
  display: none;
}

.step-content {
  min-height: 570px;
  padding: clamp(1.15rem, 3vw, 2rem);
  outline: none;
}

.step-header {
  max-width: 690px;
  margin-bottom: 1.75rem;
}

.step-header h2,
.step-header p,
.step-header span {
  margin: 0;
}

.step-kicker {
  display: block;
  margin-bottom: 0.45rem !important;
  color: #ff9467;
  font-size: 0.72rem;
  font-weight: 750;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.step-header h2 {
  font-size: clamp(1.35rem, 3vw, 1.75rem);
  letter-spacing: -0.025em;
}

.step-header p {
  margin-top: 0.45rem;
  color: var(--text-muted);
  line-height: 1.55;
}

.template-search {
  display: flex;
  min-height: 46px;
  align-items: center;
  gap: 0.65rem;
  margin-bottom: 1rem;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--surface-base);
  padding: 0 0.8rem;
  color: var(--text-muted);
}

.template-search:focus-within {
  border-color: var(--accent);
}

.template-search input {
  width: 100%;
  border: 0;
  outline: 0;
  background: transparent;
  color: var(--text);
  font: inherit;
}

.template-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0.75rem;
}

.template-card {
  position: relative;
  display: grid;
  min-height: 146px;
  cursor: pointer;
  grid-template-columns: 40px minmax(0, 1fr);
  grid-template-rows: 1fr auto;
  gap: 0.7rem 0.8rem;
  border: 1px solid var(--border);
  border-radius: 10px;
  background: var(--surface-base);
  padding: 1rem;
  color: var(--text);
  text-align: left;
  transition:
    border-color 180ms ease,
    background-color 180ms ease;
}

.template-card:hover {
  border-color: #555860;
  background: #15171a;
}

.template-card--selected {
  border-color: var(--accent);
  background: #261d19;
  box-shadow: 0 0 0 1px rgb(244 106 50 / 20%);
}

.template-card--custom {
  border-style: dashed;
}

.template-icon {
  display: inline-flex;
  width: 40px;
  height: 40px;
  align-items: center;
  justify-content: center;
  border: 1px solid #3a3d43;
  border-radius: 9px;
  background: var(--surface-subtle);
  color: #d6d8dc;
}

.template-icon--large {
  width: 48px;
  height: 48px;
}

.template-card--selected .template-icon {
  border-color: #6a422f;
  background: #35231b;
  color: #ff9d73;
}

.template-copy {
  display: grid;
  align-content: start;
  gap: 0.28rem;
  padding-right: 1rem;
}

.template-copy strong {
  font-size: 0.94rem;
}

.template-copy small {
  color: var(--text-muted);
  font-size: 0.79rem;
  font-weight: 400;
  line-height: 1.45;
}

.template-badges {
  display: flex;
  grid-column: 1 / -1;
  flex-wrap: wrap;
  gap: 0.35rem;
}

.template-badges i,
.optional-badge {
  border: 1px solid #373a40;
  border-radius: 999px;
  background: #1d1f22;
  padding: 0.25rem 0.45rem;
  color: #b8bbc1;
  font-size: 0.67rem;
  font-style: normal;
  font-weight: 600;
}

.selected-check {
  position: absolute;
  top: 0.8rem;
  right: 0.8rem;
  color: #ff9467;
}

.template-skeleton {
  min-height: 146px;
  border: 1px solid var(--border);
  border-radius: 10px;
  background:
    linear-gradient(90deg, transparent, rgb(255 255 255 / 3%), transparent),
    var(--surface-base);
  background-size: 200% 100%;
  animation: skeleton 1.4s ease-in-out infinite;
}

@keyframes skeleton {
  from {
    background-position: 200% 0;
  }
  to {
    background-position: -200% 0;
  }
}

.inline-state {
  display: grid;
  grid-template-columns: 20px minmax(0, 1fr) auto;
  align-items: center;
  gap: 0.75rem;
  margin-top: 1rem;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--surface-base);
  padding: 0.85rem;
  color: var(--text-muted);
}

.inline-state--warning {
  border-color: #624d29;
  background: #292317;
  color: #e5b65e;
}

.inline-state strong,
.inline-state p {
  margin: 0;
}

.inline-state strong {
  color: var(--text);
  font-size: 0.86rem;
}

.inline-state p {
  margin-top: 0.15rem;
  color: var(--text-muted);
  font-size: 0.78rem;
}

.button-compact {
  min-height: 44px;
  padding: 0.45rem 0.7rem;
  font-size: 0.78rem;
}

.field-section + .field-section {
  margin-top: 1.75rem;
  border-top: 1px solid var(--border);
  padding-top: 1.75rem;
}

.section-heading {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 1rem;
  margin-bottom: 1rem;
}

.section-heading h3,
.section-heading p {
  margin: 0;
}

.section-heading h3 {
  font-size: 1rem;
}

.section-heading p {
  margin-top: 0.2rem;
  color: var(--text-muted);
  font-size: 0.8rem;
}

.field-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 1rem;
}

.field {
  display: grid;
  align-content: start;
  gap: 0.42rem;
  color: #d8d9dc;
  font-size: 0.84rem;
  font-weight: 650;
}

.field--wide {
  grid-column: 1 / -1;
}

.field > span b {
  color: #ff9467;
}

.field small {
  color: var(--text-muted);
  font-size: 0.74rem;
  font-weight: 400;
  line-height: 1.4;
}

.field .field-error {
  color: #f2a1a7;
}

.control--error {
  border-color: var(--danger);
}

.control--code,
code {
  font-family: "IBM Plex Mono", "SFMono-Regular", Consolas, monospace;
}

textarea.control {
  min-height: 92px;
  resize: vertical;
}

.choice-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0.75rem;
}

.choice-card {
  position: relative;
  display: grid;
  min-height: 94px;
  cursor: pointer;
  grid-template-columns: 30px minmax(0, 1fr);
  align-items: start;
  gap: 0.7rem;
  border: 1px solid var(--border);
  border-radius: 9px;
  background: var(--surface-base);
  padding: 1rem;
  color: #c9cbd0;
  transition:
    border-color 180ms ease,
    background-color 180ms ease;
}

.choice-card:hover {
  border-color: #52555c;
}

.choice-card--selected {
  border-color: var(--accent);
  background: #261d19;
  color: #ff9d73;
}

.choice-card input {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  opacity: 0;
}

.choice-card span {
  display: grid;
  gap: 0.25rem;
}

.choice-card strong {
  color: var(--text);
  font-size: 0.88rem;
}

.choice-card small {
  color: var(--text-muted);
  font-size: 0.76rem;
  font-weight: 400;
  line-height: 1.4;
}

.choice-check {
  position: absolute;
  top: 0.75rem;
  right: 0.75rem;
}

.source-fields {
  display: grid;
  grid-template-columns: minmax(0, 1.55fr) minmax(170px, 0.45fr);
  gap: 1rem;
  margin-top: 1rem;
}

.input-with-icon {
  position: relative;
}

.input-with-icon > svg {
  position: absolute;
  top: 50%;
  left: 0.75rem;
  z-index: 1;
  transform: translateY(-50%);
  color: var(--text-muted);
}

.input-with-icon .control {
  padding-left: 2.35rem;
}

.source-notice,
.runtime-hint,
.creation-note {
  display: grid;
  grid-template-columns: 20px minmax(0, 1fr);
  gap: 0.7rem;
  margin-top: 1rem;
  border: 1px solid #384552;
  border-radius: 8px;
  background: #182129;
  padding: 0.85rem;
  color: #91b8da;
}

.source-notice p,
.runtime-hint p,
.creation-note p {
  margin: 0;
  color: #b1bdc8;
  font-size: 0.8rem;
  line-height: 1.5;
}

.selected-template-summary {
  display: grid;
  grid-template-columns: 48px minmax(0, 1fr) auto;
  align-items: center;
  gap: 0.9rem;
  border: 1px solid #65402f;
  border-radius: 10px;
  background: #261d19;
  padding: 1rem;
}

.selected-template-summary h3,
.selected-template-summary p,
.selected-template-summary span {
  margin: 0;
}

.selected-template-summary > div > span {
  color: #d78966;
  font-size: 0.7rem;
  font-weight: 700;
  text-transform: uppercase;
}

.selected-template-summary h3 {
  margin-top: 0.15rem;
  font-size: 1rem;
}

.selected-template-summary p {
  margin-top: 0.18rem;
  color: var(--text-muted);
  font-size: 0.78rem;
}

.runtime-overview {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  margin: 1rem 0 0;
  overflow: hidden;
  border: 1px solid var(--border);
  border-radius: 10px;
  background: var(--surface-base);
}

.runtime-overview div {
  min-width: 0;
  border-right: 1px solid var(--border);
  border-bottom: 1px solid var(--border);
  padding: 0.9rem;
}

.runtime-overview div:nth-child(2n) {
  border-right: 0;
}

.runtime-overview div:nth-last-child(-n + 2) {
  border-bottom: 0;
}

.runtime-overview dt,
.runtime-overview dd {
  margin: 0;
}

.runtime-overview dt {
  color: var(--text-muted);
  font-size: 0.7rem;
  font-weight: 650;
}

.runtime-overview dd {
  margin-top: 0.35rem;
  color: #e1e2e5;
  font-size: 0.82rem;
  overflow-wrap: anywhere;
}

.runtime-overview code {
  font-size: 0.76rem;
}

.suggested-env {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 0.5rem;
  margin-top: 1rem;
  border: 1px solid var(--border);
  border-radius: 9px;
  background: var(--surface-base);
  padding: 0.9rem;
}

.suggested-env > div {
  min-width: min(100%, 230px);
  margin-right: auto;
}

.suggested-env strong,
.suggested-env p {
  margin: 0;
}

.suggested-env strong {
  font-size: 0.82rem;
}

.suggested-env p {
  margin-top: 0.18rem;
  color: var(--text-muted);
  font-size: 0.74rem;
}

.suggested-env > span {
  border: 1px solid #41444a;
  border-radius: 6px;
  background: #24262a;
  padding: 0.3rem 0.45rem;
  color: #d7d9dc;
  font-family: "IBM Plex Mono", "SFMono-Regular", Consolas, monospace;
  font-size: 0.7rem;
}

.review-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0.75rem;
}

.review-card {
  min-width: 0;
  overflow: hidden;
  border: 1px solid var(--border);
  border-radius: 10px;
  background: var(--surface-base);
}

.review-card--wide {
  grid-column: 1 / -1;
}

.review-card header {
  display: grid;
  grid-template-columns: 36px minmax(0, 1fr) auto;
  align-items: center;
  gap: 0.65rem;
  border-bottom: 1px solid var(--border);
  padding: 0.75rem;
}

.review-icon {
  display: inline-flex;
  width: 36px;
  height: 36px;
  align-items: center;
  justify-content: center;
  border: 1px solid #3a3d43;
  border-radius: 8px;
  background: var(--surface-subtle);
  color: #d6d8dc;
}

.review-card h3,
.review-card p {
  margin: 0;
}

.review-card h3 {
  font-size: 0.87rem;
}

.review-card p {
  margin-top: 0.1rem;
  color: var(--text-muted);
  font-size: 0.7rem;
}

.review-card header button {
  min-height: 44px;
  cursor: pointer;
  border: 0;
  background: transparent;
  color: #ff9467;
  font-size: 0.76rem;
  font-weight: 650;
}

.review-card dl {
  display: grid;
  gap: 0;
  margin: 0;
}

.review-card dl > div {
  display: grid;
  grid-template-columns: 105px minmax(0, 1fr);
  gap: 0.75rem;
  border-bottom: 1px solid #26282c;
  padding: 0.7rem 0.8rem;
}

.review-card dl > div:last-child {
  border-bottom: 0;
}

.review-card dt,
.review-card dd {
  min-width: 0;
  margin: 0;
  font-size: 0.77rem;
}

.review-card dt {
  color: var(--text-muted);
}

.review-card dd {
  color: #dddfe2;
  text-align: right;
}

.review-card code {
  font-size: 0.72rem;
}

.review-runtime {
  grid-template-columns: repeat(2, minmax(0, 1fr));
}

.review-runtime > div:nth-last-child(2) {
  border-bottom: 0;
}

.review-runtime > div:nth-child(odd) {
  border-right: 1px solid #26282c;
}

.break-value {
  overflow-wrap: anywhere;
}

.creation-note {
  margin-top: 1rem;
  color: #ff9d73;
  border-color: #65402f;
  background: #261d19;
}

.creation-note strong {
  color: var(--text);
  font-size: 0.84rem;
}

.creation-note p {
  margin-top: 0.2rem;
}

.submit-error {
  display: grid;
  grid-template-columns: 20px minmax(0, 1fr);
  gap: 0.7rem;
  margin-top: 1rem;
  border: 1px solid #73363b;
  border-radius: 8px;
  background: #321d20;
  padding: 0.85rem;
  color: #f2a1a7;
}

.submit-error strong,
.submit-error p {
  margin: 0;
}

.submit-error strong {
  color: #ffd4d7;
  font-size: 0.84rem;
}

.submit-error p {
  margin-top: 0.18rem;
  font-size: 0.78rem;
}

.form-actions {
  position: sticky;
  bottom: 0;
  z-index: 5;
  display: flex;
  min-height: 76px;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
  border-top: 1px solid var(--border);
  border-radius: 0 0 12px 12px;
  background: rgb(24 26 29 / 96%);
  padding: 0.9rem clamp(1.15rem, 3vw, 2rem);
  backdrop-filter: blur(10px);
}

.action-context {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  margin-left: auto;
}

.action-context > span {
  color: var(--text-muted);
  font-size: 0.74rem;
}

.access-state {
  display: grid;
  max-width: 760px;
  grid-template-columns: 40px minmax(0, 1fr) auto;
  align-items: center;
  gap: 1rem;
  margin-top: 1.75rem;
  padding: 1.2rem;
  color: #e0a86e;
}

.access-state h2,
.access-state p {
  margin: 0;
}

.access-state h2 {
  color: var(--text);
  font-size: 1rem;
}

.access-state p {
  margin-top: 0.2rem;
  color: var(--text-muted);
  font-size: 0.82rem;
}

@media (max-width: 940px) {
  .creation-layout {
    grid-template-columns: 210px minmax(0, 1fr);
  }

  .steps-panel {
    padding: 0.6rem;
  }

  .step-copy small {
    display: none;
  }

  .template-grid {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 760px) {
  .creation-layout {
    display: block;
    margin-top: 1.25rem;
  }

  .steps-panel {
    display: none;
  }

  .mobile-progress {
    display: grid;
    grid-template-columns: 1fr auto;
    gap: 0.35rem 1rem;
    border-bottom: 1px solid var(--border);
    padding: 0.85rem 1rem;
    color: var(--text-muted);
    font-size: 0.76rem;
  }

  .mobile-progress strong {
    color: #d5d7db;
  }

  .progress-track {
    grid-column: 1 / -1;
    height: 3px;
    overflow: hidden;
    border-radius: 999px;
    background: #2b2d31;
  }

  .progress-track i {
    display: block;
    height: 100%;
    border-radius: inherit;
    background: var(--accent);
    transition: width 180ms ease;
  }

  .step-content {
    min-height: 0;
  }

  .source-fields {
    grid-template-columns: 1fr;
  }

  .selected-template-summary {
    grid-template-columns: 48px minmax(0, 1fr);
  }

  .selected-template-summary .button-compact {
    grid-column: 1 / -1;
  }
}

@media (max-width: 620px) {
  .draft-status {
    align-self: flex-start;
  }

  .step-content {
    padding: 1rem;
  }

  .step-header {
    margin-bottom: 1.35rem;
  }

  .template-grid,
  .field-grid,
  .choice-grid,
  .runtime-overview,
  .review-grid,
  .review-runtime {
    grid-template-columns: 1fr;
  }

  .template-card {
    min-height: 138px;
  }

  .field--wide,
  .review-card--wide {
    grid-column: auto;
  }

  .runtime-overview div,
  .runtime-overview div:nth-child(2n),
  .runtime-overview div:nth-last-child(-n + 2) {
    border-right: 0;
    border-bottom: 1px solid var(--border);
  }

  .runtime-overview div:last-child {
    border-bottom: 0;
  }

  .review-runtime > div:nth-child(odd) {
    border-right: 0;
  }

  .review-runtime > div:nth-last-child(2) {
    border-bottom: 1px solid #26282c;
  }

  .inline-state {
    grid-template-columns: 20px minmax(0, 1fr);
  }

  .inline-state .button-compact {
    grid-column: 1 / -1;
  }

  .form-actions {
    min-height: 72px;
    padding: 0.75rem 1rem;
  }

  .action-context {
    flex: 1;
  }

  .action-context > span {
    display: none;
  }

  .action-context .button-primary {
    flex: 1;
  }

  .access-state {
    grid-template-columns: 36px minmax(0, 1fr);
  }

  .access-state .button-secondary {
    grid-column: 1 / -1;
  }
}

@media (max-width: 390px) {
  .form-actions {
    align-items: stretch;
    flex-direction: column-reverse;
  }

  .form-actions > .button-secondary {
    width: 100%;
  }

  .action-context {
    width: 100%;
  }
}
</style>
