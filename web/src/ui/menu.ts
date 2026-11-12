// De pure logica achter de menu's (Menu.tsx): het item, bewegen, zoeken op de eerste letters en
// de plek op het scherm. Zonder DOM, zodat het te testen is.

import { fold } from "../i18n/fmt";
import type { IconName } from "./Icon";
import type { Combo } from "./keys";

export interface MenuItem {
  id: string;
  label: string;
  icon?: IconName;
  shortcut?: Combo;
  disabled?: boolean;
  /** Waarom het niet kan (bij disabled): als tooltip en voor schermlezers */
  reason?: string;
  danger?: boolean;
  /** Een keuze (vinkje): role menuitemcheckbox, of met radio menuitemradio */
  checked?: boolean;
  /** Eén van een groep keuzes die elkaar uitsluiten (thema, taal): role menuitemradio. Alleen
   * samen met checked. */
  radio?: boolean;
  separatorBefore?: boolean;
  /** Een submenu */
  items?: readonly MenuItem[];
  onSelect?: () => void;
}

/** De rol van een item volgens WAI-ARIA. */
export function itemRole(item: Pick<MenuItem, "checked" | "radio">): "menuitem" | "menuitemcheckbox" | "menuitemradio" {
  if (item.checked === undefined) return "menuitem";
  return item.radio ? "menuitemradio" : "menuitemcheckbox";
}

/** Het volgende item omhoog of omlaag, rondom. Ook uitgeschakelde items krijgen de focus (dan
 * leest een schermlezer waarom het niet kan). */
export function nextIndex(count: number, cur: number, dir: 1 | -1): number {
  if (count <= 0) return -1;
  if (cur < 0) return dir === 1 ? 0 : count - 1;
  return (cur + dir + count) % count;
}

/**
 * Zoeken op de eerste letters (type-ahead): het eerste item na cur waarvan het label met buffer
 * begint, rondom. Is buffer steeds dezelfde letter ("ddd"), dan door de items met die letter.
 * -1 als niets past.
 */
export function typeahead(items: readonly Pick<MenuItem, "label">[], cur: number, buffer: string): number {
  const want = fold(buffer);
  if (!want || items.length === 0) return -1;
  const same = [...want].every((c) => c === want[0]);
  const prefix = same ? want[0] : want;
  // Bij één letter (of steeds dezelfde) begint het zoeken na het huidige item; bij een woord
  // telt het huidige item ook mee (je typt verder aan dezelfde naam)
  const startAt = same ? cur + 1 : Math.max(cur, 0);
  for (let k = 0; k < items.length; k++) {
    const i = (startAt + k + items.length) % items.length;
    if (fold(items[i].label).startsWith(prefix)) return i;
  }
  return -1;
}

/**
 * De getypte tekst voor type-ahead na deze letter: erachter als de vorige letter minder dan ms
 * geleden kwam, anders opnieuw. Een spatie telt alleen mee als er al getypt wordt ("Dag 1",
 * "Map 2"); anders null: dan is hij voor de knop zelf (openen).
 */
export function typedText(prev: { text: string; at: number }, key: string, now: number, ms: number): string | null {
  const going = prev.text !== "" && now - prev.at <= ms;
  if (key === " " && !going) return null;
  return going ? prev.text + key : key;
}

export interface Box {
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface Size {
  width: number;
  height: number;
}

const MARGIN = 8;

function clamp(v: number, min: number, max: number): number {
  return Math.max(min, Math.min(v, Math.max(min, max)));
}

/**
 * Waar een menu komt. Bij een punt (rechtsklik): daar, en gespiegeld naar links/boven als het
 * anders buiten beeld valt. Bij een knop: eronder, links uitgelijnd; past het niet, dan erboven
 * of rechts uitgelijnd. Altijd binnen het venster (marge 8 px).
 */
export function placeMenu(anchor: Box | { x: number; y: number }, size: Size, viewport: Size): { x: number; y: number } {
  const isBox = "width" in anchor;
  let x: number;
  let y: number;
  if (isBox) {
    x = anchor.x;
    y = anchor.y + anchor.height;
    if (x + size.width > viewport.width - MARGIN) x = anchor.x + anchor.width - size.width;
    if (y + size.height > viewport.height - MARGIN && anchor.y - size.height >= MARGIN) y = anchor.y - size.height;
  } else {
    x = anchor.x;
    y = anchor.y;
    if (x + size.width > viewport.width - MARGIN) x = anchor.x - size.width;
    if (y + size.height > viewport.height - MARGIN) y = anchor.y - size.height;
  }
  return {
    x: clamp(x, MARGIN, viewport.width - size.width - MARGIN),
    y: clamp(y, MARGIN, viewport.height - size.height - MARGIN),
  };
}

/** Een submenu naast zijn item: rechts, of links als het rechts niet past. */
export function placeSubmenu(item: Box, size: Size, viewport: Size): { x: number; y: number } {
  let x = item.x + item.width;
  if (x + size.width > viewport.width - MARGIN) x = item.x - size.width;
  return {
    x: clamp(x, MARGIN, viewport.width - size.width - MARGIN),
    y: clamp(item.y, MARGIN, viewport.height - size.height - MARGIN),
  };
}
