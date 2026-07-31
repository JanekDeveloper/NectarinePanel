<script setup lang="ts">
import {
  IconActivity,
  IconArchive,
  IconBrandTelegram,
  IconDatabase,
  IconFolderCode,
  IconHistory,
  IconLayoutDashboard,
  IconLogout,
  IconMenu2,
  IconSettings,
  IconX,
} from "@tabler/icons-vue";
import type { User } from "~/types/api";

const route = useRoute();
const auth = useAuthStore();
const api = useApi();
const locale = useLocale();
const mobileOpen = ref(false);
useHead(() => ({ htmlAttrs: { lang: locale.locale.value } }));
onMounted(locale.restore);
auth.restore();

if (auth.authenticated && !auth.user) {
  api
    .request<User>("/auth/me", { silent: true })
    .then((user) => auth.setUser(user))
    .catch(() => auth.clear());
}

const navigation = computed(() => [
  {
    label: locale.t("nav.overview"),
    to: "/dashboard",
    icon: IconLayoutDashboard,
  },
  { label: locale.t("nav.projects"), to: "/projects", icon: IconFolderCode },
  { label: locale.t("nav.databases"), to: "/databases", icon: IconDatabase },
  { label: locale.t("nav.backups"), to: "/backups", icon: IconArchive },
  { label: locale.t("nav.monitoring"), to: "/monitoring", icon: IconActivity },
  { label: locale.t("nav.audit"), to: "/audit-logs", icon: IconHistory },
]);

function logout(): void {
  auth.clear();
  navigateTo("/login");
}

watch(
  () => route.fullPath,
  () => {
    mobileOpen.value = false;
  },
);
</script>

<template>
  <div class="shell">
    <a class="skip-link" href="#main-content">{{ locale.t("a11y.skip") }}</a>
    <button
      class="mobile-menu"
      type="button"
      :aria-expanded="mobileOpen"
      aria-controls="primary-sidebar"
      :aria-label="locale.t('a11y.openNavigation')"
      @click="mobileOpen = true"
    >
      <IconMenu2 :size="22" :stroke-width="1.7" />
    </button>

    <aside
      id="primary-sidebar"
      class="sidebar"
      :class="{ 'sidebar--open': mobileOpen }"
    >
      <div class="sidebar__brand">
        <img
          src="/nectarinepanel_dark_logo_wordmark.png"
          alt="NectarinePanel"
          width="1340"
          height="360"
        />
        <button
          class="sidebar__close"
          type="button"
          :aria-label="locale.t('a11y.closeNavigation')"
          @click="mobileOpen = false"
        >
          <IconX :size="22" :stroke-width="1.7" />
        </button>
      </div>

      <nav :aria-label="locale.t('a11y.primaryNavigation')">
        <NuxtLink
          v-for="item in navigation"
          :key="item.to"
          :to="item.to"
          class="nav-item"
          :class="{ 'nav-item--active': route.path.startsWith(item.to) }"
        >
          <component :is="item.icon" :size="19" :stroke-width="1.7" />
          <span>{{ item.label }}</span>
        </NuxtLink>
      </nav>

      <div class="sidebar__bottom">
        <LocaleSwitcher />
        <NuxtLink
          v-if="auth.canManageSettings"
          class="nav-item"
          to="/settings/telegram"
        >
          <IconBrandTelegram :size="19" :stroke-width="1.7" />
          <span>{{ locale.t("nav.telegram") }}</span>
        </NuxtLink>
        <NuxtLink
          v-if="auth.canManageUsers"
          class="nav-item"
          to="/settings/users"
        >
          <IconSettings :size="19" :stroke-width="1.7" />
          <span>{{ locale.t("nav.users") }}</span>
        </NuxtLink>
        <NuxtLink v-if="auth.canManageSettings" class="nav-item" to="/settings">
          <IconSettings :size="19" :stroke-width="1.7" />
          <span>{{ locale.t("nav.settings") }}</span>
        </NuxtLink>
        <button class="nav-item nav-item--button" type="button" @click="logout">
          <IconLogout :size="19" :stroke-width="1.7" />
          <span>{{ locale.t("nav.logout") }}</span>
        </button>
      </div>
    </aside>

    <div
      v-if="mobileOpen"
      class="scrim"
      aria-hidden="true"
      @click="mobileOpen = false"
    />
    <main id="main-content" class="content" tabindex="-1">
      <slot />
    </main>
  </div>
</template>

<style scoped>
.shell {
  min-height: 100dvh;
}
.skip-link {
  position: fixed;
  top: 0.5rem;
  left: 0.5rem;
  z-index: 100;
  transform: translateY(-150%);
  border-radius: 8px;
  background: var(--accent);
  padding: 0.6rem 0.8rem;
  color: #1a0d08;
  font-weight: 700;
}
.skip-link:focus {
  transform: translateY(0);
}
.sidebar {
  position: fixed;
  inset: 0 auto 0 0;
  z-index: 30;
  display: flex;
  width: 268px;
  flex-direction: column;
  border-right: 1px solid var(--border);
  background: #141517;
  padding: 1rem 0.8rem;
}
.sidebar__brand {
  position: relative;
  display: flex;
  min-height: 64px;
  align-items: center;
  padding: 0 0.55rem 1rem;
}
.sidebar__brand img {
  width: 218px;
  height: auto;
}
.sidebar__close {
  display: none;
}
.sidebar nav {
  display: grid;
  gap: 0.2rem;
}
.nav-item {
  display: flex;
  min-height: 44px;
  cursor: pointer;
  align-items: center;
  gap: 0.7rem;
  border: 1px solid transparent;
  border-radius: 8px;
  padding: 0 0.7rem;
  color: #b9bbc0;
  font-size: 0.9rem;
  text-decoration: none;
  transition:
    background-color 180ms ease,
    color 180ms ease;
}
.nav-item:hover {
  background: #202226;
  color: var(--text);
}
.nav-item--active {
  background: #2b211d;
  color: #ff9b70;
}
.nav-item--button {
  width: 100%;
  background: transparent;
  font: inherit;
}
.sidebar__bottom {
  display: grid;
  gap: 0.2rem;
  margin-top: auto;
  border-top: 1px solid var(--border);
  padding-top: 0.8rem;
}
.content {
  min-height: 100dvh;
  margin-left: 268px;
  padding: 2rem clamp(1rem, 3vw, 3rem) 4rem;
}
.mobile-menu,
.scrim {
  display: none;
}
@media (max-width: 900px) {
  .mobile-menu {
    position: fixed;
    top: 0.75rem;
    left: 0.75rem;
    z-index: 20;
    display: grid;
    width: 44px;
    height: 44px;
    place-items: center;
    border: 1px solid var(--border);
    border-radius: 8px;
    background: var(--surface-raised);
    color: var(--text);
  }
  .sidebar {
    transform: translateX(-100%);
    transition: transform 220ms ease;
  }
  .sidebar--open {
    transform: translateX(0);
  }
  .sidebar__close {
    position: absolute;
    right: 0;
    display: grid;
    width: 44px;
    height: 44px;
    place-items: center;
    border: 0;
    background: transparent;
    color: var(--text);
  }
  .scrim {
    position: fixed;
    inset: 0;
    z-index: 25;
    display: block;
    background: rgb(0 0 0 / 58%);
  }
  .content {
    margin-left: 0;
    padding-top: 5rem;
  }
}
</style>
