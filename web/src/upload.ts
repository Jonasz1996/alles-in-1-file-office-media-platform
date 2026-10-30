// Hervatbaar uploaden met het tus-protocol. Een bestand gaat in stukken van 16 MB; valt de
// verbinding weg, dan vraagt de client de server hoeveel er binnen is en gaat daar verder.
// De upload-URL staat in localStorage, zodat dezelfde upload ook na herladen van de pagina
// verdergaat: kies hetzelfde bestand opnieuw en hij begint waar hij was.
//
// Fouten die de client zelf maakt, hebben een code uit i18n/errors/upload.ts (connection_lost,
// upload_expired, …), zodat errorText ze in het Engels vertaalt.

import { ApiError, errorFromBody } from "./api";

export const CHUNK_SIZE = 16 * 1024 * 1024;
const MAX_RETRIES = 8;

/** Reden voor abort() als de upload alleen moet stoppen (bijv. bij uitloggen): hij blijft
 * dan op de server staan en gaat later verder. Zonder deze reden is abort() annuleren. */
export const STOP = "stop";

/** Het deel van Api dat uploaden nodig heeft (los te testen). */
export interface TusApi {
  readonly accessToken: string | null;
  refresh(): Promise<unknown>;
  /** meta: extra Upload-Metadata (conflict, lastmodified); een TusApi mag een deel negeren (een
   * uploadlink kent geen conflictkeuze). */
  tusCreate(
    parent: string,
    name: string,
    size: number,
    meta?: Record<string, string>,
  ): Promise<{ url: string; nodeId: string | null }>;
  tusStatus(url: string): Promise<{ offset: number; nodeId: string | null }>;
  tusCancel(url: string): Promise<void>;
}

export interface PatchResult {
  offset: number;
  nodeId: string | null;
}

/** Stuurt één stuk; onProgress krijgt het aantal bytes van dit stuk dat al weg is. */
export type SendChunk = (
  url: string,
  offset: number,
  chunk: Blob,
  token: string | null,
  onProgress: (sent: number) => void,
  signal?: AbortSignal,
) => Promise<PatchResult>;

export type UploadStorage = Pick<Storage, "getItem" | "setItem" | "removeItem">;

export interface UploadOptions {
  signal?: AbortSignal;
  send?: SendChunk;
  storage?: UploadStorage;
  sleep?: (ms: number) => Promise<void>;
  chunkSize?: number;
  /** Upload-URL van een eerdere poging, voor als localStorage niet werkt. */
  url?: string;
  /** Krijgt de upload-URL zodra die bekend is, om de upload later weg te kunnen gooien. */
  onUrl?: (url: string) => void;
  /** Extra Upload-Metadata bij het aanmaken (conflict, lastmodified). */
  meta?: Record<string, string>;
  /** Gestopt (STOP) en intussen toch geannuleerd? Dan wordt de upload weggegooid, ook als hij
   * pas na het stoppen ontstond (tusCreate kent het signaal niet). */
  cancelled?: () => boolean;
}

/** XMLHttpRequest, want fetch meldt geen voortgang tijdens het versturen. */
export const xhrSend: SendChunk = (url, offset, chunk, token, onProgress, signal) =>
  new Promise((resolve, reject) => {
    // Een abort-event komt niet nog eens: is er al geannuleerd, dan niets meer sturen.
    if (signal?.aborted) {
      reject(new ApiError(0, "Geannuleerd", "aborted"));
      return;
    }
    const xhr = new XMLHttpRequest();
    const onAbort = () => xhr.abort();
    const settle = (fn: () => void) => {
      signal?.removeEventListener("abort", onAbort);
      fn();
    };
    xhr.open("PATCH", url);
    xhr.setRequestHeader("Tus-Resumable", "1.0.0");
    xhr.setRequestHeader("Upload-Offset", String(offset));
    xhr.setRequestHeader("Content-Type", "application/offset+octet-stream");
    if (token) xhr.setRequestHeader("Authorization", `Bearer ${token}`);
    xhr.upload.onprogress = (e) => onProgress(e.loaded);
    xhr.onload = () =>
      settle(() => {
        if (xhr.status === 204) {
          resolve({
            offset: Number(xhr.getResponseHeader("Upload-Offset")),
            nodeId: xhr.getResponseHeader("Platform-Node-Id"),
          });
          return;
        }
        let body: unknown = null;
        try {
          body = JSON.parse(xhr.responseText);
        } catch {
          /* geen JSON */
        }
        reject(errorFromBody(xhr.status, body)); // met de code, bijv. upload_busy of name_taken
      });
    xhr.onerror = () => settle(() => reject(new ApiError(0, "Netwerkfout", "network")));
    xhr.ontimeout = xhr.onerror;
    xhr.onabort = () => settle(() => reject(new ApiError(0, "Geannuleerd", "aborted")));
    signal?.addEventListener("abort", onAbort, { once: true });
    xhr.send(chunk);
  });

