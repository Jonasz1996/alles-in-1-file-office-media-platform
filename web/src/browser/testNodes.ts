// Alleen voor de tests van de bestandsbrowser: nodes zoals de API ze geeft.

import type { FileNode } from "../api";

export function fileNode(change: Partial<FileNode> & { id: string }): FileNode {
  return {
    parent_id: "map",
    name: change.id,
    kind: "file",
    mime: "text/plain",
    size: 1,
    etag: `e-${change.id}`,
    created_at: "2026-10-01T10:00:00Z",
    modified_at: "2026-10-01T10:00:00Z",
    deleted_at: null,
    tags: [],
    ...change,
  };
}

export function dirNode(change: Partial<FileNode> & { id: string }): FileNode {
  return fileNode({ kind: "dir", mime: null, size: 0, ...change });
}
