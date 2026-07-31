<script setup lang="ts">
defineProps<{
  label: string;
  value: string;
  detail: string;
  percent?: number;
}>();
</script>

<template>
  <section class="metric">
    <p>{{ label }}</p>
    <strong>{{ value }}</strong>
    <span>{{ detail }}</span>
    <div v-if="percent !== undefined" class="metric__line" aria-hidden="true">
      <i
        :style="{
          transform: `scaleX(${Math.min(100, Math.max(0, percent)) / 100})`,
        }"
      />
    </div>
  </section>
</template>

<style scoped>
.metric {
  min-width: 0;
  border-top: 1px solid var(--border);
  padding: 1rem 0;
}
.metric p,
.metric span {
  margin: 0;
  color: var(--text-muted);
  font-size: 0.78rem;
}
.metric strong {
  display: block;
  margin: 0.45rem 0 0.2rem;
  font-family: "IBM Plex Mono", ui-monospace, monospace;
  font-size: 1.55rem;
  font-variant-numeric: tabular-nums;
}
.metric__line {
  height: 2px;
  margin-top: 0.8rem;
  overflow: hidden;
  background: #303238;
}
.metric__line i {
  display: block;
  width: 100%;
  height: 100%;
  transform-origin: left;
  background: var(--accent);
}
</style>
