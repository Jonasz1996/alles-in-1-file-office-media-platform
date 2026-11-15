// Het blok "Zoeken op beeld" in de tab "Zoeken en foto's" op /beheer (fase 6, van X3; Keuze 82):
// aan of uit voor iedereen, het model, de container analyse, hoeveel foto's bekeken zijn, het
// afgelopen uur, de ruimte, en de knoppen "Opnieuw berekenen" en "Beeldindex wissen". Z0b zette
// het blok neer (Keuze 55), in de chunk van de tab, met het gebied beeld; tot X3 het vult, zegt
// het alleen dat het later komt. De stand komt dan uit AdminSearch.beeld (./searchApi).
//
// Z7 (golf 4) geeft het blok de stand mee: null (X3 is er niet) houdt precies "komt later";
// met een stand toont het alleen de aantallen (Keuze 51). De rest van Keuze 82 is van X3.

import type { JSX } from "react";
import { fmt, t } from "../i18n";
import type { AdminBeeld } from "./searchApi";

export function BeeldBlock({ beeld = null }: { beeld?: AdminBeeld | null }): JSX.Element {
  if (beeld === null) {
    return (
      <section aria-labelledby="admin-beeld-title">
        <h3 id="admin-beeld-title">{t("beeld.title")}</h3>
        <p className="muted">{t("beeld.notReady")}</p>
      </section>
    );
  }
  const failed = beeld.failed_by_code.reduce((sum, c) => sum + c.n, 0);
  return (
    <section aria-labelledby="admin-beeld-title">
      <h3 id="admin-beeld-title">{t("beeld.title")}</h3>
      <dl className="admin-totals" data-search="beeld">
        <div>
          <dt>{t("beeld.admin.seen")}</dt>
          <dd>{t("beeld.admin.seenValue", { seen: fmt.number(beeld.seen), photos: fmt.number(beeld.photos) })}</dd>
        </div>
        <div>
          <dt>{t("beeld.admin.queries")}</dt>
          <dd>{fmt.number(beeld.hour.queries)}</dd>
        </div>
        <div>
          <dt>{t("beeld.admin.failed")}</dt>
          <dd>{fmt.number(failed)}</dd>
        </div>
      </dl>
    </section>
  );
}
