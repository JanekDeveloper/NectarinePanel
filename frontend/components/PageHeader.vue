<script setup lang="ts">
defineProps<{ title: string; description: string }>();
</script>

<template>
  <header class="page-header">
    <div>
      <h1>{{ title }}</h1>
      <p>{{ description }}</p>
    </div>
    <slot />
  </header>
</template>

<style scoped>
.page-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 1rem;
}
h1,
p {
  margin: 0;
}
h1 {
  font-size: clamp(1.7rem, 4vw, 2.35rem);
  letter-spacing: -0.035em;
}
p {
  margin-top: 0.35rem;
  color: var(--text-muted);
}
@media (max-width: 620px) {
  .page-header {
    align-items: stretch;
    flex-direction: column;
  }
}
</style>
