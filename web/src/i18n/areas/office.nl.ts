// De editorpagina (/bewerken/:id) en de meldingen van office (fase 4, O2). De meldingen
// (office/notices.ts) laden dit gebied zelf, bij de eerste gebeurtenis office.* (Keuze 33).
export default {
  title: "Bewerken",
  page: {
    close: "Sluiten",
    saveNow: "Nu opslaan",
    frameTitle: "Editor: {name}",
    opening: "De editor openen…",
    reload: "Opnieuw laden",
    leaveHint: "Alt+Shift+Q: van de editor naar deze balk",
  },
  status: {
    connecting: "Verbinden…",
    editing: "Bewerken",
    others: { one: "Bewerken met 1 ander", other: "Bewerken met {n} anderen" },
    saving: "Opslaan…",
    saved: "Opgeslagen om {time}",
    offline: "Verbinding weg",
    closed: "De editor is gesloten",
  },
  notice: {
    stale:
      "Dit bestand is buiten de editor veranderd. Wat de editor opslaat, wordt de nieuwste versie; de andere inhoud blijft bij Versies.",
    busy: "Dit document wordt net opgeslagen; het opent zo weer.",
    offline: "De verbinding met de editor is weg. Wat het laatst is opgeslagen, staat in het bestand.",
    dropped: "Je hebt geen toegang meer; de editor is gesloten.",
    notReady: "De editor kwam niet klaar. Laad de pagina opnieuw.",
    error: "De editor meldt een fout (code {code}). Laad de pagina opnieuw.",
    unsaved: {
      no_room: "Niet tussentijds opgeslagen: het quotum van de eigenaar is vol. Je werk staat nog in de editor.",
      too_large: "Niet tussentijds opgeslagen: het document is te groot geworden. Je werk staat nog in de editor.",
      owner_gone: "Niet opgeslagen: de eigenaar van dit document bestaat niet meer.",
      error_status: "De editor kon het document niet bewaren. Je werk staat nog in de editor.",
    },
  },
  toast: {
    unsaved: {
      no_room: "{name} is niet opgeslagen: er is geen ruimte meer. De beheerder kan het terugzetten zodra er ruimte is.",
      too_large: "{name} is niet opgeslagen: het document is te groot.",
      owner_gone: "{name} is niet opgeslagen: de eigenaar bestaat niet meer.",
      error_status: "{name} is niet opgeslagen: de editor meldde een fout. De beheerder ziet het op Beheer.",
    },
    recovered: {
      status3: "Je werk is bewaard als herstelkopie: {name}",
      no_writer: "Niemand mocht het origineel nog bewerken. Je werk staat in de herstelkopie {name}",
      forgotten: "Een document dat niet was opgeslagen, is teruggezet: {name}",
    },
    open: "Openen",
  },
} as const;
