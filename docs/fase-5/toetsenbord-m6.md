# Toetsenbord — M6 (de speler)

De speler is helemaal met het toetsenbord te bedienen: op de afspeelpagina `/kijken/:node` en in
de viewer van Bestanden. De tabel staat in `web/src/player/keys.ts` (`KEYMAP`, Keuze 33); de
labels (ook voor de lijst van `?`) staan in het gebied `player` (`player.keys.*`). De e2e-test
`test_alleen_met_het_toetsenbord` (`e2e/test_media_m6.py`) loopt deze lijst af op `/kijken`.

*Mod* is Ctrl, op een Mac ⌘. **Ctrl+N, Ctrl+T en Ctrl+W gebruikt de speler nooit**: die zijn van
de browser. Geen enkele toets van de speler gebruikt Ctrl, Alt of ⌘; met een van die toetsen erbij
doet de speler niets (ook de tijdbalk niet), dus de sneltoetsen van de browser en van de app
blijven werken.

Een toets doet niets als de focus in een tekstveld staat (de regel van `ui/keys`). Op de knoppen,
de tijdbalk en de schuif voor het volume werken de toetsen van de speler wel, behalve waar dat
element de toets zelf gebruikt (hieronder).

## De afspeelpagina /kijken (scope `player`)

| Toets | Wat | Wanneer |
|---|---|---|
| Spatie, K | spelen of pauzeren | altijd |
| J / L | 10 s terug / vooruit | altijd |
| ← / → | 5 s terug / vooruit | altijd (op de tijdbalk: zie onder) |
| ↑ / ↓ | volume 10 % harder / zachter (↑ zet het geluid ook weer aan) | altijd |
| M | geluid uit of aan | altijd |
| F | volledig scherm aan of uit (op de speler zelf, met de bediening erin) | altijd |
| C | ondertitels aan of uit (terug naar het laatst gekozen spoor; een beeldspoor dat ingebrand wordt, vraagt een nieuwe sessie) | als er ondertitels zijn |
| A | het volgende audiospoor (een nieuwe sessie op dezelfde plek) | als er meer dan één audiospoor is |
| P | beeld-in-beeld aan of uit | als de browser het kan |
| Page Up / Page Down | vorig / volgend hoofdstuk (binnen 3 s na het begin van een hoofdstuk: het hoofdstuk daarvoor) | als er hoofdstukken zijn |
| 0 … 9 | naar 0 … 90 % van de duur (ook met het numerieke deel) | altijd |
| Home / End | naar het begin / het eind | altijd (op de tijdbalk: zie onder) |
| N | de volgende aflevering of het volgende deel, meteen | alleen als er een volgende is |
| I | het paneel Info aan of uit (de manier van afspelen, de redenen, het formaat, het beeld, de buffer) | als er een sessie is |
| Esc | in deze volgorde: volledig scherm uit; anders de kaart "Volgende aflevering" annuleren; anders Info dicht; anders terug (naar het item in de bibliotheek, of de vorige pagina) | altijd |
| ? | de lijst van alle sneltoetsen (de hulp van de app, scope global) | altijd |

Een toets die "alleen als" heeft, staat niet in de hulp en doet niets zolang het niet kan (N bij
de laatste aflevering, A met één audiospoor).

## In de viewer van Bestanden (scope `viewer`)

In de viewer krijgt de speler alleen **Spatie, K, J, L, M, C, A, P en 0 … 9** (Keuze 33). ←, →,
Home, End, F, I, D, E en ? blijven van de viewer (vorige en volgende, het eerste en laatste
bestand, volledig scherm van de viewer, Gegevens, downloaden, enzovoort), en N bestaat er niet.
Esc sluit de viewer (van de viewer, niet van de speler); de speler sluit dan zijn sessie.

Lukt afspelen aan het begin niet (play zegt nee, of de browser kan het niet), dan toont de viewer
de `<video>` van fase 3 met zijn eigen toetsen (Spatie, K, J, L, M) of zijn eigen melding.

## Met Tab door de bediening

De bediening staat onder in beeld en verdwijnt na een paar seconden zonder beweging, maar niet
zolang een menu open is of de focus (zichtbaar, van het toetsenbord) in de speler staat; elke
toets, ook Tab, laat hem weer zien. De volgorde met Tab:

