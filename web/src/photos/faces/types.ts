// De contracten van gezichten automatisch (fase 6, X2; bouwontwerp Keuze 60–69 en de API van X2),
// vastgelegd in Z0b (Keuze 55): wat de routes onder /people/faces en /files/{id}/faces geven en
// nemen. Alleen de eigenaar ziet gezichten; een ander die de foto mag zien krijgt [], anders 404.
// Nooit een vector of een score: sure zegt alleen of het voorstel ruim boven de drempel zit.
//
// De velden die X2 in de typen van Z0 zet (faces in de foto-instellingen, het blok op /beheer,
// "faces" bij herbouwen en in search.progress) staan in photos/people/types.ts,
// admin/searchApi.ts en search/types.ts. De vormen hieronder zijn het voorstel van Z0b uit de
// tabel van de API van X2; A0 legt ze vast met app/people/faces_types.py. Tot X2 er is, geven de
// routes 501 not_built (de stubs van Z0b).

import type { AnalyseState, PersonRef } from "../../search/types";
import type { Box, OnOff } from "../people/types";

export type { Box, OnOff } from "../people/types";

// --- grenzen ---

/** Zoveel gezichten mag één verzoek (confirm, reject, hide, reset) hooguit noemen; daarboven 422
 * faces_too_many. */
export const MAX_FACES = 200;
/** Gezichten per pagina (suggestions met person, een groep). */
export const FACES_PAGE = 60;
/** De uitsneden: 96 of 192 px (WebP, no-store). */
export const CROP_SIZES = [96, 192] as const;
export type CropSize = (typeof CROP_SIZES)[number];

// --- een gezicht ---

/** Waarom een voorstel: lijkt op de bevestigde gezichten (sim), of een tag met die naam (tag). */
export type SuggestWhy = "sim" | "tag";

/** Een voorstel voor een gezicht. sure: de score is ≥ t_suggest + 0,10; de score zelf nooit. */
export interface FaceSuggest {
  person_id: string;
  name: string;
  sure: boolean;
  why: SuggestWhy;
}

/** Een gezicht naar buiten (FaceOut): het kader als deel van de preview (0–1, zoals Keuze 37),
 * de persoon als het bevestigd is, en anders misschien een voorstel. */
export interface FaceOut {
  id: string;
  node_id: string;
  box: Box;
  person: PersonRef | null;
  suggest: FaceSuggest | null;
}

// --- de stand (GET /people/faces/status) ---

/** De foto's van de eigenaar voor gezichten: alle, klaar, wachtend, mislukt en overgeslagen. */
export interface FacesPhotos {
  total: number;
  done: number;
  waiting: number;
  failed: number;
  skipped: number;
}

/** De gezichten van de eigenaar: gevonden, benoemd, open, en in hoeveel groepen. */
export interface FacesCount {
  found: number;
  named: number;
  open: number;
  groups: number;
}

/** GET /people/faces/status (eigenaar): de regel bovenaan /fotos/personen. */
export interface FacesStatus {
  setting: OnOff;
  /** De beheerder verbiedt het */
  forbidden: boolean;
  analyse: AnalyseState;
  photos: FacesPhotos;
  faces: FacesCount;
  /** "Gezichten worden gewist …" */
  wiping: boolean;
}

// --- voorstellen en groepen ---

/** Per persoon: hoeveel voorstellen er zijn, met hooguit zes uitsneden ("Is dit Anna?"). */
export interface PersonSuggestions {
  person: PersonRef;
  count: number;
  sample: FaceOut[];
}

/** GET /people/faces/suggestions?cursor= (zonder person): per persoon. */
export interface SuggestionsByPerson {
  people: PersonSuggestions[];
  cursor: string | null;
}

/** GET /people/faces/suggestions?person=&cursor=&limit=60 en GET /people/faces/groups/{key}: de
 * gezichten, beste eerst, per FACES_PAGE. */
export interface FacePage {
  faces: FaceOut[];
  cursor: string | null;
}

/** Een groep van een ronde ("Wie is dit?"): key is "<run>:<cluster>"; hooguit twaalf gezichten. */
export interface FaceGroup {
  key: string;
  count: number;
  faces: FaceOut[];
}

/** GET /people/faces/groups?cursor=: de grootste eerst. */
export interface FaceGroups {
  groups: FaceGroup[];
  cursor: string | null;
}

// --- bevestigen, afwijzen, niet tonen, ongedaan maken ---

/** POST /people/faces/confirm: een bestaande persoon of een nieuwe naam. Alleen de gezichten die
 * je zag en die geselecteerd zijn, nooit "de hele groep". */
export type FacesConfirm = { faces: string[] } & ({ person_id: string } | { name: string });

/** POST /people/faces/reject */
export interface FacesReject {
  faces: string[];
  person_id: string;
}

/** POST /people/faces/wipe-unnamed: zoveel gezichten gaan in de rij om gewist te worden. */
export interface FacesQueued {
  queued: number;
}

// --- foutcodes ---

/** De foutcodes van gezichten, met hun Engelse tekst in i18n/errors/faces.ts. Daarnaast guest,
 * not_found en rate_limited (die zijn er al). */
export const FACES_ERROR_CODES = [
  "faces_forbidden",
  "faces_off",
  "faces_unavailable",
  "faces_too_many",
  "faces_gone",
] as const;
export type FacesErrorCode = (typeof FACES_ERROR_CODES)[number];
