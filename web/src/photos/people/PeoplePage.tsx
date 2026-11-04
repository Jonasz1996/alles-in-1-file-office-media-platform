// /fotos/personen (fase 6, van Z6): je eigen personen met aantal en omslag; hernoemen,
// samenvoegen, verbergen en wissen (Keuze 38). Z0 zette een lege pagina; Z6 vult hem. Het
// contract: de default export is de pagina van de route (RouteProps), in een eigen chunk.
//
// Z0b (review contract F3): de plek van X2, PeopleFaces (de voortgang, "Is dit …?" en "Wie is
// dit?"), lazy met het gebied photos_faces. Z6 houdt hem; src/lazySlots.test.ts eist hem.

import type { JSX } from "react";
import { lazyWithAreas, t } from "../../i18n";
import type { RouteProps } from "../../router";
import { EmptyState } from "../../ui/EmptyState";
import { LazySlot } from "../../ui/LazySlot";
import { PageTitle } from "../../ui/PageTitle";

const PeopleFaces = lazyWithAreas(() => import("../faces/PeopleFaces"), ["photos_faces"]);

export default function PeoplePage(_: RouteProps): JSX.Element {
  return (
    <main>
      <PageTitle>{t("photos_people.title")}</PageTitle>
      <LazySlot>
        <PeopleFaces />
      </LazySlot>
      <EmptyState icon="user" title={t("photos_people.notReady")} />
    </main>
  );
}
