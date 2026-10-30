// De taal van de app. Volgorde: het account (na het inloggen, zie applyAccountLocale), dan de
// keuze op dit apparaat (localStorage platform-locale), dan de taal van de browser (nl* → nl,
// anders en), en anders Nederlands. Losse module zonder imports, zodat i18n/index.ts en
// i18n/fmt.ts hem allebei kunnen gebruiken zonder kringverwijzing.

import type { Locale } from "./types";

export const LOCALE_KEY = "platform-locale";

export function isLocale(value: unknown): value is Locale {
  return value === "nl" || value === "en";
}

/** De taal uit een bewaarde keuze en de talen van de browser. */
export function detectLocale(stored: string | null | undefined, languages: readonly string[] | undefined): Locale {
  if (isLocale(stored)) return stored;
  const first = languages?.find((l) => typeof l === "string" && l.length > 0);
  if (!first) return "nl";
  return first.toLowerCase().startsWith("nl") ? "nl" : "en";
}

function storedLocale(): string | null {
  try {
    return globalThis.localStorage?.getItem(LOCALE_KEY) ?? null;
  } catch {
    return null; // opslag uit
  }
}

function browserLanguages(): readonly string[] | undefined {
  // Alleen in een browser: Node (de tests) heeft ook een navigator, met de taal van de machine
  if (typeof document === "undefined") return undefined;
  const nav = globalThis.navigator as Navigator | undefined;
  if (!nav) return undefined;
  return nav.languages?.length ? nav.languages : nav.language ? [nav.language] : undefined;
}

let current: Locale = detectLocale(storedLocale(), browserLanguages());

export function currentLocale(): Locale {
  return current;
}

/** Alleen voor tests en voor initI18n: de taal wisselt in de app altijd via herladen. */
export function setCurrentLocale(l: Locale): void {
  current = l;
}

/** De BCP 47-tag voor Intl: Nederlands uit Nederland, Engels met Europese datums. */
export function bcp47(l: Locale = current): string {
  return l === "nl" ? "nl-NL" : "en-GB";
}

/** Bewaart de keuze op dit apparaat; false als dat niet lukt (opslag uit). */
export function storeLocale(l: Locale): boolean {
  try {
    globalThis.localStorage?.setItem(LOCALE_KEY, l);
    return globalThis.localStorage?.getItem(LOCALE_KEY) === l;
  } catch {
    return false;
  }
}
