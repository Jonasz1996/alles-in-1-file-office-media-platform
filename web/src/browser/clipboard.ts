// Knippen, kopiëren en plakken binnen de app (Ctrl/Cmd+X, C, V): alleen de ids, niet het
// klembord van het systeem. Knippen en plakken verplaatst; bij een andere eigenaar kan dat niet
// en wordt het een kopie (zoals bij slepen).

import type { FileNode } from "../api";

export type ClipMode = "cut" | "copy";

export interface Clip {
  mode: ClipMode;
  nodes: readonly FileNode[];
  /** De map waar ze vandaan kwamen */
  from: string | null;
}

export class ClipboardStore {
  private clip: Clip | null = null;
  private readonly listeners = new Set<() => void>();

  getSnapshot = (): Clip | null => this.clip;

  subscribe = (cb: () => void): (() => void) => {
    this.listeners.add(cb);
    return () => {
      this.listeners.delete(cb);
    };
  };

  set(mode: ClipMode, nodes: readonly FileNode[], from: string | null): void {
    this.clip = nodes.length ? { mode, nodes: [...nodes], from } : null;
    this.changed();
  }

  clear(): void {
    if (!this.clip) return;
    this.clip = null;
    this.changed();
  }

  /** Haalt nodes weg die er niet meer zijn (weggegooid); leeg = klembord leeg. */
  drop(ids: ReadonlySet<string>): void {
    if (!this.clip) return;
    const nodes = this.clip.nodes.filter((n) => !ids.has(n.id));
    if (nodes.length === this.clip.nodes.length) return;
    this.clip = nodes.length ? { ...this.clip, nodes } : null;
    this.changed();
  }

  /** Is dit id geknipt (de rij wordt dan doorschijnend getoond)? */
  isCut(id: string): boolean {
    return this.clip?.mode === "cut" && this.clip.nodes.some((n) => n.id === id);
  }

  private changed(): void {
    for (const l of [...this.listeners]) l();
  }
}

export const clipboard = new ClipboardStore();

/** Wie de eigenaar is (zonder owner: van jezelf). */
export function ownerOf(node: Pick<FileNode, "owner" | "permission">, me: string | null): string | null {
  return node.owner?.id ?? (node.permission === undefined || node.permission === "owner" ? me : null);
}

export interface Transfer {
  /** Verplaatsen (zelfde eigenaar) */
  move: string[];
  /** Kopiëren (gevraagd, of een andere eigenaar) */
  copy: string[];
  /** Overslaan: de map zelf als doel (kan niet in zichzelf) */
  skip: string[];
}

/**
 * Wat plakken of loslaten in map target doet: mode "copy" kopieert alles; "cut" (of slepen)
 * verplaatst wat dezelfde eigenaar heeft als het doel en kopieert de rest. me: de ingelogde
 * gebruiker (voor nodes zonder owner).
 */
export function planTransfer(
  nodes: readonly FileNode[],
  target: Pick<FileNode, "id" | "owner" | "permission">,
  mode: ClipMode,
  me: string | null,
): Transfer {
  const out: Transfer = { move: [], copy: [], skip: [] };
  const targetOwner = ownerOf(target, me);
  for (const n of nodes) {
    if (n.id === target.id) out.skip.push(n.id);
    else if (mode === "copy" || ownerOf(n, me) !== targetOwner) out.copy.push(n.id);
    else out.move.push(n.id);
  }
  return out;
}
