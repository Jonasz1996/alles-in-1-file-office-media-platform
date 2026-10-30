// Sporen (Keuze 29, 32): ondertitels als <track> op subs/{id}.vtt, de regel voor geforceerd, en
// een ander audiospoor (een nieuwe sessie met replace, op de positie van nu). Labels met de
// taalnamen van Intl.DisplayNames. Pure functies, zodat tracks.test.ts ze kan nalopen.

import type { AudioTrack, SubtitleTrack } from "../media/types";

/** Talen die geen taal zijn: und (onbepaald), zxx, mis, mul (zoals lang_code in M5). */
const NO_LANGUAGE = new Set(["und", "zxx", "mis", "mul", ""]);

/** Een taal als code van de browser (nld en dut worden nl), of null als het geen taal is. */
export function langKey(code: string | null | undefined): string | null {
  const word = (code ?? "").trim().toLowerCase().replace(/_/g, "-");
  if (NO_LANGUAGE.has(word)) return null;
  try {
    return new Intl.Locale(word).language || null;
  } catch {
    return /^[a-z]{2,3}$/.test(word) ? word : null;
  }
}

/** Past een geforceerd spoor bij het audiospoor? Zonder taal (of und) past alles (m3.md,
 * afwijking 19), net als _fits in M5 en decide in M3. */
export function langFits(sub: string | null | undefined, audio: string | null | undefined): boolean {
  const a = langKey(sub);
  const b = langKey(audio);
  return !a || !b || a === b;
}

/** De ondertitels die als <track> kunnen (tekst): alles behalve een beeldspoor (burn). */
export function textTracks(subs: readonly SubtitleTrack[]): SubtitleTrack[] {
  return subs.filter((s) => s.state !== "burn");
}

/**
 * De regel voor geforceerd (Keuze 29): koos je geen ondertitel, en is er een geforceerd
 * TEKSTspoor in de taal van het audiospoor (zonder taal past altijd), dan staat dat aan. Een
 * geforceerd beeldspoor gaat nooit vanzelf aan: dat biedt de speler aan (forced_available).
 */
export function autoSubtitle(subs: readonly SubtitleTrack[], audioLang: string | null | undefined): string | null {
  const hit = subs.find((s) => s.forced && s.state !== "burn" && s.state !== "failed" && langFits(s.lang, audioLang));
  return hit?.id ?? null;
}

/** Het audiospoor dat speelt: het gekozen, anders het standaardspoor, anders het eerste. */
export function currentAudio(tracks: readonly AudioTrack[], chosen: number | null | undefined): AudioTrack | null {
  return (
    tracks.find((a) => a.index === chosen) ?? tracks.find((a) => a.default) ?? tracks[0] ?? null
  );
}

/** Het volgende audiospoor (A), rondom; null als er maar één is. */
export function nextAudio(tracks: readonly AudioTrack[], chosen: number | null | undefined): number | null {
  if (tracks.length < 2) return null;
  const cur = currentAudio(tracks, chosen);
  const i = cur ? tracks.indexOf(cur) : -1;
  return tracks[(i + 1) % tracks.length].index;
}

/** C: ondertitels aan en uit. Uit gaat terug naar de laatste keuze, of de eerste die er is. */
export function toggleSubtitle(
  current: string | null,
  last: string | null,
  subs: readonly SubtitleTrack[],
): string | null {
  if (current) return null;
  const usable = subs.filter((s) => s.state !== "failed");
  if (last && usable.some((s) => s.id === last)) return last;
  return textTracks(usable)[0]?.id ?? null;
}

const names = new Map<string, Intl.DisplayNames | null>();

/** De naam van een taal in de taal van de app ("Engels", "Dutch"), of null. */
export function languageName(code: string | null | undefined, locale: string): string | null {
  const key = langKey(code);
  if (!key) return null;
  let dn = names.get(locale);
  if (dn === undefined) {
    try {
      dn = new Intl.DisplayNames([locale], { type: "language", fallback: "none" });
    } catch {
      dn = null;
    }
    names.set(locale, dn);
  }
  try {
    const name = dn?.of(key);
    return name ? name.charAt(0).toLocaleUpperCase(locale) + name.slice(1) : null;
  } catch {
    return null;
  }
}

export interface LabelWords {
  forced: string;
  sdh: string;
  /** "{label} (wordt ingebrand, kost omzetten)" */
  burn(label: string): string;
  unknown: string;
  /** Kanalen: "5.1", "stereo" */
  channels(n: number): string;
}

/** Het label van een ondertitel: de taal (of het label van de server), met geforceerd, SDH en
 * bij een beeldspoor dat hij ingebrand wordt. */
export function subtitleLabel(s: SubtitleTrack, locale: string, w: LabelWords): string {
  const name = languageName(s.lang, locale);
  // Het label van de server is de titel van het spoor, of de taal in hoofdletters ("NLD", "#2")
  const titled = /\p{Ll}/u.test(s.label);
  const base = titled
    ? name && !s.label.toLocaleLowerCase(locale).includes(name.toLocaleLowerCase(locale))
      ? `${name} · ${s.label}`
      : s.label
    : (name ?? (s.label || w.unknown));
  const extra = [s.forced && w.forced, s.sdh && w.sdh].filter(Boolean).join(", ");
  const label = extra ? `${base} (${extra})` : base;
  return s.state === "burn" ? w.burn(label) : label;
}

/** Het label van een audiospoor: taal, titel en kanalen ("Engels · Commentaar · 5.1"). */
export function audioLabel(a: AudioTrack, locale: string, w: Pick<LabelWords, "unknown" | "channels">): string {
  const parts = [languageName(a.lang, locale) ?? (a.lang && langKey(a.lang) ? a.lang : null)];
  if (a.title && a.title.trim()) parts.push(a.title.trim().slice(0, 60));
  if (a.channels) parts.push(w.channels(a.channels));
  const out = parts.filter(Boolean).join(" · ");
  return out || w.unknown;
}

/** De srclang van een <track>: een geldige BCP 47-code of leeg. */
export function srclang(code: string | null | undefined): string {
  return langKey(code) ?? "";
}
