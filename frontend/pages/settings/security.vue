<script setup lang="ts">
const api = useApi();
const auth = useAuthStore();
const { t } = useLocale();
const pending = ref(false);
const revokeOpen = ref(false);

async function revoke(): Promise<void> {
  revokeOpen.value = false;
  pending.value = true;
  await api.request("/settings/security/revoke-sessions", { method: "POST" });
  auth.clear();
  await navigateTo("/login");
}
</script>

<template>
  <PageHeader
    :title="t('security.title')"
    :description="t('security.description')"
  />
  <section class="panel security">
    <div>
      <h2>{{ t("security.revokeTitle") }}</h2>
      <p>{{ t("security.revokeDescription") }}</p>
    </div>
    <button
      class="button-danger"
      type="button"
      :disabled="pending"
      @click="revokeOpen = true"
    >
      {{ pending ? t("security.revoking") : t("security.revokeAction") }}
    </button>
  </section>
  <ConfirmDialog
    :open="revokeOpen"
    :title="t('security.revokeConfirmTitle')"
    :message="t('security.revokeConfirmMessage')"
    :confirm-label="t('security.revokeConfirm')"
    danger
    @cancel="revokeOpen = false"
    @confirm="revoke"
  />
</template>

<style scoped>
.security {
  display: flex;
  max-width: 760px;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
  margin-top: 2rem;
  padding: 1.2rem;
}
h2,
p {
  margin: 0;
}
p {
  margin-top: 0.35rem;
  color: var(--text-muted);
}
@media (max-width: 620px) {
  .security {
    align-items: stretch;
    flex-direction: column;
  }
}
</style>
