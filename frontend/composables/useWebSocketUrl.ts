import { buildWebSocketUrl } from "~/utils/websocket";

/** Build authenticated panel WebSocket URLs from the configured API base. */
export function useWebSocketUrl(path: string): string {
  const config = useRuntimeConfig();
  const auth = useAuthStore();
  return buildWebSocketUrl(
    path,
    String(config.public.apiBase),
    String(config.public.wsBase),
    auth.accessToken || "",
    import.meta.client ? window.location.href : undefined,
  );
}
