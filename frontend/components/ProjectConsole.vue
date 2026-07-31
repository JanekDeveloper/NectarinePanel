<script setup lang="ts">
import {
  IconClearAll,
  IconRefresh,
  IconTerminal2,
  IconTopologyStar3,
} from "@tabler/icons-vue";
import type { Terminal as XTerm } from "@xterm/xterm";
import type { FitAddon } from "@xterm/addon-fit";
import "@xterm/xterm/css/xterm.css";

interface TerminalSocketPayload {
  type: "ready" | "output" | "error" | "exit";
  data?: string;
  error?: string;
  exit_code?: number;
}

const props = withDefaults(
  defineProps<{ projectId: string; compact?: boolean }>(),
  { compact: false },
);
const api = useApi();
const auth = useAuthStore();
const { t } = useLocale();
const terminalElement = ref<HTMLElement | null>(null);
const logElement = ref<HTMLElement | null>(null);
const terminalConnected = ref(false);
const logsConnected = ref(false);
const logs = ref("");
const terminalSocket = shallowRef<WebSocket | null>(null);
const logsSocket = shallowRef<WebSocket | null>(null);
let terminal: XTerm | null = null;
let fitAddon: FitAddon | null = null;
let resizeObserver: ResizeObserver | null = null;
let terminalReconnectTimer: ReturnType<typeof setTimeout> | null = null;
let logsReconnectTimer: ReturnType<typeof setTimeout> | null = null;
let active = false;

function encodeTerminalData(value: string): string {
  const bytes = new TextEncoder().encode(value);
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary);
}

function decodeTerminalData(value: string): Uint8Array {
  const binary = atob(value);
  return Uint8Array.from(binary, (character) => character.charCodeAt(0));
}

function sendTerminalSize(): void {
  if (!terminal || terminalSocket.value?.readyState !== WebSocket.OPEN) {
    return;
  }
  terminalSocket.value.send(
    JSON.stringify({
      type: "resize",
      cols: terminal.cols,
      rows: terminal.rows,
    }),
  );
}

function scheduleTerminalReconnect(): void {
  if (!active || terminalReconnectTimer) return;
  terminalReconnectTimer = setTimeout(() => {
    terminalReconnectTimer = null;
    connectTerminal();
  }, 2000);
}

function connectTerminal(): void {
  if (!active || !auth.accessToken || !terminal) return;
  terminalSocket.value?.close();
  terminal.writeln(`\x1b[38;5;244m${t("console.connecting")}\x1b[0m`);
  const connection = new WebSocket(
    useWebSocketUrl(`/projects/${props.projectId}/terminal/live`),
  );
  terminalSocket.value = connection;
  connection.onopen = sendTerminalSize;
  connection.onmessage = (event) => {
    let payload: TerminalSocketPayload;
    try {
      payload = JSON.parse(String(event.data)) as TerminalSocketPayload;
    } catch {
      connection.close();
      return;
    }
    if (payload.type === "ready") {
      terminalConnected.value = true;
      terminal?.focus();
      return;
    }
    if (payload.type === "output" && payload.data) {
      terminal?.write(decodeTerminalData(payload.data));
      return;
    }
    if (payload.type === "error") {
      terminal?.writeln(
        `\r\n\x1b[31m${payload.error || t("console.terminalError")}\x1b[0m`,
      );
      return;
    }
    if (payload.type === "exit") {
      terminal?.writeln(
        `\r\n\x1b[38;5;244m${t("console.sessionEnded", { code: payload.exit_code ?? "?" })}\x1b[0m`,
      );
    }
  };
  connection.onclose = (event) => {
    if (terminalSocket.value !== connection) return;
    terminalConnected.value = false;
    if ([4401, 4404, 4409].includes(event.code)) {
      terminal?.writeln(`\r\n\x1b[31m${t("console.unavailable")}\x1b[0m`);
      return;
    }
    if (active) {
      terminal?.writeln(
        `\r\n\x1b[38;5;244m${t("console.reconnecting")}\x1b[0m`,
      );
      scheduleTerminalReconnect();
    }
  };
  connection.onerror = () => connection.close();
}

function reconnectTerminal(): void {
  if (terminalReconnectTimer) {
    clearTimeout(terminalReconnectTimer);
    terminalReconnectTimer = null;
  }
  terminalSocket.value?.close();
  connectTerminal();
}

function clearTerminal(): void {
  terminal?.clear();
}

