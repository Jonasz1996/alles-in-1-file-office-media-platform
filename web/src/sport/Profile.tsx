// Het hoogteprofiel: de gefilterde hoogte als vlak, de ruwe GPS-hoogte als stippellijn erboven
// (zo zie je wat het filter herstelde). Met de muis of een vinger wijs je een punt aan; de kaart
// toont dat punt (onHover), en eronder staan afstand, hoogte, snelheid en hartslag daar.

import { type JSX, type PointerEvent as ReactPointerEvent, useMemo } from "react";
import { t } from "../i18n";
import type { Series, SportKind } from "./api";
import { km, meters, pace, speedKmh, usesPace } from "./format";

const W = 1000;
const H = 200;

function nearest(d: readonly number[], x: number): number {
  let lo = 0;
  let hi = d.length - 1;
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if (d[mid] <= x) lo = mid;
    else hi = mid;
  }
  return x - d[lo] <= d[hi] - x ? lo : hi;
}

export function Profile({
  series,
  sport,
  hover,
  onHover,
}: {
  series: Series;
  sport: SportKind;
  hover: number | null;
  onHover(i: number | null): void;
}): JSX.Element | null {
  const { d, ele, ele_raw: raw } = series;
  const shape = useMemo(() => {
    if (!ele || d.length < 2) return null;
    const vals = ele.filter((v): v is number => v !== null);
    const rawVals = (raw ?? []).filter((v): v is number => v !== null && v > -450);
    let min = Math.min(...vals);
    let max = Math.max(...vals);
    // De ruwe lijn mag buiten het vlak vallen, maar niet het beeld platdrukken: hooguit de helft
    // van het bereik erbij aan elke kant
    const span0 = Math.max(10, max - min);
    if (rawVals.length) {
      min = Math.max(Math.min(min, ...rawVals), min - span0 / 2);
      max = Math.min(Math.max(max, ...rawVals), max + span0 / 2);
    }
    const span = Math.max(10, max - min);
    const dmax = d[d.length - 1] || 1;
    const x = (i: number) => (d[i] / dmax) * W;
    const y = (v: number) => H - ((Math.max(min, Math.min(max, v)) - min) / span) * (H - 10) - 5;
    let area = `M0,${H}`;
    let line = "";
    let rawLine = "";
    for (let i = 0; i < d.length; i++) {
      const v = ele[i];
      if (v === null) continue;
      area += `L${x(i).toFixed(1)},${y(v).toFixed(1)}`;
      line += `${line ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`;
    }
    area += `L${W},${H}Z`;
    if (raw) {
      let pen = false;
      for (let i = 0; i < d.length; i++) {
        const v = raw[i];
        if (v === null) {
          pen = false;
          continue;
        }
        rawLine += `${pen ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`;
        pen = true;
      }
    }
    return { area, line, rawLine, min: Math.min(...vals), max: Math.max(...vals), dmax, x };
  }, [d, ele, raw]);

  if (!shape || !ele) return null;

  function onMove(e: ReactPointerEvent<SVGSVGElement>): void {
    if (!shape) return;
    const rect = e.currentTarget.getBoundingClientRect();
    const f = (e.clientX - rect.left) / rect.width;
    onHover(nearest(d, f * shape.dmax));
  }

  const hx = hover === null ? null : shape.x(hover);
  const v = hover === null ? null : series.speed?.[hover];
  const hr = hover === null ? null : series.hr?.[hover];
  const he = hover === null ? null : ele[hover];
  return (
    <figure className="sport-profile">
      <svg
        viewBox={`0 0 ${W} ${H}`}
        preserveAspectRatio="none"
        role="img"
        aria-label={t("sport.detail.profileLabel", { min: meters(shape.min), max: meters(shape.max) })}
        onPointerMove={onMove}
        onPointerDown={onMove}
        onPointerLeave={() => onHover(null)}
      >
        <path d={shape.area} className="sport-profile-area" />
        <path d={shape.line} className="sport-profile-line" vectorEffect="non-scaling-stroke" />
        {shape.rawLine && <path d={shape.rawLine} className="sport-profile-raw" vectorEffect="non-scaling-stroke" />}
        {hx !== null && <line x1={hx} x2={hx} y1={0} y2={H} className="sport-profile-cursor" vectorEffect="non-scaling-stroke" />}
      </svg>
      <figcaption className="sport-profile-legend muted">
        {hover !== null ? (
          <span className="sport-readout">
            {t("sport.units.km", { v: km(d[hover]) })}
            {he !== null && he !== undefined && ` · ${t("sport.units.m", { v: meters(he) })}`}
            {v !== null &&
              v !== undefined &&
              ` · ${usesPace(sport) && v > 0 ? t("sport.units.pace", { v: pace(1000 / v, 1000) }) : t("sport.units.kmh", { v: speedKmh(v) })}`}
            {hr !== null && hr !== undefined && ` · ${t("sport.units.bpm", { v: Math.round(hr) })}`}
          </span>
        ) : (
          <>
            <span className="sport-key sport-key-filtered">{t("sport.detail.filtered")}</span>
            {shape.rawLine && <span className="sport-key sport-key-raw">{t("sport.detail.raw")}</span>}
          </>
        )}
      </figcaption>
    </figure>
  );
}
