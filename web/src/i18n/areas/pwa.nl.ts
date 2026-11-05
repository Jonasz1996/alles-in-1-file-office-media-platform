// De app installeren en offline werken (P1). Laadt pas als de offline-balk of de
// update-melding iets moet tonen; de service worker heeft dit chunk in de precache.
export default {
  title: "App",
  offline: "Je bent offline",
  offlineText: "Wat je al geopend had, werkt nog; nieuwe wijzigingen komen als je weer online bent.",
  updateReady: "Nieuwe versie beschikbaar",
  reload: "Vernieuwen",
  reloadShortcut: "Nieuwe versie laden",
} as const;
