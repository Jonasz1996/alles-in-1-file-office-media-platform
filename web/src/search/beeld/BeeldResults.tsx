// Het raster van zoeken op beeld op /zoeken (fase 6, van X3; Keuze 81: "Z4 (de keuze), X3 (het
// raster)"). In de stand beeld (?mode=image) toont de pagina van Z4 dit raster in plaats van de
// lijst; het leest q en like zelf uit de URL. Z0b zette de plek (review contract F3); Z4 laadt dit
// bestand lazy, met het gebied beeld (≤ 15 kB in budget.mjs). Het contract: de default export
// zonder props, een eigen chunk.
//
// Tot X3 het vult, staat er één zin dat het later komt (I1, S8), zonder versienummer: of X3 in
// deze fase komt, beslist de go/no-go na I1 (bouwontwerp §5). X3 vervangt dit bestand. Dezelfde
// zin staat ook verborgen in de beschrijving van het veld (search/ImageHint.tsx, I1 review R4),
// omdat dit bestand lazy komt; X3 zet daar IMAGE_SEARCH_READY op true.

import type { JSX } from "react";
import { t } from "../../i18n";

export default function BeeldResults(): JSX.Element {
  return <p className="muted">{t("beeld.later")}</p>;
}
