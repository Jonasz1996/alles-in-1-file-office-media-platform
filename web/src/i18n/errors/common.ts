// Foutcodes van de API die overal kunnen voorkomen, plus elke code die een vast contract
// noemt (de codes van ItemResult, account_disabled, invalid_input, not_logged_in). Elke code
// staat in precies één catalogus (api/tests/test_error_catalog.py). {naam} wordt ingevuld uit
// de params van de fout.
export default {
  not_found: { en: "Not found" },
  read_only: { en: "You can only view this" },
  owner_only: { en: "Only the owner can do that" },
  share_root: {
    en: "This was shared with you; only the owner can rename, move or delete it",
  },
  cross_owner: {
    en: "Moving only works within one owner's files; copy it instead",
  },
  name_taken: { en: "Something with this name already exists" },
  into_itself: { en: "A folder cannot be placed inside itself" },
  trashed: { en: "This is in the trash" },
  not_trashed: { en: "This is not in the trash" },
  too_many: { en: "Too many items at once (more than {max})" },
  album: { en: "This is not possible for an album" },
  not_own: { en: "You can only add your own files" },
  not_media: { en: "Only photos and videos can be added" },
  already: { en: "This is already there" },
  account_disabled: { en: "Your account has been blocked; ask your administrator" },
  invalid_input: { en: "Invalid input" },
  not_logged_in: { en: "You are not logged in" },
  guest: {
    en: "Guests have no files of their own; you only see what has been shared with you",
  },
  invalid_name: { en: "Invalid name" },
  folder_not_found: { en: "Folder not found" },
  no_room: { en: "Not enough storage quota" },
  no_room_owner: {
    en: "{owner} has run out of storage space; ask them to make room",
  },
  too_large: { en: "The file is too large" },
  too_large_or_quota: { en: "The file is too large or the quota is full" },
  body_timeout: { en: "Nothing arrived for too long; please try again" },
  rate_limited: { en: "Too many attempts; please try again later" },
  moved: {
    en: "The file was renamed, moved or deleted in the meantime; please try again",
  },
  no_free_name: { en: "No free name could be found" },
  root: { en: "This is not possible for your home folder" },
  not_a_file: { en: "This only works for a file" },
  wrong_token: { en: "This link belongs to another file" },
  user_not_found: { en: "User not found" },
  group_not_found: { en: "Group not found" },
} satisfies Record<string, { en: string }>;
