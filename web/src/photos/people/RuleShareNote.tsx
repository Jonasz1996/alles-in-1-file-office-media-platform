// De telling in het deeldialoog van een album met een regel (fase 6, van Z6; Keuze 42): "N foto's
// komen uit de regel; M daarvan worden gedeeld." Een eigen chunk met het gebied photos_people,
// pas geladen als het deeldialoog van een album opengaat (ShareDialogPanel.tsx). Zonder regel, of
// als het album niet te laden is: niets. Na de vraag (album.changed) telt hij opnieuw.

import { type JSX, useCallback, useEffect, useState } from "react";
import type { FileNode } from "../../api";
import { useServerEvents } from "../../events";
import { t } from "../../i18n";
import { photosApi } from "../api";
import { sharedCount } from "./rule";
import type { AlbumRuleInfo } from "./types";

export interface RuleShareNoteProps {
  /** Het album dat gedeeld wordt */
  node: FileNode;
}

export default function RuleShareNote({ node }: RuleShareNoteProps): JSX.Element | null {
  const [info, setInfo] = useState<AlbumRuleInfo | null>(null);
  const id = node.id;

  const load = useCallback(async () => {
    try {
      const a = (await photosApi.album(id)) as { rule?: AlbumRuleInfo | null };
      setInfo(a.rule ?? null);
    } catch {
      setInfo(null); // een extraatje: het deeldialoog werkt ook zonder
    }
  }, [id]);

  useEffect(() => {
    void load();
  }, [load]);

  useServerEvents((e) => {
    if (e.type === "album.changed" && String(e.data.id) === id) void load();
  });

  if (!info) return null;
  return (
    <p className="muted" role="status">
      {t("photos_people.rule.counts", { items: info.items, shared: sharedCount(info) })}
    </p>
  );
}
