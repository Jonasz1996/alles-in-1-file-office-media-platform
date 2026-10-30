// Een balk die verdwijnt door zijn eigen knop (de selectie: ×, Naar prullenbak, Uit album
// halen): de knop met de focus gaat mee weg, en de focus zou naar <body> vallen, waar de
// pijltjes en sneltoetsen van de lijst niet meer werken.

import { type RefObject, useLayoutEffect, useRef } from "react";

/**
 * Verdwijnt het component terwijl de focus in el staat, dan gone() (die zet de focus terug in
 * de lijst). Werkt bij het weghalen van het component zelf: de cleanup van een layout-effect
 * draait dan nog vóór de DOM-knopen weg zijn.
 */
export function useFocusBack(el: RefObject<HTMLElement | null>, gone: (() => void) | undefined): void {
  const latest = useRef(gone);
  latest.current = gone;
  useLayoutEffect(() => {
    const node = el.current;
    return () => {
      if (node?.contains(document.activeElement)) latest.current?.();
    };
  }, [el]);
}
