// Foutcodes van Sport (de Strava-vervanger; app/routers/sport.py). De Nederlandse tekst komt van de
// server, dit is de Engelse.
export default {
  sport_too_large: { en: "This file is too large" },
  sport_bad_tiles: { en: "The address must start with https and contain {z}, {x} and {y}" },
  sport_tiles_off: { en: "No map source has been set" },
  sport_tiles_failed: { en: "The map source returned no tile" },
} satisfies Record<string, { en: string }>;
