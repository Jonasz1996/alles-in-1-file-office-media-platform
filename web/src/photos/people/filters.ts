// De filters op /fotos (Keuze 43): personen (allemaal erop: EN), foto of video, en een periode
// op opnamedatum. Ze gaan naar GET /photos en /photos/months (dezelfde namen als /search); de
// server verbergt de querystring van /api/v1/photos in de log (QUERY_PATHS in app/main.py), en ze
// komen nooit in de adresbalk. Los van React, zodat het te testen is.
//
// /photos/ids neemt geen filters: daar zijn from en to cursors, die met de periode botsen. Een
// reeks selecteren (Shift) over een gefilterde tijdlijn bladert daarom door /photos met de filters.

import { api } from "../../api";
import { wireQuery } from "../../search/wire";
import { type IdRange, type MonthCount, PAGE, type PhotoPage, photosApi } from "../api";
import { cursorOf, MAX_RANGE, type Placed } from "../range";

export type MediaFilter = "image" | "video";

export interface PhotoFilters {
  /** Ids van eigen personen; een foto moet ze allemaal hebben */
  person: readonly string[];
  type: MediaFilter | null;
  /** JJJJ-MM-DD, op opnamedatum, beide dagen erbij */
  from: string | null;
  to: string | null;
}

export const NO_FILTERS: PhotoFilters = { person: [], type: null, from: null, to: null };

const DAY = /^\d{4}-\d{2}-\d{2}$/;

/** Staat er een filter aan? */
export function hasFilters(f: PhotoFilters): boolean {
  return f.person.length > 0 || f.type !== null || f.from !== null || f.to !== null;
}

/** Hoeveel filters er aan staan (de periode telt als één). */
export function filterCount(f: PhotoFilters): number {
  return f.person.length + (f.type ? 1 : 0) + (f.from || f.to ? 1 : 0);
}

/** Een geldige dag (JJJJ-MM-DD) of null; wat de datumkiezer half invulde, telt niet. */
export function dayOrNull(v: string): string | null {
  return DAY.test(v) ? v : null;
}

/** De querystring voor /photos en /photos/months; de periode in de goede volgorde. */
export function filterQuery(f: PhotoFilters, extra: Record<string, string | number | null> = {}): string {
  let { from, to } = f;
  if (from && to && from > to) [from, to] = [to, from];
  return wireQuery({ ...extra, person: f.person, type: f.type, from, to });
}

/** Zelfde filters? (dan hoeft de tijdlijn niet opnieuw) */
export function sameFilters(a: PhotoFilters, b: PhotoFilters): boolean {
  return (
    a.type === b.type &&
    a.from === b.from &&
    a.to === b.to &&
    a.person.length === b.person.length &&
    a.person.every((p, i) => p === b.person[i])
  );
}

/** Een persoon erbij of eraf. */
export function togglePerson(f: PhotoFilters, id: string): PhotoFilters {
  const person = f.person.includes(id) ? f.person.filter((p) => p !== id) : [...f.person, id];
  return { ...f, person };
}

/** De tijdlijn met filters; zonder filter precies photosApi. */
export const filteredApi = {
  months(f: PhotoFilters): Promise<{ months: MonthCount[] }> {
    if (!hasFilters(f)) return photosApi.months();
    return api.request<{ months: MonthCount[] }>("GET", `/api/v1/photos/months${filterQuery(f)}`);
  },

  page(f: PhotoFilters, month: string | null, after: string | null, limit = PAGE): Promise<PhotoPage> {
    if (!hasFilters(f)) return photosApi.page(month, after, limit);
    return api.request<PhotoPage>("GET", `/api/v1/photos${filterQuery(f, { month, after, limit })}`);
  },

  /** De ids van a tot en met b (a eerst op de tijdlijn), hooguit MAX_RANGE. */
  async ids(f: PhotoFilters, a: Placed, b: Placed): Promise<IdRange> {
    if (!hasFilters(f)) return photosApi.ids(a, b);
    return pagedIds((after) => filteredApi.page(f, null, after), a, b);
  },
};

/** Bladert vanaf a tot en met b; a en b in de volgorde van de tijdlijn (nieuwste eerst). */
export async function pagedIds(
  page: (after: string) => Promise<PhotoPage>,
  a: Placed,
  b: Placed,
): Promise<IdRange> {
  const ids = [a.id];
  if (a.id === b.id) return { ids, truncated: false };
  let after: string | null = cursorOf(a);
  while (after !== null) {
    const got = await page(after);
    for (const it of got.items) {
      if (ids.length >= MAX_RANGE) return { ids, truncated: true };
      ids.push(it.id);
      if (it.id === b.id) return { ids, truncated: false };
    }
    after = got.next;
  }
  return { ids, truncated: false };
}
