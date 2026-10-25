// Een afbeelding in de viewer: passend in het venster, zoomen met + en −, het wieltje (rond de
// muis), knijpen en dubbelklikken, verschuiven door te slepen of (ingezoomd) met de pijltjes,
// en draaien. Eerst de grote preview (1024 px); is er meer nodig, dan het origineel (of 2048 px
// als de browser het origineel niet kan tonen), pas na img.decode(), zodat er niets knippert.

import {
  type JSX,
  type MouseEvent as ReactMouseEvent,
  type PointerEvent as ReactPointerEvent, useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { t } from "../i18n";
import { IconButton } from "../ui/Button";
import { useShortcuts } from "../ui/keys";
import { Spinner } from "../ui/Spinner";
import type { ViewerDetail } from "./api";
import { browserShows, wantsOriginal } from "./media";
import type { Source } from "./source";
import type { ViewerItem } from "./types";
import {
  type Arrow,
  arrowPan,
  distance,
  fitted,
  isZoomed,
  neededWidth,
  panBy,
  rotate,
  type Size,
  STEP,
  type ZoomState,
  zoomAt,
  zoomTo,
} from "./zoom";

const LARGE = 1024;

interface Props {
  item: ViewerItem;
  detail: ViewerDetail;
  source: Source;
  onZoomedChange(zoomed: boolean): void;
}

/** Welk adres nu, en welke breedte dat heeft (Infinity: het origineel). */
interface Shown {
  url: string;
  width: number;
}

export default function ZoomView({ item, detail, source, onZoomedChange }: Props): JSX.Element {
  const stage = useRef<HTMLDivElement>(null);
  const [viewport, setViewport] = useState<Size>({ w: 0, h: 0 });
  const [loadedSize, setLoadedSize] = useState<Size | null>(null);
  const [zoom, setZoom] = useState<ZoomState | null>(null);
  const [shown, setShown] = useState<Shown | null>(null);
  const [broken, setBroken] = useState(false);
  const pointers = useRef(new Map<number, { x: number; y: number }>());
  const drag = useRef<{ x: number; y: number; dist: number; zoom: ZoomState } | null>(null);
  const upgrading = useRef(false);
  const meta = detail.meta;
  const native = browserShows(item.mime);
  const animated = wantsOriginal(item.mime, meta);
  const natural: Size | null =
    meta.width && meta.height ? { w: meta.width, h: meta.height } : loadedSize;

  // De beginafbeelding: wat beweegt als origineel, anders de grote preview (of het origineel
  // als er geen preview is)
  useEffect(() => {
    let live = true;
    const preview = detail.large ?? null;
    if (!animated && preview) {
      setShown({ url: preview, width: (meta.sizes ?? []).includes(LARGE) ? LARGE : 256 });
    } else if (native) {
      source.url(item, true).then(
        (url) => live && setShown({ url, width: Infinity }),
        () => live && setBroken(true),
      );
    } else if (detail.display) {
      setShown({ url: detail.display, width: 2048 });
    } else {
      setBroken(true);
    }
    return () => {
      live = false;
    };
    // Eén keer per bestand: de viewer geeft elk bestand een eigen key
  }, []);

  useLayoutEffect(() => {
    const el = stage.current;
    if (!el) return;
    const measure = () => setViewport({ w: el.clientWidth, h: el.clientHeight });
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  const state: ZoomState | null = natural && viewport.w > 0 ? (zoom ?? fitted(natural, viewport)) : null;
  const zoomed = !!(state && natural && isZoomed(state, natural, viewport));

  useEffect(() => {
    onZoomedChange(zoomed);
  }, [zoomed, onZoomedChange]);
  useEffect(() => () => onZoomedChange(false), [onZoomedChange]);

  // Na een andere venstergrootte weer passend als je niet ingezoomd was (gedraaid blijft gedraaid)
  useEffect(() => {
    if (!zoomed) setZoom((z) => (z && natural ? fitted(natural, viewport, z.rotation) : null));
  }, [viewport.w, viewport.h]);

  // Meer pixels nodig dan de preview heeft: het origineel (of 2048) laden, en pas wisselen als
  // het gedecodeerd is
  useEffect(() => {
    if (!state || !natural || !shown || upgrading.current) return;
    const need = neededWidth(state, natural, window.devicePixelRatio || 1);
    if (need <= shown.width || shown.width === Infinity) return;
    let target: Promise<Shown> | null = null;
    if (native) target = source.url(item, true).then((url) => ({ url, width: Infinity }));
    else if (detail.display && shown.width < 2048) target = Promise.resolve({ url: detail.display, width: 2048 });
    if (!target) return;
    upgrading.current = true;
    target
      .then(async (next) => {
        const img = new Image();
        img.src = next.url;
        await img.decode();
        setShown(next);
      })
      .catch(() => {
        /* dan blijft de preview staan */
      })
      .finally(() => {
        upgrading.current = false;
      });
  });

  const update = useCallback(
    (fn: (s: ZoomState, n: Size, v: Size) => ZoomState | null) => {
      if (!state || !natural) return false;
      const next = fn(state, natural, viewport);
      if (!next) return false;
      setZoom(next);
      return true;
    },
    [state, natural, viewport],
  );

  const pan = (arrow: Arrow) => () => update((s, n, v) => arrowPan(s, arrow, n, v));
  const whenZoomed = () => zoomed;
  useShortcuts(
    [
      { combo: ["+", "="], run: () => update((s, n, v) => zoomAt(s, STEP, { x: 0, y: 0 }, n, v)), label: t("viewer.keys.zoomIn") },
      { combo: "-", run: () => update((s, n, v) => zoomAt(s, 1 / STEP, { x: 0, y: 0 }, n, v)), label: t("viewer.keys.zoomOut") },
      { combo: "0", run: () => update((s, n, v) => fitted(n, v, s.rotation)), label: t("viewer.keys.fit") },
      { combo: "1", run: () => update((s, n, v) => zoomTo(s, 1, n, v)), label: t("viewer.keys.actual") },
      { combo: "r", run: () => update((s, n, v) => rotate(s, n, v)), label: t("viewer.keys.rotate") },
      { combo: "arrowleft", run: pan("left"), when: whenZoomed, label: t("viewer.keys.pan") },
      { combo: "arrowright", run: pan("right"), when: whenZoomed },
      { combo: "arrowup", run: pan("up"), when: whenZoomed },
      { combo: "arrowdown", run: pan("down"), when: whenZoomed },
    ],
    { scope: "viewer" },
  );

  // Het wieltje: zoomen rond de muis (niet passief, anders mag preventDefault niet)
  useEffect(() => {
    const el = stage.current;
    if (!el) return;
    const onWheel = (e: WheelEvent) => {
      if (!state || !natural) return;
      e.preventDefault();
      const rect = el.getBoundingClientRect();
      const point = { x: e.clientX - rect.left - rect.width / 2, y: e.clientY - rect.top - rect.height / 2 };
      const factor = e.deltaY < 0 ? STEP : 1 / STEP;
      setZoom(zoomAt(state, factor, point, natural, viewport));
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, [state, natural, viewport]);

  function centerPoint(clientX: number, clientY: number) {
    const rect = stage.current!.getBoundingClientRect();
    return { x: clientX - rect.left - rect.width / 2, y: clientY - rect.top - rect.height / 2 };
  }

  function onPointerDown(e: ReactPointerEvent<HTMLDivElement>) {
    if (!state) return;
    pointers.current.set(e.pointerId, { x: e.clientX, y: e.clientY });
    const pts = [...pointers.current.values()];
    if (pts.length === 2 || zoomed) {
      e.currentTarget.setPointerCapture(e.pointerId);
      e.stopPropagation(); // geen vegen naar het volgende bestand
    }
    drag.current = { x: e.clientX, y: e.clientY, dist: pts.length === 2 ? distance(pts[0], pts[1]) : 0, zoom: state };
  }

  function onPointerMove(e: ReactPointerEvent<HTMLDivElement>) {
    if (!pointers.current.has(e.pointerId) || !drag.current || !natural) return;
    pointers.current.set(e.pointerId, { x: e.clientX, y: e.clientY });
    const pts = [...pointers.current.values()];
    const start = drag.current;
    if (pts.length === 2 && start.dist > 0) {
      const mid = centerPoint((pts[0].x + pts[1].x) / 2, (pts[0].y + pts[1].y) / 2);
      setZoom(zoomAt(start.zoom, distance(pts[0], pts[1]) / start.dist, mid, natural, viewport));
    } else if (pts.length === 1 && isZoomed(start.zoom, natural, viewport)) {
      setZoom(panBy(start.zoom, e.clientX - start.x, e.clientY - start.y, natural, viewport));
    }
  }

  function onPointerUp(e: ReactPointerEvent<HTMLDivElement>) {
    pointers.current.delete(e.pointerId);
    if (pointers.current.size === 0) drag.current = null;
    else if (state) {
      const [p] = [...pointers.current.values()];
      drag.current = { x: p.x, y: p.y, dist: 0, zoom: state };
    }
  }

  /** Dubbelklikken: passend <-> 100 % (een kleine afbeelding die al op 100 % staat: 200 %). */
  function onDoubleClick(e: ReactMouseEvent) {
    if (!state || !natural) return;
    if (zoomed) {
      setZoom(fitted(natural, viewport, state.rotation));
      return;
    }
    const target = state.scale >= 1 ? state.scale * 2 : 1;
    setZoom(zoomAt(state, target / state.scale, centerPoint(e.clientX, e.clientY), natural, viewport));
  }

  const percent = state ? Math.round(state.scale * 100) : 100;
  return (
    <div className="zoom">
      <div
        ref={stage}
        className={`zoom-stage${zoomed ? " zoomed" : ""}`}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={onPointerUp}
        onDoubleClick={onDoubleClick}
      >
        {broken ? (
          <p className="viewer-message muted">{t("viewer.image.broken")}</p>
        ) : !shown ? (
          <Spinner label={t("viewer.loading")} />
        ) : (
          <img
            src={shown.url}
            alt={item.name}
            draggable={false}
            className={`zoom-img${state ? "" : " measuring"}`}
            onLoad={(e) => {
              const img = e.currentTarget;
              if (!natural && img.naturalWidth) setLoadedSize({ w: img.naturalWidth, h: img.naturalHeight });
            }}
            onError={() => {
              // Het origineel lukt niet (bijv. verlopen): terug naar de preview, of niets
              if (shown.width === Infinity && detail.large && !animated) setShown({ url: detail.large, width: LARGE });
              else setBroken(true);
            }}
            style={
              state && natural
                ? {
                    width: `${natural.w}px`,
                    height: `${natural.h}px`,
                    transform: `translate(-50%, -50%) translate(${state.x}px, ${state.y}px) rotate(${state.rotation}deg) scale(${state.scale})`,
                  }
                : undefined
            }
          />
        )}
      </div>
      <div className="zoom-tools" role="toolbar" aria-label={t("viewer.image.tools")}>
        <IconButton icon="zoom-out" className="ghost" label={t("viewer.image.zoomOut")} onClick={() => update((s, n, v) => zoomAt(s, 1 / STEP, { x: 0, y: 0 }, n, v))} />
        <output className="zoom-percent" aria-live="polite">
          {t("viewer.image.percent", { n: percent })}
        </output>
        <IconButton icon="zoom-in" className="ghost" label={t("viewer.image.zoomIn")} onClick={() => update((s, n, v) => zoomAt(s, STEP, { x: 0, y: 0 }, n, v))} />
        <button type="button" className="btn ghost" onClick={() => update((s, n, v) => fitted(n, v, s.rotation))}>
          {t("viewer.image.fit")}
        </button>
        <button type="button" className="btn ghost" onClick={() => update((s, n, v) => zoomTo(s, 1, n, v))}>
          {t("viewer.image.actual")}
        </button>
        <IconButton icon="rotate" className="ghost" label={t("viewer.image.rotate")} onClick={() => update((s, n, v) => rotate(s, n, v))} />
      </div>
    </div>
  );
}
