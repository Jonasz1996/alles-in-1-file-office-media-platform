// /fotos/kaart (fase 6, van Z5): je eigen foto's op een kaart zonder tegels van buiten, met de
// lijst "Plaatsen" (Keuze 31–33). Z0 zette een lege pagina; Z5 vult hem. Het contract: de default
// export is de pagina van de route (RouteProps), in een eigen chunk (mapRoute ≤ 30 kB).

import type { JSX } from "react";
import { t } from "../../i18n";
import type { RouteProps } from "../../router";
import { EmptyState } from "../../ui/EmptyState";
import { PageTitle } from "../../ui/PageTitle";

export default function MapPage(_: RouteProps): JSX.Element {
  return (
    <main>
      <PageTitle>{t("photos_map.title")}</PageTitle>
      <EmptyState icon="photo" title={t("photos_map.notReady")} />
    </main>
  );
}
