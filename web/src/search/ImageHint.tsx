// De uitleg bij het veld van /zoeken in de stand beeld (fase 6, Z4; Keuze 81). Het veld wijst er
// met aria-describedby naar (imageDescribedBy).
//
// Kent de server zoeken op beeld niet, dan hoort bij die beschrijving ook de zin dat het uitstaat
// (I1, review R4; tot 0.8.1: dat het later komt). Zichtbaar staat die zin in BeeldResults, maar dat is een lazy chunk
// die meestal pas komt als de focus al in het veld staat (Keuze 81): een schermlezer hoorde hem
// dan niet. Daarom staat hij hier nog eens, met de tekst uit het gebied search (dat laadt met de
// pagina, dus zonder te wachten) en met hidden: aria-describedby leest een verborgen element wel,
// en wie de pagina doorleest, hoort de zin maar één keer (die van BeeldResults). Hij komt vóór de
// uitleg, omdat hij het belangrijkste zegt.
//
// Eén schakelaar: IMAGE_SEARCH_READY, de stand bij het laden. X3 (X3b) liet hem op false: of
// zoeken op beeld er is, zegt pas de server (GET /photos/beeld; met de analyse uit 501, zoals in
// 0.8.0). ImageHint vraagt die stand zelf, met dezelfde gedeelde vraag als het raster
// (search/beeld/status.ts, één keer per halve minuut). Kent de server zoeken op beeld, dan zet
// setImageSearchReady(true) hem om: de verborgen zin valt weg en alleen de uitleg beschrijft het
// veld. Met de analyse uit blijft alles zoals in 0.8.0.
//
// status.ts komt met een dynamische import: een gewone import (ook vanuit het raster) maakte er
// een eigen chunk van naast SearchPage, met een regel erbij in de eerste lading.

import { type JSX, useEffect, useSyncExternalStore } from "react";
import { useCurrentUser } from "../currentUser";
import { t } from "../i18n";

/** Of zoeken op beeld er is, bij het laden van de pagina. Tot de server het zegt, zegt de
 * beschrijving van het veld dat het uitstaat. */
export const IMAGE_SEARCH_READY: boolean = false;

let ready = IMAGE_SEARCH_READY;
const listeners = new Set<() => void>();
const subscribe = (l: () => void) => {
  listeners.add(l);
  return () => {
    listeners.delete(l);
  };
};
const snapshot = () => ready;

/** Of de server zoeken op beeld kent (X3b): dan valt de verborgen zin weg. */
export function setImageSearchReady(on: boolean): void {
  if (on === ready) return;
  ready = on;
  for (const l of [...listeners]) l();
}

/** Vraagt de stand van zoeken op beeld voor wie ingelogd is; een fout verandert niets. */
function useAskReady(who: string | null): void {
  useEffect(() => {
    if (who === null) return;
    let live = true;
    import("./beeld/status")
      .then((m) => m.loadBeeld(who))
      .then(
        (k) => live && setImageSearchReady(k.state !== "off"),
        () => {},
      );
    return () => {
      live = false;
    };
  }, [who]);
}

const HINT_ID = "zoek-beeld-uitleg";
const LATER_ID = "zoek-beeld-later";

/** De ids voor aria-describedby van het veld in de stand beeld. */
export function imageDescribedBy(on: boolean = ready): string {
  return on ? HINT_ID : `${LATER_ID} ${HINT_ID}`;
}

/** De uitleg onder het veld, en zolang zoeken op beeld er niet is de verborgen zin erbij. */
export function ImageHint({ ready: forced }: { ready?: boolean }): JSX.Element {
  const user = useCurrentUser();
  useAskReady(forced === undefined ? (user?.id ?? null) : null);
  const live = useSyncExternalStore(subscribe, snapshot, snapshot);
  const on = forced ?? live;
  return (
    <>
      <p id={HINT_ID} className="muted">
        {t("search.field.hintImage")}
      </p>
      {!on && (
        <span id={LATER_ID} hidden>
          {t("search.field.imageLater")}
        </span>
      )}
    </>
  );
}
