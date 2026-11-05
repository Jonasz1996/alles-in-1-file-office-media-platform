// De plekken in het infopaneel voor fase 6 (Z0): Plek (LocationRow.tsx, van Z5) en Personen
// (PeopleRow.tsx, van Z6); Z0b zette de derde erbij: "Meer zoals deze" (LikeRow.tsx, van X3), en
// na de review een vierde: de gezichten (photos/faces/FacesRow.tsx, van X2). Elk een eigen
// bestand en een eigen chunk; het infopaneel laadt ze pas als ze iets kunnen tonen.
// Puur, zodat het in vitest te testen is.

import type { ViewerDetail } from "./api";
import type { ViewKind } from "./media";
import type { ViewerItem } from "./types";

/** Wat LocationRow, PeopleRow en LikeRow krijgen: hetzelfde als het infopaneel. Elk geeft een eigen
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

/**
 * De gezichten (X2; Z0b, review contract F3) alleen bij een foto: X2 zoekt geen gezichten in
 * video's (Keuze 60). Verder als Plek en Personen; bij de foto van een ander geeft de API [].
 */
export function showFacesRow(kind: ViewKind, item: Pick<ViewerItem, "version">, signedIn: boolean): boolean {
  return showPhotoRows(kind, item, signedIn) && kind === "image";
}

/**
 * "Meer zoals deze" (X3) alleen bij een foto: zoeken op beeld kent geen video's (Keuze 71). Verder
 * als Plek en Personen: ingelogd en de huidige versie. Bij de foto van een ander of zonder vector
 * geeft de API 404 of 409 beeld_no_vector; X3 beslist wat de rij dan toont.
 */
export function showLikeRow(kind: ViewKind, item: Pick<ViewerItem, "version">, signedIn: boolean): boolean {
  return showPhotoRows(kind, item, signedIn) && kind === "image";
}
