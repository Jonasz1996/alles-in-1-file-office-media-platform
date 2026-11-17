// Foutcodes van personen, herinneringen en slimme albums (fase 6, bouwontwerp §3.4; Z6). Z0
// zette de codes van het contract neer; de Nederlandse tekst komt van de server, dit is de Engelse.
export default {
  people_name_taken: { en: "You already have a person with this name" },
  people_merge_self: { en: "A person cannot be merged with themselves" },
  album_rule_invalid: { en: "This rule for the album is not valid" },
  album_rule_too_many: { en: "This rule matches too many photos" },
  memories_bad_hide: { en: "This cannot be hidden from your memories" },
} satisfies Record<string, { en: string }>;
