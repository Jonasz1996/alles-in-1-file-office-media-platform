// De contracten van personen, herinneringen, de instellingen voor foto's en slimme albums (fase 6,
// bouwontwerp §3.4 en Keuze 37, 38, 41, 42, 44), frozen na Z0: wat de routers van Z6 geven en
// nemen. Alleen de eigenaar ziet personen; een persoon van een ander geeft 404 not_found, en op
// een foto van een ander zie je er geen ([]). Tot Z6 zijn routes vult, geven ze 501 (de stub van
// Z0).

import type { PersonRef, SearchParams } from "../../search/types";
import type { PhotoItem } from "../api";

export type { PersonRef } from "../../search/types";

// --- personen (Keuze 37, 38) ---

/** Een eigen persoon; count en cover tellen alleen eigen, levende foto's (own_live). */
export interface Person {
  id: string;
  name: string;
  count: number;
  /** Een node-id, of null */
  cover: string | null;
  hidden: boolean;
}

/** GET /people?hidden=0|1 */
export interface People {
  people: Person[];
}

/** PATCH /people/{id}: alleen wat je meegeeft, verandert. */
export interface PersonPatch {
  name?: string;
  hidden?: boolean;
  cover?: string | null;
}

/** Een kader op de foto: x, y, w en h als deel van de foto (0–1). */
export type Box = readonly [x: number, y: number, w: number, h: number];

/** hand: met de hand gezet; auto: later uit X2 (na bevestigen). */
export type PersonSource = "hand" | "auto";

/** Een persoon op een foto, met of zonder kader. */
export interface FilePerson {
  person: PersonRef;
  box: Box | null;
  source: PersonSource;
}

/** GET /files/{id}/people; voor de foto van een ander: []. */
export interface FilePeople {
  people: FilePerson[];
}

/** POST /files/{id}/people: een bestaande persoon of een nieuwe naam, met of zonder kader. */
export type AddPerson = ({ person_id: string } | { name: string }) & { box?: Box };

/** GET /people/{id}/photos?cursor= */
export interface PersonPhotos {
  items: PhotoItem[];
  cursor: string | null;
}

// --- herinneringen (Keuze 41) ---

/** De foto's van deze dag, een aantal jaar geleden. day: JJJJ-MM-DD. */
export interface MemoryYear {
  years_ago: number;
  day: string;
  items: PhotoItem[];
}

/** GET /photos/memories?day=JJJJ-MM-DD (standaard vandaag in PLATFORM_TIMEZONE) */
export interface Memories {
  years: MemoryYear[];
}

/** Wat je kunt verbergen: een dag (JJJJ-MM-DD, die dag van dat jaar), een map (een node-id) of een
 * persoon (een id). */
export type HideKind = "day" | "folder" | "person";

/** POST /photos/memories/hide; DELETE /photos/memories/hide/{kind}/{ref}. 400 memories_bad_hide. */
export interface MemoryHide {
  kind: HideKind;
  ref: string;
}

/** GET /photos/memories/hide */
export interface MemoryHides {
  hides: MemoryHide[];
}

// --- de instellingen voor foto's (Keuze 44) ---

/** Aan of uit, zoals photo_pref het bewaart (gezichten en beeld). */
export type OnOff = "aan" | "uit";

/** GET en PATCH /me/photo-settings. location: of de plek van je foto's gebruikt wordt (mij: alleen
 * voor jou; uit: nergens). ocr_photos is van X1. Z0b (Keuze 55) zette faces (X2) en beeld (X3)
 * erbij, allebei standaard uit (Vraag 11); faces kan alleen aan als de beheerder het niet verbiedt
 * (faces_forbidden) en de container analyse er is (faces_available, Keuze 59). */
export interface PhotoSettings {
  location: "mij" | "uit";
  memories: boolean;
  ocr_photos: boolean;
  faces: OnOff;
  faces_forbidden: boolean;
  faces_available: boolean;
  beeld: OnOff;
}

/** PATCH /me/photo-settings: alleen wat verandert. faces aan: 403 faces_forbidden of 409
 * faces_unavailable; beeld aan zonder model: 409 beeld_not_installed. Uit wist (X2, X3). */
export interface PhotoSettingsPatch {
  location?: "mij" | "uit";
  memories?: boolean;
  ocr_photos?: boolean;
  faces?: OnOff;
  beeld?: OnOff;
}

// --- slimme albums (Keuze 42) ---

/** De regel van een slim album: de filters en q van zoeken, zonder offset, limit en after, en
 * zonder mode en like: een regel zoekt nooit op beeld (Keuze 80; de API geeft dan 422). */
export type AlbumRule = Omit<SearchParams, "offset" | "limit" | "after" | "mode" | "like">;

/** PUT /albums/{id}/rule */
export interface AlbumRuleBody {
  rule: AlbumRule;
  share_new?: boolean;
}

/** Het antwoord op PUT /albums/{id}/rule: needs_confirm als het album gedeeld is of een publieke
 * link heeft en share_new niet gezet is (dan de vraag van Keuze 42). */
export interface AlbumRuleSaved {
  needs_confirm: boolean;
}

/** Wat GET /albums en /albums/{id} de eigenaar erbij geven als er een regel is (het veld rule
 * van een album; voor anderen en zonder regel null). count: op hoeveel foto's de regel bij de
 * laatste verversing paste; code: album_rule_too_many als dat er meer dan 10.000 waren (dan
 * bleven de items zoals ze waren). items: hoeveel items nu uit de regel komen; private: hoeveel
 * daarvan alleen de eigenaar ziet. */
export interface AlbumRuleInfo {
  rule: AlbumRule;
  share_new: boolean;
  refreshed_at: string;
  count: number;
  code: string | null;
  items: number;
  private: number;
}

/** Waar een item van een album vandaan komt: met de hand of uit de regel. */
export type AlbumItemSource = "hand" | "rule";

/** Per item, alleen voor de eigenaar: shared false geeft het merkje "alleen voor jou". */
export interface AlbumItemRule {
  source: AlbumItemSource;
  shared: boolean;
}

/** In elk antwoord met een deling (ShareOut): bij een nieuwe deling of publieke link op een
 * album met een regel en share_new = 0 zoveel items uit de regel die alleen voor de eigenaar
 * blijven; anders null. */
export interface RuleItemsPrivate {
  rule_items_private: number | null;
}

// --- foutcodes en gebeurtenissen (§3.4) ---

/** De foutcodes van personen, herinneringen en slimme albums, met hun Engelse tekst in
 * i18n/errors/people.ts. */
export const PEOPLE_ERROR_CODES = [
  "people_name_taken",
  "people_merge_self",
  "album_rule_invalid",
  "album_rule_too_many",
  "memories_bad_hide",
] as const;
export type PeopleErrorCode = (typeof PEOPLE_ERROR_CODES)[number];

/** SSE naar de eigenaar: er is iets aan zijn personen veranderd ({}, PeopleChangedEvent). */
export const PEOPLE_CHANGED = "people.changed";
export type PeopleChangedData = Record<string, never>;
