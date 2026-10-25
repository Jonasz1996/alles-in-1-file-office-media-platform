// Kleine, losse hulpjes van de viewer: duur, wat voor speler, wanneer een nieuwe link mag,
// welk soort weergave een bestand krijgt, en wat een fout van de speler betekent.

import type { PreviewMeta } from "../api";
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
const TEXT_MIMES = ["application/json", "application/xml", "application/javascript", "application/x-sh", "application/sql"];
const TEXT_EXT =
  /\.(txt|md|markdown|csv|tsv|log|json|xml|ya?ml|toml|ini|cfg|conf|py|js|mjs|cjs|ts|tsx|jsx|css|scss|html?|java|c|h|cc|cpp|hpp|cs|go|rs|rb|php|sh|bash|zsh|sql|kt|swift|lua|pl|r|tex|srt|vtt|env|gitignore|dockerfile)$/i;

/** Hoe de viewer dit bestand toont. Wat de server in de inhoud vond, wint van het mime-type. */
export function viewKind(item: { name: string; mime: string | null }, meta: ViewerMeta): ViewKind {
  const mime = (item.mime ?? "").toLowerCase();
  const kind = category({ kind: "file", mime });
  if (meta.office || OFFICE.some((m) => mime.startsWith(m))) return "office";
  if (meta.entries !== undefined) return "archive";
  const player = playerFor(kind, meta);
  if (player) return player;
  if (meta.text !== undefined) return "text";
  if (kind === "pdf") return "pdf";
  if (kind === "image") return "image";
  if (mime.startsWith("text/") || TEXT_MIMES.includes(mime) || TEXT_EXT.test(item.name)) return "text";
  return "none";
}

/** Toont de browser dit soort afbeelding zelf? HEIC, HEIF en TIFF niet: daarvoor is display. */
export function browserShows(mime: string | null): boolean {
  const m = (mime ?? "").toLowerCase();
  return !/^image\/(hei[cf](-sequence)?|tiff?|x-tiff|x-adobe-dng|x-raw|x-canon|x-nikon|x-sony)/.test(m);
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

/** "h264 (High) · aac · mp4": voor de hint bij een formaat dat niet speelt. */
export function codecLabel(meta: ViewerMeta): string {
  const parts: string[] = [];
  if (meta.video?.codec) parts.push(meta.video.profile ? `${meta.video.codec} (${meta.video.profile})` : meta.video.codec);
  if (meta.audio?.codec) parts.push(meta.audio.codec);
  if (meta.container) parts.push(meta.container.split(",")[0]);
  return parts.join(" · ");
}
