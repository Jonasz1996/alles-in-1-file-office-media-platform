// Vertalingen. Nederlands is de bron; Engels is volledig (tsc controleert dat). De woordenboeken
// zijn per gebied opgedeeld en laden lazy: alleen common, shell en auth (nl) zitten in de eerste
// bundel. Een route of lazy onderdeel laadt eerst zijn gebieden (loadAreas, lazyWithAreas).
//
// t() nooit op moduleniveau aanroepen: dan is de taal of het gebied er nog niet. Exporteer een
// functie of getter in plaats van een constante met tekst.

import { type ComponentType, type LazyExoticComponent, lazy, useEffect, useReducer } from "react";
import authNl from "./areas/auth.nl";
import commonNl from "./areas/common.nl";
import shellNl from "./areas/shell.nl";
import { bcp47, currentLocale, isLocale, setCurrentLocale, storeLocale } from "./locale";
import type { Area, ErrorCatalog, Locale, MessageKey, Messages, PluralForms } from "./types";

export { collator, fmt, fold } from "./fmt";
export type { Area, ErrorCatalog, Locale, MessageKey, Translation } from "./types";

type Loader<T> = () => Promise<{ default: T }>;

// Eén chunk per (gebied, taal); de drie Nederlandse basiswoordenboeken staan hierboven statisch
const areaLoaders = import.meta.glob<Messages>(["./areas/*.ts", "!./areas/{common,shell,auth}.nl.ts"], {
  import: "default",
}) as Record<string, () => Promise<Messages>>;
// Foutcatalogi (Engelse teksten per foutcode); alleen nodig als de app Engels is
const errorLoaders = import.meta.glob<{ default: ErrorCatalog }>("./errors/*.ts") as Record<
  string,
  Loader<ErrorCatalog>
>;

const STATIC: Partial<Record<Area, Messages>> = { common: commonNl, shell: shellNl, auth: authNl };

const dicts = new Map<Area, Messages>();
const pending = new Map<Area, Promise<void>>();
let errorCatalog: ErrorCatalog = {};
let errorsLoaded: Promise<void> | null = null;

function reset(l: Locale): void {
  dicts.clear();
  pending.clear();
  errorCatalog = {};
  errorsLoaded = null;
  if (l === "nl") for (const [area, d] of Object.entries(STATIC)) dicts.set(area as Area, d);
}
reset(currentLocale());

export function locale(): Locale {
  return currentLocale();
}

const plurals = new Map<Locale, Intl.PluralRules>();
function pluralRules(): Intl.PluralRules {
  const l = currentLocale();
  let rules = plurals.get(l);
  if (!rules) {
    rules = new Intl.PluralRules(bcp47(l));
    plurals.set(l, rules);
  }
  return rules;
}

/** {naam} in een tekst vervangen; getallen in de opmaak van de taal ("1.234"). */
export function interpolate(text: string, vars?: Record<string, unknown>): string {
  if (!vars) return text;
  return text.replace(/\{(\w+)\}/g, (whole, name: string) => {
    const value = vars[name];
    if (value === undefined || value === null) return whole;
    if (typeof value === "number") return new Intl.NumberFormat(bcp47()).format(value);
    return String(value);
  });
}

function isPlural(value: unknown): value is PluralForms {
  return typeof value === "object" && value !== null && typeof (value as PluralForms).other === "string";
}

/**
 * De tekst bij een sleutel ("browser.selected"), met {naam} ingevuld. Een meervoud kiest zijn
 * vorm op vars.n. Is het gebied niet geladen (een fout in de code), dan de sleutel zelf en een
 * melding in de console die met "i18n:" begint (de e2e-tests letten daarop).
 */
export function t(key: MessageKey, vars?: Record<string, string | number>): string {
  const dot = key.indexOf(".");
  const area = key.slice(0, dot) as Area;
  const dict = dicts.get(area);
  if (!dict) {
    console.error(`i18n: gebied "${area}" is niet geladen voor "${key}"`);
    return key;
  }
  let node: unknown = dict;
  for (const part of key.slice(dot + 1).split(".")) {
    node = node && typeof node === "object" && !isPlural(node) ? (node as Messages)[part] : undefined;
  }
  if (typeof node === "string") return interpolate(node, vars);
  if (isPlural(node)) {
    const form = pluralRules().select(Number(vars?.n ?? 0)) as keyof PluralForms;
    return interpolate(node[form] ?? node.other, vars);
  }
  console.error(`i18n: onbekende sleutel "${key}"`);
  return key;
}

function loadArea(area: Area): Promise<void> {
  if (dicts.has(area)) return Promise.resolve();
  let p = pending.get(area);
  if (!p) {
    const l = currentLocale();
    const load = areaLoaders[`./areas/${area}.${l}.ts`];
    if (!load) {
      console.error(`i18n: geen woordenboek voor "${area}" (${l})`);
      return Promise.resolve();
    }
    p = load().then(
      (d) => {
        if (currentLocale() === l) dicts.set(area, d);
      },
      (e: unknown) => {
        pending.delete(area); // later opnieuw proberen (bijv. weer online)
        throw e;
      },
    );
    pending.set(area, p);
  }
  return p;
}

/** Laadt de woordenboeken van deze gebieden in de huidige taal; twee keer vragen laadt één keer. */
export function loadAreas(areas: readonly Area[]): Promise<void> {
  return Promise.all(areas.map(loadArea)).then(() => undefined);
}

