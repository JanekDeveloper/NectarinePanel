import tailwindcss from "@tailwindcss/vite";

export default defineNuxtConfig({
  compatibilityDate: "2026-06-28",
  srcDir: ".",
  devtools: { enabled: true },
  ssr: false,
  modules: ["@pinia/nuxt", "@nuxt/eslint"],
  css: ["~/assets/css/main.css"],
  vite: { plugins: [tailwindcss()] },
  runtimeConfig: {
    public: {
      apiBase:
        process.env.NUXT_PUBLIC_API_BASE ?? "http://localhost:8000/api/v1",
      wsBase: process.env.NUXT_PUBLIC_WS_BASE ?? "ws://localhost:8000",
    },
  },
  app: {
    head: {
      htmlAttrs: { lang: "ru" },
      title: "NectarinePanel",
      meta: [
        { name: "viewport", content: "width=device-width, initial-scale=1" },
        { name: "theme-color", content: "#111214" },
        { name: "description", content: "NectarinePanel VPS and project management" },
      ],
    },
  },
});
