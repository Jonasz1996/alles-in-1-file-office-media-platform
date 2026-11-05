# Toetsenbord — Z4 (zoeken in de web)

Z4 maakt het zoeken af dat Z0 klaarzette: het veld bovenaan wordt een combobox met suggesties
(Keuze 46), en `/zoeken` krijgt chips, snippets, "Meer laden", de meldingen van Keuze 9 en de keuze
tussen zoeken op naam en tekst of op wat er op de foto staat (Keuze 47 en 81). *Mod* is Ctrl, op
een Mac ⌘. Er is geen nieuwe sneltoets: Mod+K en `/` bestonden al.

Dit is stap 1 van Z4. De tekstlaag met de markering van `?q=` in de PDF-viewer (Keuze 18) komt in
stap 2, met de rest van `e2e/test_zoek_z4.py`.

## Eén zoekveld (z0.md, Open 12)

In 0.7.1 had Bestanden een eigen zoekveld boven de lijst (`#zoekveld`, met `/`), en `/gedeeld` ook.
Sinds Z0 doet het veld bovenaan hetzelfde (Enter gaat naar `/zoeken?q=…`). **Z4 besliste: één
zoekveld, dat bovenaan.** Het veld boven de lijst is weg (op Bestanden en op `/gedeeld`); het veld
bovenaan heeft nu het id `zoekveld`.

- `/` op Bestanden zet de focus in het veld bovenaan. Zo blijft de toets van fase 3 werken
  ("/ naar het zoekveld", [toetsenbord-p2.md](../fase-3/toetsenbord-p2.md)).
- Mod+F blijft het naamfilter van de map.
- Keuze 45 en J9 zeggen "Op Bestanden blijft `/` het naamfilter". Dat klopte niet met de code: daar
  was `/` altijd het zoekveld en Mod+F het filter (z0-web.md, noot 11). Z4 houdt wat de code deed.
- Waarom: twee velden die hetzelfde doen, zijn twee tabstops en twee plekken om te zoeken. Het
  veld van Bestanden kostte ook ruimte in de begroting: Bestanden ging van 44,3 naar 43,8 kB (grens
  45).

## Het veld bovenaan (de combobox)

Het veld is een ARIA-combobox: de focus blijft altijd in het veld, de lijst is een `listbox`
("Suggesties") en de actieve optie staat in `aria-activedescendant`. De lijst laadt pas na de
eerste toets (een eigen chunk), en vraagt 120 ms na de laatste toets `GET /search/suggest`.

| Toets | Wat |
|---|---|
| Mod+K | de focus naar het veld (zoals in Z0) |
| typen | na 120 ms de lijst: Namen, Personen, Plaatsen, Tags, Albums (elk hooguit 5), en als laatste "Zoek '…' op wat er op je foto's staat" |
| ↓ | naar de volgende optie; op de laatste blijft hij staan. Is de lijst dicht (na Esc), dan gaat hij open op de eerste |
| ↑ | naar de vorige optie; vanaf de eerste terug naar het veld zelf (geen optie actief). Is de lijst dicht, dan gaat hij open op de laatste |
| Enter, met een actieve optie | die kiezen (zie hieronder); het veld wordt leeg |
| Enter, zonder actieve optie | naar `/zoeken?q=…`, zoals in Z0; de lijst gaat dicht |
| Esc, lijst open | de lijst dicht; de tekst blijft staan |
| Esc, lijst dicht | het veld leeg; nog een keer: de focus terug naar waar hij stond (Z0) |
| Tab / Shift+Tab | de lijst dicht, en verder naar het volgende element (de knop van je account) |
| Mod, Alt of Cmd erbij, of een teken in samenstelling | de lijst doet niets; de toets is van het veld of de browser |

Waar een keuze heen gaat:

- een **map**: `/map/<id>`; een **bestand**: de map waar het in staat, met het bestand open in de
  viewer (`?bestand=`). Is het bestand intussen weg of niet meer te zien, dan `/zoeken?q=<naam>`;
- een **persoon**: `/fotos/personen/<id>`; een **album**: `/album/<id>`;
- een **plaats** of een **tag**: `/zoeken` met dat filter (`?place=` of `?tag=`);
- de laatste regel: `/zoeken?q=…&mode=image` (zoeken op wat er op de foto staat).

Met de muis: de optie onder de muis wordt actief, een klik kiest hem; de focus blijft in het veld.

Een gast krijgt de laatste regel niet (hij heeft geen eigen foto's). Zonder index (de instelling
`search_index` uit, of terwijl de index herbouwd wordt) geeft de server geen suggesties; dan staat
alleen de laatste regel er. Kan de server niet antwoorden (geen verbinding, te veel vragen), dan
ook.

## De pagina /zoeken

Na het openen staat de focus op de kop (zoals elke pagina). Met Tab, in deze volgorde:

