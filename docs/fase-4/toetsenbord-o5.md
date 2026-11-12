# Toetsenbord — O5 (beheer en bewaking)

O5 vult het tabblad Office op `/beheer`. Het voegt geen sneltoets toe: Alt+7 (naar het tabblad)
en de rij tabbladen zijn de haak van O0 in `Beheer.tsx` (`toetsenbord-o0.md`). Het tabblad is er
alleen als office ingesteld is; anders vangt de app Alt+7 niet, en is `/beheer` precies fase 3.
Alles op het tabblad zijn gewone knoppen, een vinkje en invoervelden, in de volgorde van de
pagina. Ctrl+N, Ctrl+T en Ctrl+W gebruikt de app nooit.

## /beheer/office (scope `admin`)

| Toets | Wat | Wanneer |
|---|---|---|
| Alt+7 | naar het tabblad Office (O0) | office is ingesteld (ook uitgezet of onbereikbaar) |
| Alt+1 … Alt+6 | naar de andere tabbladen (fase 3) | ongewijzigd |
| Tab, Shift+Tab | langs de knoppen en velden, in deze volgorde (zie hieronder) | |
| Enter of Spatie | op een knop: die knop | |
| Spatie | op het vinkje "Bewerken en omzetten naar PDF aan": aan of uit (nog niet opgeslagen) | |
| Enter | in een van de drie getallen: het formulier opslaan, zoals Opslaan | |
| ↑ ↓ | in een getal: één erbij of eraf (het gewone invoerveld van de browser) | |

**De volgorde van Tab** (na de rij tabbladen):

1. **Nu tussentijds opslaan.** Staat uit (en wordt overgeslagen) als er geen document open is.
   Daarna meldt het meldingengebied "Opgeslagen: x van y" (of "Er was niets nieuws om op te
   slaan.").
2. **Per rij onder "Niet opgeslagen": Terugzetten en Verwijderen.** Hun naam voor een
   schermlezer is "<naam> terugzetten" en "<naam> verwijderen". Terugzetten is er niet als er
   geen eigenaar meer is, als het bestand definitief weg is, of als de app de key niet kent.
   - **Verwijderen** opent een bevestiging. De focus staat eerst op **Annuleren**; Tab naar
     **Verwijderen** en Enter verwijdert, Escape of Enter op Annuleren doet niets (de focus gaat
     terug naar de knop).
   - **Gelukt** (de rij is weg): "Teruggezet als <naam>." of "Verwijderd." in het
     meldingengebied, en de focus staat op de kop **Niet opgeslagen**. De volgende Tab is de
     knop van de volgende rij.
   - **Mislukt** (bijvoorbeeld "Onvoldoende quotum"): de fout staat boven de tabel (`role=alert`,
     wordt voorgelezen), de rij blijft, en de focus blijft op de knop. Nog eens Enter probeert
     het opnieuw.
3. **Mislukte opnieuw** onder "Omzetten naar PDF", alleen als er mislukte conversies zijn. Daarna
   "x documenten gaan opnieuw." en, als de knop weg is, de focus op de kop **Omzetten naar PDF**.
4. **Het vinkje** "Bewerken en omzetten naar PDF aan" (Spatie), met de uitleg als beschrijving.
5. **De drie getallen:** "Hooguit zoveel bewerkers op één document", "Tussentijds opslaan na zoveel
   minuten" en "Een nieuwe versie na zoveel minuten". Elk met de grenzen als hint. Een waarde buiten
   de grenzen geeft de fout bij het veld, en er wordt niets opgeslagen.
6. **Opslaan.** "Instellingen opgeslagen." in het meldingengebied; de focus blijft op de knop.

De ververs van elke 5 seconden verplaatst de focus niet, en laat staan wat je in het formulier
typt.

## Gecontroleerd in tests

- `e2e/test_office_o5.py` (nep-DS, in de echte web-app):
  - **Alt+7** vanaf `/beheer` opent het tabblad (`aria-current="page"` op de link, de kop
    "Office: Gezond"), in het Nederlands en in het Engels ("Office: Healthy").
  - **Office uit en weer aan met alleen het toetsenbord:** Tab naar het vinkje, Spatie, Tab
    langs de drie velden naar Opslaan, Enter: `office/status` zegt `enabled` en `editing`
    onwaar. Shift+Tab terug naar het vinkje, Spatie, Tab naar het eerste veld, Enter: weer aan.
  - **De grens:** in het veld invullen en Enter slaat op.
  - **Niet opgeslagen:** Tab naar "weg-… verwijderen", Enter; in de bevestiging de focus op
    Annuleren, Tab naar Verwijderen, Enter; de rij is weg en de focus staat op de kop "Niet
    opgeslagen". Tab is dan "vol-… terugzetten"; Enter zonder ruimte geeft "Onvoldoende quotum"
    en de focus blijft op de knop; na het vrijmaken van ruimte Enter: teruggezet, en de focus
    op de kop.
- `web/src/admin/office.test.ts`: de teksten in het Nederlands en het Engels, wanneer
  Terugzetten er is (`canRestore`), en de grenzen van de velden (`parseOfficeNumber`).
- `e2e/test_office_o5_real.py` (echte DS): Alt+7 naar het tabblad terwijl twee mensen in de
  echte editor zitten.

## Nagelopen

- [x] Elke knop en elk veld met Tab te bereiken, in de volgorde van de pagina; geen `tabindex`
      boven 0. De koppen van drie blokken hebben `tabindex="-1"`: alleen om de focus te
      ontvangen als een knop met zijn rij verdwijnt, niet in de Tab-volgorde.
- [x] Elke knop in een tabel heeft een naam met het document erin ("<naam> terugzetten").
- [x] De bevestiging van Verwijderen begint op Annuleren (de veilige knop), en Escape sluit hem.
- [x] Elke tekst via `t()`, in het Nederlands en het Engels (`check:i18n --strict`: 0
      meldingen). Alleen de commando's `curl -fsSL …/install.sh | OFFICE=uit bash` en
      `… | OFFICE=aan bash` staan letterlijk in de code: dat typt de beheerder zo in de
      console.
- [x] Geen nieuwe sneltoets; Alt+7 is van O0. Geen Ctrl+N, Ctrl+T of Ctrl+W.
- [x] Fouten en meldingen worden voorgelezen: fouten met `role=alert`, gelukt in het
      meldingengebied. "Niet bereikbaar sinds …" wordt één keer voorgelezen, niet bij elke
      ververs (5 s): hoe lang geleden staat naast de melding, niet erin.
- [x] Geen inline handlers of `style`-attributen (de CSP van de app blijft).
