// Een nieuwe versie uploaden met voortgang en annuleren. fetch() geeft geen uploadvoortgang,
// dus XMLHttpRequest: PUT /files/{id}/content met het access-token. Na een 401 één keer
// verversen en opnieuw (zoals api.ts zelf doet).

import { api, errorFromBody, type FileNode, networkError } from "../api";

export type XhrFactory = () => XMLHttpRequest;

function abortError(): DOMException {
  return new DOMException("Afgebroken", "AbortError");
}

function once(
  nodeId: string,
  file: Blob,
  onProgress: (loaded: number, total: number) => void,
  signal: AbortSignal | undefined,
  makeXhr: XhrFactory,
): Promise<{ status: number; body: unknown }> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(abortError());
      return;
    }
    const xhr = makeXhr();
    xhr.open("PUT", `/api/v1/files/${nodeId}/content`);
    const token = api.accessToken;
    if (token) xhr.setRequestHeader("Authorization", `Bearer ${token}`);
    xhr.withCredentials = true;
    xhr.upload.onprogress = (e) => onProgress(e.loaded, e.lengthComputable ? e.total : file.size);
    const onAbort = () => xhr.abort();
    signal?.addEventListener("abort", onAbort, { once: true });
    const done = () => signal?.removeEventListener("abort", onAbort);
    xhr.onload = () => {
      done();
      let body: unknown = null;
      try {
        body = xhr.responseText ? JSON.parse(xhr.responseText) : null;
      } catch {
        /* geen JSON */
      }
      resolve({ status: xhr.status, body });
    };
    xhr.onerror = () => {
      done();
      reject(networkError(new TypeError("Failed to fetch")));
    };
    xhr.onabort = () => {
      done();
      reject(abortError());
    };
    xhr.send(file);
  });
}

/**
 * Vervangt de inhoud van een bestand (de huidige wordt een eerdere versie). onProgress krijgt
 * de verstuurde en de totale bytes; met signal annuleer je (dan een AbortError). Geeft het
 * bijgewerkte bestand; een fout van de server als ApiError (met code).
 */
export async function replaceWithProgress(
  nodeId: string,
  file: Blob,
  onProgress: (loaded: number, total: number) => void,
  signal?: AbortSignal,
  makeXhr: XhrFactory = () => new XMLHttpRequest(),
): Promise<FileNode> {
  let res = await once(nodeId, file, onProgress, signal, makeXhr);
  if (res.status === 401 && api.accessToken && (await api.refresh())) {
    onProgress(0, file.size);
    res = await once(nodeId, file, onProgress, signal, makeXhr);
  }
  if (res.status < 200 || res.status >= 300) throw errorFromBody(res.status, res.body);
  return res.body as FileNode;
}

/** Hoeveel procent er verstuurd is (0–100, heel getal). */
export function percentOf(loaded: number, total: number): number {
  if (!(total > 0)) return 0;
  return Math.max(0, Math.min(100, Math.floor((loaded / total) * 100)));
}
