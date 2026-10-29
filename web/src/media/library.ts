// Bibliotheken in de web-app (fase 5, M2): pure hulp voor de pagina's onder /media en de
// dialoog "Als bibliotheek gebruiken". Welke soorten en sorteringen er zijn, wat er in het adres
// staat (?sort=, ?type=), welke live gebeurtenissen een pagina opnieuw laten laden, en de korte
// teksten over sporen en de analyse. Zonder React, zodat vitest het kan testen.

import type { IconName } from "../ui/Icon";
import type { AnalysisState, AnalysisStep, ExtraKind, LibraryKind, ScanState, StreamBrief } from "./types";

/** De sorteringen van GET /libraries/{id}/items (sort=). */
export type Sort = "title" | "added" | "year";
export const SORTS: readonly Sort[] = ["title", "added", "year"];

/** De soorten in een lijst (type=); een bibliotheek muziek kent ze niet (dat is M9). */
export type ListType = "movie" | "show" | "video";

/** Hooguit zo lang mag de naam van een bibliotheek zijn (LibraryIn.name). */
export const NAME_MAX = 200;

/** Welke soorten een bibliotheek in de lijst kan tonen; de eerste is wat de server zonder
 * type= geeft. Eén of geen: dan geen keuze op de pagina. */
export function typesFor(kind: LibraryKind): readonly ListType[] {
  switch (kind) {
    case "movies":
      return ["movie", "video"];
    case "shows":
      return ["show", "video"];
    case "videos":
      return ["video"];
    default:
      return [];
  }
}

/** ?sort= uit het adres; iets onbekends is de standaard (titel). */
export function parseSort(v: string | null | undefined): Sort {
  return SORTS.includes(v as Sort) ? (v as Sort) : "title";
}

/** ?type= uit het adres, als die bij deze bibliotheek hoort; anders undefined (de standaard van
 * de server, de eerste van typesFor). */
export function parseType(kind: LibraryKind, v: string | null | undefined): ListType | undefined {
  const types = typesFor(kind);
  return types.includes(v as ListType) && v !== types[0] ? (v as ListType) : undefined;
}

/** Het soort dat de lijst nu toont (voor de keuze op de pagina). */
export function shownType(kind: LibraryKind, type: ListType | undefined): ListType | undefined {
  return type ?? typesFor(kind)[0];
}

/** Bezig met scannen (in de rij of aan het lopen). */
export function scanBusy(s: ScanState): boolean {
  return s === "queued" || s === "running";
}

/** Het icoon van een soort bibliotheek. */
export function kindIcon(kind: LibraryKind): IconName {
  return kind === "music" ? "audio" : "video";
}

/** De naam die de dialoog voorstelt: die van de map, ingekort. */
export function defaultName(folderName: string): string {
  return folderName.trim().slice(0, NAME_MAX);
}

/** De naam uit het veld van de dialoog: leeg betekent "de naam van de map" (de server kiest),
 * anders ingekort tot NAME_MAX. */
export function nameToSend(value: string): string | undefined {
  const name = value.trim();
  return name ? name.slice(0, NAME_MAX) : undefined;
}

interface EventLike {
  type: string;
  data: Record<string, unknown>;
}

/** Laat deze gebeurtenis de lijst van deze bibliotheek opnieuw laden? media.item en
 * media.library van deze bibliotheek, en een nieuwe verbinding (dan kan er iets gemist zijn). */
export function affectsLibrary(e: EventLike, libraryId: string): boolean {
  if (e.type === "connected") return true;
  if (e.type !== "media.item" && e.type !== "media.library") return false;
  return e.data.library_id === libraryId;
}

/** Laat deze gebeurtenis de start (/media) opnieuw laden? Alles van bibliotheken en items, de
 * stand van het kijken (Verder kijken), delingen (een bibliotheek die je nu wel of niet ziet). */
export function affectsHome(e: EventLike): boolean {
  return (
    e.type === "connected" ||
    e.type === "media.item" ||
    e.type === "media.library" ||
    e.type === "playback.progress" ||
    e.type.startsWith("share.")
  );
}

/** Laat deze gebeurtenis de pagina van dit item opnieuw laden? */
export function affectsItem(e: EventLike, nodeId: string): boolean {
  if (e.type === "connected") return true;
  if (e.type === "media.item" || e.type === "playback.progress") return e.data.node_id === nodeId;
  return false;
}

/** De stappen van de analyse in een vaste volgorde. */
export const ANALYSIS_STEPS: readonly AnalysisStep[] = ["probe", "keyframes", "subs", "sprites", "audio_info"];

/** De stappen die er zijn, in de vaste volgorde. */
export function analysisRows(a: Partial<Record<AnalysisStep, AnalysisState>>): [AnalysisStep, AnalysisState][] {
  const out: [AnalysisStep, AnalysisState][] = [];
  for (const step of ANALYSIS_STEPS) {
    const state = a[step];
    if (state) out.push([step, state]);
  }
  return out;
}

/** Een spoor kort: "h264", "aac · nl · Commentaar". */
export function streamText(s: Pick<StreamBrief, "codec" | "lang" | "title">): string {
  return [s.codec, s.lang, s.title].filter((x): x is string => typeof x === "string" && x.length > 0).join(" · ");
}

/** Het soort dat de dialoog voorstelt, uit de naam van de map ("Series", "Muziek", "Video's");
 * anders films. Alleen een voorstel: de gebruiker kiest. */
export function guessKind(folderName: string): LibraryKind {
  const name = folderName.toLowerCase();
  if (/\b(series?|shows?|tv)\b/.test(name)) return "shows";
  if (/\b(muziek|music|audio)\b/.test(name)) return "music";
  if (/\b(video'?s?|clips?|home ?video'?s?)\b/.test(name)) return "videos";
  return "movies";
}

/** De sleutel van een soort extra in het woordenboek ("other" is daar "misc": een object met
 * de sleutel other is voor i18n een meervoudsvorm). */
export function extraKey(kind: ExtraKind): Exclude<ExtraKind, "other"> | "misc" {
  return kind === "other" ? "misc" : kind;
}
