// De ingang van media voor de schil (fase 5, frozen na M0): de status van media in het geheugen,
// de adressen van de pagina's, de plek van de muziekbalk en de regels van de haken. Klein, want
// App.tsx, de bestandsbrowser en de viewer importeren dit: alles staat in de eerste lading.
// De aanroepen staan in ./api, de typen in ./types; de schil importeert die nooit (behalve
// typen), anders komen ze in de eerste lading (Keuze 33, budget.mjs).
//
// Of media aan staat, zegt de server met GET /api/v1/media/libraries: 200 is aan (met de
// bibliotheken die je ziet), 503 media_off is uit (MediaConfig.enabled false, of een stub van
// M0). Een oudere server (404) of een andere fout: ook uit. Zolang media uit is, zijn de haken er
// niet (geen menu-items, geen actie, de viewer van fase 3) en is de app precies 0.6.0.

import { useEffect, useSyncExternalStore } from "react";
import { api } from "../api";
import type { Library, LibraryEvent } from "./types";

export type * from "./types";

/** Het adres waarmee de status gevraagd wordt (M2: de lijst van bibliotheken). */
export const LIBRARIES_PATH = "/api/v1/media/libraries";

// --- de status in het geheugen ---

/** Zo lang (ms) geldt een antwoord (ook "uit"); daarna vragen we het opnieuw. */
export const STATUS_TTL = 60_000;
/** Zo lang (ms) na een fout zonder antwoord (geen verbinding, 502): dan opnieuw proberen. */
export const STATUS_RETRY = 15_000;

/** Een fout die een echt antwoord is: media is uit (503 media_off), de route bestaat niet
 * (404: een oudere server) of is niet voor jou (403). Dan TTL, niet RETRY. */
export function isDefinitiveOff(e: unknown): boolean {
  const err = e as { status?: unknown; code?: unknown } | null;
  if (!err || typeof err !== "object") return false;
  return err.code === "media_off" || err.status === 404 || err.status === 403;
}

/**
 * Een status van de server in het geheugen, één keer per STATUS_TTL gevraagd. peek() leest
 * zonder iets te doen (voor useSyncExternalStore); get() vraagt hem ook opnieuw als hij oud is
 * (voor de when() van een actie). Een fout geeft null: de haken zijn er dan niet. canFetch false
 * (niet ingelogd): niets vragen.
 */
export class StatusStore<T> {
  private value: T | null = null;
  private at = Number.NEGATIVE_INFINITY;
  private ttl = 0;
  private inflight: Promise<T | null> | null = null;
  private readonly listeners = new Set<() => void>();

  constructor(
    private readonly fetchStatus: () => Promise<T>,
    private readonly canFetch: () => boolean = () => true,
    private readonly now: () => number = Date.now,
  ) {}

  /** De laatste status (of null), zonder te vragen. */
  peek = (): T | null => this.value;

  /** De laatste status; is die oud, dan op de achtergrond opnieuw vragen. */
  get(): T | null {
    if (this.stale()) void this.load();
    return this.value;
  }

  stale(): boolean {
    return this.now() - this.at >= this.ttl;
  }

  /** Vraagt de status (twee keer tegelijk vragen is één verzoek); geeft hem, of null. */
  load(): Promise<T | null> {
    if (this.inflight) return this.inflight;
    if (!this.canFetch()) return Promise.resolve(this.value);
    const p = this.fetchStatus().then(
      (s) => {
        this.settle(s, STATUS_TTL);
        return this.value;
      },
      (e: unknown) => {
        this.settle(null, isDefinitiveOff(e) ? STATUS_TTL : STATUS_RETRY);
        return null;
      },
    );
    this.inflight = p;
    void p.finally(() => {
      if (this.inflight === p) this.inflight = null;
    });
    return p;
  }

  /** Een status van buitenaf (tests, of een pakket dat net iets veranderde). */
  set(s: T | null): void {
    this.settle(s, s === null ? STATUS_RETRY : STATUS_TTL);
  }

  /** Oud maken: de volgende get() of useMediaStatus() vraagt opnieuw (na uitloggen). */
  invalidate(): void {
    this.at = Number.NEGATIVE_INFINITY;
  }

  subscribe = (cb: () => void): (() => void) => {
    this.listeners.add(cb);
    return () => {
      this.listeners.delete(cb);
    };
  };

  private settle(s: T | null, ttl: number): void {
    this.at = this.now();
    this.ttl = ttl;
    // Hetzelfde antwoord: hetzelfde object (en niemand hoeft opnieuw te tekenen)
    if (JSON.stringify(s) === JSON.stringify(this.value)) return;
    this.value = s;
    for (const l of [...this.listeners]) l();
  }
}

/** Media staat aan, met de bibliotheken die je ziet. */
export interface MediaOn {
  libraries: Library[];
}

function fetchMedia(): Promise<MediaOn> {
  return api.request<Library[]>("GET", LIBRARIES_PATH).then((libraries) => ({ libraries }));
}

/** De status van media voor de hele app (null: uit, onbekend of een fout). */
export const mediaStatus = new StatusStore<MediaOn>(fetchMedia, () => api.loggedIn);

/**
 * Een gebeurtenis media.library (§3.4) in de status: een bibliotheek die we kennen, krijgt de
 * nieuwe telling en scanstaat (zonder verzoek); een onbekende (net gemaakt, of net gedeeld)
 * betekent opnieuw vragen. Geeft de nieuwe status, of "reload".
 */