| Element | Toetsen |
|---|---|
| het veld "Zoekwoorden" | Enter zoekt; de filters blijven staan. Dezelfde vraag nog eens zoekt opnieuw |
| de knop Zoeken | Enter of Spatie |
| "Zoeken op": Namen en tekst / Wat er op de foto staat | ← en → kiezen (keuzerondjes); niet voor een gast. In de stand "Wat er op de foto staat" komt het raster van X3 (tot X3 er is: niets) |
| de chips | per filter een knop × ("Filter weghalen: …"); Enter haalt het filter weg. De focus gaat naar de chip die nu op die plek staat (of de vorige), en zonder chips naar "Filters" |
| "Filters" | Enter of Spatie opent en sluit het paneel |
| in het paneel: de soorten en de tags | Spatie zet een vinkje aan of uit; de vraag verandert meteen en de focus blijft op het vinkje |
| in het paneel: Vanaf en Tot en met | typen (dag, maand, jaar; Tab gaat eerst langs die drie delen), Enter zet de datum. Het veld verlaten zet hem ook |
| de personen en plaatsen die zo heten | links; Enter opent de persoon of zoekt in die plaats |
| de treffers | één tabstop, dan als de lijst van Bestanden: ↑ ↓ Home End PageUp PageDown, Enter opent (een bestand in de viewer, met `?bestand=` naast de vraag; een map gaat open), Spatie selecteert, Mod+A, Esc heft de selectie op, Shift+F10 of de menutoets voor het menu, en de toetsen van de acties |
| "Meer laden" | Enter haalt de volgende 30; de focus gaat naar de eerste nieuwe treffer |
| "Geen foto met deze naam? Zoek op wat er op je foto's staat." | een link naar de stand beeld (alleen als er geen foto bij de treffers zit; niet voor een gast) |

- De vraag staat in het adres. Terug (Alt+←) en Vooruit geven de vorige vraag met haar filters;
  ook een datumveld waar de focus nog in staat, volgt dan (gevonden bij het nalopen, zie hieronder).
- De meldingen ("De index is nog bezig (43%)", de twee teksten van Keuze 9, "Alleen op naam
  gezocht", "Niets gevonden") staan in één `role="status"`: een schermlezer leest ze voor zonder
  dat de focus verspringt.
- De snippet onder een treffer is tekst; de woorden uit de vraag staan in `<mark>`.
- In de viewer: Esc sluit hem en de focus staat weer op de treffer.
- Het adres van `/zoeken` met zijn filters komt niet in de toegangslog van de server (alles na `?`
  wordt `***`, `api/tests/search/test_zoek_adres.py`).

## Nagelopen

In de echte app (Chromium 141, de server van de e2e met de wereld van §4.2 en de boom van 1.000,
de build van deze tak), met alleen het toetsenbord; één keer `focus()` als begin (de kop, een rij
van de lijst, een knop), zoals in de andere e2e:

- [x] `/` op Bestanden: de focus in het veld bovenaan (de combobox); Mod+F: het naamfilter.
- [x] Mod+K, "utrecht": de lijst met alleen de laatste regel (de wereld zonder index). Met de
      index aan (`zoekindex="aan"`): de groepen Namen (vier bestanden) en Albums, en de laatste
      regel.
- [x] ↓ zet `aria-activedescendant` op de eerste optie (en `aria-selected`); ↑ gaat terug naar het
      veld; ↓ blijft op de laatste staan; Esc sluit, ↑ opent weer op de laatste.
- [x] Enter op de laatste regel: `/zoeken?q=utrecht&mode=image`, het veld bovenaan leeg, "Wat er op
      de foto staat" gekozen; ← op dat rondje: terug naar Namen en tekst, met de focus erop.
- [x] Enter op een bestand in de lijst: de map, met de viewer open op dat bestand; Esc sluit hem.
- [x] Tab met de lijst open: de lijst dicht, de focus op de knop van het account.
- [x] `/zoeken`: van de kop met Tab naar Zoekwoorden, Zoeken, Namen en tekst en Filters; Enter
      opent het paneel, Spatie op "Mappen" zet het filter (de focus blijft op het vinkje); Enter op
      de × van de chip haalt hem weg, de focus staat dan op Filters.
- [x] Vanaf: 01012024 en Enter geeft de chip "Vanaf 2024-01-01". Terug: het veld is leeg en Tab
      eruit doet geen nieuwe vraag. **Dat ging eerst mis** (het veld hield de datum en zette hem bij
      het verlaten terug); hersteld, met `e2e/test_zoek_z4.py`.
- [x] De lijst: ↓ en Enter op een foto opent de viewer met `?bestand=`; Esc zet de focus terug op
      die rij.
- [x] "Meer laden" (de boom, "jpg"): de tweede pagina met `offset=30`, de focus op treffer 31.
- [x] In het Engels (`test_integratie.py`, stap 11): `/zoeken?q=verslag` zonder Nederlands.

Niet nagelopen in stap 1: een gast (de vitest toetst dat hij de laatste regel en de keuze voor de
stand niet krijgt), een snippet met `<mark>` in de echte app (de werelden hebben geen tekst met die
woorden; de vitest toetst de opmaak), en de tekstlaag van de PDF-viewer (stap 2).