function scheduleLogsReconnect(): void {
  if (!active || logsReconnectTimer) return;
  logsReconnectTimer = setTimeout(() => {
    logsReconnectTimer = null;
    connectLogs();
  }, 2000);
}

function updateLogs(content: string): void {
  const element = logElement.value;
  const followsTail =
    !element ||
    element.scrollHeight - element.scrollTop - element.clientHeight < 48;
  logs.value = content;
  if (followsTail) {
    nextTick(() => {
      if (logElement.value) {
        logElement.value.scrollTop = logElement.value.scrollHeight;
      }
    });
  }
}

function connectLogs(): void {
  if (!active || !auth.accessToken) return;
  logsSocket.value?.close();
  const connection = new WebSocket(
    useWebSocketUrl(`/projects/${props.projectId}/logs/live`),
  );
  logsSocket.value = connection;
  connection.onopen = () => {
    logsConnected.value = true;
  };
  connection.onmessage = (event) => {
    try {
      const payload = JSON.parse(String(event.data)) as { content?: string };
      if (typeof payload.content === "string") updateLogs(payload.content);
    } catch {
      connection.close();
    }
  };
  connection.onclose = (event) => {
    if (logsSocket.value !== connection) return;
    logsConnected.value = false;
    if ([4401, 4404].includes(event.code)) return;
    scheduleLogsReconnect();
  };
  connection.onerror = () => connection.close();
}

async function loadInitialLogs(): Promise<void> {
  try {
    const result = await api.request<{ content: string }>(
      `/projects/${props.projectId}/logs?lines=1000`,
      { silent: true },
    );
    updateLogs(result.content);
  } catch {
    updateLogs(t("console.logsLoadError"));
  }
}

async function initializeTerminal(): Promise<void> {
  if (!terminalElement.value) return;
  const [{ Terminal }, { FitAddon }] = await Promise.all([
    import("@xterm/xterm"),
    import("@xterm/addon-fit"),
  ]);
  terminal = new Terminal({
    allowProposedApi: false,
    cursorBlink: true,
    cursorStyle: "block",
    fontFamily:
      '"IBM Plex Mono", "JetBrains Mono", ui-monospace, SFMono-Regular, monospace',
    fontSize: props.compact ? 12 : 13,
    lineHeight: 1.35,
    scrollback: 10_000,
    convertEol: false,
    theme: {
      background: "#090a0c",
      foreground: "#e6e8eb",
      cursor: "#f46a32",
      cursorAccent: "#090a0c",
      selectionBackground: "#f46a3255",
      black: "#090a0c",
      red: "#dc5b63",
      green: "#70d49d",
      yellow: "#d99a37",
      blue: "#78a9ff",
      magenta: "#c792ea",
      cyan: "#56b6c2",
      white: "#e6e8eb",
      brightBlack: "#6f737b",
      brightWhite: "#ffffff",
    },
  });
  fitAddon = new FitAddon();
  terminal.loadAddon(fitAddon);
  terminal.open(terminalElement.value);
  fitAddon.fit();
  terminal.onData((data) => {
    if (terminalSocket.value?.readyState !== WebSocket.OPEN) return;
    terminalSocket.value.send(
      JSON.stringify({ type: "input", data: encodeTerminalData(data) }),
    );
  });
  terminal.onResize(sendTerminalSize);
  resizeObserver = new ResizeObserver(() => {
    requestAnimationFrame(() => fitAddon?.fit());
  });
  resizeObserver.observe(terminalElement.value);
}

onMounted(async () => {
  active = true;
  await initializeTerminal();
  connectTerminal();
  await loadInitialLogs();
  connectLogs();
});

onUnmounted(() => {
  active = false;
  if (terminalReconnectTimer) clearTimeout(terminalReconnectTimer);
  if (logsReconnectTimer) clearTimeout(logsReconnectTimer);
  terminalSocket.value?.close();
  logsSocket.value?.close();
  resizeObserver?.disconnect();
  terminal?.dispose();
});
</script>

