// De viewer (P4): één bestand over het hele venster, met vorige en volgende, het toetsenbord en
// vegen. Afbeeldingen (zoomen), PDF (pagina's van de server), video en audio (de speler van de
// browser), tekst en code, kantoordocumenten en archieven. Elke soort is een eigen chunk.
//
// Een eigen <dialog> (niet ui/Dialog): de toegankelijke naam is precies de bestandsnaam, en de
// kop toont "naam · 3 / 120". Zoals ui/Dialog zet hij de scope "dialog" in een layout-effect,
// en daarbovenop "viewer" voor zijn eigen sneltoetsen.

import {
  type JSX,
  lazy,
  type PointerEvent as ReactPointerEvent,
  Suspense,
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from "react";
import { formatBytes } from "../api";
import { errorText, t } from "../i18n";
import { Button, IconButton } from "../ui/Button";
import { pushScope, useShortcuts } from "../ui/keys";
import { openShortcutHelp } from "../ui/ShortcutHelp";
import { Spinner } from "../ui/Spinner";
import { modals } from "../ui/topLayer";
import type { ViewerDetail } from "./api";
import { type ViewKind, viewKind } from "./media";
import { type Source, sourceOf } from "./source";
import type { ViewerItem, ViewerProps } from "./types";
import "./viewer.css";

const ZoomView = lazy(() => import("./ZoomView"));
const PdfView = lazy(() => import("./PdfView"));
const MediaView = lazy(() => import("./MediaView"));
const TextView = lazy(() => import("./TextView"));
const OfficeView = lazy(() => import("./OfficeView"));
const InfoPanel = lazy(() => import("./InfoPanel"));

/** Zo vaak (elke 3 s) vragen we een preview die nog gemaakt wordt opnieuw. */
const PENDING_ROUNDS = 20;
const PENDING_MS = 3000;
/** Zo ver (px) vegen is naar het volgende bestand. */
const SWIPE = 60;

function keyOf(item: ViewerItem): string {
  return `${item.id}|${item.etag ?? ""}|${item.version?.id ?? ""}`;
}

/** Details per bestand, voor deze viewer: terug naar een bestand vraagt niets opnieuw. */
function useDetails(source: Source) {
  const cache = useRef(new Map<string, Promise<ViewerDetail>>());
  return useCallback(
    (item: ViewerItem, fresh = false): Promise<ViewerDetail> => {
      const key = keyOf(item);
      let hit = fresh ? undefined : cache.current.get(key);
      if (!hit) {
        hit = source.detail(item);
        cache.current.set(key, hit);
        hit.catch(() => cache.current.delete(key));
      }
      return hit;
    },
    [source],
  );
}

type Loaded = { key: string; detail?: ViewerDetail; error?: unknown };

export default function Viewer({ items, index, onIndexChange, onClose, onNeedMore, source }: ViewerProps): JSX.Element | null {
  const src = sourceOf(source);
  const item = items[index] as ViewerItem | undefined;
  const dialog = useRef<HTMLDialogElement>(null);
  const stage = useRef<HTMLDivElement>(null);
  const getDetail = useDetails(src);
  const [loaded, setLoaded] = useState<Loaded | null>(null);
  const [info, setInfo] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const zoomed = useRef(false);
  const swipe = useRef<{ id: number; x: number; y: number } | null>(null);
  const key = item ? keyOf(item) : "";

  useLayoutEffect(() => {
    const el = dialog.current;
    const before = document.activeElement;
    const popDialog = pushScope("dialog");
    if (el && !el.open) el.showModal();
    const leave = el ? modals.open(el) : () => {};
    const popViewer = pushScope("viewer");
    stage.current?.focus();
    return () => {
      popViewer();
      popDialog();
      leave();
      el?.close();
      if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
      if (before instanceof HTMLElement && before.isConnected) before.focus();
    };
  }, []);

  // Het detail van dit bestand; wat nog gemaakt wordt, een tijdje opnieuw
  useEffect(() => {
    if (!item) return;
    let live = true;
    let rounds = 0;
    let timer: ReturnType<typeof setTimeout> | undefined;
    zoomed.current = false;
    setError(null);
    const load = (fresh: boolean) => {
      getDetail(item, fresh).then(
        (detail) => {
          if (!live) return;
          setLoaded({ key, detail });
          if (detail.state === "pending" && ++rounds < PENDING_ROUNDS) timer = setTimeout(() => load(true), PENDING_MS);
        },
        (e: unknown) => live && setLoaded({ key, error: e }),
      );
    };
    load(false);
    return () => {
      live = false;
      clearTimeout(timer);
    };
    // Op key: dezelfde inhoud opnieuw doorgegeven vraagt niets opnieuw
  }, [key, getDetail]);

  // De buren alvast: hun detail, en van een afbeelding de grote preview
  useEffect(() => {
    if (!item) return;
    const timer = setTimeout(() => {
      for (const near of [items[index + 1], items[index - 1]]) {
        if (!near) continue;
        getDetail(near).then(
          (d) => {
            const url = d.display ?? d.large;
            if (url && viewKind(near, d.meta) === "image") new Image().src = url;
          },
          () => {},
        );
      }
    }, 300);
    return () => clearTimeout(timer);
  }, [key, items, index, getDetail]);

  const go = useCallback(
    (step: 1 | -1) => {
      const next = index + step;
      if (next < 0 || next >= items.length) {
        onNeedMore?.(step);
        return;
      }
      onIndexChange(next);
    },
    [index, items.length, onIndexChange, onNeedMore],
  );

  const goTo = useCallback(
    (i: number) => {
      if (i !== index && i >= 0 && i < items.length) onIndexChange(i);
    },
    [index, items.length, onIndexChange],
  );

  async function download() {
    if (!item) return;
    try {
      const a = document.createElement("a");
      a.href = await src.url(item, false);
      a.download = item.name;
      a.rel = "noopener";
      a.click();
    } catch (e) {
      setError(errorText(e));
    }
  }

  async function openInTab() {
    if (!item) return;
    try {
      window.open(await src.url(item, true), "_blank", "noopener");
    } catch (e) {
      setError(errorText(e));
    }
  }

  function toggleFullscreen() {
    const done = document.fullscreenElement ? document.exitFullscreen() : document.documentElement.requestFullscreen?.();
    done?.catch(() => setError(t("viewer.fullscreenFailed")));
  }

  const notZoomed = () => !zoomed.current;
  useShortcuts(
    [
      { combo: "escape", run: onClose, label: t("viewer.keys.close"), allowInInput: true },
      { combo: "arrowleft", run: () => go(-1), when: notZoomed, label: t("viewer.keys.prev") },
      { combo: "arrowright", run: () => go(1), when: notZoomed, label: t("viewer.keys.next") },
      { combo: "shift+arrowleft", run: () => go(-1), label: t("viewer.keys.prevAlways") },
      { combo: "shift+arrowright", run: () => go(1), label: t("viewer.keys.nextAlways") },
      { combo: "home", run: () => goTo(0), when: notZoomed, label: t("viewer.keys.first") },
      { combo: "end", run: () => goTo(items.length - 1), when: notZoomed, label: t("viewer.keys.last") },
      { combo: "f", run: toggleFullscreen, label: t("viewer.keys.fullscreen") },
      { combo: "i", run: () => setInfo((v) => !v), label: t("viewer.keys.info") },
      { combo: "d", run: () => void download(), label: t("viewer.keys.download") },
      { combo: "?", run: openShortcutHelp, label: t("viewer.keys.help") },
    ],
    { scope: "viewer" },
  );

  if (!item) return null;

  const current = loaded?.key === key ? loaded : null;
  const detail = current?.detail;
  const kind: ViewKind = viewKind(item, detail?.meta ?? {});

  function onPointerDown(e: ReactPointerEvent) {
    if (e.pointerType === "mouse" || !e.isPrimary) return;
    swipe.current = { id: e.pointerId, x: e.clientX, y: e.clientY };
  }

  function onPointerUp(e: ReactPointerEvent) {
    const start = swipe.current;
    swipe.current = null;
    if (!start || start.id !== e.pointerId || zoomed.current) return;
    const dx = e.clientX - start.x;
    const dy = e.clientY - start.y;
    if (Math.abs(dx) > SWIPE && Math.abs(dx) > 2 * Math.abs(dy)) go(dx < 0 ? 1 : -1);
  }

  let body: JSX.Element;
  if (current?.error) {
    body = (
      <div className="viewer-message">
        <p role="alert">{errorText(current.error)}</p>
        <Button icon="download" onClick={download}>
          {t("viewer.download")}
        </Button>
      </div>
    );
  } else if (!detail) {
    body = (
      <div className="viewer-message">
        <Spinner label={t("viewer.loading")} />
      </div>
    );
  } else if (kind === "image") {
    body = (
      <ZoomView
        key={key}
        item={item}
        detail={detail}
        source={src}
        onZoomedChange={(z) => {
          zoomed.current = z;
        }}
      />
    );
  } else if (kind === "pdf") {
    body = <PdfView key={key} item={item} source={src} />;
  } else if (kind === "video" || kind === "audio") {
    body = (
      <MediaView
        key={key}
        item={item}
        detail={detail}
        source={src}
        kind={kind}
        onEnded={index < items.length - 1 ? () => go(1) : undefined}
      />
    );
  } else if (kind === "text") {
    body = <TextView key={key} item={item} detail={detail} source={src} />;
  } else if (kind === "office") {
    body = <OfficeView key={key} item={item} detail={detail} source={src} />;
  } else if (kind === "archive") {
    body = <ArchiveList detail={detail} />;
  } else {
    body = <NoPreview item={item} detail={detail} onDownload={download} />;
  }

  return (
    <dialog
      ref={dialog}
      className="viewer"
      aria-label={item.name}
      onCancel={(e) => {
        e.preventDefault(); // de ouder beslist; die haalt de viewer weg
        onClose();
      }}
    >
      <header className="viewer-head">
        <h2 className="viewer-title">
          <span className="viewer-name">{item.name}</span>
          {item.version && <span className="viewer-sub"> · {item.version.label}</span>}
          {items.length > 1 && (
            <span className="viewer-sub"> · {t("viewer.position", { index: index + 1, count: items.length })}</span>
          )}
        </h2>
        <div className="viewer-tools" role="toolbar" aria-label={t("viewer.toolbar")}>
          <IconButton
            icon="chevron-left"
            className="ghost"
            label={t("viewer.prev")}
            disabled={index === 0 && !onNeedMore}
            onClick={() => go(-1)}
          />
          <IconButton
            icon="chevron-right"
            className="ghost"
            label={t("viewer.next")}
            disabled={index === items.length - 1 && !onNeedMore}
            onClick={() => go(1)}
          />
          <IconButton icon="download" className="ghost" label={t("viewer.download")} onClick={download} />
          <IconButton icon="info" className="ghost" label={t("viewer.details")} pressed={info} onClick={() => setInfo((v) => !v)} />
          <IconButton icon="link" className="ghost" label={t("viewer.newTab")} onClick={openInTab} />
          <IconButton icon="fullscreen" className="ghost" label={t("viewer.fullscreen")} onClick={toggleFullscreen} />
          <IconButton icon="close" className="ghost" label={t("common.close")} onClick={onClose} />
        </div>
      </header>
      <div className="viewer-main">
        <div
          ref={stage}
          className="viewer-stage"
          tabIndex={-1}
          data-kind={kind}
          onPointerDown={onPointerDown}
          onPointerUp={onPointerUp}
          onPointerCancel={() => {
            swipe.current = null;
          }}
        >
          <Suspense
            fallback={
              <div className="viewer-message">
                <Spinner label={t("viewer.loading")} />
              </div>
            }
          >
            {body}
          </Suspense>
        </div>
        {info && (
          <Suspense fallback={null}>
            <InfoPanel item={item} detail={detail} kind={kind} onClose={() => setInfo(false)} />
          </Suspense>
        )}
      </div>
      {error && (
        <p className="viewer-error" role="alert">
          {error}
        </p>
      )}
    </dialog>
  );
}

/** De inhoud van een archief: de eerste bestanden met hun grootte. */
function ArchiveList({ detail }: { detail: ViewerDetail }) {
  const entries = detail.meta.entries ?? [];
  const more = (detail.meta.count ?? entries.length) - entries.length;
  return (
    <div className="viewer-scroll viewer-archive">
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>{t("viewer.archive.name")}</th>
              <th>{t("viewer.archive.size")}</th>
            </tr>
          </thead>
          <tbody>
            {entries.map((e, i) => (
              // Op volgorde: een naam kan in een archief dubbel voorkomen
              <tr key={i}>
                <td className="name">{e.name}</td>
                <td>{e.name.endsWith("/") ? "" : formatBytes(e.size)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {(more > 0 || detail.meta.truncated) && (
        <p className="muted">{more > 0 ? t("viewer.archive.more", { n: more }) : t("viewer.archive.andMore")}</p>
      )}
    </div>
  );
}

/** Geen weergave voor dit soort: de preview als die er is, en downloaden. */
function NoPreview({ item, detail, onDownload }: { item: ViewerItem; detail: ViewerDetail; onDownload(): void }) {
  return (
    <div className="viewer-message">
      {detail.large ? (
        <img src={detail.large} alt={item.name} className="viewer-fallback" />
      ) : (
        <p className="muted">
          {detail.state === "pending"
            ? t("viewer.pending")
            : detail.state === "failed"
              ? t("viewer.failed")
              : t("viewer.noPreview")}
        </p>
      )}
      <Button icon="download" onClick={onDownload}>
        {t("viewer.download")}
      </Button>
    </div>
  );
}
