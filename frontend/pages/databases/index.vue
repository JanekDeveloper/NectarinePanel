<script setup lang="ts">
import { IconDatabase, IconPlus, IconServer } from "@tabler/icons-vue";
import type { DatabaseInstance, DatabaseServer } from "~/types/api";

const api = useApi();
const { t } = useLocale();
const showServerForm = ref(false);
const showDatabaseForm = ref(false);
const oneTimeSecret = ref<{
  password: string | null;
  connection_string: string;
} | null>(null);
const serverStatuses = reactive<
  Record<
    string,
    { status: string; version: string | null; details: Record<string, unknown> }
  >
>({});
const serverForm = reactive({
  name: "",
  engine: "postgresql",
  host: "",
  port: 5432,
  admin_username: "",
  admin_password: "",
  tls_enabled: true,
});
const databaseForm = reactive({
  server_id: "",
  name: "",
  username: "",
  attach_environment: false,
});
const { data: servers, refresh: refreshServers } = await useAsyncData(
  "database-servers",
  () => api.request<DatabaseServer[]>("/databases/servers"),
);
const { data: databases, refresh: refreshDatabases } = await useAsyncData(
  "databases",
  () => api.request<DatabaseInstance[]>("/databases"),
);
const provisionableServers = computed(() =>
  (servers.value || []).filter((server) =>
    ["postgresql", "mysql", "mariadb", "sqlite"].includes(server.engine),
  ),
);
const selectedDatabaseServer = computed(() =>
  provisionableServers.value.find(
    (server) => server.id === databaseForm.server_id,
  ),
);

watch(
  () => serverForm.engine,
  (engine) => {
    const defaults: Record<
      string,
      { host: string; port: number; tls: boolean }
    > = {
      postgresql: { host: "", port: 5432, tls: true },
      mysql: { host: "", port: 3306, tls: true },
      mariadb: { host: "", port: 3306, tls: true },
      redis: { host: "", port: 6379, tls: true },
      valkey: { host: "", port: 6379, tls: true },
      sqlite: { host: "local", port: 0, tls: false },
    };
    const selected = defaults[engine];
    if (!selected) return;
    serverForm.host = selected.host;
    serverForm.port = selected.port;
    serverForm.tls_enabled = selected.tls;
    if (engine === "sqlite") {
      serverForm.admin_username = "";
      serverForm.admin_password = "";
    }
  },
);

async function addServer(): Promise<void> {
  await api.request("/databases/servers", { method: "POST", body: serverForm });
  showServerForm.value = false;
  await refreshServers();
}

async function createDatabase(): Promise<void> {
  const result = await api.request<{
    password: string;
    connection_string: string;
  }>("/databases", {
    method: "POST",
    body: { ...databaseForm, project_id: null },
  });
  oneTimeSecret.value = result;
  showDatabaseForm.value = false;
  await refreshDatabases();
}

async function checkServer(serverId: string): Promise<void> {
  serverStatuses[serverId] = await api.request<{
    status: string;
    version: string | null;
    details: Record<string, unknown>;
  }>(`/databases/servers/${serverId}/status`);
}
</script>

