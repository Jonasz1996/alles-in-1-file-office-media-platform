// De regel van een slim album (Keuze 42) als formulier: naam of tekst, personen, soort en periode.
// Wat de web niet laat zien (plaats, land, tags, map, camera), blijft zoals het in de regel stond.
// Los van React, zodat het te testen is.

import type { MediaFilter } from "./filters";
import type { AlbumRule, AlbumRuleInfo } from "./types";

export interface RuleForm {
  q: string;
  person: readonly string[];
  type: MediaFilter | null;
  from: string | null;
  to: string | null;
  /** Nieuwe foto's die passen ook delen (share_new) */
  shareNew: boolean;
}

export const EMPTY_RULE_FORM: RuleForm = { q: "", person: [], type: null, from: null, to: null, shareNew: false };

/** Het formulier bij een regel (of leeg zonder regel). Een soort die de web niet kent, telt niet. */
export function formOf(info: AlbumRuleInfo | null | undefined): RuleForm {
  if (!info) return EMPTY_RULE_FORM;
  const r = info.rule;
  const type = r.type?.length === 1 && (r.type[0] === "image" || r.type[0] === "video") ? r.type[0] : null;
  return {
    q: r.q ?? "",
    person: r.person ?? [],
    type,
    from: r.from ?? null,
    to: r.to ?? null,
    shareNew: info.share_new,
  };
}

/** De regel uit het formulier, zonder wat leeg is; base: de regel die er stond. */
export function ruleOf(form: RuleForm, base: AlbumRule = {}): AlbumRule {
  const { q: _q, person: _p, type: _t, from: _f, to: _to, ...rest } = base;
  let { from, to } = form;
  if (from && to && from > to) [from, to] = [to, from];
  const q = form.q.trim().replace(/\s+/g, " ");
  return {
    ...rest,
    ...(q ? { q } : {}),
    ...(form.person.length ? { person: [...form.person] } : {}),
    ...(form.type ? { type: [form.type] } : {}),
    ...(from ? { from } : {}),
    ...(to ? { to } : {}),
  };
}

/** Een regel zonder naam of tekst en zonder filter zoekt niets (de API geeft dan 400). */
export function ruleEmpty(rule: AlbumRule): boolean {
  return Object.entries(rule).every(([, v]) => v === undefined || v === "" || (Array.isArray(v) && v.length === 0));
}

/** Hoeveel items uit de regel gedeeld worden. */
export function sharedCount(info: Pick<AlbumRuleInfo, "items" | "private">): number {
  return Math.max(0, info.items - info.private);
}
