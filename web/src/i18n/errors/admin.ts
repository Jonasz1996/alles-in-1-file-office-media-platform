// Foutcodes van beheer: groepen, achtergrondtaken, https en de server zelf.
export default {
  certs_not_running: { en: "Certificate management is not running" },
  group_or_user_not_found: { en: "Group or user not found" },
  job_not_found: { en: "Job not found" },
  no_domain: { en: "Set a domain first" },
  not_failed: { en: "This job has not failed" },
  too_soon: { en: "Please wait a moment before trying again" },
  unknown_user: { en: "One or more users do not exist" },
  web_app_missing: { en: "The web app has not been built" },
} satisfies Record<string, { en: string }>;
