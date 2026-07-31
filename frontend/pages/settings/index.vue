<script setup lang="ts">
import { localeOptions, type PanelLocale } from "~/locales/messages";

const api = useApi();
const localeController = useLocale();
const toasts = useToastStore();
const locale = ref<PanelLocale>("ru");
const disk = ref("90");
const memory = ref("90");
const cloudflareToken = ref("");
const saving = ref(false);
const { data: status } = await useAsyncData("panel-settings-status", () =>
  api.request<{
    locale: PanelLocale;
    disk_alert_percent: number;
    memory_alert_percent: number;
    cloudflare_configured: boolean;
  }>("/settings/status"),
);
watchEffect(() => {
  if (!status.value) return;
  locale.value = status.value.locale;
  disk.value = String(status.value.disk_alert_percent);
  memory.value = String(status.value.memory_alert_percent);
});

async function save(): Promise<void> {
  saving.value = true;
  try {
    const requests = [
      api.request("/settings/panel.locale", {
        method: "PUT",
        body: { value: locale.value },
      }),
      api.request("/settings/alerts.disk_percent", {
        method: "PUT",
        body: { value: disk.value },
      }),
      api.request("/settings/alerts.memory_percent", {
        method: "PUT",
        body: { value: memory.value },
      }),
    ];
    if (cloudflareToken.value) {
      requests.push(
        api.request("/settings/cloudflare.api_token", {
          method: "PUT",
          body: { value: cloudflareToken.value },
        }),
      );
    }
    await Promise.all(requests);
    localeController.apply(locale.value);
    cloudflareToken.value = "";
    toasts.add(localeController.t("settings.saved"), "success");
  } finally {
    saving.value = false;
  }
}
</script>

<template>
  <PageHeader
    :title="localeController.t('settings.title')"
    :description="localeController.t('settings.description')"
  />
  <form class="settings panel" @submit.prevent="save">
    <label
      >{{ localeController.t("language.label")
      }}<select v-model="locale" class="control">
        <option
          v-for="option in localeOptions"
          :key="option.value"
          :value="option.value"
        >
          {{ option.label }}
        </option>
      </select></label
    >
    <label
      >{{ localeController.t("settings.diskThreshold")
      }}<input v-model="disk" class="control" type="number" min="1" max="100"
    /></label>
    <label
      >{{ localeController.t("settings.memoryThreshold")
      }}<input v-model="memory" class="control" type="number" min="1" max="100"
    /></label>
    <section class="experimental">
      <strong>{{ localeController.t("settings.cloudflareTitle") }}</strong>
      <p>{{ localeController.t("settings.cloudflareDescription") }}</p>
      <label>
        {{ localeController.t("settings.cloudflareToken") }}
        <input
          v-model="cloudflareToken"
          class="control"
          type="password"
          autocomplete="new-password"
          :placeholder="
            status?.cloudflare_configured
              ? localeController.t('settings.cloudflareConfigured')
              : 'API token'
          "
        />
      </label>
    </section>
    <button class="button-primary" type="submit" :disabled="saving">
      {{
        saving
          ? localeController.t("common.saving")
          : localeController.t("common.save")
      }}
    </button>
  </form>
</template>

<style scoped>
.settings {
  display: grid;
  max-width: 680px;
  gap: 1rem;
  margin-top: 2rem;
  padding: 1.2rem;
}
label {
  display: grid;
  gap: 0.4rem;
  font-size: 0.82rem;
  font-weight: 600;
}
.experimental {
  display: grid;
  gap: 0.65rem;
  border: 1px solid #76502e;
  border-radius: 8px;
  padding: 0.9rem;
}
.experimental p {
  margin: 0;
  color: var(--text-muted);
  font-size: 0.82rem;
}
</style>
