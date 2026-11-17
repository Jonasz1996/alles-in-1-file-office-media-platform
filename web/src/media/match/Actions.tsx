// De knoppen Koppelen en Bewerken op de pagina van een item of een serie (fase 5, M8). Klein: de
// vensters zelf (MatchDialog, EditDialog) laden pas bij de klik. Alleen met schrijfrecht
// (can_edit); een aflevering koppel je via haar serie, bewerken kan wel.

import { type ComponentType, type JSX, useState } from "react";
import { errorText, t } from "../../i18n";
import { Button } from "../../ui/Button";
import { toast } from "../../ui/toast";
import type { Item, ShowDetail } from "../types";
import { canMatch, canUnmatch } from "./match";
import type { MatchTarget } from "./MatchDialog";

type Open =
  | { kind: "match"; C: ComponentType<{ target: MatchTarget; onClose(): void; onDone(): void }> }
  | { kind: "edit"; C: ComponentType<{ item: Item; onClose(): void; onDone(item: Item): void }> };

function useLazyDialog() {
  const [open, setOpen] = useState<Open | null>(null);
  const [loading, setLoading] = useState<"match" | "edit" | null>(null);
  async function show(kind: "match" | "edit") {
    if (loading) return;
    setLoading(kind);
    try {
      if (kind === "match") {
        const m = await import("./MatchDialog");
        setOpen({ kind, C: m.MatchDialog });
      } else {
        const m = await import("./EditDialog");
        setOpen({ kind, C: m.EditDialog });
      }
    } catch (e) {
      toast(errorText(e), { kind: "error" });
    } finally {
      setLoading(null);
    }
  }
  return { open, loading, show, close: () => setOpen(null) };
}

/** Koppelen (een film) en Bewerken (een film of aflevering), voor wie mag bewerken. */
export function ItemMatchActions({ item, onChanged }: { item: Item; onChanged(): void }): JSX.Element | null {
  const d = useLazyDialog();
  if (!item.can_edit || (item.kind !== "movie" && item.kind !== "episode")) return null;
  const target: MatchTarget = {
    kind: "movie",
    nodeId: item.node_id,
    title: item.title,
    year: item.year,
    matched: canUnmatch(item),
  };
  return (
    <>
      {canMatch(item) && (
        <Button icon="search" busy={d.loading === "match"} onClick={() => void d.show("match")} data-match="">
          {t("media.match.open")}
        </Button>
      )}
      <Button icon="rename" busy={d.loading === "edit"} onClick={() => void d.show("edit")} data-edit="">
        {t("media.match.edit")}
      </Button>
      {d.open?.kind === "match" && <d.open.C target={target} onClose={d.close} onDone={onChanged} />}
      {d.open?.kind === "edit" && <d.open.C item={item} onClose={d.close} onDone={() => onChanged()} />}
    </>
  );
}

/** De eerste aflevering met een bestand: daarvan komen de kandidaten van de serie. */
export function episodeOf(detail: ShowDetail): string | null {
  for (const s of detail.seasons) for (const e of s.episodes) if (e.node_id) return e.node_id;
  return null;
}

/** Koppelen voor een serie (als geheel), voor wie mag bewerken. */
export function ShowMatchAction({ detail, onChanged }: { detail: ShowDetail; onChanged(): void }): JSX.Element | null {
  const d = useLazyDialog();
  const node = episodeOf(detail);
  if (!detail.show.can_edit || !node) return null;
  const { show } = detail;
  const target: MatchTarget = { kind: "show", nodeId: node, showId: show.id, title: show.title, year: show.year, matched: false };
  return (
    <>
      <Button icon="search" busy={d.loading === "match"} onClick={() => void d.show("match")} data-match="">
        {t("media.match.open")}
      </Button>
      {d.open?.kind === "match" && <d.open.C target={target} onClose={d.close} onDone={onChanged} />}
    </>
  );
}