<template>
  <PageHeader
    :title="t('database.title')"
    :description="t('database.description')"
  >
    <div class="actions">
      <button
        class="button-secondary"
        type="button"
        @click="showServerForm = !showServerForm"
      >
        <IconServer :size="18" /> {{ t("database.server") }}
      </button>
      <button
        class="button-primary"
        type="button"
        :disabled="!provisionableServers.length"
        @click="showDatabaseForm = !showDatabaseForm"
      >
        <IconPlus :size="18" /> {{ t("database.create") }}
      </button>
    </div>
  </PageHeader>
  <form v-if="showServerForm" class="form panel" @submit.prevent="addServer">
    <label
      >{{ t("database.name")
      }}<input v-model="serverForm.name" class="control" required
    /></label>
    <label
      >{{ t("database.engine")
      }}<select v-model="serverForm.engine" class="control">
        <option value="postgresql">PostgreSQL</option>
        <option value="mysql">MySQL</option>
        <option value="mariadb">MariaDB</option>
        <option value="redis">Redis</option>
        <option value="valkey">Valkey</option>
        <option value="sqlite">SQLite</option>
      </select></label
    >
    <label
      >{{ t("database.host")
      }}<input
        v-model="serverForm.host"
        class="control"
        :readonly="serverForm.engine === 'sqlite'"
        required
    /></label>
    <label
      >{{ t("database.port")
      }}<input
        v-model.number="serverForm.port"
        class="control"
        type="number"
        min="0"
        max="65535"
        required
    /></label>
    <label v-if="serverForm.engine !== 'sqlite'"
      >{{ t("database.adminUser")
      }}<input v-model="serverForm.admin_username" class="control" required
    /></label>
    <label v-if="serverForm.engine !== 'sqlite'"
      >{{ t("database.adminPassword")
      }}<input
        v-model="serverForm.admin_password"
        class="control"
        type="password"
        required
        autocomplete="new-password"
    /></label>
    <label class="checkbox"
      ><input
        v-model="serverForm.tls_enabled"
        type="checkbox"
        :disabled="serverForm.engine === 'sqlite'"
      />
      TLS</label
    >
    <button class="button-primary" type="submit">
      {{ t("database.saveServer") }}
    </button>
  </form>
  <form
    v-if="showDatabaseForm"
    class="form panel"
    @submit.prevent="createDatabase"
  >
    <label
      >{{ t("database.server")
      }}<select v-model="databaseForm.server_id" class="control" required>
        <option disabled value="">{{ t("database.select") }}</option>
        <option
          v-for="server in provisionableServers"
          :key="server.id"
          :value="server.id"
        >
          {{ server.name }} / {{ server.engine }}
        </option>
      </select></label
    >
    <label
      >{{ t("database.databaseName")
      }}<input
        v-model="databaseForm.name"
        class="control"
        pattern="[A-Za-z_][A-Za-z0-9_]*"
        required
    /></label>
    <label v-if="selectedDatabaseServer?.engine !== 'sqlite'"
      >{{ t("database.username")
      }}<input
        v-model="databaseForm.username"
        class="control"
        pattern="[A-Za-z_][A-Za-z0-9_]*"
        required
    /></label>
    <button class="button-primary" type="submit">
      {{ t("common.create") }}
    </button>
  </form>
  <section v-if="oneTimeSecret" class="secret panel" aria-live="polite">
    <h2>{{ t("database.saveCredentials") }}</h2>
    <p>{{ t("database.passwordHidden") }}</p>
    <label v-if="oneTimeSecret.password"
      >{{ t("login.password") }}<code>{{ oneTimeSecret.password }}</code></label
    >
    <label
      >{{ t("database.connectionString")
      }}<code>{{ oneTimeSecret.connection_string }}</code></label
    >
    <button
      class="button-secondary"
      type="button"
      @click="oneTimeSecret = null"
    >
      {{ t("database.credentialsSaved") }}
    </button>
  </section>
  <section class="servers">
    <article v-for="server in servers" :key="server.id" class="panel">
      <div>
        <strong>{{ server.name }}</strong
        ><span
          >{{ server.engine }} · {{ server.host
          }}<template v-if="server.port">:{{ server.port }}</template></span
        >
      </div>
      <div v-if="serverStatuses[server.id]" class="server-status">
        <AppStatusBadge
          :status="
            serverStatuses[server.id]?.status === 'ready' ? 'running' : 'failed'
          "
        />
        <code>{{ serverStatuses[server.id]?.version || "local" }}</code>
      </div>
      <button
        class="button-secondary"
        type="button"
        @click="checkServer(server.id)"
      >
        {{ t("common.check") }}
      </button>
    </article>
  </section>
  <div class="list">
    <NuxtLink
      v-for="database in databases"
      :key="database.id"
      :to="`/databases/${database.id}`"
    >
      <IconDatabase :size="20" />
      <div>
        <strong>{{ database.name }}</strong
        ><span>{{ database.engine }} / {{ database.username }}</span>
      </div>
      <AppStatusBadge
        :status="database.status === 'ready' ? 'running' : database.status"
      />
    </NuxtLink>
    <p v-if="!databases?.length" class="empty">
      {{ t("database.empty") }}
    </p>
  </div>
</template>

<style scoped>
.actions {
  display: flex;
  gap: 0.6rem;
}
.form {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  align-items: end;
  gap: 1rem;
  margin-top: 1.5rem;
  padding: 1rem;
}
.form label,
.secret label {
  display: grid;
  gap: 0.4rem;
  font-size: 0.82rem;
  font-weight: 600;
}
.checkbox {
  display: flex !important;
  min-height: 44px;
  align-items: center;
}
.secret {
  display: grid;
  gap: 0.8rem;
  margin-top: 1.5rem;
  padding: 1.2rem;
  border-color: #76502e;
}
.secret h2,
.secret p {
  margin: 0;
}
.secret p,
.list span,
.empty {
  color: var(--text-muted);
}
.secret code {
  overflow-wrap: anywhere;
  border-radius: 8px;
  background: #101113;
  padding: 0.7rem;
}
.list {
  display: grid;
  margin-top: 2rem;
}
.servers {
  display: grid;
  gap: 0.7rem;
  margin-top: 1.5rem;
}
.servers article {
  display: grid;
  grid-template-columns: 1fr auto auto;
  align-items: center;
  gap: 1rem;
  padding: 0.9rem 1rem;
}
.servers article > div {
  display: grid;
  gap: 0.25rem;
}
.servers span,
.server-status code {
  color: var(--text-muted);
  font-size: 0.78rem;
}
.server-status {
  justify-items: end;
}
.list > a {
  display: grid;
  min-height: 70px;
  grid-template-columns: auto 1fr auto;
  align-items: center;
  gap: 0.8rem;
  border-bottom: 1px solid var(--border);
  color: var(--text);
  text-decoration: none;
}
.list > a div {
  display: grid;
  gap: 0.25rem;
}
.list span {
  font-size: 0.78rem;
}
.empty {
  padding: 3rem 0;
}
@media (max-width: 800px) {
  .form {
    grid-template-columns: 1fr;
  }
  .actions {
    flex-direction: column;
  }
  .servers article {
    grid-template-columns: 1fr;
  }
}
</style>
