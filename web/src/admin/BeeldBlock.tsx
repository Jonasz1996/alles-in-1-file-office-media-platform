// Het blok "Zoeken op beeld" in de tab "Zoeken en foto's" op /beheer (fase 6, van X3; Keuze 82):
// aan of uit voor iedereen, het model, de container analyse, hoeveel foto's bekeken zijn, het
// afgelopen uur, de ruimte, en de knoppen "Opnieuw berekenen" en "Beeldindex wissen". Z0b zette
// het blok neer (Keuze 55), in de chunk van de tab, met het gebied beeld; tot X3 het vult, zegt
// het alleen dat het later komt. De stand komt dan uit AdminSearch.beeld (./searchApi).

import type { JSX } from "react";
import { t } from "../i18n";

export function BeeldBlock(): JSX.Element {
  return (
    <section aria-labelledby="admin-beeld-title">
      <h3 id="admin-beeld-title">{t("beeld.title")}</h3>
      <p className="muted">{t("beeld.notReady")}</p>
    </section>
  );
}