/** Zijn deze gebieden er al? */
export function areasLoaded(areas: readonly Area[]): boolean {
  return areas.every((a) => dicts.has(a));
}

function loadErrors(): Promise<void> {
  errorsLoaded ??= Promise.all(
    Object.values(errorLoaders).map((load) =>
      load().then(
        (m) => m.default,
        () => ({}) as ErrorCatalog, // één ontbrekende catalogus: dan voor die codes de servertekst
      ),
    ),
  ).then((catalogs) => {
    errorCatalog = Object.assign({}, ...catalogs) as ErrorCatalog;
  });
  return errorsLoaded;
}

/** Vóór de eerste render: in het Engels de basiswoordenboeken en de foutcatalogi (parallel). */
export async function initI18n(): Promise<void> {
  const l = currentLocale();
  if (typeof document !== "undefined") document.documentElement.lang = l;
  if (l === "en") await Promise.all([loadAreas(["common", "shell", "auth"]), loadErrors()]);
}

/** Of deze gebieden geladen zijn; laadt ze zo nodig en tekent dan opnieuw. */
export function useAreas(areas: readonly Area[]): boolean {
  const ready = areasLoaded(areas);
  const [, rerender] = useReducer((n: number) => n + 1, 0);
  const key = areas.join(",");
  useEffect(() => {
    if (ready) return;
    let live = true;
    loadAreas(key.split(",") as Area[]).then(
      () => live && rerender(),
      () => live && rerender(),
    );
    return () => {
      live = false;
    };
  }, [key, ready]);
  return ready;
}

/** React.lazy dat ook de woordenboeken van zijn gebieden laadt voordat het iets toont. */
export function lazyWithAreas<P>(
  load: () => Promise<{ default: ComponentType<P> }>,
  areas: readonly Area[],
): LazyExoticComponent<ComponentType<P>> {
  return lazy(() => Promise.all([load(), loadAreas(areas)]).then(([m]) => m));
}

/** Wisselt van taal: bewaart de keuze (op dit apparaat, en met account ook bij het account) en
 * herlaadt de pagina. */
export async function setLocale(l: Locale, o?: { account?: boolean }): Promise<void> {
  storeLocale(l);
  if (o?.account) {
    try {
      const { api } = await import("../api");
      await api.setAccountLocale(l);
    } catch {
      /* niet bewaard bij het account: op dit apparaat geldt de keuze toch */
    }
  }
  window.location.reload();
}

const RELOAD_FLAG = "platform-locale-reload";

/**
 * Na het inloggen: heeft het account een andere taal dan de app nu gebruikt, dan die bewaren en
 * één keer herladen. Geeft true als de pagina herlaadt. Lukt bewaren niet (opslag uit), dan
 * blijft de huidige taal staan: anders zou de pagina blijven herladen.
 */
export function applyAccountLocale(l: Locale | null | undefined): boolean {
  if (!isLocale(l) || l === currentLocale()) return false;
  if (!storeLocale(l)) return false;
  try {
    if (sessionStorage.getItem(RELOAD_FLAG) === l) return false; // al een keer geprobeerd
    sessionStorage.setItem(RELOAD_FLAG, l);
  } catch {
    return false;
  }
  window.location.reload();
  return true;
}

/** Zo heet een mislukte fetch in Chromium, Firefox en Safari. */
const FETCH_FAILED = /^(Failed to fetch|NetworkError when attempting to fetch resource\.?|Load failed|Network request failed)$/i;

/**
 * Geen verbinding? Een ApiError met code "network" (api.ts en publicApi.ts maken die van een
 * mislukte fetch), of een kale TypeError met de tekst van een mislukte fetch. Een andere
 * TypeError is een programmeerfout, geen verbindingsprobleem.
 */
export function isNetworkError(e: unknown): boolean {
  if (e instanceof TypeError) return FETCH_FAILED.test(e.message);
  return typeof e === "object" && e !== null && (e as { code?: unknown }).code === "network";
}

/**
 * De tekst bij een fout. In het Nederlands de tekst van de server; in het Engels de vertaling
 * van de foutcode uit de catalogus (met de params ingevuld), anders toch de servertekst. Geen
 * verbinding geeft een eigen melding (zie isNetworkError). Ook een ItemResult van een bulkactie
 * ({code, message, params}) gaat hierdoor.
 */
export function errorText(e: unknown): string {
  if (isNetworkError(e)) return t("common.errors.network");
  const err = (typeof e === "object" && e !== null ? e : {}) as {
    message?: unknown;
    code?: unknown;
    params?: Record<string, unknown>;
  };
  if (currentLocale() === "en" && typeof err.code === "string") {
    const entry = errorCatalog[err.code];
    if (entry) return interpolate(entry.en, err.params);
  }
  if (typeof err.message === "string" && err.message) return err.message;
  return typeof e === "string" ? e : t("common.errors.unknown");
}

/** Alleen voor tests: een andere taal zonder te herladen (de woordenboeken beginnen opnieuw). */
export function resetI18nForTests(l: Locale): void {
  setCurrentLocale(l);
  reset(l);
}

/** Alleen voor tests: een foutcatalogus vastzetten. */
export function setErrorCatalogForTests(catalog: ErrorCatalog): void {
  errorCatalog = catalog;
  errorsLoaded = Promise.resolve();
}
