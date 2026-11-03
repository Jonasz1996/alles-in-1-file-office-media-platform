// /dubbel (fase 6, van Z5): dubbele bestanden en foto's die erop lijken, in groepen, met "niet
// gelijk" en "naar de prullenbak" zonder iets vooraf te kiezen (Keuze 34–36). Z0 zette een lege
// pagina; Z5 vult hem. Het contract: de default export is de pagina van de route (RouteProps), in
// een eigen chunk.

import type { JSX } from "react";
import { t } from "../i18n";
import type { RouteProps } from "../router";
import { EmptyState } from "../ui/EmptyState";
import { PageTitle } from "../ui/PageTitle";

export default function DupesPage(_: RouteProps): JSX.Element {
  return (
    <main>
      <PageTitle>{t("dupes.title")}</PageTitle>
      <EmptyState icon="copy" title={t("dupes.notReady")} />
    </main>
  );
}
