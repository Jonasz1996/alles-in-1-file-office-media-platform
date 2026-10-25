// Slepen binnen de bestandsbrowser: rijen en cellen oppakken, loslaten op een map in de lijst of
// in het pad erboven. Wat er gesleept wordt staat in dragState; de dataTransfer krijgt alleen de
// ids (NODE_TYPE) en een badge met het aantal. Een doel dat niet kan, krijgt dropEffect "none" en
// de reden via de live region (één keer per reden, niet bij elke dragover).

import { type DragEvent, useEffect, useMemo, useRef } from "react";
import type { FileNode } from "../api";
import { t } from "../i18n";
import { announce } from "../ui/toast";
import { dragState, dropDecision, EdgeScroller, hasNodes, NODE_TYPE, setDragBadge, wantsCopy } from "./dragState";
import type { DragCallbacks } from "./rowEvents";

export interface DragDeps {
  /** De node op plek i in de lijst (zoals die getoond wordt) */
  get(i: number): FileNode | undefined;
  /** Wat er meegaat als je rij i oppakt: de selectie als i erin zit, anders alleen i */
  pick(i: number): readonly FileNode[];
  /** De map die open staat (waar ze vandaan komen) */
  folderId: string | null;
  me: string | null;
  setDropId(id: string | null): void;
  /** Verplaatsen of kopiëren naar map target */
  transfer(nodes: readonly FileNode[], target: FileNode, mode: "cut" | "copy"): void;
}

export interface DragApi {
  /** Voor de lijst of het raster (useRowEvents) */
  rows: DragCallbacks;
  /** Voor een map buiten de lijst (een stap in het pad) */
  target(node: FileNode): {
    onDragOver(e: DragEvent<HTMLElement>): void;
    onDragLeave(): void;
    onDrop(e: DragEvent<HTMLElement>): void;
  };
  /** Bij elke dragover op de pagina: automatisch scrollen bij de rand */
  edge(e: DragEvent<HTMLElement>): void;
}

export function useDrag(deps: DragDeps): DragApi {
  const latest = useRef(deps);
  latest.current = deps;
  const lastReason = useRef<string | null>(null);
  const scroller = useMemo(() => new EdgeScroller(), []);

  useEffect(() => () => scroller.stop(), [scroller]);

  return useMemo(() => {
    const d = () => latest.current;

    /** Of loslaten op target kan; zet dropEffect en (bij nee) meldt de reden. */
    function over(target: FileNode | undefined, e: DragEvent<HTMLElement>): boolean {
      const drag = dragState.get();
      if (!drag || !hasNodes(e.dataTransfer)) return false;
      // Binnen de app altijd zelf afhandelen: de pagina (upload) doet er niets mee
      e.preventDefault();
      e.stopPropagation();
      if (!target || target.kind !== "dir") {
        e.dataTransfer.dropEffect = "none";
        d().setDropId(null);
        return false;
      }
      const decision = dropDecision(drag, target, wantsCopy(e), d().me);
      if (decision.effect === "none") {
        e.dataTransfer.dropEffect = "none";
        d().setDropId(null);
        const reason = decision.reason ? t(`browser.reasons.${decision.reason}`) : null;
        if (reason && reason !== lastReason.current) {
          lastReason.current = reason;
          announce(reason);
        }
        return false;
      }
      lastReason.current = null;
      e.dataTransfer.dropEffect = decision.effect;
      d().setDropId(target.id);
      return true;
    }

    function drop(target: FileNode | undefined, e: DragEvent<HTMLElement>): void {
      const drag = dragState.get();
      if (!drag || !hasNodes(e.dataTransfer)) return;
      e.preventDefault();
      e.stopPropagation();
      scroller.stop();
      d().setDropId(null);
      if (target && target.kind === "dir") {
        const decision = dropDecision(drag, target, wantsCopy(e), d().me);
        if (decision.effect !== "none") d().transfer(drag.nodes, target, decision.effect === "copy" ? "copy" : "cut");
      }
      dragState.end();
    }

    const rows: DragCallbacks = {
      onDragStart(i, e) {
        const nodes = d().pick(i);
        if (!nodes.length) {
          e.preventDefault();
          return;
        }
        const info = dragState.start(nodes, d().folderId);
        e.dataTransfer.effectAllowed = "copyMove";
        e.dataTransfer.setData(NODE_TYPE, JSON.stringify(info.ids));
        e.dataTransfer.setData("text/plain", nodes.map((n) => n.name).join("\n"));
        setDragBadge(e.dataTransfer, t("browser.dragBadge", { n: nodes.length }));
        lastReason.current = null;
      },
      onDragOver(i, e) {
        if (i === null) {
          if (dragState.get() && hasNodes(e.dataTransfer)) over(undefined, e);
          return;
        }
        over(d().get(i), e);
      },
      onDrop(i, e) {
        drop(i === null ? undefined : d().get(i), e);
      },
      onDragLeave() {
        d().setDropId(null);
      },
      onDragEnd() {
        scroller.stop();
        d().setDropId(null);
        dragState.end();
      },
    };

    return {
      rows,
      target: (node: FileNode) => ({
        onDragOver: (e: DragEvent<HTMLElement>) => {
          over(node, e);
        },
        onDragLeave: () => d().setDropId(null),
        onDrop: (e: DragEvent<HTMLElement>) => drop(node, e),
      }),
      edge(e: DragEvent<HTMLElement>) {
        if (dragState.get()) scroller.update(e.clientY);
      },
    };
  }, [scroller]);
}
