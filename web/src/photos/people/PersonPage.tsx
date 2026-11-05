// /fotos/personen/:id (fase 6, van Z6): de foto's van één eigen persoon (Keuze 38). Z0 zette een
// lege pagina; Z6 vult hem. Het contract: de default export is de pagina van de route
// (RouteProps, met params.id), in een eigen chunk.
//
// Z0b (review contract F3): de plek van X2, PersonFaces ("Misschien ook Anna"), lazy met het
// gebied photos_faces. Z6 houdt hem; src/lazySlots.test.ts eist hem.

import type { JSX } from "react";
import { lazyWithAreas, t } from "../../i18n";
import type { RouteProps } from "../../router";
import { EmptyState } from "../../ui/EmptyState";
import { LazySlot } from "../../ui/LazySlot";
import { PageTitle } from "../../ui/PageTitle";
import type { PersonFacesProps } from "../faces/PersonFaces";

const PersonFaces = lazyWithAreas<PersonFacesProps>(() => import("../faces/PersonFaces"), ["photos_faces"]);

export default function PersonPage({ params }: RouteProps): JSX.Element {
  return (
    <main>
      <PageTitle>{t("photos_people.person")}</PageTitle>
      <EmptyState icon="user" title={t("photos_people.notReady")} />
      <LazySlot>
        <PersonFaces personId={params.id} />
      </LazySlot>
    </main>
  );
}
