export default defineNuxtRouteMiddleware((to) => {
  if (!import.meta.client) return;
  const auth = useAuthStore();
  auth.restore();
  if (to.path !== "/login" && !auth.authenticated) return navigateTo("/login");
  if (to.path === "/login" && auth.authenticated)
    return navigateTo("/dashboard");
});
