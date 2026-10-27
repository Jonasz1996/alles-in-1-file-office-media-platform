// De werking van de editorpagina (Keuze 32), zonder DOM, zodat hij te testen is. EditorPage.tsx
// tekent de toestand en geeft de gebeurtenissen door; alles wat de browser doet (de sessie
// vragen, api.js laden en DocEditor maken, het cookie, de focus, herladen) komt binnen als Deps.
//
// Het verloop:
// - openen: POST …/session; bij 409 office_busy elke 3 s opnieuw, hooguit 30 s (Keuze 14);
// - stale: een melding "buiten de editor veranderd" (Keuze 19);
// - de editor maken met de events van Keuze 32 (nooit onRequestSaveAs: A16). Geen onAppReady
//   binnen 20 s: de editor één keer opnieuw maken, daarna een melding (A22);
// - onDocumentReady: bewerken, en de focus naar de iframe;
// - onRequestRefreshFile, en office.dropped met reden stale: een nieuwe sessie en
//   refreshFile(config); lukt dat niet, de pagina opnieuw laden (Keuze 15 en 32, A21);
// - office.dropped met reden access: de editor dicht, met een melding;
// - onWarning -18: "verbinding weg" (Keuze 28);
// - office.unsaved met final false: een melding in de pagina (de rest doet notices.ts);
// - office.presence en node.changed: de statusregel ("Bewerken met 2 anderen", "Opgeslagen
//   om 10:42");
// - het cookie elke 30 minuten vernieuwen (gate.ts), en bij het verlaten destroyEditor().

import { ApiError } from "../api";
import type { ServerEvent } from "../events";
import { errorText, fmt, t } from "../i18n";
import { matchKey } from "../ui/keys";
import type { Theme } from "../ui/theme";
import type { DocEditor } from "./loadApi";
import type { DroppedEvent, PresenceEvent, SessionIn, SessionOut, UnsavedEvent } from "./types";
import { OFFICE_EVENTS } from "./types";

export const BUSY_RETRY_MS = 3_000;
export const BUSY_MAX_MS = 30_000;
export const READY_MS = 20_000;
/** Zo lang blijft "Opslaan…" staan als er geen node.changed komt (niets te bewaren). */
export const SAVING_MS = 10_000;
/** De waarschuwing van DS als de verbinding weg is [gemeten]. */
export const WARN_OFFLINE = -18;

export type Phase = "opening" | "loading" | "ready" | "failed" | "gone";
export type Connection = "connecting" | "online" | "offline";

export type NoticeKind = "stale" | "busy" | "offline" | "dropped" | "not_ready" | "error" | "unsaved" | "failed";

export interface Notice {
  kind: NoticeKind;
  text: string;
  /** Met de knop "Opnieuw laden" */
  reload: boolean;
}

export interface EditorState {
  phase: Phase;
  connection: Connection;
  /** Hoeveel anderen er volgens office.presence in zitten */
  others: number;
  saving: boolean;
  /** Wanneer het bestand het laatst veranderde terwijl de editor open was (ms) */
  savedAt: number | null;
  notices: readonly Notice[];
  key: string | null;
  /** De naam in de editor (document.title van de config) */
  title: string | null;
}

/** De events die de pagina aan DocEditor geeft (Keuze 32). Nooit onRequestSaveAs (A16). */
export interface EditorEvents {
  onAppReady(): void;
  onDocumentReady(): void;
  onDocumentStateChange(e: { data?: unknown }): void;
  onError(e: { data?: { errorCode?: number; errorDescription?: string } }): void;
  onWarning(e: { data?: { warningCode?: number; warningDescription?: string } }): void;
  onRequestClose(): void;
  onRequestRefreshFile(): void;
}

export interface Timers {
  set(fn: () => void, ms: number): unknown;
  clear(handle: unknown): void;
}

