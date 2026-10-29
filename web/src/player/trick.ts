// De sprites voor de tijdbalk (Keuze 30): trick.vtt van de sessie, met per cue een adres
// trick/<i>.jpg#xywh=x,y,w,h, relatief aan trick.vtt. De speler toont bij hover en slepen het
// vak van de tijd onder de muis. trick.vtt komt van de server, maar telt als onvertrouwde bytes:
// de lezer is begrensd en heeft een fuzz-test met een vaste seed (regel 10).

export interface TrickCue {
  start: number;
  end: number;
  /** Het plaatje, als absoluut pad */
  url: string;
  x: number;
  y: number;
  w: number;
  h: number;
}

/** Hooguit zoveel cues (een film van 12 uur met een cue per 10 s is er 4.320). */
export const MAX_CUES = 20_000;
/** Hooguit zoveel tekens lezen (2 MB). */
export const MAX_CHARS = 2 * 1024 * 1024;

const TIME = /^(?:(\d{1,3}):)?([0-5]?\d):([0-5]\d)[.,](\d{3})$/;

/** Een tijd van WebVTT in seconden ("00:01:02.003" of "01:02.003"), of null. */
export function vttTime(s: string): number | null {
  const m = TIME.exec(s.trim());
  if (!m) return null;
  return Number(m[1] ?? 0) * 3600 + Number(m[2]) * 60 + Number(m[3]) + Number(m[4]) / 1000;
}

/** Een adres uit trick.vtt, absoluut gemaakt ten opzichte van base (het adres van trick.vtt).
 * Alleen een pad op dezelfde origin; iets anders (een ander domein, javascript:) telt niet. */
export function resolveTile(path: string, base: string, origin: string): string | null {
  try {
    const url = new URL(path, new URL(base, origin));
    // Ook geen pad dat met // begint: als CSS-adres is dat een ander domein
    if (url.origin !== origin || url.pathname.startsWith("//")) return null;
    return url.pathname + url.search;
  } catch {
    return null;
  }
}

/** trick.vtt lezen. Wat niet klopt, slaat hij over; een kapotte regel breekt het geheel niet. */
export function parseTrickVtt(text: string, base: string, origin: string): TrickCue[] {
  const out: TrickCue[] = [];
  const lines = text.slice(0, MAX_CHARS).split(/\r\n|\r|\n/);
  for (let i = 0; i < lines.length && out.length < MAX_CUES; i++) {
    const arrow = lines[i].indexOf("-->");
    if (arrow < 0) continue;
    const start = vttTime(lines[i].slice(0, arrow));
    const end = vttTime(lines[i].slice(arrow + 3).trim().split(/\s+/)[0] ?? "");
    const ref = (lines[i + 1] ?? "").trim();
    if (start === null || end === null || end <= start || !ref) continue;
    const hash = ref.lastIndexOf("#xywh=");
    if (hash <= 0) continue;
    const box = ref
      .slice(hash + 6)
      .split(",")
      .map((v) => Number(v));
    if (box.length !== 4 || box.some((v) => !Number.isInteger(v) || v < 0 || v > 100_000)) continue;
    const [x, y, w, h] = box;
    if (w === 0 || h === 0) continue;
    const url = resolveTile(ref.slice(0, hash), base, origin);
    if (!url) continue;
    out.push({ start, end, url, x, y, w, h });
    i++;
  }
  return out.sort((a, b) => a.start - b.start);
}

/** De cue bij tijd t (s), of null. */
export function trickAt(cues: readonly TrickCue[], t: number): TrickCue | null {
  let lo = 0;
  let hi = cues.length - 1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    const c = cues[mid];
    if (t < c.start) hi = mid - 1;
    else if (t >= c.end) lo = mid + 1;
    else return c;
  }
  return null;
}
