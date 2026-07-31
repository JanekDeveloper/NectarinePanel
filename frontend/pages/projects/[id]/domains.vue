<script setup lang="ts">
import { IconGlobe, IconPlus, IconRefresh, IconTrash } from "@tabler/icons-vue";
import type { Domain, Project } from "~/types/api";

const route = useRoute();
const projectId = String(route.params.id);
const api = useApi();
const toasts = useToastStore();
const { t, dateTime } = useLocale();
const form = reactive({ hostname: "", upstream_port: 18000, issue_ssl: true });
const pending = ref(false);
const deletePending = ref(false);
const errorMessage = ref("");
const deletingDomain = ref<Domain | null>(null);
const renewingDomain = ref<Domain | null>(null);
const { data: project } = await useAsyncData(
  `project-domain-${projectId}`,
  () => api.request<Project>(`/projects/${projectId}`),
);
const { data, refresh } = await useAsyncData(`domains-${projectId}`, () =>
  api.request<Domain[]>(`/projects/${projectId}/domains`),
);
const isStaticRuntime = computed(
  () => project.value?.runtime_type === "static",
);

async function addDomain(): Promise<void> {
  pending.value = true;
  errorMessage.value = "";
  try {
    await api.request(`/projects/${projectId}/domains`, {
      method: "POST",
      body: {
        hostname: form.hostname,
        issue_ssl: form.issue_ssl,
        is_primary: !data.value?.length,
        ...(isStaticRuntime.value ? {} : { upstream_port: form.upstream_port }),
      },
    });
    form.hostname = "";
    await refresh();
  } catch (error: unknown) {
    const detail = (error as { data?: { detail?: string } }).data?.detail;
    errorMessage.value = detail || t("domains.dnsError");
  } finally {
    pending.value = false;
  }
}

async function removeDomain(confirmation: string): Promise<void> {
  if (!deletingDomain.value || deletePending.value) return;
  const domain = deletingDomain.value;
  deletePending.value = true;
  deletingDomain.value = null;
  try {
    await api.request(`/projects/${projectId}/domains/${domain.id}`, {
      method: "DELETE",
      body: { confirm_hostname: confirmation },
    });
    await refresh();
  } catch {
    errorMessage.value = t("domains.deleteError");
    deletingDomain.value = domain;
  } finally {
    deletePending.value = false;
  }
}

async function renewCertificate(confirmation: string): Promise<void> {
  if (!renewingDomain.value || pending.value) return;
  const domain = renewingDomain.value;
  pending.value = true;
  renewingDomain.value = null;
  try {
    await api.request(`/projects/${projectId}/domains/${domain.id}/renew`, {
      method: "POST",
      body: { confirm_hostname: confirmation },
    });
    await refresh();
    toasts.add(
      t("domains.renewQueued", { hostname: domain.hostname }),
      "success",
    );
  } catch {
    renewingDomain.value = domain;
  } finally {
    pending.value = false;
  }
}

function expiryLabel(value: string | null): string {
  if (!value) return t("domains.expiryUnknown");
  return t("domains.expiresOn", {
    date: dateTime(value, { dateStyle: "medium" }),
  });
}
</script>

