// Een treffer op /zoeken: het pad en waar een klik heen gaat (fase 6, Z4; van searchQuery.ts van
// 0.7.0, dat Z4 verving). Losse functies, zonder React, zodat ze te testen zijn.

import type { SearchHit } from "../api";
import { t } from "../i18n";
import { isShared } from "../sharing";
import { category } from "../Thumb";

/**
 * "Mijn bestanden / Foto's / 2026" voor het pad van een zoekresultaat. Bij iets dat met je
 * gedeeld is begint het pad van de server bij de gedeelde map: "Gedeeld met mij / Vakantie".
 */
export function pathLabel(
  path: SearchHit["path"],
  shared = false,
  labels: { mine: string; shared: string } = { mine: "Mijn bestanden", shared: "Gedeeld met mij" },
): string {
  if (shared) return [labels.shared, ...path.map((p) => p.name)].join(" / ");
  return path.map((p, i) => (i === 0 && p.name === "" ? labels.mine : p.name)).join(" / ");
}

/** pathLabel met de vertaalde namen (voor in de app). */
export function pathText(path: SearchHit["path"], shared = false): string {
  return pathLabel(path, shared, { mine: t("browser.myFiles"), shared: t("browser.sharedWithMe") });
}

/** Het pad van de map waar een resultaat in staat; zonder het resultaat zelf (een gedeelde
 * map die zelf gevonden wordt, staat bij sommige servers vooraan in zijn eigen pad). */
export function hitPath(hit: SearchHit): SearchHit["path"] {
  const path = hit.path;
  return path.length && path[path.length - 1].id === hit.node.id ? path.slice(0, -1) : path;
}

/** De map waar een resultaat in staat. Wat boven een gedeelde map staat, zie je niet: dan
 * naar Gedeeld met mij. */
export function parentHref(hit: SearchHit): string {
  const path = hitPath(hit);
  if (isShared(hit.node)) return path.length === 0 ? "/gedeeld" : `/map/${path[path.length - 1].id}`;
  return path.length <= 1 ? "/" : `/map/${path[path.length - 1].id}`;
}

/** Waar een klik op een resultaat heen gaat: een map zelf, of de map waar een bestand in staat. */
export function folderHref(hit: SearchHit): string {
  return hit.node.kind === "dir" ? `/map/${hit.node.id}` : parentHref(hit);
}

/** Is dit een foto (voor de regel "Geen foto met deze naam?", Keuze 81)? Zoals node.ftype
 * image: een bestand met een mime image/…, op dezelfde manier als het icoon (Thumb.tsx). */
export function isPhoto(hit: SearchHit): boolean {
  return category(hit.node) === "image";
}

/** Voegt een volgende pagina toe ("Meer laden"); wat er al staat, komt er niet nog eens bij (een
 * volgende ronde kan een treffer opnieuw geven). */
export function appendHits<H extends SearchHit>(have: readonly H[], more: readonly H[]): H[] {
  const seen = new Set(have.map((h) => h.node.id));
  const out = [...have];
  for (const h of more) {
    if (seen.has(h.node.id)) continue;
    seen.add(h.node.id);
    out.push(h);
  }
  return out;
}
