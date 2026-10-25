// Waar de viewer de inhoud vandaan haalt: als ingelogde gebruiker (defaultSource) of via een
// publieke link (publicSource). Alleen P4 maakt ze; de rest geeft er hooguit een door.

import { api } from "../api";
import type { PublicLink } from "../publicApi";
import type { DocumentInfo, ViewerDetail } from "./api";
import type { ViewerItem, ViewerSource } from "./types";

export interface Source extends ViewerSource {
  readonly kind: "user" | "public";
  /** Alles wat de viewer over dit bestand (of deze versie) wil weten */
  detail(item: ViewerItem): Promise<ViewerDetail>;
  /** De pagina's van een PDF of de inhoud van een kantoordocument */
  document(item: ViewerItem): Promise<DocumentInfo>;
  /** Een adres voor <a>, <img>, <video>; inline om te tonen of af te spelen */
  url(item: ViewerItem, inline: boolean): Promise<string>;
  /** Het adres werkte niet meer (verlopen): de volgende keer een nieuw */
  forget(item: ViewerItem): void;
  /** Bytes start..end (exclusief) van de inhoud; total is de hele grootte als de server die geeft */
  range(item: ViewerItem, start: number, end: number): Promise<{ bytes: Uint8Array; total: number | null }>;
}

/** Zo lang (ms) voor het verlopen maken we al een nieuw adres. */
const MARGIN = 60_000;

/** Een downloadadres hergebruiken tot vlak voor het verloopt. */
export class UrlMemo {
  private readonly urls = new Map<string, { url: string; until: number }>();

  constructor(private readonly now: () => number = Date.now) {}

  async get(key: string, make: () => Promise<{ url: string; expires_in: number }>): Promise<string> {
    const hit = this.urls.get(key);
    if (hit && hit.until > this.now()) return hit.url;
    const made = await make();
    const lifetime = Math.max(0, made.expires_in) * 1000;
    this.urls.set(key, { url: made.url, until: this.now() + lifetime - MARGIN });
    return made.url;
  }

  forget(prefix: string): void {
    for (const key of [...this.urls.keys()]) if (key.startsWith(prefix)) this.urls.delete(key);
  }
}

function versionQuery(item: ViewerItem): string {
  return item.version ? `?${new URLSearchParams({ version: item.version.id })}` : "";
}

function itemKey(item: ViewerItem): string {
  return `${item.id}|${item.etag ?? ""}|${item.version?.id ?? ""}|`;
}

/** De bytes van een Range-verzoek; een server die Range negeert, geeft alles (dan knippen). */
export async function readRange(
  url: string,
  start: number,
  end: number,
  fetchFn: typeof fetch = (...args) => fetch(...args),
): Promise<{ bytes: Uint8Array; total: number | null }> {
  const res = await fetchFn(url, { headers: { Range: `bytes=${start}-${Math.max(start, end - 1)}` } });
  if (res.status === 416) return { bytes: new Uint8Array(0), total: start };
  if (!res.ok) throw new Error(String(res.status));
  const bytes = new Uint8Array(await res.arrayBuffer());
  if (res.status === 206) {
    const total = /\/(\d+)\s*$/.exec(res.headers.get("Content-Range") ?? "")?.[1];
    return { bytes, total: total ? Number(total) : null };
  }
  return { bytes: bytes.subarray(start, end), total: bytes.length };
}

function userSource(): Source {
  const memo = new UrlMemo();
  return {
    kind: "user",
    detail: (item) => api.request<ViewerDetail>("GET", `/api/v1/files/${item.id}/preview${versionQuery(item)}`),
    document: (item) => api.request<DocumentInfo>("GET", `/api/v1/files/${item.id}/document${versionQuery(item)}`),
    async url(item, inline) {
      const url = await memo.get(itemKey(item), () =>
        api.request<{ url: string; expires_in: number }>(
          "POST",
          `/api/v1/files/${item.id}/download-link${versionQuery(item)}`,
        ),
      );
      return inline ? `${url}&inline=true` : url;
    },
    forget: (item) => memo.forget(itemKey(item)),
    async range(item, start, end) {
      return readRange(await this.url(item, false), start, end);
    },
  };
}

let shared: Source | null = null;

/** De bron voor een ingelogde gebruiker (één voor de hele app: de adressen blijven bewaard). */
export function defaultSource(): Source {
  shared ??= userSource();
  return shared;
}

/** De bron voor een publieke link: alles met de sleutel van de link. */
export function publicSource(link: PublicLink): Source {
  return {
    kind: "public",
    detail: (item) => link.preview<ViewerDetail>(item.id),
    document: (item) => link.document<DocumentInfo>(item.id),
    url: (item, inline) => link.downloadUrl(item.id, inline),
    forget: () => {},
    async range(item, start, end) {
      return readRange(await link.downloadUrl(item.id, false), start, end);
    },
  };
}

/** Een bron die P4 maakte, of de standaard. */
export function sourceOf(source: ViewerSource | undefined): Source {
  if (source && "detail" in source && "url" in source) return source as Source;
  return defaultSource();
}
