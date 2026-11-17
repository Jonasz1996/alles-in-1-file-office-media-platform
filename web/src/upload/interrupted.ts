// "Onderbroken uploads": wat er na herladen (of een andere computer) nog op de server staat en
// niet in de wachtrij zit (GET /api/v1/tus). Kies het bestand opnieuw en hij gaat verder waar
// hij was; of gooi hem weg, dan komt de gereserveerde ruimte meteen vrij.

import type { PendingUpload, UploadRequests } from "./api";

/** Hetzelfde bestand? Op naam en grootte (de server kent geen inhoud of datum van de client). */
export function matches(entry: Pick<PendingUpload, "name" | "size">, file: Pick<File, "name" | "size">): boolean {
  return entry.name === file.name && entry.size === file.size;
}

/** Een weggegooide upload ook uit localStorage halen (upload.ts bewaart per bestand en map de
 * URL; welk bestand het was, weten we hier niet, dus zoeken op de URL). */
export function forgetStoredUrl(url: string): void {
  try {
    const store = window.localStorage;
    const keys: string[] = [];
    for (let i = 0; i < store.length; i++) {
      const key = store.key(i);
      if (key?.startsWith("platform-upload:") && store.getItem(key) === url) keys.push(key);
    }
    for (const key of keys) store.removeItem(key);
  } catch {
    /* geen opslag: dan is er ook niets te vergeten */
  }
}

export class InterruptedStore {
  private list: readonly PendingUpload[] = [];
  private checking: Promise<void> | null = null;
  private readonly listeners = new Set<() => void>();

  subscribe = (cb: () => void): (() => void) => {
    this.listeners.add(cb);
    return () => {
      this.listeners.delete(cb);
    };
  };

  getSnapshot = (): readonly PendingUpload[] => this.list;

  /** Vraagt de server welke uploads er nog liggen; skip(url): zit al in de wachtrij. Twee keer
   * tegelijk vragen doet één verzoek. Een fout (offline) laat de lijst zoals hij was. */
  check(requests: Pick<UploadRequests, "pending">, skip: (id: string) => boolean): Promise<void> {
    this.checking ??= requests
      .pending()
      .then(
        (found) => this.set(found.filter((u) => !skip(u.id))),
        () => {},
      )
      .finally(() => {
        this.checking = null;
      });
    return this.checking;
  }

  remove(id: string): void {
    if (this.list.some((u) => u.id === id)) this.set(this.list.filter((u) => u.id !== id));
  }

  clear(): void {
    if (this.list.length) this.set([]);
  }

  private set(list: readonly PendingUpload[]): void {
    this.list = list;
    for (const l of this.listeners) l();
  }
}

export const interrupted = new InterruptedStore();
