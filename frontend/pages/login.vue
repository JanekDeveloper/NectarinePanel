<script setup lang="ts">
import type { LoginChallenge, LoginResponse, TokenPair } from "~/types/api";

definePageMeta({ layout: "auth" });

const config = useRuntimeConfig();
const auth = useAuthStore();
const { t } = useLocale();
const username = ref("");
const password = ref("");
const pending = ref(false);
const twoFactorPending = ref(false);
const challengeToken = ref<string | null>(null);
const errorMessage = ref("");

function isChallenge(response: LoginResponse): response is LoginChallenge {
  return "two_factor_required" in response && response.two_factor_required;
}

async function completeTwoFactor(): Promise<void> {
  if (!challengeToken.value) return;
  errorMessage.value = "";
  pending.value = true;
  try {
    const tokens = await $fetch<TokenPair>("/auth/2fa/complete", {
      baseURL: config.public.apiBase,
      method: "POST",
      body: { challenge_token: challengeToken.value },
    });
    auth.setTokens(tokens);
    await navigateTo("/dashboard");
  } catch (error: unknown) {
    const status = (error as { response?: { status?: number } }).response
      ?.status;
    if (status === 409) {
      errorMessage.value = t("login.awaitingTelegram");
      return;
    }
    if (status === 410) {
      twoFactorPending.value = false;
      challengeToken.value = null;
      errorMessage.value = t("login.expired");
      return;
    }
    errorMessage.value = t("login.completeError");
  } finally {
    pending.value = false;
  }
}

async function submit(): Promise<void> {
  errorMessage.value = "";
  pending.value = true;
  try {
    const response = await $fetch<LoginResponse>("/auth/login", {
      baseURL: config.public.apiBase,
      method: "POST",
      body: { username: username.value, password: password.value },
    });
    if (isChallenge(response)) {
      challengeToken.value = response.challenge_token;
      twoFactorPending.value = true;
      return;
    }
    auth.setTokens(response);
    await navigateTo("/dashboard");
  } catch {
    errorMessage.value = t("login.invalidCredentials");
  } finally {
    pending.value = false;
  }
}
</script>

<template>
  <section class="login-card" aria-labelledby="login-title">
    <img
      src="/nectarinepanel_dark_logo_wordmark.png"
      alt="NectarinePanel"
      width="1340"
      height="360"
    />
    <div>
      <h1 id="login-title">{{ t("login.title") }}</h1>
      <p>{{ t("login.description") }}</p>
    </div>
    <form v-if="!twoFactorPending" novalidate @submit.prevent="submit">
      <label>
        <span>{{ t("login.username") }}</span>
        <input
          v-model="username"
          class="control"
          name="username"
          autocomplete="username"
          required
          minlength="3"
        />
      </label>
      <label>
        <span>{{ t("login.password") }}</span>
        <input
          v-model="password"
          class="control"
          type="password"
          name="password"
          autocomplete="current-password"
          required
          minlength="12"
        />
      </label>
      <p v-if="errorMessage" class="form-error" role="alert">
        {{ errorMessage }}
      </p>
      <button class="button-primary" type="submit" :disabled="pending">
        {{ pending ? t("login.checking") : t("login.submit") }}
      </button>
    </form>
    <div v-else class="two-factor-card" role="status">
      <h2>{{ t("login.telegramTitle") }}</h2>
      <p>{{ t("login.telegramDescription") }}</p>
      <p v-if="errorMessage" class="form-error" role="alert">
        {{ errorMessage }}
      </p>
      <div class="actions">
        <button
          class="button-primary"
          type="button"
          :disabled="pending"
          @click="completeTwoFactor"
        >
          {{ pending ? t("login.checking") : t("login.checkConfirmation") }}
        </button>
        <button
          class="button-secondary"
          type="button"
          :disabled="pending"
          @click="
            twoFactorPending = false;
            challengeToken = null;
            errorMessage = '';
          "
        >
          {{ t("login.back") }}
        </button>
      </div>
    </div>
  </section>
</template>

<style scoped>
.login-card {
  display: grid;
  width: min(100%, 430px);
  gap: 1.5rem;
  border: 1px solid var(--border);
  border-radius: 16px;
  background: var(--surface-raised);
  padding: clamp(1.4rem, 5vw, 2.2rem);
  box-shadow: 0 24px 70px rgb(0 0 0 / 25%);
}
.login-card > img {
  width: 280px;
  max-width: 100%;
  height: auto;
}
h1 {
  margin: 0;
  font-size: 1.65rem;
}
p {
  margin: 0.45rem 0 0;
  color: var(--text-muted);
}
form {
  display: grid;
  gap: 1rem;
}
.two-factor-card {
  display: grid;
  gap: 1rem;
  border: 1px solid rgb(255 122 26 / 22%);
  border-radius: 14px;
  background: rgb(255 122 26 / 7%);
  padding: 1rem;
}
.two-factor-card h2 {
  margin: 0;
  font-size: 1.1rem;
}
.actions {
  display: flex;
  flex-wrap: wrap;
  gap: 0.75rem;
}
label {
  display: grid;
  gap: 0.45rem;
  font-size: 0.88rem;
  font-weight: 600;
}
.form-error {
  margin: 0;
  color: #f5a1a6;
  font-size: 0.88rem;
}
</style>
