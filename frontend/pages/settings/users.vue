<script setup lang="ts">
import {
  IconRefresh,
  IconShield,
  IconUserPlus,
  IconUserX,
} from "@tabler/icons-vue";
import type { User, UserRole } from "~/types/api";
import { canEditUser } from "~/utils/rbac";

const api = useApi();
const auth = useAuthStore();
const toasts = useToastStore();
const { t } = useLocale();
const creating = ref(false);
const savingUserId = ref<string | null>(null);
const form = reactive({
  username: "",
  displayName: "",
  password: "",
  role: "viewer" as UserRole,
});

const {
  data: users,
  pending,
  error,
  refresh,
} = await useAsyncData("settings-users", () => api.request<User[]>("/users"));

const editableRoles = computed<UserRole[]>(() =>
  auth.role === "owner"
    ? ["owner", "admin", "maintainer", "viewer"]
    : ["maintainer", "viewer"],
);

function canEdit(user: User): boolean {
  return canEditUser(auth.role, user);
}

function resetForm(): void {
  form.username = "";
  form.displayName = "";
  form.password = "";
  form.role = "viewer";
}

async function createUser(): Promise<void> {
  creating.value = true;
  try {
    await api.request<User>("/users", {
      method: "POST",
      body: {
        username: form.username,
        display_name: form.displayName || null,
        password: form.password,
        role: form.role,
      },
    });
    resetForm();
    await refresh();
    toasts.add(t("users.created"), "success");
  } finally {
    creating.value = false;
  }
}

async function updateUser(user: User, role: UserRole): Promise<void> {
  savingUserId.value = user.id;
  try {
    await api.request<User>(`/users/${user.id}`, {
      method: "PATCH",
      body: { role },
    });
    await refresh();
  } finally {
    savingUserId.value = null;
  }
}

async function setActive(user: User, active: boolean): Promise<void> {
  savingUserId.value = user.id;
  try {
    await api.request(`/users/${user.id}/${active ? "enable" : "disable"}`, {
      method: "POST",
    });
    await refresh();
  } finally {
    savingUserId.value = null;
  }
}
</script>

<template>
  <PageHeader :title="t('users.title')" :description="t('users.description')" />

  <section class="users-layout">
    <form class="panel user-form" @submit.prevent="createUser">
      <header>
        <IconUserPlus :size="20" />
        <div>
          <strong>{{ t("users.new") }}</strong>
          <span>{{ t("users.newDescription") }}</span>
        </div>
      </header>
      <label>
        <span>{{ t("users.username") }}</span>
        <input
          v-model="form.username"
          class="control"
          required
          minlength="3"
          maxlength="64"
          autocomplete="username"
        />
      </label>
      <label>
        <span>{{ t("users.displayName") }}</span>
        <input v-model="form.displayName" class="control" maxlength="120" />
      </label>
      <label>
        <span>{{ t("login.password") }}</span>
        <input
          v-model="form.password"
          class="control"
          type="password"
          required
          minlength="12"
          maxlength="256"
          autocomplete="new-password"
        />
      </label>
      <label>
        <span>{{ t("users.role") }}</span>
        <select v-model="form.role" class="control">
          <option v-for="role in editableRoles" :key="role" :value="role">
            {{ role }}
          </option>
        </select>
      </label>
      <button class="button-primary" type="submit" :disabled="creating">
        <IconUserPlus :size="18" />
        {{ creating ? t("common.creating") : t("common.create") }}
      </button>
    </form>

    <section class="panel users-list">
      <header>
        <div>
          <strong>{{ t("users.list") }}</strong>
          <span>{{ t("users.ownerProtection") }}</span>
        </div>
        <button class="button-secondary" type="button" @click="() => refresh()">
          <IconRefresh :size="18" /> {{ t("common.refresh") }}
        </button>
      </header>

      <div v-if="pending" class="state">{{ t("users.loading") }}</div>
      <div v-else-if="error" class="state state--error" role="alert">
        {{ t("users.loadError") }}
      </div>
      <div v-else-if="!users?.length" class="state">{{ t("users.empty") }}</div>
      <article v-for="user in users" v-else :key="user.id" class="user-row">
        <div class="identity">
          <IconShield :size="20" />
          <div>
            <strong>{{ user.display_name || user.username }}</strong>
            <span>@{{ user.username }}</span>
          </div>
        </div>
        <select
          class="control role-select"
          :value="user.role"
          :disabled="!canEdit(user) || savingUserId === user.id"
          @change="
            updateUser(
              user,
              ($event.target as HTMLSelectElement).value as UserRole,
            )
          "
        >
          <option v-for="role in editableRoles" :key="role" :value="role">
            {{ role }}
          </option>
          <option v-if="!editableRoles.includes(user.role)" :value="user.role">
            {{ user.role }}
          </option>
        </select>
        <span class="status" :class="{ 'status--disabled': !user.is_active }">
          {{ user.is_active ? t("users.active") : t("users.disabled") }}
        </span>
        <button
          class="button-secondary"
          type="button"
          :disabled="!canEdit(user) || savingUserId === user.id"
          @click="setActive(user, !user.is_active)"
        >
          <IconUserX :size="18" />
          {{ user.is_active ? t("users.disable") : t("users.enable") }}
        </button>
      </article>
    </section>
  </section>
</template>

<style scoped>
.users-layout {
  display: grid;
  grid-template-columns: minmax(280px, 360px) minmax(0, 1fr);
  gap: 1rem;
  margin-top: 2rem;
}
.user-form,
.users-list {
  display: grid;
  align-content: start;
  gap: 1rem;
  padding: 1.2rem;
}
header,
.identity,
.user-row {
  display: flex;
  align-items: center;
  gap: 0.75rem;
}
header {
  justify-content: space-between;
}
header div,
.identity div {
  display: grid;
  gap: 0.2rem;
}
header span,
.identity span {
  color: var(--text-muted);
  font-size: 0.82rem;
}
label {
  display: grid;
  gap: 0.4rem;
  font-size: 0.84rem;
  font-weight: 600;
}
.user-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 160px 96px auto;
  border-top: 1px solid var(--border);
  padding-top: 0.9rem;
}
.role-select {
  min-width: 0;
}
.status {
  color: #70d49d;
  font-size: 0.82rem;
  font-weight: 700;
}
.status--disabled {
  color: #f3a1a6;
}
.state {
  color: var(--text-muted);
}
.state--error {
  color: #f3a1a6;
}
@media (max-width: 900px) {
  .users-layout,
  .user-row {
    grid-template-columns: 1fr;
  }
  header {
    align-items: flex-start;
    flex-direction: column;
  }
}
</style>
