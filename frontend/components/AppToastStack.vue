<script setup lang="ts">
import {
  IconAlertCircle,
  IconCheck,
  IconInfoCircle,
  IconX,
} from "@tabler/icons-vue";

const toasts = useToastStore();
const { t } = useLocale();
const icons = {
  success: IconCheck,
  error: IconAlertCircle,
  info: IconInfoCircle,
};
</script>

<template>
  <div class="toast-stack" aria-live="polite" aria-atomic="false">
    <article
      v-for="toast in toasts.items"
      :key="toast.id"
      class="toast"
      :class="`toast--${toast.kind}`"
    >
      <component :is="icons[toast.kind]" :size="19" :stroke-width="1.8" />
      <p>{{ toast.message }}</p>
      <button
        type="button"
        :aria-label="t('a11y.closeNotification')"
        @click="toasts.remove(toast.id)"
      >
        <IconX :size="17" />
      </button>
    </article>
  </div>
</template>

<style scoped>
.toast-stack {
  position: fixed;
  z-index: 120;
  right: 1rem;
  bottom: 1rem;
  display: grid;
  width: min(390px, calc(100vw - 2rem));
  gap: 0.65rem;
  pointer-events: none;
}
.toast {
  display: grid;
  min-height: 52px;
  grid-template-columns: auto 1fr auto;
  align-items: center;
  gap: 0.7rem;
  border: 1px solid var(--border);
  border-radius: 10px;
  background: #202226;
  padding: 0.75rem;
  box-shadow: 0 14px 40px rgb(0 0 0 / 38%);
  pointer-events: auto;
}
.toast--success {
  border-color: #356c4b;
}
.toast--error {
  border-color: #77363b;
}
.toast p {
  margin: 0;
  font-size: 0.86rem;
}
.toast button {
  display: grid;
  width: 32px;
  height: 32px;
  place-items: center;
  border: 0;
  background: transparent;
  color: var(--text-muted);
}
</style>
