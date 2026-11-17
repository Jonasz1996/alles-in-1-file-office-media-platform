// De rij van wat nu speelt (fase 5, M9; Keuze 37): in de client, in sessionStorage, niet op de
// server. Herladen houdt de rij (in dit tabblad); een ander tabblad of een nieuwe login niet.
//
// - Geen sid en geen ondertekend adres (een hoes) in de opslag: alleen node-id's, titels en de
//   plek. De hoes staat alleen in het geheugen.
// - De opslag hoort bij één gebruiker (owner): een rij van een ander wordt niet teruggezet.
// - Wat uit de opslag komt, is niet te vertrouwen: parseQueue houdt alleen geldige regels over.
// - Hooguit QUEUE_MAX nummers (zoveel als een afspeellijst).

import { PLAYLIST_MAX } from "../media/types";

/** Een nummer in de rij. */
export interface QueueItem {
  /** Uniek in deze rij (hetzelfde nummer kan twee keer in de rij staan): de speler volgt
   * hiermee welk nummer in de buffer staat, ook als de rij intussen verandert */
  uid: string;
  node_id: string;
  title: string;
  artist: string | null;
  album: string | null;
  album_id: string | null;
  duration_ms: number | null;
  /** Alleen in het geheugen (een ondertekend adres van de server) */
  cover?: string | null;
}

export interface QueueState {
  items: QueueItem[];
  /** De plek van het nummer dat speelt (of klaarstaat); -1 bij een lege rij */
  index: number;
  /** Waar in dat nummer (s), voor na herladen */
  position: number;
}

export const QUEUE_MAX = PLAYLIST_MAX;
export const QUEUE_KEY = "music.queue.v1";

/** Een nummer om in de rij te zetten (de uid komt erbij). */
export type QueueInput = Omit<QueueItem, "uid">;

export const EMPTY: QueueState = { items: [], index: -1, position: 0 };

let counter = 0;

/** Een nieuwe uid (alleen uniek in dit tabblad; geen geheim). */
export function newUid(): string {
  counter += 1;
  return `${Date.now().toString(36)}-${counter.toString(36)}`;
}

function withUid(item: QueueInput): QueueItem {
  return { ...item, uid: newUid() };
}

const MAX_TEXT = 500;

function text(v: unknown): string | null {
  return typeof v === "string" ? v.slice(0, MAX_TEXT) : null;
}

/** Eén regel uit de opslag, of null als hij niet klopt. */
function parseItem(raw: unknown): QueueItem | null {
  if (!raw || typeof raw !== "object") return null;
  const r = raw as Record<string, unknown>;
  const node = text(r.node_id);
  const title = text(r.title);
  if (!node || title === null) return null;
  const ms = r.duration_ms;
  const uid = typeof r.uid === "string" && /^[a-z0-9-]{1,40}$/.test(r.uid) ? r.uid : newUid();
  return {
    uid,
    node_id: node,
    title,
    artist: text(r.artist),
    album: text(r.album),
    album_id: text(r.album_id),
    duration_ms: typeof ms === "number" && Number.isFinite(ms) && ms >= 0 ? ms : null,
  };
}

/** De rij uit de opslag (van deze gebruiker), of EMPTY. */
export function parseQueue(raw: string | null, owner: string | null): QueueState {
  if (!raw || !owner) return EMPTY;
  try {
    const data = JSON.parse(raw) as { owner?: unknown; items?: unknown; index?: unknown; position?: unknown };
    if (data.owner !== owner || !Array.isArray(data.items)) return EMPTY;
    const seen = new Set<string>();
    const items: QueueItem[] = [];
    for (const raw of data.items.slice(0, QUEUE_MAX)) {
      const item = parseItem(raw);
      if (!item) continue;
      if (seen.has(item.uid)) item.uid = newUid();
      seen.add(item.uid);
      items.push(item);
    }
    if (!items.length) return EMPTY;
    const i = typeof data.index === "number" && Number.isInteger(data.index) ? data.index : 0;
    const p = typeof data.position === "number" && Number.isFinite(data.position) ? data.position : 0;
    return { items, index: Math.max(0, Math.min(items.length - 1, i)), position: Math.max(0, p) };
  } catch {
    return EMPTY;
  }
}

/** Wat in de opslag gaat: zonder hoes. */
export function serializeQueue(state: QueueState, owner: string): string {
  const items = state.items.map(({ uid, node_id, title, artist, album, album_id, duration_ms }) => ({
    uid,
    node_id,
    title,
    artist,
    album,
    album_id,
    duration_ms,
  }));
  return JSON.stringify({ owner, items, index: state.index, position: Math.round(state.position * 10) / 10 });
}