function safeStorage(): UploadStorage | undefined {
  try {
    return window.localStorage;
  } catch {
    return undefined; // bijv. privévenster met opslag uit: dan alleen hervatten binnen deze pagina
  }
}

/** Eén upload per bestand en map: onder deze sleutel staat de upload-URL. */
export function storageKey(parent: string, file: File): string {
  return storageKeyFor(parent, file.name, file.size, file.lastModified);
}

/** Zoals storageKey, met grootte en datum die al gelezen zijn. In Chromium is elke lezing van
 * size of lastModified van een gekozen bestand een synchrone vraag aan de browser; bij 5.000
 * bestanden telt dat op (de wachtrij leest ze daarom één keer, in stukjes). */
export function storageKeyFor(parent: string, name: string, size: number, lastModified: number): string {
  return `platform-upload:${parent}:${name}:${size}:${lastModified}`;
}

const wait = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

/** Wacht ms, maar houdt meteen op als de upload geannuleerd wordt. */
function pause(sleep: (ms: number) => Promise<void>, ms: number, signal?: AbortSignal): Promise<void> {
  if (!signal) return sleep(ms);
  return new Promise((resolve) => {
    if (signal.aborted) return resolve();
    const done = () => {
      signal.removeEventListener("abort", done);
      resolve();
    };
    signal.addEventListener("abort", done);
    sleep(ms).then(done, done);
  });
}

/** Fouten waarbij opnieuw proberen niet helpt: de upload is weg of wordt geweigerd. */
function isFinal(e: ApiError): boolean {
  return (
    [400, 403, 404, 410, 412, 413, 415, 422].includes(e.status) || e.code === "aborted" || e.code === "name_taken"
  );
}

/**
 * Uploadt `file` naar map `parent`. onProgress krijgt een fractie van 0 tot 1.
 * Geeft het id van het nieuwe bestand terug.
 */
