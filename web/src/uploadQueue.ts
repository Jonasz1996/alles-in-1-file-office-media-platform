// De rij met uploads staat buiten de pagina Bestanden: uploads lopen door als je naar een
// andere pagina gaat, en je ziet ze terug (met voortgang en Annuleren) als je terugkomt.
// Per bestand en map loopt er hooguit één upload tegelijk.

import { useSyncExternalStore } from "react";
import { ApiError, api } from "./api";
import { discardUpload, STOP, storageKey, type TusApi, type UploadOptions, uploadFile } from "./upload";

export interface UploadItem {
  /** Alleen binnen deze pagina (React-key). Een teller: crypto.randomUUID bestaat alleen via
   * https of localhost, niet via http op het LAN-adres. */
  key: string;
  file: File;
  parent: string;
  progress: number;
  /** waiting: nog niet aan de beurt; running: loopt; failed: mislukt; discarding: wordt weggegooid. */
  state: "waiting" | "running" | "failed" | "discarding";
  error?: string;
  /** Upload-URL zodra de server de upload kent; zo werkt weggooien ook zonder localStorage. */
  url?: string;
}

export class UploadQueue {
  private items: UploadItem[] = [];
  private readonly controllers = new Map<string, AbortController>();
  private readonly listeners = new Set<() => void>();
  private readonly finishedListeners = new Set<(parent: string, ok: boolean, file: File) => void>();
  private seq = 0;

  constructor(
    private readonly api: TusApi,
    private readonly options: Omit<UploadOptions, "signal" | "url" | "onUrl"> = {},
  ) {}

  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };

  getSnapshot = () => this.items;

  /** listener krijgt na elke upload (gelukt of niet) de map waar hij in moest, of hij lukte
   * (niet bij mislukt of geannuleerd) en het bestand. */
  onFinished(listener: (parent: string, ok: boolean, file: File) => void): () => void {
    this.finishedListeners.add(listener);
    return () => {
      this.finishedListeners.delete(listener);
    };
  }

  private setItems(items: UploadItem[]) {
    this.items = items;
    for (const listener of this.listeners) listener();
  }

  private find(key: string) {
    return this.items.find((x) => x.key === key);
  }

  private change(key: string, change: Partial<UploadItem>) {
    if (this.find(key)) this.setItems(this.items.map((x) => (x.key === key ? { ...x, ...change } : x)));
  }

  private remove(key: string) {
    if (this.find(key)) this.setItems(this.items.filter((x) => x.key !== key));
  }

  /**
   * Zet bestanden in de rij en uploadt ze na elkaar. Staat hetzelfde bestand voor dezelfde
   * map er al, dan komt er geen tweede bij: loopt of wacht hij, dan blijft dat zo; is hij
   * mislukt, dan gaat hij op zijn beurt opnieuw (verder waar hij was).
   */
  async add(parent: string, files: Iterable<File>): Promise<void> {
    const batch: string[] = [];
    for (const file of files) {
      const id = storageKey(parent, file);
      const same = this.items.find((x) => storageKey(x.parent, x.file) === id);
      if (same) {
        if (same.state === "failed" && !batch.includes(same.key)) batch.push(same.key);
        continue;
      }
      const item: UploadItem = { key: `${file.name}-${++this.seq}`, file, parent, progress: 0, state: "waiting" };
      this.setItems([...this.items, item]);
      batch.push(item.key);
    }
    for (const key of batch) await this.run(key);
  }

  /** Start of hervat één upload. Doet niets als hij al loopt of weg is. */
  async run(key: string): Promise<void> {
    const item = this.find(key);
    if (!item || (item.state !== "waiting" && item.state !== "failed")) return;
    const controller = new AbortController();
    this.controllers.set(key, controller);
    this.change(key, { state: "running", error: undefined });
    let ok = false;
    try {
      await uploadFile(this.api, item.parent, item.file, (progress) => this.change(key, { progress }), {
        ...this.options,
        signal: controller.signal,
        url: item.url,
        onUrl: (url) => this.change(key, { url }),
      });
      ok = true;
      this.remove(key);
    } catch (e) {
      if (e instanceof ApiError && e.code === "aborted") this.remove(key);
      else this.change(key, { state: "failed", error: (e as Error).message });
    } finally {
      this.controllers.delete(key);
    }
    for (const listener of this.finishedListeners) listener(item.parent, ok, item.file);
  }

  /** Annuleren: de server gooit de upload weg. */
  cancel(key: string): void {
    this.controllers.get(key)?.abort();
  }

  /** Een mislukte upload weggooien, zodat de server de gereserveerde ruimte vrijgeeft. */
  async discard(key: string): Promise<void> {
    const item = this.find(key);
    if (!item || item.state !== "failed") return;
    this.change(key, { state: "discarding" });
    try {
      await discardUpload(this.api, item.parent, item.file, item.url, this.options.storage);
      this.remove(key);
    } catch (e) {
      this.change(key, { state: "failed", error: `Weggooien lukte niet: ${(e as Error).message}` });
    }
  }

  /**
   * Bij uitloggen: alle uploads stoppen en de lijst leegmaken. Ze blijven op de server staan;
   * hetzelfde bestand opnieuw kiezen gaat later verder waar hij was.
   */
  stopAll(): void {
    for (const controller of this.controllers.values()) controller.abort(STOP);
    this.setItems([]);
  }
}

export const uploadQueue = new UploadQueue(api);

export function useUploads(): UploadItem[] {
  return useSyncExternalStore(uploadQueue.subscribe, uploadQueue.getSnapshot);
}