// --- de bewerkingen (puur) ---

/** Een nieuwe rij, met het nummer op plek start. */
export function replaceQueue(items: readonly QueueInput[], start = 0): QueueState {
  const list = items.slice(0, QUEUE_MAX).map(withUid);
  if (!list.length) return EMPTY;
  return { items: list, index: Math.max(0, Math.min(list.length - 1, start)), position: 0 };
}

/** Nummers achteraan erbij (tot QUEUE_MAX). Geeft ook hoeveel er bij kwamen. */
export function appendItems(state: QueueState, items: readonly QueueInput[]): { state: QueueState; added: number } {
  const room = Math.max(0, QUEUE_MAX - state.items.length);
  const extra = items.slice(0, room).map(withUid);
  if (!extra.length) return { state, added: 0 };
  const all = [...state.items, ...extra];
  return { state: { ...state, items: all, index: state.index < 0 ? 0 : state.index }, added: extra.length };
}

/** Een nummer uit de rij. Was het het huidige, dan wordt het volgende het huidige. */
export function removeAt(state: QueueState, i: number): QueueState {
  if (i < 0 || i >= state.items.length) return state;
  const items = state.items.filter((_, k) => k !== i);
  if (!items.length) return EMPTY;
  let index = state.index;
  let position = state.position;
  if (i < index) index -= 1;
  else if (i === index) {
    index = Math.min(index, items.length - 1);
    position = 0;
  }
  return { items, index, position };
}

/** Een nummer naar een andere plek; het huidige blijft het huidige. */
export function moveItem(state: QueueState, from: number, to: number): QueueState {
  const n = state.items.length;
  if (from < 0 || from >= n || to < 0 || to >= n || from === to) return state;
  const items = [...state.items];
  const [item] = items.splice(from, 1);
  items.splice(to, 0, item);
  let index = state.index;
  if (index === from) index = to;
  else if (from < index && to >= index) index -= 1;
  else if (from > index && to <= index) index += 1;
  return { ...state, items, index };
}

// --- de rij in het geheugen en in sessionStorage ---

type StorageLike = Pick<Storage, "getItem" | "setItem" | "removeItem">;

function session(): StorageLike | null {
  try {
    return window.sessionStorage;
  } catch {
    return null;
  }
}

/** De rij voor de app: subscribe/snapshot voor useSyncExternalStore. */
export class QueueStore {
  private state: QueueState = EMPTY;
  private owner: string | null = null;
  private readonly listeners = new Set<() => void>();

  constructor(private readonly storage: () => StorageLike | null = session) {}

  snapshot = (): QueueState => this.state;

  subscribe = (cb: () => void): (() => void) => {
    this.listeners.add(cb);
    return () => {
      this.listeners.delete(cb);
    };
  };

  /** Voor wie de rij is. Een andere gebruiker dan de vorige: de rij uit de opslag van hem (of leeg). */
  bind(owner: string | null): void {
    if (owner === this.owner) return;
    this.owner = owner;
    let raw: string | null = null;
    try {
      raw = this.storage()?.getItem(QUEUE_KEY) ?? null;
    } catch {
      raw = null;
    }
    this.set(parseQueue(raw, owner), false);
  }

  set(next: QueueState, save = true): void {
    if (next === this.state) return;
    this.state = next;
    if (save) this.save();
    for (const l of [...this.listeners]) l();
  }

  /** De plek in het huidige nummer (geen melding: dat zou elke tik een render zijn). */
  setPosition(position: number): void {
    if (!Number.isFinite(position) || Math.abs(position - this.state.position) < 1) return;
    this.state = { ...this.state, position };
    this.save();
  }

  /** Leeg, ook in de opslag (sluiten, uitloggen). */
  clear(): void {
    try {
      this.storage()?.removeItem(QUEUE_KEY);
    } catch {
      // geblokkeerd: niets te wissen
    }
    this.set(EMPTY, false);
  }

  private save(): void {
    if (!this.owner) return;
    try {
      const s = this.storage();
      if (!s) return;
      if (this.state.items.length) s.setItem(QUEUE_KEY, serializeQueue(this.state, this.owner));
      else s.removeItem(QUEUE_KEY);
    } catch {
      // vol of geblokkeerd: de rij blijft in het geheugen
    }
  }
}

export const queue = new QueueStore();
