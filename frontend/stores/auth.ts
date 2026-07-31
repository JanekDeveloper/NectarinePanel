import type { TokenPair, User } from "~/types/api";
import {
  canManageSettings as roleCanManageSettings,
  canManageUsers as roleCanManageUsers,
  canRevealSecrets as roleCanRevealSecrets,
  canWriteProjects as roleCanWriteProjects,
} from "~/utils/rbac";

export const useAuthStore = defineStore("auth", () => {
  const accessToken = ref<string | null>(null);
  const refreshToken = ref<string | null>(null);
  const user = ref<User | null>(null);
  const initialized = ref(false);

  const authenticated = computed(() => Boolean(accessToken.value));
  const role = computed(() => user.value?.role ?? null);
  const canManageUsers = computed(() => roleCanManageUsers(user.value?.role));
  const canManageSettings = computed(() =>
    roleCanManageSettings(user.value?.role),
  );
  const canRevealSecrets = computed(() =>
    roleCanRevealSecrets(user.value?.role),
  );
  const canWriteProjects = computed(() =>
    roleCanWriteProjects(user.value?.role),
  );

  function restore(): void {
    if (!import.meta.client || initialized.value) return;
    accessToken.value = sessionStorage.getItem("nectarine.access");
    refreshToken.value = sessionStorage.getItem("nectarine.refresh");
    initialized.value = true;
  }

  function setTokens(tokens: TokenPair): void {
    accessToken.value = tokens.access_token;
    refreshToken.value = tokens.refresh_token;
    if (import.meta.client) {
      sessionStorage.setItem("nectarine.access", tokens.access_token);
      sessionStorage.setItem("nectarine.refresh", tokens.refresh_token);
    }
  }

  function setUser(currentUser: User | null): void {
    user.value = currentUser;
  }

  function clear(): void {
    accessToken.value = null;
    refreshToken.value = null;
    user.value = null;
    if (import.meta.client) {
      sessionStorage.removeItem("nectarine.access");
      sessionStorage.removeItem("nectarine.refresh");
    }
  }

  return {
    accessToken,
    refreshToken,
    user,
    initialized,
    authenticated,
    role,
    canManageUsers,
    canManageSettings,
    canRevealSecrets,
    canWriteProjects,
    restore,
    setTokens,
    setUser,
    clear,
  };
});
