// Foutcodes van de profielen in media (fase 11, stap 7). Apart van media.ts: die bevat precies de
// codes van §3.4 (media/api.test.ts).
export default {
  media_profile_locked: { en: "Profiles cannot be managed from this profile" },
  media_profile_limit: { en: "At most 8 profiles per account" },
  media_profile_pin: { en: "The PIN is not correct" },
} satisfies Record<string, { en: string }>;
