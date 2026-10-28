// /muziek (fase 5, van M9). Muziek: albums, artiesten en afspeellijsten.
// M0 zette een lege pagina achter de poort (media/Gate.tsx); M9 vult hem. Het contract:
// de default export is de pagina van de route (RouteProps).

import type { JSX } from "react";
import { t } from "../i18n";
import type { RouteProps } from "../router";
import { MediaGate, NotReady } from "../media/Gate";

export default function MusicHome({ params }: RouteProps): JSX.Element {
  return <MediaGate params={params}>{() => <NotReady title={t("music.title")} text={t("music.notReady")} icon="audio" />}</MediaGate>;
}
