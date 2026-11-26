// De kaart van een activiteit: de route als SVG in Webmercator, met zoomen (knoppen, wieltje) en
// slepen. Met een tegelbron (GET /sport/config) liggen de kaarttegels eronder; die komen via de
// server (GET /sport/tiles/z/x/y, met het token) als object-URL, zodat de CSP 'self' blijft.
// Zonder bron een raster. hover: het punt dat het hoogteprofiel aanwijst.

import { type JSX, type PointerEvent as ReactPointerEvent, useEffect, useMemo, useRef, useState } from "react";
import { t } from "../i18n";
import { IconButton } from "../ui/Button";
import { type SportConfig, tileUrl } from "./api";

const TILE = 256;
const MAX_Z = 18;
const PAD = 24;

function worldX(lon: number, z: number): number {
  return ((lon + 180) / 360) * TILE * 2 ** z;
}

function worldY(lat: number, z: number): number {
  const r = (Math.max(-85.05, Math.min(85.05, lat)) * Math.PI) / 180;
  return ((1 - Math.asinh(Math.tan(r)) / Math.PI) / 2) * TILE * 2 ** z;
}

interface View {
  z: number;
  /** Het midden in wereldpixels op zoom z */
  cx: number;
  cy: number;
}

function fit(bbox: readonly [number, number, number, number], w: number, h: number): View {
  const [s, west, n, e] = bbox;
  let z = MAX_Z;
  for (; z > 1; z--) {
    const dx = worldX(e, z) - worldX(west, z);
    const dy = worldY(s, z) - worldY(n, z);
    if (dx <= w - 2 * PAD && dy <= h - 2 * PAD) break;
  }
  return { z, cx: (worldX(west, z) + worldX(e, z)) / 2, cy: (worldY(n, z) + worldY(s, z)) / 2 };
}

function rezoom(v: View, z: number, ax: number, ay: number): View {
  // ax, ay: het punt (in wereldpixels op v.z) dat op zijn plek blijft
  const nz = Math.max(1, Math.min(MAX_Z, z));
  const f = 2 ** (nz - v.z);
  return { z: nz, cx: ax * f - (ax - v.cx), cy: ay * f - (ay - v.cy) };
}

/** Object-URL's van tegels, gedeeld tussen kaarten tot de pagina weggaat. */
class TileCache {
  private urls = new Map<string, string | null>();
  private pending = new Map<string, Promise<void>>();
  constructor(private onLoad: () => void) {}
  get(z: number, x: number, y: number): string | null | undefined {
    const key = `${z}/${x}/${y}`;
    if (this.urls.has(key)) return this.urls.get(key);
    if (!this.pending.has(key)) {
      this.pending.set(
        key,
        tileUrl(z, x, y).then((u) => {
          this.urls.set(key, u);
          this.pending.delete(key);
          this.onLoad();
        }),
      );
    }
    return undefined;
  }
  dispose(): void {
    for (const u of this.urls.values()) if (u) URL.revokeObjectURL(u);
    this.urls.clear();
  }
}

