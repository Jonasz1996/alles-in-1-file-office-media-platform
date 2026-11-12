// De uitleg bij het veld van /zoeken in de stand beeld (fase 6, Z4; Keuze 81). Het veld wijst er
// met aria-describedby naar (imageDescribedBy).
//
// Zolang het raster van X3 er niet is, hoort bij die beschrijving ook de zin dat zoeken op beeld
// later komt (I1, review R4). Zichtbaar staat die zin in BeeldResults, maar dat is een lazy chunk
// die meestal pas komt als de focus al in het veld staat (Keuze 81): een schermlezer hoorde hem
// dan niet. Daarom staat hij hier nog eens, met de tekst uit het gebied search (dat laadt met de
// pagina, dus zonder te wachten) en met hidden: aria-describedby leest een verborgen element wel,
// en wie de pagina doorleest, hoort de zin maar één keer (die van BeeldResults). Hij komt vóór de
// uitleg, omdat hij het belangrijkste zegt.
//
// Eén schakelaar: IMAGE_SEARCH_READY. X3 zet hem op true als zijn raster BeeldResults vervangt;
// dan beschrijft alleen de uitleg het veld en kan search.field.imageLater weg.

import type { JSX } from "react";
import { t } from "../i18n";

/** Of zoeken op beeld er is (het raster van X3). Tot dan zegt de beschrijving van het veld dat
 * het later komt. */
export const IMAGE_SEARCH_READY: boolean = false;

const HINT_ID = "zoek-beeld-uitleg";
const LATER_ID = "zoek-beeld-later";

/** De ids voor aria-describedby van het veld in de stand beeld. */
export function imageDescribedBy(ready: boolean = IMAGE_SEARCH_READY): string {
  return ready ? HINT_ID : `${LATER_ID} ${HINT_ID}`;
}

/** De uitleg onder het veld, en zolang zoeken op beeld er niet is de verborgen zin erbij. */
export function ImageHint({ ready = IMAGE_SEARCH_READY }: { ready?: boolean }): JSX.Element {
  return (
    <>
      <p id={HINT_ID} className="muted">
        {t("search.field.hintImage")}
      </p>
      {!ready && (
        <span id={LATER_ID} hidden>
          {t("search.field.imageLater")}
        </span>
      )}
    </>
  );
}
