// Foutcodes van previews en de viewer.
export default {
  link_invalid: { en: "This link is invalid or has expired" },
  no_preview: { en: "No preview" },
  not_a_document: { en: "This file has no pages to show" },
  invalid_width: { en: "This width does not exist; choose 640, 1280, 1920 or 2560" },
  page_not_found: { en: "This page does not exist" },
  render_failed: { en: "This page could not be made right now; try again" },
} satisfies Record<string, { en: string }>;
