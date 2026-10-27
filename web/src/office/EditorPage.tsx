// De editorpagina /bewerken/:id (Keuze 32), een lazy chunk met het gebied office. Van O2.
//
// Boven de iframe een kopbalk: de naam (<h1>), Sluiten (eerste in de tabvolgorde), Nu opslaan en
// een statusregio. Toetsen in de iframe gaan naar DS; Alt+Shift+Q zet de focus op Sluiten (de
// pagina luistert ook in de iframe: dezelfde origin). De werking zelf staat in editor.ts; hier
// alleen de DOM: api.js laden, DocEditor maken, de iframe een titel en de focus geven.
//
// Zonder office (of als de server het niet zegt) is het adres er niet, net als in fase 3.

import "./office.css";
import { type JSX, useEffect, useRef, useState, useSyncExternalStore } from "react";
import { api, type FileNode } from "../api";
import { useServerEvents } from "../events";
import { t } from "../i18n";
import { back, type RouteProps } from "../router";
import NotFound from "../shellPages";
import { Button } from "../ui/Button";
import { useScope, useShortcuts } from "../ui/keys";
import { PageTitle } from "../ui/PageTitle";
import { readTheme } from "../ui/theme";
import { EditorSession, type EditorState, initialState, isLeaveKey, LEAVE_COMBO, sessionBody, statusText } from "./editor";
import { keepGate } from "./gate";
import { officeStatus, openSession, saveNow, useOfficeStatus } from "./index";
import { loadApi, withoutInlineHandlers } from "./loadApi";

/** Het element dat DocEditor door zijn iframe vervangt. */
const PLACEHOLDER = "office-editor";
const NARROW = "(max-width: 40rem)";
const INITIAL = initialState();
const noSubscribe = () => () => {};

export default function EditorPage({ params }: RouteProps): JSX.Element {
  const status = useOfficeStatus();
  // Eerst een vers antwoord van de server (ook als dat een fout is)
  const [asked, setAsked] = useState(false);
  useEffect(() => {
    let live = true;
    void officeStatus.load().then(() => live && setAsked(true));
    return () => {
      live = false;
    };
  }, []);
  if (!asked) return <main className="center">{t("common.loading")}</main>;
  if (status === null || status.state === "off") return <NotFound params={params} />;
  return <Editor key={params.id} id={params.id} />;
}

function media(query: string): boolean {
  return typeof window !== "undefined" && !!window.matchMedia?.(query).matches;
}

/** De map van het bestand: waar Sluiten heen gaat als er geen stap terug is (een deep link). */
function folderOf(node: FileNode | null): string {
  if (!node) return "/";
  return node.parent_id ? `/map/${node.parent_id}` : "/gedeeld";
}

function Editor({ id }: { id: string }): JSX.Element {
  const box = useRef<HTMLDivElement>(null);
  const closeButton = useRef<HTMLButtonElement>(null);
  const node = useRef<FileNode | null>(null);
  const [name, setName] = useState<string | null>(null);
  const [editor, setEditor] = useState<EditorSession | null>(null);
  const state: EditorState = useSyncExternalStore(
    editor?.subscribe ?? noSubscribe,
    editor?.getState ?? (() => INITIAL),
  );

  useScope("editor");
  useShortcuts([{ combo: LEAVE_COMBO, run: () => closeButton.current?.focus(), label: t("office.page.leaveHint"), allowInInput: true }], {
    scope: "editor",
  });

  // De naam en de map van het bestand (voor de kop en voor Sluiten)
  useEffect(() => {
    let live = true;
    api.request<FileNode>("GET", `/api/v1/files/${encodeURIComponent(id)}`).then(
      (n) => {
        if (!live) return;
        node.current = n;
        setName(n.name);
      },
      () => {
        /* geen naam: dan die uit de editorconfig */
      },
    );
    return () => {
      live = false;
    };
  }, [id]);

  useEffect(() => {
    const frames = new WeakSet<HTMLIFrameElement>();
    let title = "";

    /** Elke iframe die DocEditor neerzet: een titel, en Alt+Shift+Q in zijn venster. */
    function watchFrames(): void {
      const frame = box.current?.querySelector("iframe");
      if (!frame) return;
      if (title) frame.title = title;
      if (frames.has(frame)) return;
      frames.add(frame);
      const listen = () => {
        try {
          frame.contentWindow?.addEventListener("keydown", (e) => {
            if (!isLeaveKey(e)) return;
            e.preventDefault();
            closeButton.current?.focus();
          });
        } catch {
          /* een andere origin: dan alleen de sneltoets van de pagina */
        }
      };
      frame.addEventListener("load", listen);
      listen();
    }

    const observer = new MutationObserver(watchFrames);
    if (box.current) observer.observe(box.current, { childList: true, subtree: true });

    const session = new EditorSession(id, {
      open: () => openSession(id, sessionBody(readTheme(), media("(prefers-color-scheme: dark)"), media(NARROW))),
      mount: async (out, events) => {
        const docs = await loadApi(out.api_url);
        const host = box.current;
        if (!host) throw new Error("de pagina is weg");
        const doc = (out.config.document ?? {}) as { title?: unknown };
        title = t("office.page.frameTitle", { name: typeof doc.title === "string" ? doc.title : "" });
        const placeholder = document.createElement("div");
        placeholder.id = PLACEHOLDER;
        host.replaceChildren(placeholder);
        // Nooit onRequestSaveAs: dan toont DS "Kopie opslaan als", en dat bouwen we niet (A16)
        const made = withoutInlineHandlers(() => new docs.DocEditor(PLACEHOLDER, { ...out.config, events }));
        watchFrames();
        return made;
      },
      saveNow: () => saveNow(id),
      keepGate: () => keepGate(),
      focusFrame: () => {
        const frame = box.current?.querySelector("iframe");
        frame?.focus();
        try {
          frame?.contentWindow?.focus();
        } catch {
          /* andere origin */
        }
      },
      close: () => back(folderOf(node.current)),
      reload: () => window.location.reload(),
    });
    setEditor(session);
    void session.start();
    return () => {
      observer.disconnect();
      session.destroy();
    };
  }, [id]);

  useServerEvents((event) => editor?.onServerEvent(event));

  const heading = name ?? state.title ?? t("office.title");
  return (
    <main className="editor-page">
      <div className="editor-bar">
        <PageTitle
          actions={
            <>
              <Button ref={closeButton} onClick={() => (editor ? editor.close() : back(folderOf(node.current)))}>
                {t("office.page.close")}
              </Button>
              <Button
                variant="primary"
                disabled={state.phase !== "ready"}
                busy={state.saving}
                onClick={() => void editor?.save()}
              >
                {t("office.page.saveNow")}
              </Button>
            </>
          }
        >
          {heading}
        </PageTitle>
        <p className="editor-status" role="status">
          {statusText(state)}
        </p>
      </div>
      {state.notices.length > 0 && (
        <div className="editor-notices">
          {state.notices.map((n) => (
            <div key={n.kind} className={`editor-notice editor-notice-${n.kind}`}>
              <p role={n.kind === "stale" || n.kind === "busy" ? "status" : "alert"}>{n.text}</p>
              {n.reload && <Button onClick={() => window.location.reload()}>{t("office.page.reload")}</Button>}
            </div>
          ))}
        </div>
      )}
      {state.phase === "opening" && <p className="editor-opening muted">{t("office.page.opening")}</p>}
      <div ref={box} className="editor-frame" />
    </main>
  );
}
