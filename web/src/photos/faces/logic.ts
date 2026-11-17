// Het denkwerk van gezichten in de web (fase 6, X2b; Keuze 66), los van React zodat vitest het
// zonder DOM toetst: of gezichten voor jou aan staan, de regel met de stand, wanneer die opnieuw
// vraagt, welke toets wat doet in het raster, welke gezichten naar confirm gaan, en hoe lang
// "ongedaan maken" kan.
//
// Uit is onzichtbaar: zonder de analyse (faces_available false, zoals 0.8.0) of met gezichten uit
// vraagt geen scherm een route van gezichten; alleen GET /me/photo-settings (van 0.8.0), één keer
// per minuut hooguit per gebruiker, gedeeld door alle plekken (facesSettings).

import { lazy } from "react";
import { api, type FileNode } from "../../api";
import { locale, t } from "../../i18n";
import { seg } from "../../search/wire";
import { keyToMove, type Move } from "../../ui/keys";
import type { PhotoSettings } from "../people/types";
import { fileFaces, personFaceSuggestions } from "./api";
import { type FaceOut, type FacePage, type FacesStatus, MAX_FACES } from "./types";

/** Het veld Naam (van Z6) pas als het nodig is ("Wie is dit?"), en de aanroepen van personen
 * (../people/api, van Z6) dynamisch: zo blijven ze buiten de chunks van gezichten (budget faces,
 * 12 kB). Op /fotos/personen en in het infopaneel heeft Z6 dat chunk al geladen. */
export const PersonCombo = lazy(() => import("../people/PersonCombo").then((m) => ({ default: m.PersonCombo })));
export const peopleApi = () => import("../people/api");

// --- de instellingen, gedeeld ---

/** Zo lang geldt een gelezen GET /me/photo-settings voor de plekken van gezichten. */
export const SETTINGS_MS = 60_000;

let cached: { who: string; at: number; value: Promise<PhotoSettings> } | null = null;

/** De foto-instellingen van deze gebruiker (who: zijn id), hooguit één keer per SETTINGS_MS
 * gevraagd. Per gebruiker: na uitloggen en inloggen als een ander in hetzelfde tabblad geldt de
 * stand van de vorige niet. */
export function facesSettings(who: string, now = Date.now()): Promise<PhotoSettings> {
  if (!cached || cached.who !== who || now - cached.at > SETTINGS_MS) {
    const value = peopleApi().then((m) => m.photoSettings());
    const mine = { who, at: now, value };
    cached = mine;
    value.catch(() => {
      if (cached === mine) cached = null;
    });
  }
  return cached.value;
}

/** Na PATCH /me/photo-settings: de nieuwe stand voor iedereen. s null vergeet hem (tests). */
export function rememberSettings(who: string, s: PhotoSettings | null, now = Date.now()): void {
  cached = s ? { who, at: now, value: Promise.resolve(s) } : null;
}

/** Kan deze server gezichten (de analyse draait)? Zo niet: geen plek toont iets (0.8.0). */
export function available(s: PhotoSettings): boolean {
  return s.faces_available;
}

/** Gezichten staan voor jou aan (en de server kan het). */
export function isOn(s: PhotoSettings): boolean {
  return s.faces_available && !s.faces_forbidden && s.faces === "aan";
}

/** Wat /fotos/personen bovenaan toont: niets (de server kan het niet: de pagina van 0.8.0), de
 * regel "de beheerder heeft het uitgezet", de regel "staat uit" met de uitleg, of alles. */
export function peoplePart(s: PhotoSettings): "none" | "forbidden" | "off" | "on" {
  if (!available(s)) return "none";
  if (isOn(s)) return "on";
  return s.faces_forbidden ? "forbidden" : "off";
}

/** De gezichten op een foto voor de rij in het infopaneel, of null (geen rij): alleen met
 * gezichten aan, en alleen voor de eigenaar (een ander krijgt van de API [] en ziet niets).
 * Zonder gezichten vraagt dit geen route van gezichten. */
export async function rowFaces(who: string, fileId: string): Promise<FaceOut[] | null> {
  if (!isOn(await facesSettings(who))) return null;
  const [node, found] = await Promise.all([
    api.request<FileNode>("GET", `/api/v1/files/${seg(fileId)}`),
    fileFaces(fileId),
  ]);
  // Zoals permissionOf (sharing.ts), zonder dat chunk mee te laden (budget faces, 12 kB)
  return (node.permission ?? "owner") === "owner" ? found : null;
}

/** Zoveel uitsneden op de kaart "Misschien ook Anna". */
export const SAMPLE = 6;

/** De eerste voorstellen voor een persoon ("Misschien ook Anna"), of null zonder gezichten (dan
 * vraagt dit geen route van gezichten). */
export async function personSample(who: string, personId: string): Promise<FacePage | null> {
  if (!isOn(await facesSettings(who))) return null;
  return personFaceSuggestions(personId, null, SAMPLE);
}

