// Plaatshouder (P0) voor de viewer van P4: toont items[index] in het bestaande voorbeeldpaneel,
// met ← en → naar het vorige en volgende bestand. Alleen voor ingelogde gebruikers (source
// wordt nog genegeerd).

import { type JSX, type KeyboardEvent, useEffect, useState } from "react";
import { api, type Preview } from "../api";
import { PreviewPanel } from "../PreviewPanel";
import type { ViewerProps } from "./types";

export default function Viewer({ items, index, onIndexChange, onClose, onNeedMore }: ViewerProps): JSX.Element | null {
  const item = items[index];
  const [preview, setPreview] = useState<{ id: string; preview: Preview } | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!item) return;
    let live = true;
    api
      .previews([item.id])
      .then((got) => {
        if (live && got[item.id]) setPreview({ id: item.id, preview: got[item.id] });
      })
      .catch(() => {
        /* geen voorbeeld: dan zegt het paneel dat */
      });
    return () => {
      live = false;
    };
  }, [item]);

  if (!item) return null;

  function go(step: 1 | -1) {
    const next = index + step;
    if (next < 0 || next >= items.length) {
      onNeedMore?.(step);
      return;
    }
    onIndexChange(next);
  }

  async function download() {
    try {
      const a = document.createElement("a");
      a.href = item.version
        ? await api.versionDownloadUrl(item.id, item.version.id)
        : await api.downloadUrl(item.id);
      a.download = item.name;
      a.click();
    } catch (e) {
      setError((e as Error).message);
    }
  }

  return (
    <div
      className="viewer-stub"
      onKeyDown={(e: KeyboardEvent) => {
        if (e.target instanceof HTMLInputElement || e.altKey || e.ctrlKey || e.metaKey) return;
        if (e.key === "ArrowLeft") go(-1);
        else if (e.key === "ArrowRight") go(1);
      }}
    >
      <PreviewPanel
        key={item.id}
        node={{ ...item, kind: "file" }}
        preview={preview?.id === item.id ? preview.preview : undefined}
        onClose={onClose}
        onDownload={download}
      />
      {error && (
        <p className="sr-only" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}
