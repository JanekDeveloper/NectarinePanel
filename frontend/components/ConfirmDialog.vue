<script setup lang="ts">
const props = withDefaults(
  defineProps<{
    open: boolean;
    title: string;
    message: string;
    confirmLabel?: string;
    cancelLabel?: string;
    danger?: boolean;
    expectedText?: string | null;
    requireInput?: boolean;
    inputLabel?: string;
    initialValue?: string;
  }>(),
  {
    confirmLabel: undefined,
    cancelLabel: undefined,
    danger: false,
    expectedText: null,
    requireInput: false,
    inputLabel: undefined,
    initialValue: "",
  },
);
const { t } = useLocale();
const resolvedConfirmLabel = computed(
  () => props.confirmLabel ?? t("common.confirm"),
);
const resolvedCancelLabel = computed(
  () => props.cancelLabel ?? t("common.cancel"),
);
const resolvedInputLabel = computed(
  () => props.inputLabel ?? t("common.confirmation"),
);

const emit = defineEmits<{
  cancel: [];
  confirm: [value: string];
}>();

const value = ref("");
const showInput = computed(
  () => props.expectedText !== null || props.requireInput,
);
const canConfirm = computed(() => {
  if (props.expectedText !== null) return value.value === props.expectedText;
  if (props.requireInput) return value.value.trim().length > 0;
  return true;
});

watch(
  () => props.open,
  (open) => {
    value.value = open ? props.initialValue : "";
  },
);
</script>

<template>
  <Teleport to="body">
    <div
      v-if="open"
      class="confirm-backdrop"
      role="presentation"
      @click.self="emit('cancel')"
    >
      <section
        class="confirm-dialog panel"
        role="dialog"
        aria-modal="true"
        aria-labelledby="confirm-title"
      >
        <div>
          <h2 id="confirm-title">{{ title }}</h2>
          <p>{{ message }}</p>
        </div>
        <label v-if="showInput">
          <span>{{ resolvedInputLabel }}</span>
          <input v-model="value" class="control" autocomplete="off" />
        </label>
        <div class="confirm-actions">
          <button
            class="button-secondary"
            type="button"
            @click="emit('cancel')"
          >
            {{ resolvedCancelLabel }}
          </button>
          <button
            :class="danger ? 'button-danger' : 'button-primary'"
            type="button"
            :disabled="!canConfirm"
            @click="emit('confirm', value)"
          >
            {{ resolvedConfirmLabel }}
          </button>
        </div>
      </section>
    </div>
  </Teleport>
</template>

<style scoped>
.confirm-backdrop {
  position: fixed;
  z-index: 80;
  display: grid;
  inset: 0;
  place-items: center;
  background: rgb(0 0 0 / 68%);
  padding: 1rem;
}
.confirm-dialog {
  display: grid;
  width: min(460px, 100%);
  gap: 1rem;
  padding: 1.25rem;
}
h2,
p {
  margin: 0;
}
p {
  margin-top: 0.4rem;
  color: var(--text-muted);
}
label {
  display: grid;
  gap: 0.4rem;
  font-size: 0.82rem;
  font-weight: 600;
}
.confirm-actions {
  display: flex;
  justify-content: flex-end;
  gap: 0.7rem;
}
@media (max-width: 560px) {
  .confirm-actions {
    align-items: stretch;
    flex-direction: column-reverse;
  }
}
</style>