<template>
  <div class="console-workspace" :class="{ compact: props.compact }">
    <section class="runtime-pane panel">
      <header class="pane-header">
        <div>
          <IconTerminal2 :size="18" :stroke-width="1.8" aria-hidden="true" />
          <span>{{ t("console.terminal") }}</span>
          <span class="status" :class="{ online: terminalConnected }">
            {{
              terminalConnected
                ? t("console.connected")
                : t("console.disconnected")
            }}
          </span>
        </div>
        <div class="pane-actions">
          <button
            type="button"
            :title="t('console.clear')"
            :aria-label="t('console.clear')"
            @click="clearTerminal"
          >
            <IconClearAll :size="18" :stroke-width="1.8" />
          </button>
          <button
            type="button"
            :title="t('console.reconnect')"
            :aria-label="t('console.reconnect')"
            @click="reconnectTerminal"
          >
            <IconRefresh :size="18" :stroke-width="1.8" />
          </button>
        </div>
      </header>
      <div
        ref="terminalElement"
        class="terminal-screen"
        role="application"
        :aria-label="t('console.terminalAria')"
      />
    </section>

    <section class="logs-pane panel">
      <header class="pane-header">
        <div>
          <IconTopologyStar3
            :size="18"
            :stroke-width="1.8"
            aria-hidden="true"
          />
          <span>{{ t("console.runtimeLogs") }}</span>
          <span class="status" :class="{ online: logsConnected }">
            {{ logsConnected ? "Live" : t("console.reconnection") }}
          </span>
        </div>
      </header>
      <pre ref="logElement" class="log-output" aria-live="polite">{{
        logs || t("console.emptyLogs")
      }}</pre>
    </section>
  </div>
</template>

<style scoped>
.console-workspace {
  display: grid;
  grid-template-columns: minmax(0, 1.7fr) minmax(300px, 0.8fr);
  gap: 0.8rem;
  min-width: 0;
}
.runtime-pane,
.logs-pane {
  min-width: 0;
  overflow: hidden;
  background: #090a0c;
}
.pane-header {
  display: flex;
  min-height: 48px;
  align-items: center;
  justify-content: space-between;
  gap: 0.75rem;
  border-bottom: 1px solid var(--border);
  background: var(--surface-raised);
  padding: 0 0.65rem 0 0.9rem;
}
.pane-header > div {
  display: flex;
  min-width: 0;
  align-items: center;
  gap: 0.55rem;
  font-size: 0.82rem;
  font-weight: 650;
}
.status {
  display: inline-flex;
  align-items: center;
  gap: 0.35rem;
  color: var(--text-muted);
  font-size: 0.7rem;
  font-weight: 500;
}
.status::before {
  width: 6px;
  height: 6px;
  border-radius: 999px;
  background: var(--text-muted);
  content: "";
}
.status.online {
  color: #70d49d;
}
.status.online::before {
  background: #70d49d;
  box-shadow: 0 0 8px rgb(112 212 157 / 55%);
}
.pane-actions {
  flex: 0 0 auto;
  gap: 0.25rem !important;
}
.pane-actions button {
  display: inline-flex;
  width: 44px;
  height: 44px;
  cursor: pointer;
  align-items: center;
  justify-content: center;
  border: 0;
  border-radius: 7px;
  background: transparent;
  color: var(--text-muted);
  transition:
    background-color 180ms ease,
    color 180ms ease;
}
.pane-actions button:hover {
  background: var(--surface-subtle);
  color: var(--text);
}
.terminal-screen {
  height: min(65dvh, 680px);
  min-height: 440px;
  padding: 0.7rem 0.35rem 0.35rem 0.7rem;
}
.log-output {
  height: min(65dvh, 680px);
  min-height: 440px;
  margin: 0;
  overflow: auto;
  padding: 0.85rem;
  color: #c8cbd0;
  font:
    0.75rem/1.55 "IBM Plex Mono",
    "JetBrains Mono",
    ui-monospace,
    monospace;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.compact {
  grid-template-columns: minmax(0, 1.5fr) minmax(260px, 0.75fr);
}
.compact .terminal-screen,
.compact .log-output {
  height: 320px;
  min-height: 320px;
}
@media (max-width: 960px) {
  .console-workspace,
  .compact {
    grid-template-columns: minmax(0, 1fr);
  }
  .terminal-screen {
    height: 52dvh;
    min-height: 360px;
  }
  .log-output {
    height: 280px;
    min-height: 280px;
  }
}
@media (max-width: 520px) {
  .pane-header {
    padding-left: 0.7rem;
  }
  .status {
    font-size: 0;
  }
  .terminal-screen {
    height: 58dvh;
    min-height: 320px;
    padding-left: 0.45rem;
  }
}
@media (prefers-reduced-motion: reduce) {
  .pane-actions button {
    transition: none;
  }
}
</style>