export interface Deps {
  /** POST …/session */
  open(): Promise<SessionOut>;
  /** api.js laden en new DocsAPI.DocEditor(…, config met events) maken */
  mount(session: SessionOut, events: EditorEvents): Promise<DocEditor>;
  /** POST …/save */
  saveNow(): Promise<void>;
  /** Het cookie elke 30 minuten vernieuwen; geeft de functie om te stoppen */
  keepGate(): () => void;
  /** De focus naar de iframe (na onDocumentReady) */
  focusFrame(): void;
  /** Terug naar waar je vandaan kwam (Sluiten, onRequestClose) */
  close(): void;
  /** De hele pagina opnieuw laden */
  reload(): void;
  timers?: Timers;
  now?: () => number;
}

const browserTimers: Timers = {
  set: (fn, ms) => setTimeout(fn, ms),
  clear: (handle) => clearTimeout(handle as ReturnType<typeof setTimeout>),
};

export function initialState(): EditorState {
  return {
    phase: "opening",
    connection: "connecting",
    others: 0,
    saving: false,
    savedAt: null,
    notices: [],
    key: null,
    title: null,
  };
}

/** Is dit de fout met deze code? */
export function hasCode(e: unknown, code: string): boolean {
  return e instanceof ApiError && e.code === code;
}

/** Uit de iframe naar de kopbalk (Keuze 32; DS gebruikt hem niet, spike stap 6). */
export const LEAVE_COMBO = "alt+shift+q";

/** Alt+Shift+Q, ook als de toets een ander teken geeft (op een Mac "Œ"). */
export function isLeaveKey(
  e: Pick<KeyboardEvent, "key" | "code" | "altKey" | "shiftKey" | "ctrlKey" | "metaKey">,
  isMac?: boolean,
): boolean {
  return matchKey(e, LEAVE_COMBO, isMac);
}

/** Wat de pagina bij het openen meestuurt: het thema van de app (systeem: wat het systeem nu
 * zegt) en "mobile" op een smal scherm (Keuze 32). */
export function sessionBody(theme: Theme, systemDark: boolean, narrow: boolean): SessionIn {
  const dark = theme === "dark" || (theme === "system" && systemDark);
  return { type: narrow ? "mobile" : "desktop", theme: dark ? "dark" : "light" };
}

/** De tekst van de statusregio (role="status"). */
export function statusText(s: EditorState): string {
  if (s.phase === "gone") return t("office.status.closed");
  if (s.phase !== "ready") return t("office.status.connecting");
  if (s.connection === "offline") return t("office.status.offline");
  if (s.connection === "connecting") return t("office.status.connecting");
  if (s.saving) return t("office.status.saving");
  const who = s.others > 0 ? t("office.status.others", { n: s.others }) : t("office.status.editing");
  return s.savedAt === null ? who : `${who} · ${t("office.status.saved", { time: fmt.time(s.savedAt) })}`;
}

/** Hoort deze gebeurtenis bij dit bestand? (node.changed kan ook in bulk: ids) */
function aboutNode(event: ServerEvent, nodeId: string): boolean {
  const d = event.data;
  if (d.node_id === nodeId || d.id === nodeId) return true;
  return Array.isArray(d.ids) && d.ids.includes(nodeId);
}

export class EditorSession {
  private state: EditorState = initialState();
  private readonly listeners = new Set<() => void>();
  private readonly timers: Timers;
  private readonly now: () => number;
  private editor: DocEditor | null = null;
  private session: SessionOut | null = null;
  private readyTimer: unknown = null;
  private savingTimer: unknown = null;
  private stopGate: (() => void) | null = null;
  private appReady = false;
  private mounts = 0;
  private refreshing = false;
  private destroyed = false;

  constructor(
    private readonly nodeId: string,
    private readonly deps: Deps,
  ) {
    this.timers = deps.timers ?? browserTimers;
    this.now = deps.now ?? Date.now;
  }

  // --- voor React (useSyncExternalStore) ---

  getState = (): EditorState => this.state;

  subscribe = (cb: () => void): (() => void) => {
    this.listeners.add(cb);
    return () => {
      this.listeners.delete(cb);
    };
  };

