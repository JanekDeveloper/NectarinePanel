<script setup lang="ts">
import type { AuditLog } from "~/types/api";

const api = useApi();
const { t, dateTime } = useLocale();
const { data } = await useAsyncData("audit-logs", () =>
  api.request<AuditLog[]>("/audit-logs?limit=200"),
);
</script>

<template>
  <PageHeader :title="t('audit.title')" :description="t('audit.description')" />
  <div class="table-wrap">
    <table>
      <thead>
        <tr>
          <th>{{ t("audit.time") }}</th>
          <th>{{ t("audit.action") }}</th>
          <th>{{ t("audit.resource") }}</th>
          <th>IP</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="event in data" :key="event.id">
          <td>{{ dateTime(event.created_at) }}</td>
          <td>
            <code>{{ event.action }}</code>
          </td>
          <td>
            {{ event.resource_type || "-" }} {{ event.resource_id || "" }}
          </td>
          <td>{{ event.ip_address || "-" }}</td>
        </tr>
      </tbody>
    </table>
  </div>
</template>

<style scoped>
.table-wrap {
  margin-top: 2rem;
  overflow-x: auto;
}
table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.82rem;
}
th,
td {
  min-height: 48px;
  border-bottom: 1px solid var(--border);
  padding: 0.8rem;
  text-align: left;
}
th {
  color: var(--text-muted);
  font-weight: 600;
}
code {
  color: #ff9b70;
}
</style>
