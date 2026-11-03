// De tab "Zoeken en foto's" op /beheer (fase 6, van Z7; Keuze 51): de motor, de stand van de
// index, OCR, plaatsnamen en de knoppen herbouwen, mislukte opnieuw en pauzeren. Z0 zette een lege
// tab; Z7 vult hem met de aanroepen uit ./searchApi. Het contract: de default export, in een eigen
// chunk.

import type { JSX } from "react";
import { t } from "../i18n";
import { EmptyState } from "../ui/EmptyState";

export default function SearchTab(): JSX.Element {
  return <EmptyState icon="search" title={t("admin.search.notReady")} />;
}
