// /kijken/:node (fase 5, van M6). De afspeelpagina (?t= is de start in seconden).
// M0 zette een lege pagina achter de poort (media/Gate.tsx); M6 vult hem. Het contract:
// de default export is de pagina van de route (RouteProps).

import type { JSX } from "react";
import { t } from "../i18n";
import type { RouteProps } from "../router";
import { MediaGate, NotReady } from "../media/Gate";

export default function WatchPage({ params }: RouteProps): JSX.Element {
  return <MediaGate params={params}>{() => <NotReady title={t("player.title")} text={t("player.notReady")} icon="play" />}</MediaGate>;
}
