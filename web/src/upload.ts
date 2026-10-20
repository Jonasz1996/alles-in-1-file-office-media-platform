// Hervatbaar uploaden met het tus-protocol. Een bestand gaat in stukken van 16 MB; valt de
// verbinding weg, dan vraagt de client de server hoeveel er binnen is en gaat daar verder.
// De upload-URL staat in localStorage, zodat dezelfde upload ook na herladen van de pagina
// verdergaat: kies hetzelfde bestand opnieuw en hij begint waar hij was.

import { ApiError } from "./api";

export const CHUNK_SIZE = 16 * 1024 * 1024;
const MAX_RETRIES = 8;

/** Het deel van Api dat uploaden nodig heeft (los te testen). */
export interface TusApi {
  readonly accessToken: string | null;
  refresh(): Promise<unknown>;
  tusCreate(parent: string, name: string, size: number): Promise<{ url: string; nodeId: string | null }>;
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

export interface UploadOptions {
  signal?: AbortSignal;
  send?: SendChunk;
  storage?: Pick<Storage, "getItem" | "setItem" | "removeItem">;
  sleep?: (ms: number) => Promise<void>;
  chunkSize?: number;
}

/** XMLHttpRequest, want fetch meldt geen voortgang tijdens het versturen. */
export const xhrSend: SendChunk = (url, offset, chunk, token, onProgress, signal) =>
  new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("PATCH", url);
    xhr.setRequestHeader("Tus-Resumable", "1.0.0");
    xhr.setRequestHeader("Upload-Offset", String(offset));
    xhr.setRequestHeader("Content-Type", "application/offset+octet-stream");
    if (token) xhr.setRequestHeader("Authorization", `Bearer ${token}`);
    xhr.upload.onprogress = (e) => onProgress(e.loaded);
    xhr.onload = () => {
      if (xhr.status === 204) {
        resolve({
          offset: Number(xhr.getResponseHeader("Upload-Offset")),
          nodeId: xhr.getResponseHeader("Platform-Node-Id"),
        });
        return;
      }
      let detail = `Fout ${xhr.status}`;
      try {
        const body = JSON.parse(xhr.responseText);
        detail = typeof body.detail === "string" ? body.detail : (body.detail?.message ?? detail);
      } catch {
        /* geen JSON */
      }
      reject(new ApiError(xhr.status, detail));
    };
    xhr.onerror = () => reject(new ApiError(0, "Netwerkfout"));
    xhr.onabort = () => reject(new ApiError(0, "Geannuleerd", "aborted"));
    signal?.addEventListener("abort", () => xhr.abort(), { once: true });
    xhr.send(chunk);
  });

function safeStorage(): UploadOptions["storage"] {
  try {
    return window.localStorage;
  } catch {
    return undefined; // bijv. privévenster met opslag uit: dan alleen hervatten binnen deze pagina
  }
}

function storageKey(parent: string, file: File): string {
  return `platform-upload:${parent}:${file.name}:${file.size}:${file.lastModified}`;
}

const wait = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

/** Fouten waarbij opnieuw proberen niet helpt: de upload is weg of wordt geweigerd. */
function isFinal(e: ApiError): boolean {
  return [400, 403, 404, 410, 412, 413, 415, 422].includes(e.status) || e.code === "aborted";
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
    const created = await api.tusCreate(parent, file.name, file.size);
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
        if (nodeId !== null) break;
      }
      const start = offset;
      const chunk = file.slice(start, start + chunkSize);
      const result = await send(url, start, chunk, api.accessToken, (sent) => report(start + sent), signal);
      if (result.offset <= start && chunk.size > 0) throw new ApiError(0, "De server nam niets aan");
      offset = result.offset;
      nodeId = result.nodeId;
      failures = 0;
      refreshed = false;
      report(offset);
      if (nodeId === null && offset >= file.size) throw new ApiError(0, "Geen bestand terug");
    } catch (e) {
      const error = e instanceof ApiError ? e : new ApiError(0, String(e));
      if (error.code === "aborted" || signal?.aborted) break;
      if (error.status === 401 && !refreshed) {
        refreshed = true; // token verlopen tijdens een lange upload
        if (await api.refresh()) continue;
        throw error;
      }
      if (error.status === 409) {
        needSync = true; // offset liep uit de pas, bijv. een stuk dat toch aankwam
        failures++;
        if (failures <= MAX_RETRIES) continue;
      }
      if (error.status === 404 || error.status === 410) {
        forget();
        throw new ApiError(error.status, "De upload is verlopen; begin opnieuw");
      }
      if (isFinal(error)) {
        forget();
        throw error;
      }
      failures++;
      if (failures > MAX_RETRIES) {
        throw new ApiError(0, "De verbinding is weg. Probeer het opnieuw; de upload gaat verder waar hij was");
      }
      await sleep(Math.min(30_000, 1000 * 2 ** (failures - 1)));
      needSync = true;
    }
  }

  if (signal?.aborted) {
    forget();
    await api.tusCancel(url).catch(() => {});
    throw new ApiError(0, "Geannuleerd", "aborted");
  }
  forget();
  return nodeId;
}
