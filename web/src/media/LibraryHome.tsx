// /media (fase 5, van M2). De start van media: bibliotheken, Verder kijken en Volgende aflevering (M7), en Nieuw.
// M0 zette een lege pagina achter de poort (media/Gate.tsx); M2 vult hem. Het contract:
// de default export is de pagina van de route (RouteProps).

import type { JSX } from "react";
import { t } from "../i18n";
import type { RouteProps } from "../router";
import { MediaGate, NotReady } from "./Gate";

export default function LibraryHome({ params }: RouteProps): JSX.Element {
  return <MediaGate params={params}>{() => <NotReady title={t("media.title")} text={t("media.notReady")} icon="video" />}</MediaGate>;
}
