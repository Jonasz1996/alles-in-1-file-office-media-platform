// /muziek/lijst/:id (fase 5, van M9). Een afspeellijst.
// M0 zette een lege pagina achter de poort (media/Gate.tsx); M9 vult hem. Het contract:
// de default export is de pagina van de route (RouteProps).

import type { JSX } from "react";
import { t } from "../i18n";
import type { RouteProps } from "../router";
import { MediaGate, NotReady } from "../media/Gate";

export default function PlaylistPage({ params }: RouteProps): JSX.Element {
  return <MediaGate params={params}>{() => <NotReady title={t("music.playlist")} text={t("music.notReady")} icon="audio" />}</MediaGate>;
}
