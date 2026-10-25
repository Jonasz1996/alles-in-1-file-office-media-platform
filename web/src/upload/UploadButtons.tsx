// "Uploaden" en "Map uploaden" in de werkbalk van Bestanden, met U en Shift+U (scope
// "browser", die Files.tsx neerzet). De knop "Uploaden" houdt precies die naam (DOM-contract
// van de rooktest).

import { type JSX, useEffect, useRef } from "react";
import { t } from "../i18n";
import { Button } from "../ui/Button";
import { useShortcuts } from "../ui/keys";
import { checkInterrupted } from "../uploadQueue";
import { uploadFiles } from "./start";
import "./upload.css";

/** "Uploaden" en "Map uploaden". Zonder canUpload niets (wel een leeg element). */
export function UploadButtons({ folderId, canUpload }: { folderId: string; canUpload: boolean }): JSX.Element {
  const files = useRef<HTMLInputElement>(null);
  const folder = useRef<HTMLInputElement>(null);

  // Na herladen: liggen er nog onderbroken uploads op de server? (de lade toont ze)
  useEffect(() => {
    if (canUpload) void checkInterrupted();
  }, [canUpload]);

  useShortcuts(
    [
      { combo: "u", label: t("upload.shortcuts.upload"), run: () => files.current?.click() },
      { combo: "shift+u", label: t("upload.shortcuts.uploadFolder"), run: () => folder.current?.click() },
    ],
    { scope: "browser", enabled: canUpload },
  );

  if (!canUpload) return <span hidden />;

  const picked = (input: HTMLInputElement) => {
    const list = input.files ? Array.from(input.files) : [];
    input.value = ""; // hetzelfde bestand nog eens kiezen moet weer een change geven
    uploadFiles(folderId, list);
  };

  return (
    <span className="upload-buttons">
      <Button icon="upload" aria-keyshortcuts="U" onClick={() => files.current?.click()}>
        {t("upload.upload")}
      </Button>
      <Button icon="folder-upload" aria-keyshortcuts="Shift+U" onClick={() => folder.current?.click()}>
        {t("upload.uploadFolder")}
      </Button>
      <input ref={files} type="file" multiple hidden onChange={(e) => picked(e.currentTarget)} />
      <input
        ref={(el) => {
          folder.current = el;
          // webkitdirectory staat niet in de React-typen; als attribuut werkt het overal
          el?.setAttribute("webkitdirectory", "");
        }}
        type="file"
        multiple
        hidden
        onChange={(e) => picked(e.currentTarget)}
      />
    </span>
  );
}
