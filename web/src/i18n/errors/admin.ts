// Foutcodes van beheer: gebruikers, groepen, achtergrondtaken, https en de server zelf.
export default {
  certs_not_running: { en: "Certificate management is not running" },
  group_or_user_not_found: { en: "Group or user not found" },
  guest_has_files: {
    en: "A guest cannot have files of their own; delete or move them first",
  },
  job_not_found: { en: "Job not found" },
  // Een code in analysis.code (fase 6): geen HTTP-fout, alleen te zien op /beheer.
  // render.py geeft hem als er geen tesseract in het image zit.
  ocr_missing: { en: "Text recognition is not installed on this server" },
  last_admin: { en: "At least one active administrator must remain" },
  no_domain: { en: "Set a domain first" },
  not_disabled: { en: "Block the account first" },
  not_failed: { en: "This job has not failed" },
  own_account: { en: "You cannot block or delete your own account" },
  too_soon: { en: "Please wait a moment before trying again" },
  unknown_user: { en: "One or more users do not exist" },
  user_being_deleted: { en: "This account is already being deleted" },
  web_app_missing: { en: "The web app has not been built" },
  arr_link_auth: { en: "Prowlarr or qBittorrent refuses the key or password" },
  arr_link_unreachable: { en: "Prowlarr or qBittorrent cannot be reached" },
  arr_tmdb_needed: { en: "This needs a working TMDB key" },
  arr_library_kind: { en: "A film belongs in a film library, a series in a series library" },
  arr_wanted_exists: { en: "You already follow this title in that library" },
  arr_request_exists: { en: "You already requested this" },
  arr_request_limit: { en: "You already have many requests waiting" },
  arr_request_decided: { en: "This request has already been decided" },
  arr_upscale_exists: { en: "This video is already in the queue" },
  arr_upscale_kind: { en: "Only a film, episode or video can be upscaled" },
  arr_upscale_stopped: { en: "This job is no longer running" },
  arr_no_library: { en: "There is no library for films or shows to put it in yet" },
} satisfies Record<string, { en: string }>;
