// Compat (P4): het oude voorbeeldpaneel is opgegaan in de viewer (viewer/). Wat andere code er
// nog uit haalt, staat nu in viewer/media.ts.

export { formatDuration, mayRenew, playerFor } from "./viewer/media";

/** Waar de inhoud vandaan komt: een downloadadres, met inline=true om te tonen of af te spelen. */
export type UrlSource = (inline: boolean) => Promise<string>;
