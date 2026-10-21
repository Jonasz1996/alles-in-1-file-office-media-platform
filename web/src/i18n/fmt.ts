// Getallen, datums, groottes en duur opmaken in de taal van de app. De Intl-objecten worden per
// soort bewaard: een lijst van 10.000 rijen maakt er zo geen 10.000 aan.

import { bcp47, currentLocale } from "./locale";

type When = string | number | Date;
type DateStyle = "short" | "medium" | "long";

const cache = new Map<string, Intl.DateTimeFormat | Intl.NumberFormat>();

function dateFormat(key: string, options: Intl.DateTimeFormatOptions): Intl.DateTimeFormat {
  const id = `d:${currentLocale()}:${key}`;
  let f = cache.get(id) as Intl.DateTimeFormat | undefined;
  if (!f) {
    f = new Intl.DateTimeFormat(bcp47(), options);
    cache.set(id, f);
  }
  return f;
}

function numberFormat(options: Intl.NumberFormatOptions = {}): Intl.NumberFormat {
  const id = `n:${currentLocale()}:${JSON.stringify(options)}`;
  let f = cache.get(id) as Intl.NumberFormat | undefined;
  if (!f) {
    f = new Intl.NumberFormat(bcp47(), options);
    cache.set(id, f);
  }
  return f;
}

function toDate(v: When): Date {
  return v instanceof Date ? v : new Date(v);
}

const UNITS = ["B", "KB", "MB", "GB", "TB"];

/** Stappen voor fmt.relative: tot zoveel seconden deze eenheid. */
const RELATIVE: [Intl.RelativeTimeFormatUnit, number, number][] = [
  ["second", 60, 1],
  ["minute", 3600, 60],
  ["hour", 86400, 3600],
  ["day", 86400 * 30, 86400],
  ["month", 86400 * 365, 86400 * 30],
  ["year", Infinity, 86400 * 365],
];

export const fmt = {
  /** Alleen de datum: "21 okt 2026" (medium). */
  date(v: When, style: DateStyle = "medium"): string {
    return dateFormat(`date-${style}`, { dateStyle: style }).format(toDate(v));
  },
  /** Datum en tijd: "21 okt 2026, 14:05". */
  dateTime(v: When): string {
    return dateFormat("dateTime", { dateStyle: "medium", timeStyle: "short" }).format(toDate(v));
  },
  /** Alleen de tijd: "14:05". */
  time(v: When): string {
    return dateFormat("time", { timeStyle: "short" }).format(toDate(v));
  },
  /** Een kloktijd zoals hij op de camera stond (taken_at): opgeslagen als UTC, dus ook als UTC
   * getoond, zonder omrekenen naar de tijdzone van de browser. */
  wallClock(v: When, style: DateStyle = "medium"): string {
    return dateFormat(`wall-${style}`, { dateStyle: style, timeStyle: "short", timeZone: "UTC" }).format(toDate(v));
  },
  /** 1024-basis, één decimaal vanaf KB: "512 B", "1,5 KB" (nl), "1.5 KB" (en). */
  bytes(n: number): string {
    let value = n;
    let i = 0;
    while (value >= 1024 && i < UNITS.length - 1) {
      value /= 1024;
      i++;
    }
    const digits = i === 0 ? 0 : 1;
    const text = numberFormat({ minimumFractionDigits: digits, maximumFractionDigits: digits, useGrouping: false })
      // afronden zoals toFixed: 1023,96 KB blijft "1024,0 KB"
      .format(Number(value.toFixed(digits)));
    return `${text} ${UNITS[i]}`;
  },
  number(n: number, o?: Intl.NumberFormatOptions): string {
    return numberFormat(o).format(n);
  },
  /** 0.25 → "25%" (nl: "25%"). */
  percent(f: number): string {
    return numberFormat({ style: "percent", maximumFractionDigits: 0 }).format(f);
  },
  /** "3 minuten geleden", "over 2 dagen", "gisteren". */
  relative(v: When, now: number = Date.now()): string {
    const seconds = (toDate(v).getTime() - now) / 1000;
    const abs = Math.abs(seconds);
    const [unit, , size] = RELATIVE.find(([, limit]) => abs < limit) ?? RELATIVE[RELATIVE.length - 1];
    const rtf = new Intl.RelativeTimeFormat(bcp47(), { numeric: "auto" });
    return rtf.format(Math.round(seconds / size), unit);
  },
  /** 83 → "1:23", 3723 → "1:02:03". */
  duration(s: number): string {
    const total = Math.max(0, Math.round(s));
    const h = Math.floor(total / 3600);
    const m = Math.floor((total % 3600) / 60);
    const sec = String(total % 60).padStart(2, "0");
    return h ? `${h}:${String(m).padStart(2, "0")}:${sec}` : `${m}:${sec}`;
  },
  /** ["a", "b", "c"] → "a, b en c". */
  list(items: string[]): string {
    return new Intl.ListFormat(bcp47(), { style: "long", type: "conjunction" }).format(items);
  },
};

/** Sorteren op naam zoals mensen dat verwachten: "foto 2" vóór "foto 10", zonder op hoofdletters
 * en accenten te letten. */
export const collator: Intl.Collator = new Intl.Collator(bcp47(), { numeric: true, sensitivity: "base" });

/** Zonder hoofdletters en accenten, zoals app/text.py fold(): "Één Café" → "een cafe", ß → ss. */
export function fold(s: string): string {
  return s.normalize("NFKD").replace(/\p{Mn}/gu, "").toLowerCase().replace(/ß/g, "ss").replace(/ς/g, "σ");
}