export async function uploadFile(
  api: TusApi,
  parent: string,
  file: File,
  onProgress: (fraction: number) => void,
  options: UploadOptions = {},
): Promise<string | null> {
  const send = options.send ?? xhrSend;
  const storage = options.storage ?? safeStorage();
  const sleep = options.sleep ?? wait;
  const chunkSize = options.chunkSize ?? CHUNK_SIZE;
  const signal = options.signal;
  const key = storageKey(parent, file);
  const report = (bytes: number) => onProgress(file.size ? Math.min(1, bytes / file.size) : 1);

  const forget = () => {
    try {
      storage?.removeItem(key);
    } catch {
      /* opslag niet beschikbaar */
    }
  };

  let url: string | null = null;
  try {
    url = storage?.getItem(key) ?? null;
  } catch {
    url = null;
  }
  url ??= options.url ?? null;
  let offset = 0;
  let nodeId: string | null = null;
  if (url) {
    try {
      ({ offset, nodeId } = await api.tusStatus(url));
    } catch (e) {
      if (!(e instanceof ApiError) || !isFinal(e)) throw e;
      forget(); // verlopen of opgeruimd: opnieuw beginnen
      url = null;
    }
  }
  if (!url) {
    const created = await api.tusCreate(parent, file.name, file.size, options.meta);
    url = created.url;
    nodeId = created.nodeId; // een leeg bestand is meteen klaar
    if (!nodeId) {
      try {
        storage?.setItem(key, url);
      } catch {
        /* opslag vol of uit */
      }
    }
  }
  options.onUrl?.(url);
  report(offset);

  // Klaar is pas als de server zegt welk bestand het werd. Ook het laatste stuk kan
  // verloren gaan; dan sturen we het (lege) restant opnieuw en antwoordt de server
  // nog eens met het bestand.
  let failures = 0;
  let needSync = false;
  let refreshed = false;
  while (nodeId === null && !signal?.aborted) {
    try {
      if (needSync) {
        ({ offset, nodeId } = await api.tusStatus(url));
        needSync = false;
        report(offset);
        if (nodeId !== null || signal?.aborted) break; // klaar, of geannuleerd tijdens het vragen
      }
      const start = offset;
      const chunk = file.slice(start, start + chunkSize);
      const result = await send(url, start, chunk, api.accessToken, (sent) => report(start + sent), signal);
      if (result.offset <= start && chunk.size > 0) {
        throw new ApiError(0, "De server nam niets aan", "nothing_accepted");
      }
      offset = result.offset;
      nodeId = result.nodeId;
      failures = 0;
      refreshed = false;
      report(offset);
      if (nodeId === null && offset >= file.size) throw new ApiError(0, "Geen bestand terug", "no_file_returned");
    } catch (e) {
      const error = e instanceof ApiError ? e : new ApiError(0, String(e));
      if (error.code === "aborted" || signal?.aborted) break;
      if (error.status === 401 && !refreshed) {
        refreshed = true; // token verlopen tijdens een lange upload
        if (await api.refresh()) continue;
        throw error; // verversen mislukt: Api heeft de sessie al beëindigd
      }
      if (error.status === 404 || error.status === 410) {
        forget();
        throw new ApiError(error.status, "De upload is verlopen; begin opnieuw", "upload_expired");
      }
      if (isFinal(error)) {
        forget();
        throw error;
      }
      failures++;
      if (failures > MAX_RETRIES) {
        // Steeds bezet: dan zegt de melding van de server meer dan "verbinding weg"
        if (error.code === "upload_busy") throw error;
        throw new ApiError(
          0,
          "De verbinding is weg. Probeer het opnieuw; de upload gaat verder waar hij was",
          "connection_lost",
        );
      }
      needSync = true;
      // Een eerste 409 is meestal een offset die uit de pas liep (een stuk dat toch aankwam):
      // dan meteen opnieuw kijken. Verder steeds langer wachten, en bij een upload waar al een
      // ander verzoek mee bezig is altijd: meteen opnieuw proberen helpt dan niet.
      const resyncNow = error.status === 409 && error.code !== "upload_busy" && failures === 1;
      if (!resyncNow) await pause(sleep, Math.min(30_000, 1000 * 2 ** (failures - 1)), signal);
    }
  }

  // Was het bestand al af toen er geannuleerd werd, dan is het er gewoon.
  if (nodeId === null && signal?.aborted) {
    if (signal.reason !== STOP || options.cancelled?.()) {
      forget();
      await api.tusCancel(url).catch(() => {});
    }
    throw new ApiError(0, "Geannuleerd", "aborted");
  }
  forget();
  return nodeId;
}

/**
 * Gooit een mislukte upload weg, zodat de server de gereserveerde ruimte meteen vrijgeeft
 * (anders pas na 24 uur). `url` is de URL die de upload zelf meldde, voor als localStorage
 * niet werkt. Lukt het niet, dan blijft alles staan en komt de fout terug.
 */
export async function discardUpload(
  api: TusApi,
  parent: string,
  file: File,
  url?: string,
  storage: UploadStorage | undefined = safeStorage(),
): Promise<void> {
  const key = storageKey(parent, file);
  let stored: string | null = null;
  try {
    stored = storage?.getItem(key) ?? null;
  } catch {
    stored = null;
  }
  for (const u of new Set([stored, url])) {
    if (!u) continue;
    try {
      await api.tusCancel(u);
    } catch (e) {
      // Al weg (verlopen of opgeruimd) is ook goed
      if (!(e instanceof ApiError) || (e.status !== 404 && e.status !== 410)) throw e;
    }
  }
  try {
    storage?.removeItem(key);
  } catch {
    /* opslag niet beschikbaar */
  }
}
