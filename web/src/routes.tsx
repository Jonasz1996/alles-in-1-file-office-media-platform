// De routetabel (frozen na P0). Elke pagina is lazy: load() wacht op het chunk én op de
// woordenboeken die de pagina nodig heeft, zodat t() meteen werkt bij de eerste render.

import type { ComponentType } from "react";
import { preloadFolder } from "./browser/preload";
import { type Area, loadAreas, type MessageKey } from "./i18n";
import { match, type NavSection, type RouteDef, type RouteProps } from "./router";

type PageModule = { default: ComponentType<RouteProps> };

// Eén belofte per chunk: / en /map/:id delen Bestanden, en de schil ziet dan bij de tweede
// route meteen een geladen pagina (geen "Laden…" en Bestanden blijft gewoon staan)
const loading = new Map<() => Promise<PageModule>, Promise<PageModule>>();

function loadOnce(chunk: () => Promise<PageModule>, areas: readonly Area[]): Promise<PageModule> {
  let p = loading.get(chunk);
  if (!p) {
    p = Promise.all([chunk(), loadAreas(areas)]).then(([m]) => m);
    // Mislukt (geen verbinding): de volgende keer opnieuw proberen
    p.catch(() => loading.delete(chunk));
    loading.set(chunk, p);
  }
  return p;
}

function route(
  pattern: string,
  chunk: () => Promise<PageModule>,
  areas: readonly Area[],
  title: MessageKey,
  section: NavSection | null,
  extra?: Pick<RouteDef, "roles" | "preload">,
): RouteDef {
  return {
    pattern,
    areas,
    title,
    section,
    ...extra,
    load: () => loadOnce(chunk, areas),
  };
}

const files = () => import("./Files");
const photos: readonly Area[] = ["photos", "browser", "sharing"];
const browse: readonly Area[] = ["browser", "upload", "sharing"];

/** Alle pagina's voor wie ingelogd is, in volgorde: de eerste die past, wint. */
export const routes: readonly RouteDef[] = [
  route("/", files, browse, "browser.title", "files", { preload: () => preloadFolder(null) }),
  route("/map/:id", files, browse, "browser.title", "files", { preload: (p) => preloadFolder(p.id) }),
  route("/zoeken", () => import("./Search"), ["browser", "sharing"], "browser.searchTitle", null),
  route("/gedeeld", () => import("./SharedWithMe"), ["sharing", "browser"], "sharing.sharedWithMe", "shared"),
  route("/gedeeld/door-mij", () => import("./shares/MyShares"), ["sharing", "browser"], "sharing.myShares", "shared"),
  route("/prullenbak", () => import("./Trash"), ["trash", "browser"], "trash.title", "trash", {
    roles: ["admin", "user"],
  }),
  route("/fotos", () => import("./photos/PhotosPage"), photos, "photos.title", "photos", { roles: ["admin", "user"] }),
  route("/albums", () => import("./photos/AlbumsPage"), photos, "photos.albums", "photos", { roles: ["admin", "user"] }),
  route("/album/:id", () => import("./photos/AlbumPage"), photos, "photos.album", "photos"),
  route("/beheer/:tab?", () => import("./Beheer"), ["admin"], "admin.title", "admin", { roles: ["admin"] }),
  route("/account", () => import("./Account"), ["account"], "account.title", "account"),
  route("*", () => import("./shellPages"), [], "shell.notFound.title", null),
];

/** Pagina's buiten de login (een publieke link werkt zonder account, ook als je ingelogd bent). */
export const publicRoutes: readonly RouteDef[] = [
  route("/s/:token", () => import("./Public"), ["public", "upload"], "public.title", null),
];

/** Het adres valt onder de publieke pagina's: dan niets van de login (geen refresh, geen live stroom). */
export function isPublicPath(path: string): boolean {
  return path === "/s" || path.startsWith("/s/");
}

export interface Resolved {
  def: RouteDef;
  params: Record<string, string>;
}

/** De route voor een pad; met "*" als laatste vindt hij er altijd een. */
export function resolve(path: string, table: readonly RouteDef[] = routes): Resolved {
  for (const def of table) {
    const params = match(def.pattern, path);
    if (params) return { def, params };
  }
  const last = table[table.length - 1];
  return { def: last, params: {} };
}

/** Of deze rol er mag komen (zonder roles: iedereen die ingelogd is). */
export function allowed(def: RouteDef, role: "admin" | "user" | "guest"): boolean {
  return !def.roles || def.roles.includes(role);
}
