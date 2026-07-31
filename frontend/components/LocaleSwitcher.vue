<script setup lang="ts">
import { IconLanguage } from "@tabler/icons-vue";
import { localeOptions, type PanelLocale } from "~/locales/messages";

withDefaults(defineProps<{ compact?: boolean }>(), { compact: false });
const localeController = useLocale();

function change(event: Event): void {
  localeController.apply(
    (event.target as HTMLSelectElement).value as PanelLocale,
  );
}
</script>

<template>
  <label class="locale-switcher" :class="{ compact }">
    <IconLanguage :size="18" :stroke-width="1.7" aria-hidden="true" />
    <span v-if="!compact">{{ localeController.t("language.label") }}</span>
    <select
      :value="localeController.locale.value"
      :aria-label="localeController.t('language.label')"
      @change="change"
    >
      <option
        v-for="option in localeOptions"
        :key="option.value"
        :value="option.value"
      >
        {{ compact ? option.shortLabel : option.label }}
      </option>
    </select>
  </label>
</template>

<style scoped>
.locale-switcher {
  display: grid;
  min-height: 44px;
  grid-template-columns: auto 1fr;
  align-items: center;
  gap: 0.45rem 0.65rem;
  color: var(--text-muted);
  font-size: 0.78rem;
  font-weight: 600;
}
.locale-switcher select {
  min-height: 44px;
  grid-column: 1 / -1;
  cursor: pointer;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--surface-raised);
  padding: 0 2.3rem 0 0.7rem;
  color: var(--text);
}
.locale-switcher.compact {
  grid-template-columns: auto 1fr;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--surface-raised);
  padding-left: 0.7rem;
}
.locale-switcher.compact select {
  min-width: 68px;
  grid-column: auto;
  border: 0;
  background: transparent;
}
</style>
