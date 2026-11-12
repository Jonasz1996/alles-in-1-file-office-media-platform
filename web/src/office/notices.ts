// De meldingen bij office.unsaved en office.recovered, met een link naar de herstelkopie. De
// luisteraar in App.tsx laadt dit pas bij de eerste gebeurtenis office.* (Keuze 33), samen met
// het gebied office, en geeft daarna elke gebeurtenis office.* hier door.
//
// office.unsaved met final false (een tussenstand; de sessie loopt door) toont de editorpagina
// zelf. Hier alleen wat blijft: niet opgeslagen (final true) en een herstelkopie. Beide blijven
// staan tot je ze sluit: ze gaan over werk.

import { api, type FileNode } from "../api";
import type { ServerEvent } from "../events";
import { errorText, t } from "../i18n";
import { nodeHref } from "../links";
import { navigate } from "../router";
import { type ToastOptions, toast } from "../ui/toast";
import { OFFICE_EVENTS, type RecoveredEvent, type UnsavedEvent } from "./types";

const UNSAVED_REASONS: readonly UnsavedEvent["reason"][] = ["no_room", "too_large", "owner_gone", "error_status"];
const RECOVERED_REASONS: readonly RecoveredEvent["reason"][] = ["status3", "no_writer", "forgotten"];

export interface NoticeDeps {
  show(message: string, o: ToastOptions): void;
  node(id: string): Promise<Pick<FileNode, "id" | "kind" | "parent_id">>;
  go(href: string): void;
  failed(message: string): void;
}

const browserDeps: NoticeDeps = {
  show: (message, o) => {
    toast(message, o);
  },
  node: (id) => api.request<FileNode>("GET", `/api/v1/files/${encodeURIComponent(id)}`),
  go: (href) => navigate(href),
  failed: (message) => {
    toast(message, { kind: "error" });
  },
};

/** De tekst van een melding, of null als het niet om een melding gaat. */
export function noticeFor(event: ServerEvent): { message: string; kind: "error" | "info"; copy: string | null } | null {
  if (event.type === OFFICE_EVENTS.unsaved) {
    const d = event.data as unknown as UnsavedEvent;
    if (!d.final || !UNSAVED_REASONS.includes(d.reason)) return null;
    return { message: t(`office.toast.unsaved.${d.reason}`, { name: d.name }), kind: "error", copy: null };
  }
  if (event.type === OFFICE_EVENTS.recovered) {
    const d = event.data as unknown as RecoveredEvent;
    if (!RECOVERED_REASONS.includes(d.reason) || typeof d.copy_id !== "string") return null;
    return { message: t(`office.toast.recovered.${d.reason}`, { name: d.name }), kind: "info", copy: d.copy_id };
  }
  return null;
}

export function handle(event: ServerEvent, deps: NoticeDeps = browserDeps): void {
  const notice = noticeFor(event);
  if (!notice) return;
  const copy = notice.copy;
  deps.show(notice.message, {
    kind: notice.kind,
    timeoutMs: 0,
    id: `office-${event.type}-${copy ?? String(event.data.node_id ?? event.data.name)}`,
    action: copy
      ? {
          label: t("office.toast.open"),
          run: () => {
            deps.node(copy).then(
              (node) => deps.go(nodeHref(node)),
              (e: unknown) => deps.failed(errorText(e)),
            );
          },
        }
      : undefined,
  });
}
