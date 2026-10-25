// De verzoeken van uploaden buiten tus om: mappen voor een mapupload, bezette namen vooraf, en
// de onderbroken uploads (P3, api/app/routers/folder_tree.py en uploads.py).

import type { Api } from "../api";

export type FolderError = "name_taken" | "invalid_name" | "too_deep";

export interface TreeResult {
  /** Per pad (zoals gestuurd) het id van de map */
  folders: Record<string, string>;
  /** De paden waarvan de map nieuw is */
  created: string[];
  errors: Record<string, FolderError>;
}

export interface TakenName {
  /** De naam zoals gevraagd */
  name: string;
  id: string;
  kind: string;
}

/** Een eigen upload die nog niet af is (GET /api/v1/tus). */
export interface PendingUpload {
  id: string;
  name: string;
  parent_id: string;
  size: number;
  offset: number;
  created_at: string;
  expires_at: string;
}

export interface UploadRequests {
  foldersTree(folderId: string, paths: string[]): Promise<TreeResult>;
  namesCheck(folderId: string, names: string[]): Promise<TakenName[]>;
  pending(): Promise<PendingUpload[]>;
  /** Een onderbroken upload weggooien (DELETE /api/v1/tus/{id}); al weg is ook goed. */
  discard(id: string): Promise<void>;
}

/** Zoveel paden of namen per verzoek (de grens van de server). */
export const CHUNK = 1000;

export function tusUrl(id: string): string {
  return `/api/v1/tus/${id}`;
}

export function uploadRequests(api: Pick<Api, "request" | "tusCancel">): UploadRequests {
  return {
    foldersTree: (folderId, paths) =>
      api.request<TreeResult>("POST", `/api/v1/files/${folderId}/folders/tree`, { paths }),
    namesCheck: async (folderId, names) =>
      (await api.request<{ taken: TakenName[] }>("POST", `/api/v1/files/${folderId}/names/check`, { names })).taken,
    pending: () => api.request<PendingUpload[]>("GET", "/api/v1/tus"),
    discard: async (id) => {
      try {
        await api.tusCancel(tusUrl(id));
      } catch (e) {
        const status = (e as { status?: number }).status;
        if (status !== 404 && status !== 410) throw e;
      }
    },
  };
}
