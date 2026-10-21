// De deeldialoog (lazy, met het woordenboek "sharing"). De dialoog zelf staat in
// shares/ShareDialogPanel.tsx en laadt pas als iemand op Delen… klikt.

import { type JSX, Suspense } from "react";
import type { FileNode } from "./api";
import { lazyWithAreas } from "./i18n";

export interface ShareDialogProps {
  node: FileNode;
  /** Een selectie delen met personen of groepen (P6; ≤ 100). Wordt nog genegeerd. */
  nodes?: FileNode[];
  onClose: () => void;
  onChanged: () => void;
}

const Panel = lazyWithAreas<ShareDialogProps>(() => import("./shares/ShareDialogPanel"), ["sharing"]);

/** Delen met personen en groepen, en publieke links. Alleen voor de eigenaar. */
export function ShareDialog(p: ShareDialogProps): JSX.Element {
  return (
    <Suspense fallback={null}>
      <Panel {...p} />
    </Suspense>
  );
}
