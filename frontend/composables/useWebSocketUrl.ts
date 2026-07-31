/** Build authenticated panel WebSocket URLs from the configured API base. */
export function useWebSocketUrl(path: string): string {
  const config = useRuntimeConfig();
  const auth = useAuthStore();
  const wsBase = String(config.public.wsBase).replace(/\/$/, "");
  const apiBase = String(config.public.apiBase);
  const apiPath = apiBase.startsWith("http")
    ? new URL(apiBase).pathname.replace(/\/$/, "")
    : `/${apiBase.replace(/^\/|\/$/g, "")}`;
  const normalizedPath = `/${path.replace(/^\/+/, "")}`;
  return `${wsBase}${apiPath}${normalizedPath}?token=${encodeURIComponent(auth.accessToken || "")}`;
}
