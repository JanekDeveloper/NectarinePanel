<script setup lang="ts">
const api = useApi();
const toasts = useToastStore();
const { t } = useLocale();
const ownerId = ref("");
const token = ref("");
const twoFactor = ref(false);
const message = ref("");
const { data: status, refresh } = await useAsyncData("telegram-status", () =>
  api.request<{
    configured: boolean;
    owner_id: string | null;
    two_factor_enabled: boolean;
  }>("/settings/telegram/status"),
);
watchEffect(() => {
  if (status.value?.owner_id) ownerId.value = status.value.owner_id;
  twoFactor.value = status.value?.two_factor_enabled ?? false;
});

async function save(): Promise<void> {
  await api.request("/settings/telegram.owner_id", {
    method: "PUT",
    body: { value: ownerId.value },
  });
  if (token.value) {
    await api.request("/settings/telegram.bot_token", {
      method: "PUT",
      body: { value: token.value },
    });
  }
  await api.request("/settings/telegram.two_factor_enabled", {
    method: "PUT",
    body: { value: String(twoFactor.value) },
  });
  token.value = "";
  message.value = t("telegram.savedRestart");
  toasts.add(t("telegram.saved"), "success");
  await refresh();
}
</script>

<template>
  <PageHeader title="Telegram" :description="t('telegram.description')" />
  <form class="settings panel" @submit.prevent="save">
    <p class="status">
      {{ t("common.status") }}:
      <strong>{{
        status?.configured
          ? t("telegram.configured")
          : t("telegram.notConfigured")
      }}</strong>
    </p>
    <label>
      Telegram owner ID
      <input v-model="ownerId" class="control" inputmode="numeric" required />
    </label>
    <label>
      Bot token
      <input
        v-model="token"
        class="control"
        type="password"
        autocomplete="new-password"
        :placeholder="t('telegram.keepToken')"
      />
    </label>
    <label class="checkbox">
      <input v-model="twoFactor" type="checkbox" />
      {{ t("telegram.twoFactor") }}
    </label>
    <button class="button-primary" type="submit">{{ t("common.save") }}</button>
    <p v-if="message" class="success" aria-live="polite">{{ message }}</p>
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
.checkbox {
  display: flex;
  min-height: 44px;
  align-items: center;
}
.status,
.success {
  margin: 0;
}
.status {
  color: var(--text-muted);
}
.success {
  color: #70d49d;
}
</style>
