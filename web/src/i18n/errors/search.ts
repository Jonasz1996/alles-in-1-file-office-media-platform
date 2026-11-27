// Foutcodes van zoeken (fase 6, bouwontwerp §3.4; Z1 en Z4). Z0 zette de codes van het contract
// neer; de Nederlandse tekst komt van de server, dit is de Engelse.
export default {
  search_query_too_long: { en: "Your search is too long" },
  search_bad_offset: { en: "You cannot load more results than this" },
  search_bad_filter: { en: "Type a word or choose a filter to search" },
  search_busy: { en: "Searching is busy right now; try again in a moment" },
  search_rebuild_running: { en: "The search index is already being rebuilt" },
} satisfies Record<string, { en: string }>;
