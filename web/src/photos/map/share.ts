// De waarschuwing in het deeldialoog (fase 6, Z5; Keuze 30): wat GET /photos/share-check per node
// zegt, opgeteld over alles wat je deelt, en de zinnen die het dialoog daarvan maakt. Losse
// functies, zonder React, zodat ze te testen zijn; shares/LocationWarning.tsx toont ze.
//
// De server telt met blob_meta.gps: ook als je locatie op "uit" hebt, want de bytes van het bestand
// gaan gewoon mee. Een video waarvan het onbekend is (media uit), telt als "onbekend". Waar geen
// foto of video in kan zitten (een los document), vraagt de web niets.

import { api, type FileNode } from "../../api";
import { t } from "../../i18n";
import { wireQuery } from "../../search/wire";
import type { ShareCheck } from "./types";

const API = "/api/v1";

/** Hooguit zoveel controles tegelijk: een selectie is hooguit 100 items (MAX_MULTI_SHARE). */
export const CHECKS_AT_ONCE = 4;

/** Het pad van GET /photos/share-check, zoals shareCheck() in ./api.ts het vraagt. */
export function shareCheckPath(nodeId: string): string {
  return `${API}/photos/share-check${wireQuery({ node: nodeId })}`;
}

/** shareCheck() met een AbortSignal: het dialoog kan dicht voordat het antwoord er is. */
export function getShareCheck(nodeId: string, signal?: AbortSignal): Promise<ShareCheck> {
  return api.request<ShareCheck>("GET", shareCheckPath(nodeId), undefined, { signal });
}

/** Kan hier een foto of video met een plek in zitten? Een map en een album wel; een bestand alleen
 * als het een foto of video is (mime image/… of video/…, zoals photometa ze leest). */
export function mayHoldPlace(node: Pick<FileNode, "kind" | "mime">): boolean {
  if (node.kind !== "file") return true;
  const mime = (node.mime ?? "").toLowerCase();
  return mime.startsWith("image/") || mime.startsWith("video/");
}

/** Wat er gedeeld wordt, voor de zinnen: één foto, één video, of meer (een map, een album of een
 * selectie: "hierin"). */
export type Subject = "photo" | "video" | "many";

export function subjectOf(nodes: readonly Pick<FileNode, "kind" | "mime">[]): Subject {
  if (nodes.length !== 1 || nodes[0].kind !== "file") return "many";
  return (nodes[0].mime ?? "").toLowerCase().startsWith("video/") ? "video" : "photo";
}

/** Opgeteld over wat er gedeeld wordt; failed: hoeveel controles mislukten. */
export interface Tally {
  with_location: number;
  unknown: number;
  failed: number;
}

export const NOTHING: Tally = { with_location: 0, unknown: 0, failed: 0 };

/** Een telling van de server, als heel getal ≥ 0 (iets anders telt als 0). */
function count(v: unknown): number {
  return typeof v === "number" && Number.isFinite(v) && v > 0 ? Math.floor(v) : 0;
}

/**
 * Alle controles, hooguit CHECKS_AT_ONCE tegelijk, opgeteld. Een controle die mislukt telt bij
 * failed; de rest gaat door. Afgebroken (het dialoog ging dicht): dan gooit hij de AbortError.
 */
export async function tallyChecks(
  ids: readonly string[],
  check: (id: string, signal: AbortSignal) => Promise<ShareCheck>,
  signal: AbortSignal,
): Promise<Tally> {
  const total = { ...NOTHING };
  let next = 0;
  async function worker(): Promise<void> {
    while (next < ids.length) {
      const id = ids[next++];
      signal.throwIfAborted();
      try {
        const r = await check(id, signal);
        total.with_location += count(r.with_location);
        total.unknown += count(r.unknown);
      } catch (e) {
        if (signal.aborted) throw e;
        total.failed++;
      }
    }
  }
  await Promise.all(Array.from({ length: Math.min(CHECKS_AT_ONCE, ids.length) }, worker));
  signal.throwIfAborted();
  return total;
}

/**
 * De zinnen voor het dialoog; leeg als er niets te zeggen is. Bij meer dan één: "12 foto's hierin
 * hebben een locatie in het bestand. Wie ze downloadt, krijgt die mee." en zo nodig "Van 3 video's
 * weten we het niet (media staat uit)." Staat er geen foto met een plek in, dan zegt de zin over
 * de video's zelf waar het om gaat.
 */
export function warningLines(tally: Tally, subject: Subject): string[] {
  const lines: string[] = [];
  const { with_location: places, unknown, failed } = tally;
  if (subject === "many") {
    if (places > 0) lines.push(t("photos_map.share.photos", { n: places }));
    if (unknown > 0) lines.push(t(places > 0 ? "photos_map.share.unknown" : "photos_map.share.unknownOnly", { n: unknown }));
    if (failed > 0) lines.push(t("photos_map.share.failed"));
    return lines;
  }
  if (places > 0) lines.push(t(subject === "video" ? "photos_map.share.video" : "photos_map.share.photo"));
  else if (unknown > 0) lines.push(t("photos_map.share.unknownVideo"));
  else if (failed > 0) lines.push(t("photos_map.share.failedOne"));
  return lines;
}
