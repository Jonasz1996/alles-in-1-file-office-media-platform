// De eerste pagina van een map al ophalen tijdens het opstarten, parallel met /auth/refresh en
// het chunk van Bestanden (routes.tsx roept dit aan via RouteDef.preload). id null = de eigen
// hoofdmap. Deze module zit in de eerste bundel: hij blijft klein en kent de store niet; die
// neemt de pagina over met takePreloaded().

import { api } from "../api";
import { FIRST_PAGE, type Page, pagePath } from "./api";

/** Zo lang (ms) is een vooraf opgehaalde pagina bruikbaar. */
const FRESH_MS = 15_000;

let pending: { id: string | null; at: number; page: Promise<Page | null> } | null = null;

export function preloadFolder(id: string | null): void {
  const page = (async () => {
    // Nog niet ingelogd (het opstarten): refresh() deelt het verzoek met dat van de schil
    if (!api.loggedIn && !(await api.refresh())) return null;
    try {
      return await api.request<Page>("GET", pagePath(id, { limit: FIRST_PAGE }));
    } catch {
      return null; // de store vraagt het zelf opnieuw en toont dan de fout
    }
  })();
  pending = { id, at: Date.now(), page };
}

/** De vooraf opgehaalde eerste pagina van deze map (één keer), of null. */
export function takePreloaded(id: string | null): Promise<Page | null> | null {
  const p = pending;
  if (!p || p.id !== id || Date.now() - p.at > FRESH_MS) return null;
  pending = null;
  return p.page;
}
