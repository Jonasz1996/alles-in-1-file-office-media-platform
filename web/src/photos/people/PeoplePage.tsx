// /fotos/personen (fase 6, van Z6): je eigen personen met aantal en omslag; hernoemen,
// samenvoegen, verbergen en wissen (Keuze 38). Z0 zette een lege pagina; Z6 vult hem. Het
// contract: de default export is de pagina van de route (RouteProps), in een eigen chunk.

import type { JSX } from "react";
import { t } from "../../i18n";
import type { RouteProps } from "../../router";
import { EmptyState } from "../../ui/EmptyState";
import { PageTitle } from "../../ui/PageTitle";

export default function PeoplePage(_: RouteProps): JSX.Element {
  return (
    <main>
      <PageTitle>{t("photos_people.title")}</PageTitle>
      <EmptyState icon="user" title={t("photos_people.notReady")} />
    </main>
  );
}
