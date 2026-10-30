# Toetsenbord — M0 (de haken van media in het web)

M0 zet alleen de haken: de menu-items, de actie, de keuze in de viewer en het tabblad, die er
zijn zodra media aan staat. Wat ze doen, maken M2 (bibliotheken), M6 (de speler), M9 (muziek) en
M10 (het tabblad) af. Zolang `GET /api/v1/media/libraries` niet met 200 antwoordt (de stubs van
M0 geven 503 `media_off`), is er niets van, en gedragen alle toetsen zich precies als in fase 4:
Alt+8 vangt de app dan niet. *Mod* is Ctrl, op een Mac ⌘. Ctrl+N, Ctrl+T en Ctrl+W gebruikt de
app nooit.

## De balk bovenaan

- Media en Muziek staan als gewone links in het hoofdmenu, na Foto's: met Tab bereikbaar, Enter
  volgt de link, `aria-current="page"` op de pagina waar je bent (ook op `/kijken`, dat onder
  Media valt). Een gast ziet ze alleen met een gedeelde bibliotheek.
- Geen nieuwe sneltoets.

## Bestanden (scope `browser`)

| Toets | Wat | Wanneer |
|---|---|---|
| — | "Als bibliotheek gebruiken" | in het contextmenu (Shift+F10 of de menutoets) en het menu ⋯ van één map van jezelf, als media aan staat; geen eigen sneltoets |

- De actie staat in de groep delen, direct na "Toevoegen aan album". In het menu: ↑ ↓, Home,
  End, Enter; Escape sluit en zet de focus terug.
- Is de map al een bibliotheek, dan staat het item uitgeschakeld met de reden ("Deze map is al
  de bibliotheek Films").
- In M0 opent de keuze nog geen dialoog maar een melding; de dialoog (het soort kiezen) is van
  M2 en volgt de regels van `ui/Dialog` (focus erin, Escape sluit, focus terug).

## De viewer (scope `viewer`)

- Een video laadt de speler van M6 alleen als je ingelogd bent, het de huidige versie is en media
  aan staat. Tot M6 toont die de `<video>` van fase 3, met dezelfde toetsen: Spatie en K (spelen
  en pauze), J en L (10 s terug en vooruit) en M (geluid). ←, →, Home, End, F, I, D, E en ? blijven
  van de viewer.
- Terwijl het chunk van de speler laadt, staat er een spinner met de tekst "De speler laden…";
  de focus blijft waar hij was.
- Via een publieke link, voor audio en voor een eerdere versie verandert er niets.

## De pagina's /media, /kijken en /muziek

In M0 een lege pagina: de kop (die na navigeren de focus krijgt, zoals elke pagina) en de tekst
dat de pagina later komt. Zonder media de pagina "niet gevonden" van de schil, met de link Naar
het begin (Tab, Enter). De toetsen van de afspeelpagina (Keuze 33) komen met M6, die van muziek
met M9.

## De muziekbalk

In M0 alleen de plek: hij laadt pas als er muziek speelt (`musicBar.show()`, M9), als een
`<section>` met het label "Muziekspeler", buiten `#inhoud`. Hij blijft staan bij navigeren.

## /beheer (scope `admin`)

| Toets | Wat | Wanneer |
|---|---|---|
| Alt+8 | naar het tabblad Media (`/beheer/media`) | alleen als `GET /api/v1/admin/media` antwoordt; anders vangt de app Alt+8 niet en is `/beheer/media` het overzicht |

Alt+1 … Alt+7 blijven Overzicht, Gebruikers, Opslag, Taken, Groepen, Beveiliging en Office.
Alt+8 is Media, ook als het tabblad Office er niet is (dan is Media het zevende tabblad in de rij,
maar de toets blijft Alt+8). De inhoud is van M10; in M0 alleen de tekst dat het overzicht later
komt.

## Nagelopen

- [x] Zonder media: geen Media of Muziek in het menu, geen "Als bibliotheek gebruiken", de
      `<video>` van fase 3, geen tabblad Media; Alt+8 doet niets (`browser/actions.test.ts`,
      `beheer.test.ts`, de e2e van fase 3 en 4 ongewijzigd groen).
- [x] Geen nieuwe sneltoets in de scope `browser`; de test "elke actie met een sneltoets…" blijft
      groen. Alt+8 botst met niets in de scope `admin` (`beheer.test.ts`: Alt+1 … Alt+8 elk vast
      bij één tabblad).
- [x] De sneltoetsen van de `<video>` staan alleen in het deel van fase 3, niet in de keuze:
      de speler van M6 registreert zijn eigen toetsen.
- [x] Elke tekst van de haken via `t()`, in het Nederlands en het Engels (`check:i18n --strict`).