  private set(change: Partial<EditorState>): void {
    this.state = { ...this.state, ...change };
    for (const l of [...this.listeners]) l();
  }

  private notice(kind: NoticeKind, text: string, reload = false): void {
    const others = this.state.notices.filter((n) => n.kind !== kind);
    this.set({ notices: [...others, { kind, text, reload }] });
  }

  private clearNotice(kind: NoticeKind): void {
    if (this.state.notices.some((n) => n.kind === kind)) {
      this.set({ notices: this.state.notices.filter((n) => n.kind !== kind) });
    }
  }

  // --- openen ---

  async start(): Promise<void> {
    let session: SessionOut | null;
    try {
      session = await this.openRetrying();
    } catch (e) {
      if (this.destroyed) return;
      this.clearNotice("busy");
      this.set({ phase: "failed" });
      this.notice("failed", errorText(e), !(e instanceof ApiError) || e.status >= 500);
      return;
    }
    if (!session || this.destroyed) return;
    this.accept(session);
    this.stopGate = this.deps.keepGate();
    await this.mount();
  }

  /** POST …/session; bij office_busy elke 3 s opnieuw, hooguit 30 s. null: de pagina is weg. */
  private async openRetrying(): Promise<SessionOut | null> {
    const tries = Math.floor(BUSY_MAX_MS / BUSY_RETRY_MS) + 1;
    for (let i = 1; ; i++) {
      try {
        const out = await this.deps.open();
        this.clearNotice("busy");
        return out;
      } catch (e) {
        if (this.destroyed) return null;
        if (!hasCode(e, "office_busy") || i >= tries) throw e;
        this.notice("busy", t("office.notice.busy"));
        await this.wait(BUSY_RETRY_MS);
        if (this.destroyed) return null;
      }
    }
  }

  private wait(ms: number): Promise<void> {
    return new Promise((resolve) => {
      this.timers.set(resolve, ms);
    });
  }

  private accept(session: SessionOut): void {
    this.session = session;
    const doc = (session.config.document ?? {}) as { title?: unknown };
    this.set({ key: session.key, title: typeof doc.title === "string" ? doc.title : this.state.title });
    if (session.stale) this.notice("stale", t("office.notice.stale"));
  }

  private async mount(): Promise<void> {
    if (!this.session || this.destroyed) return;
    this.mounts += 1;
    this.appReady = false;
    this.set({ phase: "loading", connection: "connecting" });
    try {
      this.editor = await this.deps.mount(this.session, this.events());
    } catch (e) {
      if (this.destroyed) return;
      this.set({ phase: "failed" });
      this.notice("failed", errorText(e), true);
      return;
    }
    if (this.destroyed) {
      this.editor.destroyEditor();
      this.editor = null;
      return;
    }
    this.timers.clear(this.readyTimer);
    this.readyTimer = this.timers.set(() => this.notReady(), READY_MS);
  }

  /** Geen onAppReady binnen 20 s (A22): één keer opnieuw, daarna een melding. */
  private notReady(): void {
    if (this.appReady || this.destroyed) return;
    this.editor?.destroyEditor();
    this.editor = null;
    if (this.mounts < 2) {
      void this.mount();
      return;
    }
    this.set({ phase: "failed" });
    this.notice("not_ready", t("office.notice.notReady"), true);
  }

  private events(): EditorEvents {
    return {
      onAppReady: () => {
        this.appReady = true;
        this.timers.clear(this.readyTimer);
      },
      onDocumentReady: () => {
        if (this.destroyed) return;
        this.appReady = true;
        this.timers.clear(this.readyTimer);
        this.clearNotice("offline");
        this.set({ phase: "ready", connection: "online" });
        this.deps.focusFrame();
      },
      onDocumentStateChange: () => {
        // DS meldt zo dat er wijzigingen zijn (true) of dat ze bij DS zijn (false): de verbinding
        // werkt dus
        if (this.state.connection === "offline") {
          this.clearNotice("offline");
          this.set({ connection: "online" });
        }
      },
      onError: (e) => {
        if (this.destroyed) return;
        const code = e.data?.errorCode ?? 0;
        this.notice("error", t("office.notice.error", { code }), true);
      },
      onWarning: (e) => {
        if (this.destroyed) return;
        if (e.data?.warningCode === WARN_OFFLINE) {
          this.set({ connection: "offline" });
          this.notice("offline", t("office.notice.offline"), true);
        }
      },
      onRequestClose: () => this.close(),
      onRequestRefreshFile: () => {
        void this.refresh();
      },
    };
  }

