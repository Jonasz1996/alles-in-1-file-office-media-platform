// Welk bestand de viewer toont, in de adresbalk (?bestand=<id>): zo werken terug, herladen en
// een gedeelde link naar een bestand.

import { useCallback, useEffect } from "react";
import { back, navigate, useQueryParam } from "../router";

export const VIEWER_PARAM = "bestand";

/** De viewer is door ons geopend (een push): sluiten is dan gewoon terug. */
let pushed = false;

function hrefWith(id: string | null): string {
  const params = new URLSearchParams(window.location.search);
  if (id === null) params.delete(VIEWER_PARAM);
  else params.set(VIEWER_PARAM, id);
  const qs = params.toString();
  return `${window.location.pathname}${qs ? `?${qs}` : ""}${window.location.hash}`;
}

/**
 * [het id in ?bestand=, zetten]. Openen is een nieuwe stap in de geschiedenis (push), naar het
 * volgende bestand gaan vervangt die stap (replace), en sluiten gaat terug: één keer Terug in de
 * browser sluit de viewer dus ook. Kwam je binnen met een link naar het bestand (deep link), dan
 * haalt sluiten alleen ?bestand= weg (replace), zodat je op de pagina blijft.
 */
export function useViewerParam(): [string | null, (id: string | null) => void] {
  const [value] = useQueryParam(VIEWER_PARAM);

  useEffect(() => {
    if (value === null) pushed = false; // dicht, ook als dat via de knop Terug van de browser ging
  }, [value]);

  const set = useCallback((id: string | null) => {
    const cur = new URLSearchParams(window.location.search).get(VIEWER_PARAM);
    if (id === cur) return;
    if (id === null) {
      if (pushed) {
        pushed = false;
        back(hrefWith(null));
      } else navigate(hrefWith(null), { replace: true });
      return;
    }
    if (cur === null) {
      navigate(hrefWith(id));
      pushed = true;
    } else navigate(hrefWith(id), { replace: true });
  }, []);

  return [value, set];
}

/**
 * De bestanden voor de viewer: wat de lijst toont (shown, gefilterd en gesorteerd), plus het
 * bestand dat open moet (viewing) als het filter dat verbergt maar het wel in de map staat (all).
 * Zo opent een nieuw tekstbestand ook als het niet bij de zoekwoorden past (review txt, punt 5);
 * het komt achteraan.
 */
export function withViewing(shown: readonly string[], all: readonly string[], viewing: string | null): readonly string[] {
  if (viewing === null || shown.includes(viewing) || !all.includes(viewing)) return shown;
  return [...shown, viewing];
}
