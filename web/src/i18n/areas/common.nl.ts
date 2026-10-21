// Gedeelde teksten: knoppen, laden, fouten en de ui/-onderdelen. Altijd geladen.
export default {
  loading: "Laden…",
  close: "Sluiten",
  cancel: "Annuleren",
  ok: "OK",
  save: "Opslaan",
  retry: "Opnieuw proberen",
  back: "Terug",
  comingSoon: "Komt eraan",
  comingSoonText: "Dit onderdeel wordt nog gebouwd.",
  errors: {
    network: "Geen verbinding met de server. Probeer het opnieuw.",
    unknown: "Er ging iets mis.",
  },
  dialog: {
    required: "Vul iets in.",
    typeToConfirm: "Typ {text} om te bevestigen.",
  },
  toast: {
    region: "Meldingen",
    dismiss: "Melding sluiten",
  },
  usage: {
    ofTotal: "{used} van {total}",
    usedOnly: "{used} gebruikt",
  },
  /** Vervaldatum van een link (sharing.ts: expiryLabel), ook op de publieke pagina */
  expires: {
    on: "verloopt op {date}",
    dateAt: "{date} om {time}",
    past: "verlopen",
  },
  items: { one: "{n} item", other: "{n} items" },
} as const;