  /** Een nieuwe sessie en refreshFile(config); lukt dat niet, dan de pagina opnieuw laden. */
  async refresh(): Promise<void> {
    if (this.refreshing || this.destroyed) return;
    this.refreshing = true;
    try {
      const session = await this.openRetrying();
      if (!session || this.destroyed) return;
      const editor = this.editor;
      if (!editor?.refreshFile) {
        this.deps.reload();
        return;
      }
      this.accept(session);
      this.set({ connection: "connecting" });
      editor.refreshFile(session.config);
      this.set({ connection: this.state.phase === "ready" ? "online" : this.state.connection });
    } catch {
      if (!this.destroyed) this.deps.reload();
    } finally {
      this.refreshing = false;
    }
  }

  // --- de kopbalk ---

  async save(): Promise<void> {
    if (this.state.phase !== "ready" || this.destroyed) return;
    this.set({ saving: true });
    this.timers.clear(this.savingTimer);
    this.savingTimer = this.timers.set(() => this.set({ saving: false }), SAVING_MS);
    try {
      await this.deps.saveNow();
      this.clearNotice("error");
    } catch (e) {
      this.timers.clear(this.savingTimer);
      this.set({ saving: false });
      this.notice("error", errorText(e));
    }
  }

  close(): void {
    this.destroy();
    this.deps.close();
  }

  dismiss(kind: NoticeKind): void {
    this.clearNotice(kind);
  }

  // --- gebeurtenissen van de server ---

  onServerEvent(event: ServerEvent): void {
    if (this.destroyed) return;
    switch (event.type) {
      case OFFICE_EVENTS.presence: {
        const d = event.data as unknown as PresenceEvent;
        if (d.node_id !== this.nodeId) return;
        this.set({ others: Math.max(0, Number(d.count) - 1) });
        return;
      }
      case OFFICE_EVENTS.dropped: {
        const d = event.data as unknown as DroppedEvent;
        if (d.node_id !== this.nodeId) return;
        if (d.reason === "stale") {
          // De editor had een oude key: stil een nieuwe sessie
          if (d.key === this.state.key) void this.refresh();
          return;
        }
        this.destroy();
        this.notice("dropped", t("office.notice.dropped"));
        return;
      }
      case OFFICE_EVENTS.unsaved: {
        const d = event.data as unknown as UnsavedEvent;
        if (d.node_id !== this.nodeId || d.final) return;
        this.notice("unsaved", t(`office.notice.unsaved.${d.reason}`));
        return;
      }
      case "node.changed": {
        if (!aboutNode(event, this.nodeId) || this.state.phase !== "ready") return;
        this.timers.clear(this.savingTimer);
        this.clearNotice("unsaved");
        this.set({ saving: false, savedAt: this.now() });
        return;
      }
      default:
        return;
    }
  }

  // --- weg ---

  /** De editor weg (bij het verlaten van de pagina, Sluiten, of geen toegang meer). */
  destroy(): void {
    if (this.destroyed) return;
    this.destroyed = true;
    this.timers.clear(this.readyTimer);
    this.timers.clear(this.savingTimer);
    this.stopGate?.();
    this.stopGate = null;
    const editor = this.editor;
    this.editor = null;
    try {
      editor?.destroyEditor();
    } catch (e) {
      console.error(e);
    }
    this.set({ phase: "gone", saving: false });
  }
}