export function RouteMap({
  lat,
  lon,
  bbox,
  config,
  hover,
}: {
  lat: readonly number[];
  lon: readonly number[];
  bbox: readonly [number, number, number, number];
  config: SportConfig | null;
  hover: number | null;
}): JSX.Element {
  const box = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ w: 0, h: 0 });
  const [view, setView] = useState<View | null>(null);
  const [, setTick] = useState(0);
  const cache = useRef<TileCache | null>(null);
  const drag = useRef<{ x: number; y: number; v: View } | null>(null);

  if (!cache.current) cache.current = new TileCache(() => setTick((n) => n + 1));
  useEffect(() => () => cache.current?.dispose(), []);

  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setSize({ w: el.clientWidth, h: el.clientHeight }));
    ro.observe(el);
    setSize({ w: el.clientWidth, h: el.clientHeight });
    return () => ro.disconnect();
  }, []);

  useEffect(() => {
    if (size.w > 0 && size.h > 0 && view === null) setView(fit(bbox, size.w, size.h));
  }, [size, bbox, view]);

  const v = view;
  const path = useMemo(() => {
    if (!v) return "";
    const ox = v.cx - size.w / 2;
    const oy = v.cy - size.h / 2;
    let d = "";
    for (let i = 0; i < lat.length; i++) {
      const x = worldX(lon[i], v.z) - ox;
      const y = worldY(lat[i], v.z) - oy;
      d += `${i === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`;
    }
    return d;
  }, [v, size, lat, lon]);

  function screen(i: number): [number, number] | null {
    if (!v || i < 0 || i >= lat.length) return null;
    return [worldX(lon[i], v.z) - (v.cx - size.w / 2), worldY(lat[i], v.z) - (v.cy - size.h / 2)];
  }

  function zoomBy(dz: number, sx = size.w / 2, sy = size.h / 2): void {
    setView((cur) => (cur ? rezoom(cur, cur.z + dz, cur.cx - size.w / 2 + sx, cur.cy - size.h / 2 + sy) : cur));
  }

  function onDown(e: ReactPointerEvent<SVGSVGElement>): void {
    if (!v) return;
    e.currentTarget.setPointerCapture(e.pointerId);
    drag.current = { x: e.clientX, y: e.clientY, v };
  }

  function onMove(e: ReactPointerEvent<SVGSVGElement>): void {
    const d = drag.current;
    if (!d) return;
    setView({ ...d.v, cx: d.v.cx - (e.clientX - d.x), cy: d.v.cy - (e.clientY - d.y) });
  }

  useEffect(() => {
    const el = box.current;
    if (!el) return;
    let acc = 0;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      acc += e.deltaY;
      if (Math.abs(acc) < 80) return;
      const rect = el.getBoundingClientRect();
      zoomBy(acc < 0 ? 1 : -1, e.clientX - rect.left, e.clientY - rect.top);
      acc = 0;
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  });

  const tiles: JSX.Element[] = [];
  if (v && config?.tiles && cache.current) {
    const n = 2 ** v.z;
    const ox = v.cx - size.w / 2;
    const oy = v.cy - size.h / 2;
    const x0 = Math.floor(ox / TILE);
    const y0 = Math.floor(oy / TILE);
    const x1 = Math.floor((ox + size.w) / TILE);
    const y1 = Math.floor((oy + size.h) / TILE);
    for (let ty = Math.max(0, y0); ty <= Math.min(n - 1, y1); ty++) {
      for (let tx = x0; tx <= x1; tx++) {
        const wx = ((tx % n) + n) % n;
        const href = cache.current.get(v.z, wx, ty);
        if (!href) continue;
        tiles.push(
          <image key={`${v.z}/${tx}/${ty}`} href={href} x={tx * TILE - ox} y={ty * TILE - oy} width={TILE} height={TILE} />,
        );
      }
    }
  }

  const start = screen(0);
  const finish = screen(lat.length - 1);
  const mark = hover === null ? null : screen(hover);

  return (
    <div className="sport-map" ref={box}>
      <svg
        role="img"
        aria-label={t("sport.detail.mapLabel")}
        width={size.w}
        height={size.h}
        onPointerDown={onDown}
        onPointerMove={onMove}
        onPointerUp={() => {
          drag.current = null;
        }}
        onDoubleClick={(e) => {
          const rect = e.currentTarget.getBoundingClientRect();
          zoomBy(1, e.clientX - rect.left, e.clientY - rect.top);
        }}
      >
        {!config?.tiles && (
          <>
            <defs>
              <pattern id="sport-grid" width="32" height="32" patternUnits="userSpaceOnUse">
                <path d="M32 0H0V32" className="sport-grid-line" />
              </pattern>
            </defs>
            <rect width="100%" height="100%" fill="url(#sport-grid)" />
          </>
        )}
        {tiles}
        <path d={path} className="sport-route-casing" />
        <path d={path} className="sport-route" />
        {start && <circle cx={start[0]} cy={start[1]} r={6} className="sport-start" />}
        {finish && <circle cx={finish[0]} cy={finish[1]} r={6} className="sport-finish" />}
        {mark && <circle cx={mark[0]} cy={mark[1]} r={7} className="sport-hover" />}
      </svg>
      <div className="sport-map-tools">
        <IconButton icon="zoom-in" label={t("sport.detail.zoomIn")} onClick={() => zoomBy(1)} />
        <IconButton icon="zoom-out" label={t("sport.detail.zoomOut")} onClick={() => zoomBy(-1)} />
        <IconButton icon="fullscreen" label={t("sport.detail.fit")} onClick={() => setView(fit(bbox, size.w, size.h))} />
      </div>
      {config?.tiles && config.attribution && <div className="sport-map-attribution">{config.attribution}</div>}
    </div>
  );
}
