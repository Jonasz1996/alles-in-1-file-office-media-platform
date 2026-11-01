# Toetsenbord — M10 (het tabblad Media op /beheer)

Het tabblad Media op `/beheer/media` (`web/src/admin/MediaTab.tsx`) is helemaal met het
toetsenbord te bedienen. Er zijn geen eigen sneltoetsen: alles is een gewone knop, een
keuzelijst, een vinkje of een invoerveld, in de volgorde van de pagina. De teksten staan in het
gebied `admin` (`admin.media.*`, NL en EN; `admin/media.test.ts` toetst dat elke staat, reden en
soort in beide talen een tekst heeft). De e2e `test_het_tabblad_met_het_toetsenbord` en
`test_een_sessie_stoppen` (`e2e/test_media_m10.py`) lopen het eerste deel van deze lijst af.

Geen enkele toets van het tabblad gebruikt Ctrl of ⌘, dus **Ctrl+N, Ctrl+T en Ctrl+W** en de
andere sneltoetsen van de browser en de app blijven werken.

## Naar het tabblad

| Toets | Wat |
|---|---|
| Alt+8 | naar `/beheer/media`, overal op `/beheer` (de haak van M0 in `Beheer.tsx`; Alt+8 blijft Alt+8, ook zonder office). Alleen als de server het overzicht geeft |
| Tab / Shift+Tab in de rij tabbladen | ←/→ tussen de tabbladen, Enter opent (de tabbladen van fase 3) |

## Op het tabblad

| Toets | Wat |
|---|---|
| Tab / Shift+Tab | door de knoppen en velden, van boven naar beneden: Meten, Stop per sessie, Mislukte opnieuw, Opnieuw scannen per bibliotheek, Opruimen per soort cache, de sleutel van TMDB (Bewaren, Testen, Weghalen), de import, de instellingen en de belastingtest |
| Enter of spatie op een knop | de knop. Stop, Opruimen en Sleutel weghalen vragen eerst een bevestiging |
| In de bevestiging | Enter bevestigt, Esc sluit zonder iets te doen; de focus gaat terug naar de knop |
| Enter in een formulier | versturen (de sleutel, de import, de instellingen, de belastingtest) |
| Spatie op een vinkje of keuzerondje | aan of uit (Media aan, Publieke links mogen omzetten, HDR op 1080, MusicBrainz; Kopiëren of Verplaatsen) |
| ↑ / ↓ in een keuzelijst | de bronmap of de eigenaar van de import |
| ↑ / ↓ in een getalveld | een stap omhoog of omlaag (cpu-budget per 0,5 core) |
| Enter op "Overgeslagen" | de lijst van wat de import oversloeg open of dicht (`<details>`) |

Na een knop die iets deed, blijft de focus op die knop. Gaat de knop weg (een sessie die gestopt
is, Mislukte opnieuw die verdwijnt), dan gaat de focus naar de kop van het blok, niet naar het
begin van de pagina. Een fout staat onder het blok als melding (`role="alert"`), een gelukte
actie als toast. De voortgang van de import en de staat van de belastingtest zijn `role="status"`.

## De doelmap van de import kiezen

"Kies een map" opent de mapkiezer van fase 2 (`FolderPicker.tsx`, met zijn eigen toetsen: ↑/↓,
Home en End door de mappen, Enter of spatie opent een map, typen springt naar een naam, Tab naar
de knoppen van de dialoog, Esc sluit). Alleen mappen van jezelf zijn een doel; bij een andere eigenaar gaat de import in diens
hoofdmap (een submap van een ander kan met `app.cli media-import --naar`).

## Wat de speler doet na Stop

Stop op `/beheer` sluit de sessie; de speler van de kijker stopt en zegt "De beheerder heeft het
afspelen gestopt." (M6, `player.stopped.stopped`) en vraagt zelf geen nieuwe sessie. De toetsen
van de speler staan in [toetsenbord-m6.md](toetsenbord-m6.md).
