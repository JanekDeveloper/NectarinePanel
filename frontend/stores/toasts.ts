export type ToastKind = "success" | "error" | "info";

export interface ToastMessage {
  id: number;
  kind: ToastKind;
  message: string;
}

export const useToastStore = defineStore("toasts", () => {
  const items = ref<ToastMessage[]>([]);
  let nextId = 1;

  function remove(id: number): void {
    items.value = items.value.filter((item) => item.id !== id);
  }

  function add(
    message: string,
    kind: ToastKind = "info",
    timeoutMs = 5000,
  ): number {
    const id = nextId++;
    items.value.push({ id, kind, message });
    if (import.meta.client && timeoutMs > 0) {
      window.setTimeout(() => remove(id), timeoutMs);
    }
    return id;
  }

  return { items, add, remove };
});