1. (op `/kijken`) **Terug** en de titel (de titel krijgt na het laden de focus, zoals elke kop van
   een pagina; zelf is hij niet met Tab te bereiken);
2. **Afspelen** (groot, midden in beeld) als de video gepauzeerd staat;
3. de kaart **Volgende aflevering** als die er staat: **Nu afspelen**, **Annuleren** en het vakje
   **Automatisch verder** (Spatie);
4. het paneel **Info** als het open staat: de knop **Sluiten**;
5. de **Tijdbalk** (een slider): ← en → zoeken 5 s, Home naar het begin, End naar het eind. Een
   schermlezer hoort de tijd, de duur en het hoofdstuk;
6. **Afspelen/Pauzeren**, **10 seconden terug**, **10 seconden vooruit**, **Geluid uit/aan**;
7. de schuif **Volume** (← → ↑ ↓ zoals elke schuif, met de waarde in procenten);
8. **Ondertitels**, **Audiospoor** en **Kwaliteit**: elk alleen als er iets te kiezen is;
9. **Info**, **Beeld-in-beeld** (als de browser het kan) en **Volledig scherm**.

Elke knop heeft een naam (`aria-label`) en een tooltip met dezelfde tekst. Een knop die aan of uit
staat, heeft `aria-pressed` (Info).

## De menu's Ondertitels, Audiospoor en Kwaliteit

- Enter of Spatie op de knop opent het menu (`aria-haspopup="menu"`, `aria-expanded`); de focus
  gaat naar de gekozen keuze (`menuitemradio` met `aria-checked`).
- ↑ / ↓ lopen langs de keuzes (rond), Home en End naar de eerste en de laatste.
- Enter of Spatie kiest; het menu sluit en de focus gaat terug naar de knop.
- Esc sluit zonder te kiezen, de focus terug op de knop. Tab sluit ook.
- Zolang een menu open is, doen de sneltoetsen van de speler niets (de scope `menu`).
- Het menu staat in de speler zelf, zodat het ook in volledig scherm te zien is.
- Een keuze met een tweede regel zegt wat ze kost (een beeldondertitel: "wordt ingebrand, kost
  omzetten").

## Wat de speler zegt

Een verborgen `role="status"` (`aria-live="polite"`) zegt wat een keuze deed als de nieuwe sessie
speelt (ondertitels aan of uit, een ander audiospoor, een andere kwaliteit), wat er vanzelf
gebeurt ("Het haperde: kwaliteit verlaagd naar 480p" na twee keer haperen) en een ondertitel die
niet laadt. De aftelling van de kaart staat als tekst op de kaart (die een `region` met een naam
is) en wordt niet elke seconde voorgelezen. Meldingen in beeld (opnieuw verbinden, de server is te
druk, de ondertitel wordt voorbereid) zijn ook `role="status"`; een stop (geen toegang meer) is
`role="alert"`.

## Checklist

- [x] Elke actie van de speler heeft een toets of is met Tab en Enter te bereiken.
- [x] Geen Ctrl+N, Ctrl+T of Ctrl+W, en geen toets met Ctrl, Alt of ⌘.
- [x] Niets als de focus in een tekstveld staat.
- [x] De combinaties van global, viewer, player en `/kijken` zijn uniek of bewust overschreven
      (`keys.test.ts`).
- [x] De viewer krijgt alleen Spatie, K, J, L, M, C, A, P en 0 … 9.
- [x] Esc in de volgorde volledig scherm, kaart, Info, terug.
- [x] De menu's: focus erin, ↑ ↓ Home End, Enter/Spatie, Esc en Tab sluiten, de focus terug.
- [x] De tijdbalk als slider met ← → Home End en een tekst voor de schermlezer.
- [x] Ook in volledig scherm is alles bereikbaar (de menu's en de kaart staan in de speler).
- [x] e2e: `test_alleen_met_het_toetsenbord`, `test_de_kaart_volgende_aflevering` (Esc, N),
      `test_sprites_bij_hover_en_hoofdstukken` (Page Down) en `test_pip_vraagt_beeld_in_beeld` (P).
