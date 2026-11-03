// /fotos/personen/:id (fase 6, van Z6): de foto's van één eigen persoon (Keuze 38). Z0 zette een
// lege pagina; Z6 vult hem. Het contract: de default export is de pagina van de route
// (RouteProps, met params.id), in een eigen chunk.

import type { JSX } from "react";
import { t } from "../../i18n";
import type { RouteProps } from "../../router";
import { EmptyState } from "../../ui/EmptyState";
import { PageTitle } from "../../ui/PageTitle";

export default function PersonPage(_: RouteProps): JSX.Element {
  return (
    <main>
      <PageTitle>{t("photos_people.person")}</PageTitle>
      <EmptyState icon="user" title={t("photos_people.notReady")} />
    </main>
  );
}
