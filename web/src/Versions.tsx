// De versies van een bestand (lazy, met het woordenboek "versions"). De dialoog zelf staat in
// versions/VersionsDialog.tsx en laadt pas als iemand Versies… kiest.

import { type JSX, Suspense } from "react";
import type { FileNode } from "./api";
import { lazyWithAreas } from "./i18n";
import "./versions.css";

export interface VersionsProps {
  node: FileNode;
  onClose: () => void;
  onChanged: () => void;
}

const VersionsDialog = lazyWithAreas<VersionsProps>(() => import("./versions/VersionsDialog"), ["versions"]);

/** Eerdere inhoud van een bestand: downloaden, terugzetten, verwijderen of een nieuwe uploaden. */
export function Versions(p: VersionsProps): JSX.Element {
  return (
    <Suspense fallback={null}>
      <VersionsDialog {...p} />
    </Suspense>
  );
}
