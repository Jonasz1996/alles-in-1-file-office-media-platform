// Het blok "Gezichten" in de tab "Zoeken en foto's" op /beheer (fase 6, van X2; Keuze 67): de
// container analyse en de modellen, de rij, aantallen (nooit per gebruiker, nooit een gezicht), de
// schakelaar "Gezichtsherkenning toestaan" en de zin over reservekopieën. Z0b zette het blok neer
// (Keuze 55), in de chunk van de tab, met het gebied photos_faces; tot X2 het vult, zegt het
// alleen dat het later komt. De stand komt dan uit AdminSearch.faces (./searchApi).
//
// Z7 (golf 4) geeft het blok de stand mee: null (X2 is er niet) houdt precies "komt later";
// met een stand toont het alleen de aantallen (Keuze 51). De rest van Keuze 67 is van X2.

import type { JSX } from "react";
import { fmt, t } from "../i18n";
import type { AdminFaces } from "./searchApi";

export function FacesBlock({ faces = null }: { faces?: AdminFaces | null }): JSX.Element {
  if (faces === null) {
    return (
      <section aria-labelledby="admin-faces-title">
        <h3 id="admin-faces-title">{t("photos_faces.title")}</h3>
        <p className="muted">{t("photos_faces.notReady")}</p>
      </section>
    );
  }
  const failed = faces.failed_by_code.reduce((sum, c) => sum + c.n, 0);
  const rows: [string, number][] = [
    [t("photos_faces.admin.faces"), faces.faces],
    [t("photos_faces.admin.named"), faces.named],
    [t("photos_faces.admin.usersOn"), faces.users_on],
    [t("photos_faces.admin.waiting"), faces.queue.waiting],
    [t("photos_faces.admin.failed"), failed],
  ];
  return (
    <section aria-labelledby="admin-faces-title">
      <h3 id="admin-faces-title">{t("photos_faces.title")}</h3>
      <dl className="admin-totals" data-search="faces">
        {rows.map(([label, n]) => (
          <div key={label}>
            <dt>{label}</dt>
            <dd>{fmt.number(n)}</dd>
          </div>
        ))}
      </dl>
    </section>
  );
}
