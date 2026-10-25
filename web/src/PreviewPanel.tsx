// Compat (P4): het oude voorbeeldpaneel is opgegaan in de viewer (viewer/). Wat andere code er
// nog uit haalt, staat nu in viewer/media.ts.

import type { FileNode } from "./api";

export { formatDuration, mayRenew, playerFor } from "./viewer/media";

/** Wat de preview van een map of bestand laat zien (ook bij een publieke link). */
export type Shown = Pick<FileNode, "id" | "name" | "kind" | "mime" | "size" | "modified_at">;

/** Waar de inhoud vandaan komt: een downloadadres, met inline=true om te tonen of af te spelen. */
export type UrlSource = (inline: boolean) => Promise<string>;
