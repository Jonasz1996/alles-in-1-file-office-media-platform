// Het blok "Gezichten" in de tab "Zoeken en foto's" op /beheer (fase 6, van X2; Keuze 67): de
// container analyse en de modellen, de rij, aantallen (nooit per gebruiker, nooit een gezicht), de
// schakelaar "Gezichtsherkenning toestaan" en de zin over reservekopieën. Z0b zette het blok neer
// (Keuze 55), in de chunk van de tab, met het gebied photos_faces; tot X2 het vult, zegt het
// alleen dat het later komt. De stand komt dan uit AdminSearch.faces (./searchApi).

import type { JSX } from "react";
import { t } from "../i18n";

export function FacesBlock(): JSX.Element {
  return (
    <section aria-labelledby="admin-faces-title">
      <h3 id="admin-faces-title">{t("photos_faces.title")}</h3>
      <p className="muted">{t("photos_faces.notReady")}</p>
    </section>
  );
}