// --- de stand bovenaan /fotos/personen ---

/** Elke 10 s opnieuw vragen, zolang de regel te zien is en er nog werk is. */
export const POLL_MS = 10_000;

/** De regel met de stand (Keuze 66). De beheerder en het wissen gaan voor; daarna wacht of zoekt
 * het, of is het klaar. Mislukte foto's komen erachter. */
export function statusLine(s: FacesStatus): string {
  if (s.forbidden) return t("photos_faces.status.forbidden");
  if (s.wiping) return t("photos_faces.status.wiping");
  let line: string;
  if (s.photos.waiting > 0 && s.analyse !== "ok") line = t("photos_faces.status.waiting");
  else if (s.photos.waiting > 0) {
    line = t("photos_faces.status.scanning", { done: s.photos.done, total: s.photos.total });
  } else line = t("photos_faces.status.done", { faces: s.faces.found, photos: s.photos.done });
  if (s.photos.failed > 0) line += ` · ${t("photos_faces.status.failed", { n: s.photos.failed })}`;
  return line;
}

/** Is er nog werk (dan vraagt de regel elke POLL_MS opnieuw)? */
export function busy(s: FacesStatus): boolean {
  return s.setting === "aan" && !s.forbidden && (s.photos.waiting > 0 || s.wiping);
}

// --- het raster ---

export type GridMode = "suggest" | "group";
export type GridAction = "yes" | "no" | "hide" | "select" | "open" | "undo" | "close" | Move;

/** De letters per taal: J/N/V in het Nederlands, Y/N/H in het Engels (Keuze 66). */
export function letters(lang = locale()): { yes: string; no: string; hide: string } {
  return lang === "en" ? { yes: "y", no: "n", hide: "h" } : { yes: "j", no: "n", hide: "v" };
}

type KeyLike = { key: string; ctrlKey: boolean; metaKey: boolean; altKey: boolean; shiftKey?: boolean };

/** Wat een toets in het raster doet, of null (dan gaat hij door). J en N alleen bij voorstellen,
 * Spatie alleen in "Wie is dit?"; Ctrl+Z (of Cmd+Z) maakt de laatste keuze ongedaan. */
export function gridKey(e: KeyLike, mode: GridMode, lang = locale()): GridAction | null {
  const key = e.key.toLowerCase();
  if ((e.ctrlKey || e.metaKey) && !e.altKey) return key === "z" && !e.shiftKey ? "undo" : null;
  if (e.altKey || e.ctrlKey || e.metaKey) return null;
  if (e.key === "Escape") return "close";
  if (e.key === "Enter") return "open";
  if (e.key === " ") return mode === "group" ? "select" : null;
  const l = letters(lang);
  if (key === l.hide) return "hide";
  if (mode === "suggest" && key === l.yes) return "yes";
  if (mode === "suggest" && key === l.no) return "no";
  return keyToMove(e);
}

/** De gezichten voor confirm: alleen die je zag en die geselecteerd zijn, in de volgorde van het
 * raster; nooit "de hele groep" (Keuze 66). */
export function confirmIds(faces: readonly FaceOut[], selected: ReadonlySet<string>, seen: ReadonlySet<string>): string[] {
  return faces.filter((f) => selected.has(f.id) && seen.has(f.id)).map((f) => f.id);
}

/** In stukken van hooguit MAX_FACES (anders 422 faces_too_many). */
export function inChunks<T>(items: readonly T[], size = MAX_FACES): T[][] {
  const out: T[][] = [];
  for (let i = 0; i < items.length; i += size) out.push(items.slice(i, i + size));
  return out;
}

/** Het volgende gezicht zonder keuze na i (of ervoor, als er na i niets meer is); -1: geen. */
export function nextOpen(count: number, i: number, done: (j: number) => boolean): number {
  for (let j = i + 1; j < count; j++) if (!done(j)) return j;
  for (let j = i - 1; j >= 0; j--) if (!done(j)) return j;
  return -1;
}

// --- ongedaan maken ---

/** Zo lang staat de melding met "Ongedaan maken" (de standaard van toast met een actie), en zo
 * lang werkt Ctrl+Z. De API staat het tot 10 minuten na de keuze toe. */
export const UNDO_MS = 8_000;

/** De laatste keuze die nog ongedaan kan: Ctrl+Z neemt hem één keer, binnen UNDO_MS. */
export class LastChoice {
  private last: { run: () => void; until: number } | null = null;

  set(run: () => void, now = Date.now()): void {
    this.last = { run, until: now + UNDO_MS };
  }

  /** De actie als die nog kan (en daarna niet meer), anders null. */
  take(now = Date.now()): (() => void) | null {
    const last = this.last;
    this.last = null;
    return last && now <= last.until ? last.run : null;
  }

  clear(): void {
    this.last = null;
  }
}