<template>
  <PageHeader
    :title="t('domains.title')"
    :description="t('domains.description')"
  >
    <button class="button-secondary" type="button" @click="() => refresh()">
      <IconRefresh :size="18" /> {{ t("common.refresh") }}
    </button>
  </PageHeader>
  <ProjectNav :project-id="projectId" />
  <form class="domain-form panel" @submit.prevent="addDomain">
    <label
      ><span>{{ t("domains.domain") }}</span
      ><input
        v-model="form.hostname"
        class="control"
        required
        placeholder="app.example.com"
    /></label>
    <label v-if="!isStaticRuntime">
      <span>{{ t("domains.localPort") }}</span>
      <input
        v-model.number="form.upstream_port"
        class="control"
        type="number"
        min="1024"
        max="65535"
        required
      />
    </label>
    <label class="checkbox"
      ><input v-model="form.issue_ssl" type="checkbox" />
      {{ t("domains.issueSsl") }}</label
    >
    <button class="button-primary" type="submit" :disabled="pending">
      <IconPlus :size="18" />
      {{ pending ? t("domains.checkingDns") : t("cron.add") }}
    </button>
    <p v-if="errorMessage" class="error" role="alert">{{ errorMessage }}</p>
  </form>
  <div class="domain-list">
    <article v-for="domain in data" :key="domain.id">
      <IconGlobe :size="21" :stroke-width="1.7" />
      <div>
        <strong>{{ domain.hostname }}</strong>
        <span>{{
          domain.upstream_port
            ? `127.0.0.1:${domain.upstream_port}`
            : "static files"
        }}</span>
      </div>
      <span class="ssl">
        {{ domain.ssl_status }}
        <small v-if="domain.ssl_status !== 'disabled'">{{
          expiryLabel(domain.certificate_expires_at)
        }}</small>
      </span>
      <button
        v-if="domain.ssl_status !== 'disabled'"
        type="button"
        :aria-label="t('domains.renewSslAria', { hostname: domain.hostname })"
        @click="renewingDomain = domain"
      >
        <IconRefresh :size="19" />
      </button>
      <span v-else class="renew-placeholder" aria-hidden="true" />
      <button
        type="button"
        :aria-label="t('cron.deleteAria', { name: domain.hostname })"
        @click="deletingDomain = domain"
      >
        <IconTrash :size="19" />
      </button>
    </article>
    <p v-if="!data?.length" class="empty">{{ t("domains.empty") }}</p>
  </div>
  <ConfirmDialog
    :open="Boolean(deletingDomain)"
    :title="t('domains.deleteTitle')"
    :message="
      t('domains.deleteMessage', { hostname: deletingDomain?.hostname || '' })
    "
    :confirm-label="t('common.delete')"
    danger
    :expected-text="deletingDomain?.hostname || null"
    :input-label="
      t('domains.enterDomain', { hostname: deletingDomain?.hostname || '' })
    "
    @cancel="deletingDomain = null"
    @confirm="removeDomain"
  />
  <ConfirmDialog
    :open="Boolean(renewingDomain)"
    :title="t('domains.renewTitle')"
    :message="
      t('domains.renewMessage', { hostname: renewingDomain?.hostname || '' })
    "
    :confirm-label="t('domains.renew')"
    :expected-text="renewingDomain?.hostname || null"
    :input-label="
      t('domains.enterDomain', { hostname: renewingDomain?.hostname || '' })
    "
    @cancel="renewingDomain = null"
    @confirm="renewCertificate"
  />
</template>

<style scoped>
.domain-form {
  display: grid;
  grid-template-columns: 2fr 1fr auto auto;
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
.error {
  grid-column: 1 / -1;
  margin: 0;
  color: #f3a1a6;
}
.domain-list article {
  display: grid;
  min-height: 72px;
  grid-template-columns: auto 1fr auto auto auto;
  align-items: center;
  gap: 0.8rem;
  border-bottom: 1px solid var(--border);
}
.domain-list article div {
  display: grid;
  gap: 0.25rem;
}
.domain-list article span {
  color: var(--text-muted);
  font-size: 0.78rem;
}
.ssl {
  display: grid;
  gap: 0.2rem;
  font-family: ui-monospace, monospace;
}
.ssl small {
  color: var(--text-muted);
  font-family: inherit;
  font-size: 0.68rem;
}
.renew-placeholder {
  width: 44px;
}
.domain-list button {
  display: grid;
  width: 44px;
  height: 44px;
  place-items: center;
  border: 0;
  background: transparent;
  color: #f09a9f;
}
.empty {
  padding: 3rem 0;
  color: var(--text-muted);
}
@media (max-width: 800px) {
  .domain-form {
    grid-template-columns: 1fr;
  }
}
</style>
