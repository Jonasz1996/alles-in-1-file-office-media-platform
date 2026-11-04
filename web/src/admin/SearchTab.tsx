// De tab "Zoeken en foto's" op /beheer (fase 6, van Z7; Keuze 51): de motor, de stand van de
// index, OCR, plaatsnamen en de knoppen herbouwen, mislukte opnieuw en pauzeren. Z0 zette een lege
// tab; Z7 vult hem met de aanroepen uit ./searchApi. Het contract: de default export, in een eigen
// chunk.
//
// Z0b (Keuze 55): onder het deel van Z7 de blokken "Gezichten" (FacesBlock.tsx, van X2) en "Zoeken
// op beeld" (BeeldBlock.tsx, van X3), in dezelfde chunk. Beheer.tsx laadt de gebieden
// photos_faces en beeld met de tab. Z7 houdt de twee blokken onderaan staan.

import type { JSX } from "react";
import { t } from "../i18n";
import { EmptyState } from "../ui/EmptyState";
import { BeeldBlock } from "./BeeldBlock";
import { FacesBlock } from "./FacesBlock";

export default function SearchTab(): JSX.Element {
  return (
    <>
      <EmptyState icon="search" title={t("admin.search.notReady")} />
      <FacesBlock />
      <BeeldBlock />
    </>
  );
}
