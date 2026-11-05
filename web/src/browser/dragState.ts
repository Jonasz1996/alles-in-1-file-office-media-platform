// Slepen binnen de app (HTML5 drag and drop): rijen en cellen naar een map in de lijst, een map
// in het pad erboven of een doel in de navigatie. Wat er gesleept wordt, staat hier (de
// dataTransfer is tijdens dragover niet te lezen); in de dataTransfer zelf alleen de ids, onder
// NODE_TYPE. Verplaatsen binnen dezelfde eigenaar, kopiëren met Ctrl/Alt of naar een andere
// eigenaar. Bestanden van buiten de browser gaan naar het uploaden (P3).

import type { FileNode } from "../api";
import { canWrite, permissionOf } from "../sharing";
import { ownerOf } from "./clipboard";

export const NODE_TYPE = "application/x-platform-nodes";

export interface DragInfo {
  ids: readonly string[];
  nodes: readonly FileNode[];
  kinds: readonly FileNode["kind"][];
  mimes: readonly (string | null)[];
  /** De map waar ze vandaan komen */
  from: string | null;
}

type Listener = () => void;

class DragState {
  private info: DragInfo | null = null;
  private readonly listeners = new Set<Listener>();

  get(): DragInfo | null {
    return this.info;
  }

  start(nodes: readonly FileNode[], from: string | null): DragInfo {
    this.info = {
      ids: nodes.map((n) => n.id),
      nodes: [...nodes],
      kinds: nodes.map((n) => n.kind),
      mimes: nodes.map((n) => n.mime),
      from,
    };
    this.changed();
    return this.info;
  }

  end(): void {
    if (!this.info) return;
    this.info = null;
    this.changed();
  }

  subscribe(cb: Listener): () => void {
    this.listeners.add(cb);
    return () => {
      this.listeners.delete(cb);
    };
  }

  private changed(): void {
    for (const l of [...this.listeners]) l();
  }
}

export const dragState = new DragState();

/** Zit er een sleepactie van de app in deze dataTransfer? */
export function hasNodes(dt: DataTransfer | null): boolean {
  return !!dt && Array.from(dt.types ?? []).includes(NODE_TYPE);
}

export type DropReason = "readOnly" | "itself" | "same" | "notFolder";

export interface DropDecision {
  effect: "move" | "copy" | "none";
  /** Waarom het niet kan (bij none) */
  reason?: DropReason;
}

/**
 * Wat loslaten op map target doet. copy: Ctrl (of Cmd) of Alt ingedrukt. me: de ingelogde
 * gebruiker. Een andere eigenaar: kopiëren (verplaatsen kan alleen binnen één eigenaar).
 */
export function dropDecision(
  drag: Pick<DragInfo, "ids" | "nodes" | "from">,
  target: Pick<FileNode, "id" | "kind" | "owner" | "permission">,
  copy: boolean,
  me: string | null,
): DropDecision {
  if (target.kind !== "dir") return { effect: "none", reason: "notFolder" };
  if (drag.ids.includes(target.id)) return { effect: "none", reason: "itself" };
  if (!canWrite(permissionOf(target))) return { effect: "none", reason: "readOnly" };
  const owner = ownerOf(target, me);
  const crossOwner = drag.nodes.some((n) => ownerOf(n, me) !== owner);
  if (copy || crossOwner) return { effect: "copy" };
  if (drag.from === target.id) return { effect: "none", reason: "same" };
  return { effect: "move" };
}

/** Kopiëren in plaats van verplaatsen? Ctrl (Windows, Linux), Alt (Mac: Option) of Cmd. */
export function wantsCopy(e: { ctrlKey: boolean; altKey: boolean; metaKey: boolean }): boolean {
  return e.ctrlKey || e.altKey || e.metaKey;
}

/** Een afbeelding bij het slepen: een badge met het aantal, alleen met klassen (geen
 * style-attribuut). Hij moet even in de pagina staan; daarna weer weg. */
export function setDragBadge(dt: DataTransfer, text: string): void {
  if (typeof document === "undefined" || typeof dt.setDragImage !== "function") return;
  const badge = document.createElement("div");
  badge.className = "drag-badge";
  badge.textContent = text;
  document.body.appendChild(badge);
  dt.setDragImage(badge, 12, 12);
  setTimeout(() => badge.remove(), 0);
}

/** Automatisch scrollen bij de rand van het venster tijdens het slepen (per frame). */
export class EdgeScroller {
  private frame = 0;
  private speed = 0;
  private readonly edge: number;

  constructor(edge = 64) {
    this.edge = edge;
  }

  /** Bij elke dragover: hoe dicht bij de rand (clientY). */
  update(clientY: number): void {
    const h = typeof window === "undefined" ? 0 : window.innerHeight;
    if (clientY < this.edge) this.speed = -Math.ceil((this.edge - clientY) / 4);
    else if (clientY > h - this.edge) this.speed = Math.ceil((clientY - (h - this.edge)) / 4);
    else this.speed = 0;
    if (this.speed !== 0 && !this.frame) this.frame = requestAnimationFrame(this.tick);
  }

  private tick = (): void => {
    this.frame = 0;
    if (this.speed === 0) return;
    window.scrollBy(0, this.speed);
    this.frame = requestAnimationFrame(this.tick);
  };

  stop(): void {
    this.speed = 0;
    if (this.frame) cancelAnimationFrame(this.frame);
    this.frame = 0;
  }
}
