// Foutcodes van uploaden (tus).
export default {
  bad_conflict: { en: "conflict must be rename, replace or fail" },
  bad_content_type: { en: "Content-Type must be application/offset+octet-stream" },
  bad_length: { en: "Upload-Length cannot be negative" },
  bad_metadata: { en: "Invalid Upload-Metadata for '{key}'" },
  bad_parent: { en: "Upload-Metadata is missing a valid 'parent'" },
  offset_mismatch: { en: "Upload-Offset does not match" },
  too_many_bytes: { en: "More bytes than Upload-Length" },
  tus_version: { en: "Only tus 1.0.0 is supported" },
  upload_busy: { en: "A request for this upload is already running" },
  upload_not_found: { en: "Upload not found" },
} satisfies Record<string, { en: string }>;
