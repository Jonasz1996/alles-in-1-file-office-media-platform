// Foutcodes van de bestandsbrowser: lijsten, tags, zoeken en versies.
export default {
  tag_not_found: { en: "Tag not found" },
  too_many_tags: { en: "You can have at most {max} tags" },
  version_not_found: { en: "Version not found" },
  invalid_cursor: { en: "Invalid position in the list; reload the folder" },
} satisfies Record<string, { en: string }>;
