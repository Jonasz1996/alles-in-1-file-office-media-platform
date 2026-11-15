// De rij Plek in het infopaneel (fase 6, van Z5; Keuze 27–30): de plaatsnaam, de coördinaten, de
// hoogte en een kleine kaart, alleen voor de eigenaar (GET /files/{id}/location; een ander krijgt
// 404, net als een foto zonder plek, en dan toont de rij niets). Het contract: de default export
// met InfoRowProps, een eigen chunk, met het gebied photos_map.
//
// "Op de kaart" opent /fotos/kaart met deze plek in het midden. De plek gaat via het geheugen van
// de pagina (photos/map/focus.ts), nooit via het adres: een plek hoort niet in de adresbalk of in
// het access-log. De kleine kaart is versiering (aria-hidden); de tekst zegt hetzelfde.

import { type JSX, type MouseEvent, useEffect, useState } from "react";
import { t } from "../i18n";
import { Link } from "../links";
import { fileLocation } from "../photos/map/api";
import { showOnMap } from "../photos/map/focus";
import { latToY, lonToX } from "../photos/map/projection";
import { locationLines } from "../photos/map/text";
import type { FileLocation } from "../photos/map/types";
import { clampView, type Size, toScreen } from "../photos/map/view";
import { type Country, countryPaths, loadWorld } from "../photos/map/world";
import { isPlainLeftClick } from "../router";
import type { InfoRowProps } from "./infoRows";
import "./location.css";

/** De kleine kaart: zo groot (in pixels van de viewBox) en zo ver ingezoomd (een stuk werelddeel). */
const MINI: Size = { w: 240, h: 120 };
const MINI_ZOOM = 3;

function MiniMap({ countries, loc }: { countries: readonly Country[]; loc: FileLocation }): JSX.Element {
  const x = lonToX(loc.lon);
  const y = latToY(loc.lat);
  const v = clampView({ x, y, zoom: MINI_ZOOM }, MINI);
  const [tx, ty] = toScreen(v, MINI, 0, 0);
  const [px, py] = toScreen(v, MINI, x, y);
  const paths = countryPaths(countries);
  return (
    <svg className="location-map" viewBox={`0 0 ${MINI.w} ${MINI.h}`} aria-hidden="true" focusable="false">
      <g transform={`translate(${tx} ${ty}) scale(${2 ** v.zoom})`}>
        {paths.map((d, i) => (
          <path key={countries[i].a3} d={d} />
        ))}
      </g>
      <circle className="location-pin" cx={px} cy={py} r={5} />
    </svg>
  );
}

export default function LocationRow({ item }: InfoRowProps): JSX.Element | null {
  const [loc, setLoc] = useState<FileLocation | null>(null);
  const [countries, setCountries] = useState<Country[] | null>(null);
  const fileId = item.id;

  useEffect(() => {
    let live = true;
    setLoc(null);
    fileLocation(fileId).then(
      (l) => {
        if (live) setLoc(l);
      },
      () => {
        // 404 (geen plek, of niet van jou), weg of geen verbinding: dan geen rij
        if (live) setLoc(null);
      },
    );
    return () => {
      live = false;
    };
  }, [fileId]);

  const hasLoc = loc !== null;
  useEffect(() => {
    if (!hasLoc) return;
    let live = true;
    loadWorld().then(
      (w) => {
        if (live) setCountries(w);
      },
      () => {},
    );
    return () => {
      live = false;
    };
  }, [hasLoc]);

  if (!loc) return null;
  const headingId = `plek-${fileId}`;
  return (
    <section className="location-row" aria-labelledby={headingId}>
      <h4 id={headingId}>{t("photos_map.location.title")}</h4>
      {countries && <MiniMap countries={countries} loc={loc} />}
      {locationLines(loc).map((line) => (
        <p key={line}>{line}</p>
      ))}
      <p className="muted">{t("photos_map.location.onlyYou")}</p>
      <Link
        to="/fotos/kaart"
        className="btn"
        onClick={(e: MouseEvent<HTMLAnchorElement>) => {
          if (isPlainLeftClick(e)) showOnMap(loc.lat, loc.lon);
        }}
      >
        {t("photos_map.location.onMap")}
      </Link>
    </section>
  );
}
