// Foutcodes van beheer: gebruikers, groepen, achtergrondtaken, https en de server zelf.
export default {
  certs_not_running: { en: "Certificate management is not running" },
  group_or_user_not_found: { en: "Group or user not found" },
  guest_has_files: {
    en: "A guest cannot have files of their own; delete or move them first",
  },
  job_not_found: { en: "Job not found" },
  last_admin: { en: "At least one active administrator must remain" },
  no_domain: { en: "Set a domain first" },
  not_disabled: { en: "Block the account first" },
  not_failed: { en: "This job has not failed" },
  own_account: { en: "You cannot block or delete your own account" },
  too_soon: { en: "Please wait a moment before trying again" },
  unknown_user: { en: "One or more users do not exist" },
  user_being_deleted: { en: "This account is already being deleted" },
  web_app_missing: { en: "The web app has not been built" },
} satisfies Record<string, { en: string }>;
