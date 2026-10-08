/** Build authenticated WebSocket URLs for local proxies or remote APIs. */
export function buildWebSocketUrl(
  path: string,
  apiBase: string,
  wsBase: string,
  token: string,
  pageUrl?: string,
): string {
  const absoluteApi = /^https?:\/\//i.test(apiBase);
  const apiPath = absoluteApi
    ? new URL(apiBase).pathname.replace(/\/$/, "")
    : `/${apiBase.replace(/^\/+|\/+$/g, "")}`;
  const base = !absoluteApi && pageUrl ? new URL(pageUrl).origin : wsBase;
  const url = new URL(
    `${base.replace(/\/$/, "")}${apiPath}/${path.replace(/^\/+/, "")}`,
  );
  if (url.protocol === "http:") url.protocol = "ws:";
  if (url.protocol === "https:") url.protocol = "wss:";
  url.searchParams.set("token", token);
  return url.toString();
}
