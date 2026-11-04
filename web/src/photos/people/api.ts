// De aanroepen van personen, herinneringen, de instellingen voor foto's en slimme albums (fase 6,
// bouwontwerp §3.4), frozen na Z0: precies de eindpunten van Z6, met de vorm van ./types. Losse
// functies via api.request; tot Z6 zijn routes vult, geven ze 501.

import { api } from "../../api";
import { seg, wireQuery } from "../../search/wire";
import type {
  AddPerson,
  AlbumRuleBody,
  AlbumRuleSaved,
  FilePeople,
  HideKind,
  Memories,
  MemoryHide,
  MemoryHides,
  People,
  Person,
  PersonPatch,
  PersonPhotos,
  PhotoSettings,
  PhotoSettingsPatch,
} from "./types";

const API = "/api/v1";

// --- personen ---

/** GET /people?hidden=0|1 */
export function people(hidden = false): Promise<People> {
  return api.request<People>("GET", `${API}/people${wireQuery({ hidden: hidden ? 1 : 0 })}`);
}

/** POST /people {name} → de persoon; 409 people_name_taken. */
export function createPerson(name: string): Promise<Person> {
  return api.request<Person>("POST", `${API}/people`, { name });
}

/** PATCH /people/{id} {name?, hidden?, cover?} */
export function updatePerson(id: string, patch: PersonPatch): Promise<Person> {
  return api.request<Person>("PATCH", `${API}/people/${seg(id)}`, patch);
}

/** POST /people/{id}/merge {into} → de persoon die blijft; 400 people_merge_self. */
export function mergePerson(id: string, into: string): Promise<Person> {
  return api.request<Person>("POST", `${API}/people/${seg(id)}/merge`, { into });
}

/** DELETE /people/{id} → 204 */
export function deletePerson(id: string): Promise<void> {
  return api.request<void>("DELETE", `${API}/people/${seg(id)}`);
}

/** GET /people/{id}/photos?cursor= */
export function personPhotos(id: string, cursor: string | null = null): Promise<PersonPhotos> {
  return api.request<PersonPhotos>("GET", `${API}/people/${seg(id)}/photos${wireQuery({ cursor })}`);
}

/** GET /files/{id}/people (voor de foto van een ander: geen personen) */
export function filePeople(fileId: string): Promise<FilePeople> {
  return api.request<FilePeople>("GET", `${API}/files/${seg(fileId)}/people`);
}

/** POST /files/{id}/people → 201; alleen de eigenaar van de node. */
export function addFilePerson(fileId: string, body: AddPerson): Promise<void> {
  return api.request<void>("POST", `${API}/files/${seg(fileId)}/people`, body);
}

/** DELETE /files/{id}/people/{person_id} → 204 */
export function removeFilePerson(fileId: string, personId: string): Promise<void> {
  return api.request<void>("DELETE", `${API}/files/${seg(fileId)}/people/${seg(personId)}`);
}

// --- herinneringen ---

/** GET /photos/memories?day=JJJJ-MM-DD; zonder day: vandaag. */
export function memories(day: string | null = null): Promise<Memories> {
  return api.request<Memories>("GET", `${API}/photos/memories${wireQuery({ day })}`);
}

/** GET /photos/memories/hide */
export function memoryHides(): Promise<MemoryHides> {
  return api.request<MemoryHides>("GET", `${API}/photos/memories/hide`);
}

/** POST /photos/memories/hide {kind, ref}; 400 memories_bad_hide. */
export function hideMemories(hide: MemoryHide): Promise<void> {
  return api.request<void>("POST", `${API}/photos/memories/hide`, hide);
}

/** DELETE /photos/memories/hide/{kind}/{ref} */
export function unhideMemories(kind: HideKind, ref: string): Promise<void> {
  return api.request<void>("DELETE", `${API}/photos/memories/hide/${seg(kind)}/${seg(ref)}`);
}

// --- de instellingen voor foto's ---

/** GET /me/photo-settings */
export function photoSettings(): Promise<PhotoSettings> {
  return api.request<PhotoSettings>("GET", `${API}/me/photo-settings`);
}

/** PATCH /me/photo-settings: alleen wat verandert (ook faces en beeld, X2 en X3). */
export function updatePhotoSettings(patch: PhotoSettingsPatch): Promise<PhotoSettings> {
  return api.request<PhotoSettings>("PATCH", `${API}/me/photo-settings`, patch);
}

// --- slimme albums ---

/** PUT /albums/{id}/rule {rule, share_new?}; 400 album_rule_invalid, album_rule_too_many. */
export function setAlbumRule(albumId: string, body: AlbumRuleBody): Promise<AlbumRuleSaved> {
  return api.request<AlbumRuleSaved>("PUT", `${API}/albums/${seg(albumId)}/rule`, body);
}

/** POST /albums/{id}/rule/confirm {share_new}: het antwoord op de vraag van Keuze 42. Geeft
 * AlbumRuleSaved, zoals PUT (needs_confirm is dan false). */
export function confirmAlbumRule(albumId: string, shareNew: boolean): Promise<AlbumRuleSaved> {
  return api.request<AlbumRuleSaved>("POST", `${API}/albums/${seg(albumId)}/rule/confirm`, { share_new: shareNew });
}

/** DELETE /albums/{id}/rule → 204; de items uit de regel blijven, met hun shared. */
export function deleteAlbumRule(albumId: string): Promise<void> {
  return api.request<void>("DELETE", `${API}/albums/${seg(albumId)}/rule`);
}

/** POST /albums/{id}/refresh → 202 */
export function refreshAlbum(albumId: string): Promise<void> {
  return api.request<void>("POST", `${API}/albums/${seg(albumId)}/refresh`);
}
