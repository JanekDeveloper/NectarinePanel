import { ofetch, type FetchOptions } from "ofetch";

import type { TokenPair } from "~/types/api";

let refreshRequest: Promise<TokenPair> | null = null;
type RequestOptions = FetchOptions<"json"> & { silent?: boolean };

export function useApi() {
  const config = useRuntimeConfig();
  const auth = useAuthStore();
  const toasts = useToastStore();
  const { t } = useLocale();
  auth.restore();

  function errorMessage(error: unknown): string {
    const payload = error as {
      data?: { detail?: unknown };
      response?: { _data?: { detail?: unknown } };
    };
    const detail = payload.data?.detail ?? payload.response?._data?.detail;
    if (typeof detail === "string") return detail;
    if (detail && typeof detail === "object" && "message" in detail) {
      const message = (detail as { message?: unknown }).message;
      if (typeof message === "string") return message;
    }
    return t("api.requestFailed");
  }

  async function request<T>(
    path: string,
    options: RequestOptions = {},
  ): Promise<T> {
    const headers = new Headers(options.headers);
    const silent = Boolean(options.silent);
    const { silent: _silent, ...fetchOptions } = options;
    if (auth.accessToken)
      headers.set("Authorization", `Bearer ${auth.accessToken}`);
    try {
      return await ofetch<T>(path, {
        baseURL: config.public.apiBase,
        ...fetchOptions,
        headers,
      });
    } catch (error: unknown) {
      const response = (error as { response?: { status?: number } }).response;
      if (
        response?.status === 401 &&
        auth.refreshToken &&
        path !== "/auth/refresh"
      ) {
        try {
          const refreshToken = auth.refreshToken;
          refreshRequest ??= ofetch<TokenPair>("/auth/refresh", {
            baseURL: config.public.apiBase,
            method: "POST",
            body: { refresh_token: refreshToken },
          }).finally(() => {
            refreshRequest = null;
          });
          const tokens = await refreshRequest;
          auth.setTokens(tokens);
          return await request<T>(path, options);
        } catch {
          auth.clear();
          await navigateTo("/login");
        }
      }
      if (!silent) toasts.add(errorMessage(error), "error");
      throw error;
    }
  }

  return { request };
}
