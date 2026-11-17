// Foutcodes van delen en publieke links.
export default {
  already_shared: { en: "Already shared with this person or group" },
  bad_permission: { en: "This permission does not fit this share" },
  expires_past: { en: "The expiry date is in the past" },
  file: { en: "This link is for a single file" },
  key_expired: { en: "Open the link again" },
  label_too_long: { en: "A label can have at most {max} characters" },
  link_expired: { en: "This link has expired" },
  link_not_found: { en: "This link does not exist (anymore)" },
  link_only: { en: "A password or label only belongs to a link" },
  password_required: { en: "This link needs a password" },
  self: { en: "No need to share with yourself: it is already yours" },
  share_not_found: { en: "Share not found" },
  upload_folder_only: { en: "An upload link only works for a folder" },
  upload_only: { en: "With this link you can only upload" },
  wrong_password: { en: "Wrong password" },
} satisfies Record<string, { en: string }>;
