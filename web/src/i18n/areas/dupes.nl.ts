// Dubbele bestanden en foto's die erop lijken (fase 6, Z5; Keuze 34–36). Het gebied is van Z5.
export default {
  title: "Dubbele bestanden",
  intro:
    "Bestanden die precies gelijk zijn, en foto's die erop lijken. Er staat nooit iets vooraf aangevinkt: jij kiest wat weg mag. Het gaat naar de prullenbak; daar kun je het terughalen.",
  exactNote:
    "Een bestand dat precies gelijk is, kost geen extra ruimte: de inhoud staat er één keer. Opruimen maakt het alleen overzichtelijker.",
  tabs: {
    label: "Soort",
    exact: "Precies gelijk",
    near: "Lijken erop",
  },
  level: {
    exact: "Precies gelijk",
    zeker: "Bijna zeker gelijk",
    lijkt: "Lijkt erop",
  },
  group: {
    files: { one: "{n} bestand", other: "{n} bestanden" },
  },
  item: {
    view: "{name} bekijken",
    pick: "Kiezen",
    pickLabel: "{name} kiezen voor de prullenbak",
    taken: "Gemaakt",
    modified: "Gewijzigd",
    size: "Grootte",
    folder: "Map",
  },
  myFiles: "Mijn bestanden",
  trash: {
    none: "Naar de prullenbak",
    some: { one: "{n} bestand naar de prullenbak", other: "{n} bestanden naar de prullenbak" },
    hint: "Vink eerst aan wat weg mag.",
    done: { one: "{n} bestand naar de prullenbak", other: "{n} bestanden naar de prullenbak" },
    undo: "Ongedaan maken",
    allTitle: "Alles uit deze groep weg?",
    allMessage: {
      one: "Je hebt het enige bestand van deze groep aangevinkt.",
      other: "Je hebt alle {n} bestanden van deze groep aangevinkt. Dan blijft er geen over. Ze gaan naar de prullenbak; daar kun je ze terughalen.",
    },
    allConfirm: { one: "Naar de prullenbak", other: "Alle {n} naar de prullenbak" },
  },
  notSame: "Niet gelijk",
  ignored: "Deze groep komt niet meer terug.",
  more: "Meer laden",
  loading: "De groepen laden…",
  failed: "De groepen konden niet laden.",
  empty: {
    exact: "Geen bestanden die precies gelijk zijn",
    near: "Geen foto's die erop lijken",
    nearHint: "Een nieuwe foto komt hier pas als hij gelezen is. Kijk later nog eens.",
  },
} as const;
