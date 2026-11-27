// Getallen van Sport leesbaar maken: afstand, tijd, tempo en snelheid.

import { fmt, t } from "../i18n";
import type { SportKind } from "./api";

export function km(meters: number, digits = 2): string {
  return fmt.number(meters / 1000, { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

export function meters(m: number): string {
  return fmt.number(Math.round(m));
}

/** 1:02:03 of 12:34 */
export function duration(seconds: number): string {
  return fmt.duration(seconds);
}

/** Tempo per km (5:12) uit seconden en meters; "–" zonder afstand. */
export function pace(seconds: number, distance: number): string {
  if (distance < 1 || seconds <= 0) return "–";
  return duration((seconds / distance) * 1000);
}

/** km/u uit m/s */
export function speedKmh(ms: number): string {
  return fmt.number(ms * 3.6, { minimumFractionDigits: 1, maximumFractionDigits: 1 });
}

/** Lopen en wandelen in tempo, de rest in km/u. */
export function usesPace(sport: SportKind): boolean {
  return sport === "run" || sport === "walk" || sport === "hike";
}

export function sportLabel(s: SportKind): string {
  // "other" kan geen sleutel zijn (een object met "other" is een meervoud): daarom "misc"
  return s === "other" ? t("sport.sports.misc") : t(`sport.sports.${s}`);
}
