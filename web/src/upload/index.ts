// Het uploadcontract (frozen na P0; P3 bouwt de echte versie). Deze module wordt statisch
// geïmporteerd: uploadDropped moet synchroon in de drop-handler lopen, vóór elke await.

import { type ComponentType, createElement, type JSX, useRef } from "react";
import { lazyWithAreas, t } from "../i18n";
import { toast } from "../ui/toast";
import { uploadQueue } from "../uploadQueue";

/** "Uploaden" (P3: ook "Map uploaden"). Zonder canUpload niets. */
export function UploadButtons({ folderId, canUpload }: { folderId: string; canUpload: boolean }): JSX.Element {
  const input = useRef<HTMLInputElement>(null);
  if (!canUpload) return createElement("span", { hidden: true });
  return createElement(
    "span",
    { className: "upload-buttons" },
    createElement("button", { type: "button", onClick: () => input.current?.click() }, t("upload.upload")),
    createElement("input", {
      ref: input,
      type: "file",
      multiple: true,
      hidden: true,
      onChange: (e: { target: HTMLInputElement }) => {
        const files = e.target.files ? Array.from(e.target.files) : [];
        e.target.value = "";
        if (files.length) start(folderId, files);
      },
    }),
  );
}

function start(folderId: string, files: File[]): void {
  uploadQueue.add(folderId, files).catch((e: Error) => toast(e.message, { kind: "error" }));
}

/** Zitten er bestanden (van buiten de browser) in deze sleepactie? */
export function hasFiles(dt: DataTransfer | null): boolean {
  if (!dt) return false;
  return Array.from(dt.types ?? []).includes("Files");
}

/**
 * Bestanden die op een map zijn losgelaten uploaden. MOET synchroon in de drop-handler worden
 * aangeroepen, vóór elke await: daarna is dt leeg. Leest dt.files meteen uit (P3: ook
 * dt.items voor mappen).
 */
export function uploadDropped(dt: DataTransfer, folderId: string): void {
  const files = Array.from(dt.files ?? []);
  if (files.length) start(folderId, files);
}

/** De uploadlade rechtsonder (P3). In P0 nog niets: Bestanden toont de uploads zelf. */
export const UploadTray: ComponentType = lazyWithAreas(() => import("./UploadTray"), ["upload"]);
