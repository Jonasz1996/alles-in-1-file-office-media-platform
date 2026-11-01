# Toetsenbord — M8 (Koppelen, Bewerken en Nakijken)

De lijst Nakijken op de startpagina van een bibliotheek, de knoppen Koppelen en Bewerken op de
pagina van een item of serie, en de vensters Koppelen en Bewerken (`web/src/media/match/`) zijn
helemaal met het toetsenbord te bedienen. De teksten staan in het gebied `media`
(`media.match.*`, NL en EN). De e2e-test `test_twintig_films_vanzelf_en_met_de_hand`
(`e2e/test_media_m8.py`) loopt het pad Nakijken → item → Koppelen → kandidaat → Bewerken af met
alleen Tab, Enter en gewone letters.

Geen enkele toets van M8 gebruikt Ctrl, Alt of ⌘ als sneltoets (Ctrl+A in een veld is het gewone
selecteren van de browser), dus **Ctrl+N, Ctrl+T en Ctrl+W** en de andere sneltoetsen van de
browser en de app blijven werken. Zolang een venster open is, staat de sneltoets-scope "dialog"
bovenop: sneltoetsen van de pagina eronder werken dan niet.

## De lijst Nakijken (op `/media/bibliotheek/{id}`)

Alleen in een bibliotheek met films of series en online opzoeken, en alleen als er iets na te
kijken is. Een gewone sectie met de kop "Nakijken (n)".

| Toets | Wat |
|---|---|
| Tab / Shift+Tab | langs de regels: elke regel is een link (titel met jaar, of "Titel · S01E09") |
| Enter | naar het item of de serie (daar staat Koppelen) |
| Tab naar "Meer laden", Enter | de volgende 30 (alleen als er meer zijn) |

Na koppelen of verbeteren elders (`media.item`, `media.library`) laadt de lijst opnieuw.

## De knoppen op de pagina van een item of serie

Alleen voor wie mag bewerken (schrijfrecht). Ze staan in de rij met Afspelen.

| Knop | Wat | Wanneer |
|---|---|---|
| Koppelen | opent het venster Koppelen met TMDB | een film; op de pagina van een serie: de serie |
| Bewerken | opent het venster Gegevens bewerken | een film of aflevering |

Tab gaat erheen, Enter of spatie opent het venster. Na het sluiten staat de focus weer op de knop.

## Het venster Koppelen met TMDB

Bij het openen staat de focus in het zoekveld (Titel), met de titel van het item; het zoekt
meteen op de titel en het jaar van het item.

| Toets | Wat |
|---|---|
| typen, Enter (in Titel of Jaar) | opnieuw zoeken met deze titel en dit jaar (Jaar leeg: elk jaar) |
| Tab / Shift+Tab | Titel → Jaar → Zoeken → de kandidaten (elk een knop) → de link naar TMDB → Ontkoppelen (als die er is) → Annuleren; de knop Sluiten staat bovenaan, vóór Titel |
| Enter of spatie op een kandidaat | koppelen; een melding "Titel (jaar) is gekoppeld.", het venster sluit en de pagina laadt opnieuw |
| Enter op Ontkoppelen | terug naar de gegevens uit de naam van het bestand |
| Esc, of Annuleren | sluiten zonder iets te veranderen |

Een schermlezer hoort per kandidaat "Koppelen met Titel (jaar)"; de poster is versiering
(`alt=""`), de oorspronkelijke titel en de samenvatting staan erbij als tekst. Een fout van de
provider (uit, onbereikbaar, afremmen, de sleutel) staat als `role="alert"` in het venster; het
venster blijft open en de focus blijft waar hij was. Tijdens het zoeken is de lijst
`aria-busy`.

## Het venster Gegevens bewerken

Zodra de velden geladen zijn, staat de focus in Titel.

| Toets | Wat |
|---|---|
| Tab / Shift+Tab | per veld: het veld, dan het vinkje "… vastzetten"; daarna Annuleren en Opslaan; de knop Sluiten staat bovenaan, vóór Titel |
| typen | een veld wijzigen; het vinkje Vastzetten van dat veld gaat vanzelf aan |
| spatie op een vinkje | vastzetten of losmaken; los bij een gewijzigd veld zet de wijziging terug |
| Enter (in een tekstveld, niet in Samenvatting) | opslaan; een melding "De gegevens zijn opgeslagen." |
| Esc, of Annuleren | sluiten zonder op te slaan |

Een ongeldig veld (lege titel, jaar buiten 1800–2200, seizoen of aflevering te groot) geeft een
melding als `role="alert"`; er gaat niets naar de server. De sorteertitel heeft een hint
("Leeg: automatisch uit de titel.") via `aria-describedby`.

## De bronvermelding

"Gegevens: TMDB" is een gewone link (opent TMDB in een nieuw tabblad); de zin van TMDB staat
erbij in het Engels (`lang="en"`). Op de pagina van een item staat hij bij "Gegevens" als die van
TMDB komen; in het venster Koppelen altijd.

## Wat niet van M8 is

- Het raster van de bibliotheek, de pagina van het item en Afspelen:
  [toetsenbord-m2.md](toetsenbord-m2.md).
- De sleutel van TMDB instellen, testen en weghalen op /beheer, en de bronvermelding daar: M10.
