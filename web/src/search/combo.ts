// De suggesties onder het zoekveld bovenaan (fase 6, Z4; Keuze 46 en 81): wat zonder React te
// testen is. De opties in hun volgorde (namen, personen, plaatsen, tags, albums, en als laatste
// "Zoek '…' op wat er op je foto's staat"), hun id voor aria-activedescendant, waar een keuze
// heen gaat, en wat een pijl doet. Hoort bij het lazy chunk van Suggest.tsx.

import { SUGGEST_LIST_ID } from "./appBar";
import { queryHref } from "./query";
import type { Suggestions } from "./types";

/** De groepen in de lijst, in deze volgorde; image is de laatste regel (geen groep). */
export const SUGGEST_GROUPS = ["names", "people", "places", "tags", "albums"] as const;
export type SuggestGroup = (typeof SUGGEST_GROUPS)[number] | "image";

/** Waar een keuze heen gaat: een adres, of een bestand (de map waar het in staat haalt
 * Suggest.tsx eerst op, met GET /files/{id}). */
export type Target = { href: string } | { file: string; name: string };

export interface Option {
  /** Het id van het element (role=option), voor aria-activedescendant */
  id: string;
  group: SuggestGroup;
  label: string;
  target: Target;
}

/** Het id van optie i: uniek op de pagina, want de lijst is er maar één keer. */
export function optionId(i: number): string {
  return `${SUGGEST_LIST_ID}-${i}`;
}

const seg = encodeURIComponent;

/** De opties van een antwoord van GET /search/suggest, plat, in de volgorde van het scherm. Zonder
 * antwoord (een fout, 429) alleen de laatste regel. withImage: zoeken op beeld mag (geen gast,
 * Keuze 81). */
export function optionsOf(s: Suggestions | null, q: string, withImage: boolean): Option[] {
  const text = q.trim();
  const out: Omit<Option, "id">[] = [];
  if (s) {
    for (const n of s.names) {
      const target: Target =
        n.kind === "file" ? { file: n.id, name: n.name } : { href: n.kind === "album" ? `/album/${seg(n.id)}` : `/map/${seg(n.id)}` };
      out.push({ group: "names", label: n.name, target });
    }
    // Een persoon zet het filter person (Keuze 38), zoals een plaats en een tag hun filter zetten
    for (const p of s.people) out.push({ group: "people", label: p.name, target: { href: queryHref({ person: [p.id] }) } });
    for (const p of s.places) {
      const label = p.admin1 ? `${p.name}, ${p.admin1}` : p.name;
      out.push({ group: "places", label, target: { href: queryHref({ place: p.id }) } });
    }
    for (const tag of s.tags) out.push({ group: "tags", label: tag.name, target: { href: queryHref({ tag: [tag.id] }) } });
    for (const a of s.albums) out.push({ group: "albums", label: a.name, target: { href: `/album/${seg(a.id)}` } });
  }
  if (withImage && text) out.push({ group: "image", label: text, target: { href: queryHref({ q: text, mode: "image" }) } });
  return out.map((o, i) => ({ ...o, id: optionId(i) }));
}

/** Een bestand dat niet meer te vinden is (weg, of de toegang is ingetrokken): zoek op zijn naam. */
export function fallbackHref(name: string): string {
  return queryHref({ q: name });
}

/** De nieuwe actieve optie na een pijl, of null voor elke andere toets. -1 is het veld zelf (geen
 * optie actief). Omlaag stopt bij de laatste, omhoog vanaf de eerste gaat terug naar het veld. */
export function move(active: number, key: string, n: number): number | null {
  if (n === 0) return key === "ArrowDown" || key === "ArrowUp" ? -1 : null;
  if (key === "ArrowDown") return Math.min(active + 1, n - 1);
  if (key === "ArrowUp") return Math.max(active - 1, -1);
  return null;
}

/** De opties per groep, voor het scherm: [groep, [index, optie]…], zonder lege groepen. */
export function grouped(options: readonly Option[]): [SuggestGroup, [number, Option][]][] {
  const out: [SuggestGroup, [number, Option][]][] = [];
  options.forEach((o, i) => {
    const last = out[out.length - 1];
    if (last && last[0] === o.group) last[1].push([i, o]);
    else out.push([o.group, [[i, o]]]);
  });
  return out;
}

/** De lijst: open of dicht, en de actieve optie (-1: geen, de focus "staat" in het veld). */
export interface ListState {
  open: boolean;
  active: number;
}

/** Wat een toets in het veld met de lijst doet. show: de lijst (open) met deze actieve optie;
 * choose: deze optie kiezen; close: de lijst dicht (prevent: de toets houdt hier op). null: de
 * toets is niet van de lijst (het veld, het formulier of AppBarSearch doet ermee wat het deed). */
export type KeyResult =
  | { do: "show"; state: ListState }
  | { do: "choose"; index: number }
  | { do: "close"; prevent: boolean }
  | null;

/** De toets zoals de browser hem geeft (een KeyboardEvent past). */
export interface KeyLike {
  key: string;
  altKey?: boolean;
  ctrlKey?: boolean;
  metaKey?: boolean;
  isComposing?: boolean;
}

/** De toetsen van de combobox (Keuze 46): ↓ en ↑ door de opties (na Esc weer open, op de eerste
 * of de laatste), Enter kiest de actieve optie, Esc sluit een open lijst, Tab sluit hem en gaat
 * verder. Met Ctrl, Alt of Cmd, of tijdens het samenstellen van een teken: niets. */
export function onKey(s: ListState, n: number, e: KeyLike): KeyResult {
  if (e.isComposing || e.altKey || e.ctrlKey || e.metaKey) return null;
  switch (e.key) {
    case "ArrowDown":
    case "ArrowUp": {
      if (n === 0) return null;
      if (!s.open) return { do: "show", state: { open: true, active: e.key === "ArrowDown" ? 0 : n - 1 } };
      return { do: "show", state: { open: true, active: move(s.active, e.key, n) ?? s.active } };
    }
    case "Enter":
      return s.open && s.active >= 0 && s.active < n ? { do: "choose", index: s.active } : null;
    case "Escape":
      return s.open && n > 0 ? { do: "close", prevent: true } : null;
    case "Tab":
      return s.open ? { do: "close", prevent: false } : null;
    default:
      return null;
  }
}

/**
 * De opties als ze nog bij de tekst in het veld horen (q: de vraag waar ze het antwoord op zijn);
 * anders geen. Het veld kan leeg worden zonder input-gebeurtenis (Esc en Enter in AppBarSearch):
 * dan opent ↓ geen lijst van een vraag die er niet meer staat (review golf 2, web A).
 */
export function optionsFor<T>(value: string, q: string, options: readonly T[]): readonly T[] {
  return q !== "" && value.trim() === q ? options : [];
}

/** Wat de aria-attributen van het veld zijn: open of dicht, en welke optie actief is. */
export interface ComboState {
  open: boolean;
  active: string | null;
}

/** aria-expanded en aria-activedescendant van het veld bij deze lijst. */
export function fieldState(s: ListState, options: readonly Option[]): ComboState {
  const open = s.open && options.length > 0;
  return { open, active: open && s.active >= 0 ? (options[s.active]?.id ?? null) : null };
}
