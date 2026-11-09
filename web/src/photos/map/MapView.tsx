// De kaart zelf (Keuze 31): de landen als SVG in Webmercator, de groepen foto's als ronde knoppen,
// en wat je met muis, vinger en toetsenbord doet. Slepen schuift, knijpen en het wieltje zoomen
// rond het punt eronder, dubbelklik zoomt in. Met de focus op de kaart schuiven de pijlen, zoomen
// + en −, en zet Home al je foto's in beeld (keys.ts); Tab gaat langs de groepen in leesvolgorde,
// Enter of Spatie kiest er een. Geen tegels van buiten: alles komt uit de wereld van /kaart/ en de
// thumbnails van de app zelf (de CSP blijft gelijk).
//
// De landen staan één keer in pixels op zoom 0; de kaart schuift en schaalt ze met één transform,
// zodat slepen alleen een attribuut verandert. De stippen staan in pixels van het venster.

import {
  type JSX,
  type KeyboardEvent as ReactKeyboardEvent,
  memo,
  type PointerEvent as ReactPointerEvent,
  type ReactNode,
  useEffect,
  useRef,
} from "react";
import { t } from "../../i18n";
import type { Dot } from "./cells";
import { mapKey } from "./keys";
import { panBy, type Size, toScreen, type View, zoomAround } from "./view";
import { type Country, countryPaths } from "./world";

/** Pas na zoveel pixels is een druk op de kaart slepen (en geen klik op een stip). */
const DRAG_SLOP = 4;
/** Zoveel pixels van het wieltje is één zoomstap. */
const WHEEL_STEP = 300;

export interface MapViewProps {
  countries: readonly Country[] | null;
  view: View | null;
  dots: readonly Dot[];
  /** De naam voor een schermlezer: "Utrecht, 34 foto's" */
  labelOf(d: Dot): string;
  /** De plaatsnaam onder de stip (vanaf zoom 6), of null */
  nameOf(d: Dot): string | null;
  /** Het adres van de thumbnail van de omslag, of null/undefined (dan een gewone stip) */
  thumbOf(d: Dot): string | null | undefined;
  /** Een punt in de wereld (0–1) met een ring eromheen: de plek van "Op de kaart" */
  mark: readonly [number, number] | null;
  describedBy: string;
  onSize(size: Size): void;
  /** Het venster veranderen; f krijgt het laatste venster (ook als dat nog niet getekend is) */
  onView(f: (v: View) => View): void;
  onHome(): void;
  /** Een groep kiezen; true als de kaart daarvoor inzoomde (dan krijgt de kaart de focus, want de
   * stip is weg) */
  onActivate(d: Dot): boolean;
  /** Wat boven op de kaart ligt: de knoppen en meldingen */
  children?: ReactNode;
}

type Gesture =
  | { kind: "drag"; id: number; x0: number; y0: number; x: number; y: number; moved: boolean }
  | { kind: "pinch"; view: View; dist: number; mx: number; my: number };

/** De landen: alleen opnieuw als de wereld verandert, niet bij elke stap van het slepen. */
const Land = memo(function Land({ countries }: { countries: readonly Country[] }): JSX.Element {
  const paths = countryPaths(countries);
  return (
    <>
      {paths.map((d, i) => (
        <path key={countries[i].a3} d={d} />
      ))}
    </>
  );
});

function countText(n: number): string {
  return n >= 1000 ? `${Math.floor(n / 1000)}k` : String(n);
}

