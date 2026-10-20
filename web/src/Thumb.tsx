import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, type FileNode, type Preview } from "./api";
import { useServerEvents } from "./events";

export type Category = "folder" | "image" | "video" | "audio" | "pdf" | "document" | "archive" | "other";

const ARCHIVES = ["zip", "x-tar", "gzip", "x-gzip", "x-7z-compressed", "vnd.rar", "x-rar-compressed", "x-bzip2", "x-xz"];
const DOCUMENTS = ["msword", "vnd.ms-", "vnd.openxmlformats-officedocument", "vnd.oasis.opendocument", "rtf", "json", "xml"];

/** Soort bestand voor het icoon en de preview, op het mime-type. */
export function category(node: Pick<FileNode, "kind" | "mime">): Category {
  if (node.kind === "dir") return "folder";
  const mime = (node.mime ?? "").toLowerCase();
  const [top, sub = ""] = mime.split("/");
  if (top === "image") return "image";
  if (top === "video") return "video";
  if (top === "audio") return "audio";
  if (mime === "application/pdf") return "pdf";
  if (top === "application" && ARCHIVES.includes(sub)) return "archive";
  if (top === "text" || (top === "application" && DOCUMENTS.some((d) => sub.startsWith(d)))) return "document";
  if (mime === "application/epub+zip") return "document";
  return "other";
}

const ICONS: Record<Category, string> = {
  folder: "📁",
  image: "🖼️",
  video: "🎬",
  audio: "🎵",
  pdf: "📕",
  document: "📝",
  archive: "🗜️",
  other: "📄",
};

const SIZE = 32;
const BATCH = 500;

/** Thumbnail als die er is, anders een icoon voor het soort bestand. */
export function Thumb({ node, preview }: { node: FileNode; preview?: Preview }) {
  const [broken, setBroken] = useState<string | null>(null);
  const box = { width: SIZE, height: SIZE, flex: "none", marginRight: "0.5rem", verticalAlign: "middle" } as const;
  if (preview?.thumb && broken !== preview.thumb) {
    return (
      <img
        src={preview.thumb}
        alt=""
        loading="lazy"
        decoding="async"
        width={SIZE}
        height={SIZE}
        style={{ ...box, objectFit: "cover", borderRadius: 4, background: "var(--surface)" }}
        onError={() => setBroken(preview.thumb)} // bijv. verlopen adres: dan het icoon
      />
    );
  }
  return (
    <span aria-hidden="true" style={{ ...box, display: "inline-block", textAlign: "center", lineHeight: `${SIZE}px` }}>
      {ICONS[category(node)]}
    </span>
  );
}

/** Previews van de bestanden in een lijst. Vraagt alleen wat nieuw of veranderd is (andere
 * inhoud = andere etag) en ververst zodra de server meldt dat een preview klaar is. */
export function usePreviews(nodes: FileNode[] | undefined): Record<string, Preview> {
  const [previews, setPreviews] = useState<Record<string, Preview>>({});
  const files = useMemo(() => (nodes ?? []).filter((n) => n.kind === "file"), [nodes]);
  const versions = files.map((n) => `${n.id}:${n.etag}`).join(",");
  const shown = useRef(new Set<string>());
  const asked = useRef(new Map<string, string>()); // id -> etag waarvoor we het vroegen

  const fetchSome = useCallback(async (ids: string[]) => {
    for (let i = 0; i < ids.length; i += BATCH) {
      try {
        const got = await api.previews(ids.slice(i, i + BATCH));
        setPreviews((old) => {
          const next = { ...old };
          for (const [id, preview] of Object.entries(got)) if (shown.current.has(id)) next[id] = preview;
          return next;
        });
      } catch {
        /* geen previews: dan de iconen */
      }
    }
  }, []);

  useEffect(() => {
    shown.current = new Set(files.map((n) => n.id));
    for (const id of [...asked.current.keys()]) if (!shown.current.has(id)) asked.current.delete(id);
    setPreviews((old) => Object.fromEntries(Object.entries(old).filter(([id]) => shown.current.has(id))));
    const changed = files.filter((n) => asked.current.get(n.id) !== n.etag);
    for (const n of changed) asked.current.set(n.id, n.etag);
    if (changed.length) void fetchSome(changed.map((n) => n.id));
    // Op versions en niet op files: een lijst die opnieuw geladen is maar niet veranderde,
    // vraagt niets opnieuw
  }, [versions, fetchSome]);

  useServerEvents((event) => {
    if (event.type === "preview.ready") {
      const ids = (event.data.node_ids as string[] | undefined) ?? [];
      const hit = ids.filter((id) => shown.current.has(id));
      if (hit.length) void fetchSome(hit);
    } else if (event.type === "connected") {
      // Misschien kwam er een preview klaar terwijl de verbinding weg was
      const waiting = [...shown.current].filter((id) => !previews[id] || previews[id].state === "pending");
      if (waiting.length) void fetchSome(waiting);
    }
  });

  return previews;
}
