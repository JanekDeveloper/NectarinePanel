<script setup lang="ts">
const locale = useLocale();
useHead(() => ({ htmlAttrs: { lang: locale.locale.value } }));
onMounted(locale.restore);
</script>

<template>
  <main class="auth-layout">
    <LocaleSwitcher class="locale" compact />
    <slot />
  </main>
</template>

<style scoped>
.auth-layout {
  position: relative;
  display: grid;
  min-height: 100dvh;
  place-items: center;
  padding: 1.25rem;
  background:
    radial-gradient(circle at 15% 15%, rgb(244 106 50 / 9%), transparent 35%),
    var(--surface-base);
}
.locale {
  position: absolute;
  top: 1rem;
  right: 1rem;
}
</style>