export function MapView(p: MapViewProps): JSX.Element {
  const box = useRef<HTMLDivElement>(null);
  const size = useRef<Size>({ w: 0, h: 0 });
  const pointers = useRef(new Map<number, { x: number; y: number }>());
  const gesture = useRef<Gesture | null>(null);
  /** Na slepen telt het loslaten niet als klik op een stip */
  const dragged = useRef(false);
  const latest = useRef(p);
  latest.current = p;

  // De grootte van het venster
  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const measure = () => {
      const s = { w: Math.round(el.clientWidth), h: Math.round(el.clientHeight) };
      if (s.w === size.current.w && s.h === size.current.h) return;
      size.current = s;
      latest.current.onSize(s);
    };
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // Het wieltje: niet passief, zodat de pagina niet meeschuift
  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const onWheel = (e: WheelEvent) => {
      if (!latest.current.view) return;
      e.preventDefault();
      const unit = e.deltaMode === 1 ? 16 : e.deltaMode === 2 ? 400 : 1;
      const by = Math.max(-1, Math.min(1, (-e.deltaY * unit) / WHEEL_STEP));
      const r = el.getBoundingClientRect();
      const sx = e.clientX - r.left;
      const sy = e.clientY - r.top;
      latest.current.onView((v) => zoomAround(v, size.current, v.zoom + by, sx, sy));
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, []);

  function local(e: { clientX: number; clientY: number }): { x: number; y: number } {
    const r = box.current?.getBoundingClientRect();
    return { x: e.clientX - (r?.left ?? 0), y: e.clientY - (r?.top ?? 0) };
  }

  function startPinch(): void {
    const [a, b] = [...pointers.current.values()];
    const v = p.view;
    if (!a || !b || !v) return;
    gesture.current = {
      kind: "pinch",
      view: v,
      dist: Math.max(1, Math.hypot(a.x - b.x, a.y - b.y)),
      mx: (a.x + b.x) / 2,
      my: (a.y + b.y) / 2,
    };
  }

  function onPointerDown(e: ReactPointerEvent<HTMLDivElement>): void {
    if (e.pointerType === "mouse" && e.button !== 0) return;
    const at = local(e);
    pointers.current.set(e.pointerId, at);
    dragged.current = false;
    if (pointers.current.size === 2) {
      box.current?.setPointerCapture?.(e.pointerId);
      startPinch();
    } else if (pointers.current.size === 1) {
      gesture.current = { kind: "drag", id: e.pointerId, x0: at.x, y0: at.y, x: at.x, y: at.y, moved: false };
    }
  }

  function onPointerMove(e: ReactPointerEvent<HTMLDivElement>): void {
    if (!pointers.current.has(e.pointerId)) return;
    const at = local(e);
    pointers.current.set(e.pointerId, at);
    const g = gesture.current;
    if (!g) return;
    if (g.kind === "pinch") {
      const [a, b] = [...pointers.current.values()];
      if (!a || !b) return;
      const dist = Math.max(1, Math.hypot(a.x - b.x, a.y - b.y));
      const mx = (a.x + b.x) / 2;
      const my = (a.y + b.y) / 2;
      const s = size.current;
      const zoomed = zoomAround(g.view, s, g.view.zoom + Math.log2(dist / g.dist), g.mx, g.my);
      p.onView(() => panBy(zoomed, s, mx - g.mx, my - g.my));
      dragged.current = true;
      return;
    }
    if (g.id !== e.pointerId) return;
    if (!g.moved) {
      if (Math.hypot(at.x - g.x0, at.y - g.y0) < DRAG_SLOP) return;
      g.moved = true;
      dragged.current = true;
      box.current?.setPointerCapture?.(e.pointerId);
    }
    const dx = at.x - g.x;
    const dy = at.y - g.y;
    g.x = at.x;
    g.y = at.y;
    p.onView((v) => panBy(v, size.current, dx, dy));
  }

  function onPointerEnd(e: ReactPointerEvent<HTMLDivElement>): void {
    if (!pointers.current.delete(e.pointerId)) return;
    const left = [...pointers.current.entries()];
    if (left.length === 1) {
      // Van knijpen terug naar slepen met de vinger die er nog ligt
      const [id, at] = left[0];
      gesture.current = { kind: "drag", id, x0: at.x, y0: at.y, x: at.x, y: at.y, moved: true };
    } else if (left.length === 0) {
      gesture.current = null;
    }
  }

  function onKeyDown(e: ReactKeyboardEvent<HTMLDivElement>): void {
    if (e.defaultPrevented || !p.view) return;
    const action = mapKey(e, size.current);
    if (!action) return;
    e.preventDefault();
    const s = size.current;
    if (action.kind === "pan") p.onView((v) => panBy(v, s, action.dx, action.dy));
    else if (action.kind === "zoom") p.onView((v) => zoomAround(v, s, v.zoom + action.by, s.w / 2, s.h / 2));
    else p.onHome();
  }

  function onDotKey(e: ReactKeyboardEvent<SVGGElement>, d: Dot): void {
    if (e.key !== "Enter" && e.key !== " ") return;
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    e.preventDefault();
    activate(d);
  }

  function activate(d: Dot): void {
    if (p.onActivate(d)) box.current?.focus({ preventScroll: true });
  }

  const v = p.view;
  const s = size.current;
  let land: JSX.Element | null = null;
  let mark: JSX.Element | null = null;
  if (v && s.w > 0) {
    const scale = 2 ** v.zoom;
    const [tx, ty] = toScreen(v, s, 0, 0);
    if (p.countries) {
      land = (
        <g className="map-land" aria-hidden="true" transform={`translate(${tx} ${ty}) scale(${scale})`}>
          <Land countries={p.countries} />
        </g>
      );
    }
    if (p.mark) {
      const [mx, my] = toScreen(v, s, p.mark[0], p.mark[1]);
      mark = <circle className="map-mark" aria-hidden="true" cx={mx} cy={my} r={26} />;
    }
  }

  return (
    <div
      ref={box}
      className="map-view"
      tabIndex={0}
      role="region"
      aria-roledescription={t("photos_map.map.role")}
      aria-label={t("photos_map.map.label")}
      aria-describedby={p.describedBy}
      onKeyDown={onKeyDown}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerEnd}
      onPointerCancel={onPointerEnd}
      onDoubleClick={(e) => {
        // Niet op een stip of een knop: die doen zelf iets bij een klik
        if (!p.view || (e.target as Element).closest?.("[data-dot], button")) return;
        const at = local(e);
        p.onView((cur) => zoomAround(cur, size.current, cur.zoom + 1, at.x, at.y));
      }}
    >
      {v && s.w > 0 && (
        <svg className="map-svg" width={s.w} height={s.h} viewBox={`0 0 ${s.w} ${s.h}`}>
          <defs>
            <clipPath id="map-rond" clipPathUnits="objectBoundingBox">
              <circle cx="0.5" cy="0.5" r="0.5" />
            </clipPath>
          </defs>
          {land}
          {mark}
          {p.dots.map((d) => {
            const thumb = p.thumbOf(d);
            const name = p.nameOf(d);
            const inner = d.r - 3;
            return (
              <g
                key={d.key}
                data-dot=""
                className="map-dot"
                role="button"
                tabIndex={0}
                aria-label={p.labelOf(d)}
                transform={`translate(${d.sx.toFixed(1)} ${d.sy.toFixed(1)})`}
                onClick={() => {
                  if (!dragged.current) activate(d);
                }}
                onKeyDown={(e) => onDotKey(e, d)}
              >
                <circle className="map-dot-ring" r={d.r} />
                {thumb ? (
                  <image
                    href={thumb}
                    x={-inner}
                    y={-inner}
                    width={inner * 2}
                    height={inner * 2}
                    preserveAspectRatio="xMidYMid slice"
                    clipPath="url(#map-rond)"
                  />
                ) : (
                  <circle className="map-dot-fill" r={inner} />
                )}
                {d.n > 1 && (
                  <g className="map-count" transform={`translate(${(d.r * 0.72).toFixed(1)} ${(-d.r * 0.72).toFixed(1)})`}>
                    <rect
                      x={-(5 + 3.6 * countText(d.n).length)}
                      y={-9}
                      width={10 + 7.2 * countText(d.n).length}
                      height={18}
                      rx={9}
                    />
                    <text textAnchor="middle" dy="0.35em">
                      {countText(d.n)}
                    </text>
                  </g>
                )}
                {name && (
                  <text className="map-label" textAnchor="middle" y={d.r + 15}>
                    {name}
                  </text>
                )}
              </g>
            );
          })}
        </svg>
      )}
      {p.children}
    </div>
  );
}