export function applyLibraryEvent(status: MediaOn | null, data: Partial<LibraryEvent>): MediaOn | "reload" | null {
  if (!status || typeof data.library_id !== "string") return status;
  const i = status.libraries.findIndex((l) => l.id === data.library_id);
  if (i < 0) return "reload";
  const old = status.libraries[i];
  const next: Library = {
    ...old,
    scan_state: data.scan_state ?? old.scan_state,
    item_count: typeof data.item_count === "number" ? data.item_count : old.item_count,
  };
  const libraries = [...status.libraries];
  libraries[i] = next;
  return { libraries };
}

/** Voor de live stroom (App.tsx): houdt de status bij met media.library. */
export function mediaEvents(event: { type: string; data: Record<string, unknown> }): void {
  if (event.type !== "media.library") return;
  const next = applyLibraryEvent(mediaStatus.peek(), event.data as Partial<LibraryEvent>);
  if (next === "reload") void mediaStatus.load();
  else if (next !== mediaStatus.peek()) mediaStatus.set(next);
}

/**
 * Een status uit een StatusStore in een component; vraagt hem bij het tonen als hij oud is.
 * active false: niets vragen en null geven.
 */
export function useStatus<T>(store: StatusStore<T>, active = true): T | null {
  const status = useSyncExternalStore(store.subscribe, store.peek, () => null);
  useEffect(() => {
    if (active && store.stale()) void store.load();
  }, [store, active]);
  return active ? status : null;
}

/**
 * De status van media (null: uit of onbekend). Vraagt hem bij het tonen als hij oud is.
 * active false: niets vragen en null geven (bijv. de viewer via een publieke link).
 */
export function useMediaStatus(active = true): MediaOn | null {
  return useStatus(mediaStatus, active);
}

/** Na uitloggen: de bibliotheken en de muziekbalk waren van de vorige gebruiker. De volgende
 * die inlogt, vraagt de status opnieuw. */
export function resetMedia(): void {
  mediaStatus.set(null);
  mediaStatus.invalidate();
  musicBar.hide();
}

// --- de regels van de haken ---

/** Media en Muziek in het menu: als media aan staat. Een gast heeft geen eigen mappen: alleen
 * als hij een gedeelde bibliotheek ziet. */
export function showMediaNav(status: MediaOn | null, guest: boolean): boolean {
  if (!status) return false;
  return !guest || status.libraries.length > 0;
}

/** De bibliotheek op deze map, als die er is. */
export function libraryOn(status: MediaOn | null, folderId: string): Library | null {
  return status?.libraries.find((l) => l.root_id === folderId) ?? null;
}

/**
 * Laadt de viewer de speler van fase 5 (M6) in plaats van de <video> van fase 3? Alleen voor
 * video, als ingelogde gebruiker, voor de huidige versie van het bestand (play kent geen
 * eerdere versies) en als media aan staat. Een publieke link houdt de <video> van fase 3: daar
 * zegt de server niet of media aan staat (zie docs/fase-5/m0-web.md).
 */
export function wantsPlayer(
  kind: "video" | "audio",
  source: "user" | "public",
  hasVersion: boolean,
  status: MediaOn | null,
): boolean {
  return kind === "video" && source === "user" && !hasVersion && status !== null;
}

// --- de adressen van de pagina's (routes.tsx) ---

const seg = encodeURIComponent;

export const MEDIA_HOME = "/media";
export const MUSIC_HOME = "/muziek";

export function libraryPath(id: string): string {
  return `/media/bibliotheek/${seg(id)}`;
}

export function itemPath(nodeId: string): string {
  return `/media/item/${seg(nodeId)}`;
}

export function showPath(showId: string): string {
  return `/media/serie/${seg(showId)}`;
}

/** /kijken/:node; t: de start in seconden (?t=), bijv. "Verder kijken". */
export function watchPath(nodeId: string, t?: number): string {
  const base = `/kijken/${seg(nodeId)}`;
  return t && t > 0 ? `${base}?t=${Math.floor(t)}` : base;
}

export function albumPath(id: string): string {
  return `/muziek/album/${seg(id)}`;
}

export function artistPath(key: string): string {
  return `/muziek/artiest/${seg(key)}`;
}

export function playlistPath(id: string): string {
  return `/muziek/lijst/${seg(id)}`;
}

/** Een adres onder /api/v1/stream/{sid}/ (de sid zit in het pad; nooit in een log of opslag). */
export function streamUrl(sid: string, rest: string): string {
  return `/api/v1/stream/${seg(sid)}/${rest.replace(/^\/+/, "")}`;
}

// --- de muziekbalk (Keuze 37) ---

/**
 * Of de muziekbalk er staat. App.tsx laadt hem (music/MusicBar.tsx, M9) pas als er muziek
 * speelt, en houdt hem buiten de pagina: hij blijft staan bij navigeren (één <audio> in de
 * schil). M9 zet hem aan met show() en weg met hide().
 */
class MusicBarState {
  private shown = false;
  private readonly listeners = new Set<() => void>();

  visible = (): boolean => this.shown;

  show(): void {
    this.change(true);
  }

  hide(): void {
    this.change(false);
  }

  subscribe = (cb: () => void): (() => void) => {
    this.listeners.add(cb);
    return () => {
      this.listeners.delete(cb);
    };
  };

  private change(shown: boolean): void {
    if (shown === this.shown) return;
    this.shown = shown;
    for (const l of [...this.listeners]) l();
  }
}

export const musicBar = new MusicBarState();
