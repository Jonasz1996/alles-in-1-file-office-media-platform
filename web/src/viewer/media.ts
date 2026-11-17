// Kleine, losse hulpjes van de viewer: duur, wat voor speler, wanneer een nieuwe link mag,
// welk soort weergave een bestand krijgt, en wat een fout van de speler betekent.

import type { PreviewMeta } from "../api";
import type { ServerEvent } from "../events";
import { type Category, category } from "../Thumb";
import type { ViewerMeta } from "./api";

/** 83 -> "1:23", 3723 -> "1:02:03". */
export function formatDuration(seconds: number): string {
  const total = Math.max(0, Math.round(seconds));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = String(total % 60).padStart(2, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${s}` : `${m}:${s}`;
}

/**
 * Speler voor video of audio, tenzij de server in de inhoud iets anders vond (tekst of een
 * archief): het soort komt van het mime-type, en dat kan ernaast zitten.
 */
export function playerFor(kind: Category, meta: PreviewMeta): "video" | "audio" | null {
  if (meta.text !== undefined || meta.entries !== undefined) return null;
  return kind === "video" || kind === "audio" ? kind : null;
}

const RENEW_QUIET = 60_000;

/**
 * Mag de viewer na een fout een nieuwe link? Hooguit drie kort na elkaar (dan is het bestand
 * zelf het probleem), maar een link die na lang afspelen verliep, mag altijd weer.
 */
export function mayRenew(state: { count: number; at: number }, now: number): boolean {
  if (now - state.at > RENEW_QUIET) state.count = 0;
  if (state.count >= 3) return false;
  state.count++;
  state.at = now;
  return true;
}

export type ViewKind = "image" | "pdf" | "video" | "audio" | "text" | "office" | "archive" | "none";

const OFFICE = [
  "application/vnd.openxmlformats-officedocument.",
  "application/vnd.ms-word.document.macroenabled",
  "application/vnd.ms-excel.sheet.macroenabled",
  "application/vnd.ms-powerpoint.presentation.macroenabled",
  "application/vnd.oasis.opendocument.text",
  "application/vnd.oasis.opendocument.spreadsheet",
  "application/vnd.oasis.opendocument.presentation",
];
// Een PDF van de editor (fase 4, O3) die er is, gemaakt wordt of gevraagd kan worden: dan de
// weergave van kantoordocumenten, ook voor doc, xls, ppt en rtf (zonder eigen voorbeeld)
const CONVERTED: readonly string[] = ["ready", "pending", "ask"];
const TEXT_MIMES = ["application/json", "application/xml", "application/javascript", "application/x-sh", "application/sql"];
const TEXT_EXT =
  /\.(txt|md|markdown|csv|tsv|log|json|xml|ya?ml|toml|ini|cfg|conf|py|js|mjs|cjs|ts|tsx|jsx|css|scss|html?|java|c|h|cc|cpp|hpp|cs|go|rs|rb|php|sh|bash|zsh|sql|kt|swift|lua|pl|r|tex|srt|vtt|env|gitignore|dockerfile)$/i;

/** Hoe de viewer dit bestand toont. Wat de server in de inhoud vond, wint van het mime-type. */
export function viewKind(item: { name: string; mime: string | null }, meta: ViewerMeta): ViewKind {
  const mime = (item.mime ?? "").toLowerCase();
  const kind = category({ kind: "file", mime });
  if (meta.office || OFFICE.some((m) => mime.startsWith(m))) return "office";
  if (meta.pdf_state && CONVERTED.includes(meta.pdf_state)) return "office";
  if (meta.entries !== undefined) return "archive";
  const player = playerFor(kind, meta);
  if (player) return player;
  if (meta.text !== undefined) return "text";
  if (kind === "pdf") return "pdf";
  if (kind === "image") return "image";
  if (mime.startsWith("text/") || TEXT_MIMES.includes(mime) || TEXT_EXT.test(item.name)) return "text";
  return "none";
}

/** Toont de browser dit soort afbeelding zelf? HEIC, HEIF, TIFF, PSD, JPEG 2000 en camera-RAW
 * niet: daarvoor is display (render.DISPLAY_FORMATS en RAW_MIMES). */
export function browserShows(mime: string | null): boolean {
  const m = (mime ?? "").toLowerCase();
  if (/^image\/(hei[cf](-sequence)?|tiff?|x-tiff|jp2|vnd\.adobe\.photoshop)/.test(m)) return false;
  return !/^image\/(x-adobe-dng|x-raw|x-canon|x-nikon|x-sony|x-fuji|x-olympus|x-panasonic|x-pentax)/.test(m);
}

/** Komt er nog meer van de preview? Nog in de maak, of alleen de thumbnail (partial: de grote
 * versie en 2048 komen in een tweede ronde). Dan vraagt de viewer het detail opnieuw: een RAW of
 * HEIC toont anders 256 px, ook als de grote versie er al is. Mislukte de tweede ronde
 * (large_failed), dan komt er niets meer. */
export function moreComing(detail: { state: string; meta: ViewerMeta }): boolean {
  if (detail.state === "pending") return true;
  return detail.state === "ready" && detail.meta.partial === true && detail.meta.large_failed !== true;
}

/** Is dit preview.ready voor bestand id (data.node_ids)? */
export function previewReadyFor(event: ServerEvent, id: string): boolean {
  const ids = event.data.node_ids;
  return event.type === "preview.ready" && Array.isArray(ids) && ids.map(String).includes(id);
}

/** Het origineel tonen in plaats van de preview: wat beweegt (een preview staat stil). */
export function wantsOriginal(mime: string | null, meta: ViewerMeta): boolean {
  if (meta.animated === true) return true;
  if (meta.animated === false) return false;
  // Nog geen animated (een preview van vóór deze versie): wat kan bewegen, als origineel
  return /^image\/(gif|webp|apng)$/.test((mime ?? "").toLowerCase());
}

/** Wat er misging bij afspelen. */
export type MediaProblem = "unsupported" | "expired" | "network" | null;

/**
 * Een MediaError (code 1 afgebroken, 2 netwerk, 3 decoderen, 4 bron niet bruikbaar) plus, bij
 * 2 en 4, de status van een los verzoek naar hetzelfde adres: werkt dat niet (401, 403, 404,
 * 410), dan is de link verlopen of de toegang weg; werkt het wel, dan kan de browser het
 * formaat niet afspelen.
 */
export function mediaProblem(code: number | undefined, probeStatus?: number | null): MediaProblem {
  if (code === 1) return null;
  if (code === 3) return "unsupported";
  if (probeStatus !== undefined && probeStatus !== null) {
    if ([401, 403, 404, 410].includes(probeStatus)) return "expired";
    if (probeStatus >= 200 && probeStatus < 300) return code === 2 ? "network" : "unsupported";
    return "network";
  }
  if (code === 4) return "unsupported";
  return "network";
}

// Codecs van een stilstaand beeld. render.codecs_of neemt het eerste videospoor, ook een
// ingebedde hoes (attached_pic); die speelt de browser niet als beeld af, en dat is geen fout.
// Een echte film in MJPEG valt hier dus ook onder; die speelt dan zonder melding alleen het
// geluid, zoals eerst.
const STILL = new Set(["mjpeg", "png", "bmp", "gif", "webp", "tiff"]);

/**
 * Speelt de browser van een video alleen het geluid? Na loadedmetadata is de maat van het beeld
 * bekend. Is die 0 terwijl de server een videospoor vond, dan kan deze browser dat beeld niet
 * decoderen. Een MediaError komt dan niet: Chromium slaat een spoor dat het niet kan over, speelt
 * de rest, en geeft pas een fout als er niets overblijft (nagespeeld in Chromium 141: HEVC met
 * Opus speelt zonder fout alleen het geluid). In CI (Chrome 156, op 1a6dac0) kwam zo bij
 * hevc10_hdr10.mkv geen fout en geen melding: Chrome heeft AAC, en zonder GPU geen HEVC. Chromium
 * 141 kan geen AAC en gaf daar wel een fout. Zonder meta.video (nog geen preview) weten we het
 * niet: dan geen melding.
 */
export function pictureMissing(meta: ViewerMeta, videoWidth: number): boolean {
  const codec = meta.video?.codec?.toLowerCase();
  return videoWidth === 0 && !!codec && !STILL.has(codec);
}

/** "h264 (High) · aac · mp4": voor de hint bij een formaat dat niet speelt. */
export function codecLabel(meta: ViewerMeta): string {
  const parts: string[] = [];
  if (meta.video?.codec) parts.push(meta.video.profile ? `${meta.video.codec} (${meta.video.profile})` : meta.video.codec);
  if (meta.audio?.codec) parts.push(meta.audio.codec);
  if (meta.container) parts.push(meta.container.split(",")[0]);
  return parts.join(" · ");
}
