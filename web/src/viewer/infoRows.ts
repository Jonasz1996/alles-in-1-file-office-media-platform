// De plekken in het infopaneel voor fase 6 (Z0): Plek (LocationRow.tsx, van Z5) en Personen
// (PeopleRow.tsx, van Z6). Elk een eigen bestand en een eigen chunk; het infopaneel laadt ze pas
// als ze iets kunnen tonen. Puur, zodat het in vitest te testen is.

import type { ViewerDetail } from "./api";
import type { ViewKind } from "./media";
import type { ViewerItem } from "./types";

/** Wat LocationRow en PeopleRow krijgen: hetzelfde als het infopaneel. Elk geeft een eigen
 * <section> (met een <h4>) of niets. */
export interface InfoRowProps {
  item: ViewerItem;
  detail: ViewerDetail | undefined;
  kind: ViewKind;
}

/**
 * Plek en Personen alleen voor een foto of video, ingelogd (nooit via een publieke link, Keuze
 * 27), en niet bij een eerdere versie. Of je de eigenaar bent, weet de viewer niet: de server
 * geeft een ander 404 (plek) of [] (personen), en dan tonen de rijen niets.
 */
export function showPhotoRows(kind: ViewKind, item: Pick<ViewerItem, "version">, signedIn: boolean): boolean {
  return signedIn && !item.version && (kind === "image" || kind === "video");
}
