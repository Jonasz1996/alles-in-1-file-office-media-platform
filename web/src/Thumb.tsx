import { memo, useEffect, useState } from "react";
import type { FileNode, Preview } from "./api";
import { mayLoad, waitToLoad } from "./browser/imageGate";
import { usePreviewRecord } from "./browser/previews";
import type { IconName } from "./ui/Icon";
import "./files.css";

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

const ICONS: Record<Category, IconName> = {
  folder: "folder",
  image: "image",
  video: "video",
  audio: "audio",
  pdf: "pdf",
  document: "document",
  archive: "archive",
  other: "file",
};

/** Het icoon voor een map, album of bestand. */
export function iconOf(node: Pick<FileNode, "kind" | "mime">): IconName {
  return node.kind === "album" ? "album" : ICONS[category(node)];
}

/**
 * Thumbnail als die er is, anders een icoon voor het soort bestand. size 32 (een rij in de
 * lijst) of 180 (een cel in het raster); beide gebruiken de thumbnail van 256 px.
 */
export const Thumb = memo(function Thumb({
  node,
  preview,
  size = 32,
}: {
  node: Pick<FileNode, "kind" | "mime">;
  preview?: Preview;
  size?: 32 | 180;
}) {
  const [broken, setBroken] = useState<string | null>(null);
  const cls = `thumb thumb-${size}`;
  const url = preview?.thumb && broken !== preview.thumb ? preview.thumb : null;
  // Tijdens snel scrollen wacht een nieuwe thumbnail even op zijn beurt (browser/imageGate.ts);
  // tot dan een leeg vlak
  const [allowed, setAllowed] = useState<string | null>(null);
  const ready = url !== null && (allowed === url || mayLoad(url));
  useEffect(() => {
    if (url === null || ready) return;
    return waitToLoad(url, () => setAllowed(url));
  }, [url, ready]);
  if (url !== null && !ready) return <span aria-hidden="true" className={`${cls} thumb-pending`} />;
  if (url !== null) {
    return (
      <img
        src={url}
        alt=""
        decoding="async"
        width={size}
        height={size}
        className={cls}
        onError={() => setBroken(url)} // bijv. een verlopen adres: dan het icoon
      />
    );
  }
  // Het icoon als masker in CSS (files.css): één element, geen svg per rij
  return <span aria-hidden="true" className={`${cls} thumb-icon`} data-kind={iconOf(node)} />;
});

/** Previews van de bestanden in een lijst (compat: SharedWithMe en anderen). Vraagt alleen wat
 * nieuw of veranderd is (andere inhoud = andere etag) en ververst zodra de server meldt dat een
 * preview klaar is; de cache (browser/previews.ts) deelt hij met de bestandsbrowser. */
export function usePreviews(nodes: FileNode[] | undefined): Record<string, Preview> {
  return usePreviewRecord(nodes);
}
