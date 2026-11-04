// De aanroepen van gezichten automatisch (fase 6, X2; de API van X2), vastgelegd in Z0b (Keuze
// 55): precies de routes onder /people/faces en /files/{id}/faces, met de vorm van ./types. Losse
// functies via api.request, zodat een chunk alleen meeneemt wat het gebruikt. Nog geen scherm
// roept ze aan; tot X2 er is, geven ze 501 not_built. Aan- en uitzetten gaat via PATCH
// /me/photo-settings (updatePhotoSettings in ../people/api).

import { api } from "../../api";
import { seg, wireQuery } from "../../search/wire";
import type {
  CropSize,
  FaceGroups,
  FaceOut,
  FacePage,
  FacesConfirm,
  FacesQueued,
  FacesReject,
  FacesStatus,
  SuggestionsByPerson,
} from "./types";

const API = "/api/v1";
const FACES = `${API}/people/faces`;

/** GET /people/faces/status */
export function facesStatus(): Promise<FacesStatus> {
  return api.request<FacesStatus>("GET", `${FACES}/status`);
}

/** GET /people/faces/suggestions?cursor=: per persoon, met zes uitsneden. */
export function faceSuggestions(cursor: string | null = null): Promise<SuggestionsByPerson> {
  return api.request<SuggestionsByPerson>("GET", `${FACES}/suggestions${wireQuery({ cursor })}`);
}

/** GET /people/faces/suggestions?person=&cursor=&limit=: de voorstellen voor één persoon, beste
 * eerst. */
export function personFaceSuggestions(
  personId: string,
  cursor: string | null = null,
  limit: number | null = null,
): Promise<FacePage> {
  return api.request<FacePage>("GET", `${FACES}/suggestions${wireQuery({ person: personId, cursor, limit })}`);
}

/** GET /people/faces/groups?cursor=: de groepen, de grootste eerst. */
export function faceGroups(cursor: string | null = null): Promise<FaceGroups> {
  return api.request<FaceGroups>("GET", `${FACES}/groups${wireQuery({ cursor })}`);
}

/** GET /people/faces/groups/{key}?cursor=: alle gezichten van één groep. 409 faces_gone na een
 * nieuwe ronde. */
export function faceGroup(key: string, cursor: string | null = null): Promise<FacePage> {
  return api.request<FacePage>("GET", `${FACES}/groups/${seg(key)}${wireQuery({ cursor })}`);
}

/** POST /people/faces/confirm {faces, person_id | name}; 422 faces_too_many, 409 faces_gone. */
export function confirmFaces(body: FacesConfirm): Promise<void> {
  return api.request<void>("POST", `${FACES}/confirm`, body);
}

/** POST /people/faces/reject {faces, person_id}: "nee", en dat wordt onthouden. */
export function rejectFaces(body: FacesReject): Promise<void> {
  return api.request<void>("POST", `${FACES}/reject`, body);
}

/** POST /people/faces/hide {faces}: niet tonen. */
export function hideFaces(faces: string[]): Promise<void> {
  return api.request<void>("POST", `${FACES}/hide`, { faces });
}

/** POST /people/faces/reset {faces}: ongedaan maken, hooguit 10 minuten na de keuze. */
export function resetFaces(faces: string[]): Promise<void> {
  return api.request<void>("POST", `${FACES}/reset`, { faces });
}

/** POST /people/faces/wipe-unnamed: de gezichten die je niet benoemde, wissen. */
export function wipeUnnamedFaces(): Promise<FacesQueued> {
  return api.request<FacesQueued>("POST", `${FACES}/wipe-unnamed`);
}

/** GET /files/{id}/faces: de gezichten op een eigen foto; een ander die hem mag zien krijgt []. */
export function fileFaces(fileId: string): Promise<FaceOut[]> {
  return api.request<FaceOut[]>("GET", `${API}/files/${seg(fileId)}/faces`);
}

/** POST /files/{id}/faces/rescan: opnieuw zoeken, vooraan in de rij; hooguit 20 per uur. */
export function rescanFaces(fileId: string): Promise<void> {
  return api.request<void>("POST", `${API}/files/${seg(fileId)}/faces/rescan`);
}

/** Het adres van een uitsnede (GET /people/faces/{id}/crop?size=): WebP, voor een <img>. */
export function faceCropUrl(faceId: string, size: CropSize): string {
  return `${FACES}/${seg(faceId)}/crop${wireQuery({ size })}`;
}
