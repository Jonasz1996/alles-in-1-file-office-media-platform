# P8 fase6-rest: de rest van fase 6 die nu kan (0.8.2)

Branch `wip/i3-fase6-rest`, begin `debd1d96`. Pakket P8 uit het plan voor 0.8.2: de open punten
van fase 6 die zonder een wijziging aan een contract of een nieuwe migratie kunnen. Wat niet kan,
staat onder [Uitgesteld](#uitgesteld-met-de-reden), elk met de reden. `ci.yml` is niet veranderd.

## Wat er gebouwd is

| Wat | Commit | Getoetst met |
|---|---|---|
| **GPS met precies 60,0 seconden** telt als de volgende minuut (54° 47' 60" = 54° 48'). Boven 60 seconden, en 60 minuten, blijft het geen plek. Eerder gaf een afgeronde waarde geen plek ([metingen.md](../metingen.md), Bevinding 5). | `45f80aa3` | `test_render_meta.py`: 60,0, 60/1, 600/10 en 59,999 geven een plek; 54° 59' 60" is 55,0; 60,5, 601/10, 61,0 en 3.600 niet; 60 minuten niet. `test_geo_read.py` neemt 60,5 als foute waarde. |
| **OCR na een upload van een inhoud die er al was** ([x1.md](../x1.md), Open 4). `add_ref` meldt de inhoud na de commit via een nieuwe haak, `search.hooks.blob_reused`. OCR zet de foto in de rij als de eigenaar "Tekst in foto's herkennen" aan heeft en een van zijn nodes met die inhoud nog geen tekst heeft (`store.owner_wants`, dezelfde regel als `wanting_shas`). Een foto die voor hem al tekst heeft, leest hij niet opnieuw. | `4f6864a8` | `test_een_inhoud_die_er_al_was_leest_ocr_voor_de_nieuwe_eigenaar` (`tests/ocr/test_ocr_step.py`), `test_een_inhoud_die_er_al_was_meldt_blob_reused` (`tests/search/test_hotspots.py`) |
| **"Dubbel" in het hoofdmenu**, na Prullenbak, niet voor een gast ([z5.md](../z5.md), Open). `/dubbel` heeft een eigen onderdeel `dupes`, zodat de link daar als huidige pagina staat. | `2f53a424` | `router.test.ts`; `e2e/test_uit080.py` (de link is de enige toevoeging van 0.8.2 op elke pagina met het menu: `MENU_082`) |
| **De viewer is 100% breed, niet 100vw** ([z4.md](../z4.md), Open). `base.css` houdt altijd ruimte voor de schuifbalk; met 100vw viel de rechterrand van de viewer daaronder. | `b2591101` | `viewerCss.test.ts`; gemeten in de Chromium van de sandbox met echte schuifbalken: zonder de regel voor de goot eindigt 100vw 15 px onder de schuifbalk, 100% precies aan de rand |
| **De tab Zoeken in Beheer luistert naar `search.progress`** ([z0-web.md](../z0-web.md), Open 6; [z7.md](../z7.md)). Een bericht over het herbouwen dat de tab toont, zet de voortgang meteen bij (tussen 0 en 1). Een ander soort bericht wacht op de volgende peiling; de peiling van 5 s blijft. | `3abfd3bc` | `searchTab.test.ts`, blok "search.progress op de tab" |
| **De rij OCR voor wie een PDF alleen mag lezen** ([x1.md](../x1.md), Open 7). Hij ziet de stand, zonder knop. Een foto blijft van de eigenaar: zijn tekst hoort bij de node van de eigenaar (Keuze 23), en de API geeft een ander `skip`. | `82988365` | `ocr.test.ts`, blok "wie de rij ziet"; `e2e/test_zoek_ocr.py` (Bram als lezer van de scan van Eva; job `ocr`) |
| **Herinneringen per map verbergen** ([z6.md](../z6.md), Open). Naast de kop van de herinneringen staat "Een map verbergen…": de mapkiezer van Bestanden (lazy), alleen een eigen map en niet Mijn bestanden zelf. Daarna, net als bij een dag: een melding met Ongedaan maken en de focus op de kop van de pagina. | `7bd9d0cf` | `memories.test.ts` (`folderProblem` en de bron) |
| **"Meer laden" in het raster van gezichten**: na de laatste pagina is de knop weg; de focus staat dan op het eerste nieuwe gezicht. Eerder viel hij op de pagina. | zie [Commits](#commits) | de e2e hieronder; `faces.test.ts` |
| **e2e: de rest van "Wie is dit?"** met alleen het toetsenbord: End en Home, "Meer laden", Spatie (deselecteren en weer selecteren), V, Bevestigen met alleen wat geselecteerd en niet verborgen is, en "Niet benoemde gezichten wissen" (Esc doet niets, Wissen wist). | zie [Commits](#commits) | `test_wie_is_dit_met_v_spatie_home_end_meer_laden_en_wissen` (`e2e/test_zoek_gezichten.py`, marker `analyse`) |
| **e2e: "Meer laden" in de lijst van mislukte op `/beheer`.** 61 inhouden van de boom krijgen hun stap text op failed (rechtstreeks in SQLite, zoals de fixture); de lijst toont 50, Enter haalt de andere 11 met de cursor van de server; daarna staat de focus op de kop. | zie [Commits](#commits) | `test_meer_laden_in_de_lijst_van_mislukte` (`e2e/test_zoek_beheer.py`) |
| **e2e: "Wissen" op de pagina van een persoon** (review integratie-2, C5). Hangt GET `/me/photo-settings`, dan komt het dialoog na 3 s toch, met de zin van 0.8.0; een tweede Shift+Delete in die tijd vraagt niets (één verzoek, één dialoog). Antwoordt de server, dan komt het dialoog meteen. | zie [Commits](#commits) | `test_wissen_wacht_hooguit_3_s_en_vraagt_een_keer` (`e2e/test_zoek_personen.py`) |

## Afwijkingen

1. **De grenzen van OCR per gebruiker blijven in het geheugen van het proces** ([x1.md](../x1.md),
   Open 2). Zo werken alle grenzen van de app (`security.RateLimiter`). Na een herstart begint de
   telling opnieuw; dat geeft hooguit één dag of uur extra. Een grens die een herstart overleeft,
   vraagt een tabel (een migratie) en hoort bij het harden in fase 8
   ([integratie.md](../integratie.md), de regel met `files.free_name`).
2. **`pages_queued` op `/beheer` telt inhouden, geen pagina's** ([x1.md](../x1.md), Open 3). De
   pagina's van een PDF kent de app pas na de eerste blik van de worker. De tab zegt het al ("1
   inhoud (een PDF of …)"). Pagina's tellen vraagt een veld in het contract van `/admin/search`.
3. **GPS: geen nieuwe versie van photometa.** Dan zou de update elke foto opnieuw lezen. Alleen
   nieuwe foto's en foto's die opnieuw gelezen worden, krijgen de plek. Een beheerder die het voor
   alle foto's wil, kiest op `/beheer` "Plek, camera en datum opnieuw lezen" (dat zet
   `meta_version` een terug, en photometa leest elke foto opnieuw).
4. **Een haak erbij in `app/search/hooks.py`**: `register_blob_reused`, `blob_reused` en
   `blob_reused_after_commit`, naast `blob_added` en met hetzelfde patroon (in `session.info`, weg
   bij een rollback, een fout in de haak logt alleen). Zonder die haak kan OCR niet horen dat
   `add_ref` een bestaande inhoud gebruikt.
5. **`web/vite.config.ts`**: `test.css.include` laat alleen `?raw` van een stylesheet door.
   Vitest maakte van elke stylesheet een lege module, ook van `x.css?raw`. Een test op de tekst van
   een stylesheet zag dus `""`: ook de controle op CSS in `beeld.test.ts` was leeg, en is nu echt.
   Een gewone import van een stylesheet blijft leeg. Alleen vitest; de build ziet het niet.
6. **Een nieuw onderdeel `dupes` in `NavSection`** (`router.ts`). Anders stond Bestanden als
   huidige pagina op `/dubbel`. `test_uit080.py` legt de link vast als `MENU_082`: precies één
   link Dubbel na Prullenbak op elke pagina met het menu, behalve voor de gast in de viewer.
7. **De "Meer laden" van de gezichten knipt de test zelf** (`Knip`): de server geeft een groep in
   pagina's van 60, en 61 foto's door de nep-worker kost te lang. De test haalt bij elk verzoek de
   hele groep bij de server (zonder cursor) en geeft er een stuk van, met een eigen cursor. De
   server ziet nooit een cursor van de test. De pagina's van de server zelf toetst
   `api/tests/people/faces/`.
8. **De test van "Wissen" op de pagina van een persoon is een e2e, geen vitest.** Vitest draait
   hier zonder DOM (geen jsdom, en een nieuwe afhankelijkheid mag niet). Playwright houdt het
   verzoek vast met `context.route`.
9. **Open 7 van x1.md: wie de rij ziet.** Het plan zegt "the OCR row only for users who may ask".
   Gebouwd is: bij een PDF ziet iedere lezer de rij (de stand), de knop blijft voor wie mag
   schrijven. Bij een foto ziet alleen de eigenaar hem, zoals eerst.
10. **Het budget `faces` (12 kB gzip) had 1 byte over** (11.999 B op `debd1d96`). De focus na de
    laatste "Meer laden" kost 17 B. Ruimte kwam van drie herschrijvingen die hetzelfde doen:
    in `gridKey` de tweede controle op Ctrl en Cmd (die kan daar alleen nog met Alt), in `choose`
    één aanroep voor ja en nee, en `!x.length` in plaats van `x.length === 0`. Nu 11.995 B. De
    focus van het openen en die na "Meer laden" delen één effect (`next`).

## Uitgesteld, met de reden

| Wat | Waarom niet nu | Waar het staat |
|---|---|---|
| "Niet gelijk" op `/dubbel` ongedaan maken | vraagt een route erbij (een `ignore` terugdraaien): een wijziging aan het contract. Fase 7. | [z5.md](../z5.md), Open; README, "Bekende grenzen" |
| Het paginanummer bij een treffer | een veld erbij in de treffers van `/search`: contract. Fase 7. | [z4.md](../z4.md); [integratie.md](../integratie.md) |
| 204 in plaats van 404 voor `/pages/{n}/words` | een ander antwoord op een bestaande route: contract. Fase 7. | [z4.md](../z4.md), Open |
| Een eigen zin bij een PDF zonder gescande pagina | vraagt een code in de API die §3.4 niet kent: contract. Fase 7. | [x1.md](../x1.md), Open 6 |
| `files.free_name` is kwadratisch | pas merkbaar bij honderden kopieën van dezelfde naam in één map; hoort bij het harden in fase 8. | [z1.md](../z1.md), Open; [integratie.md](../integratie.md) |
| Meer dan 64 gedeelde wortels | de tijdsgrens houdt het antwoord binnen 120 ms (met `partial`); zo'n gebruiker is er in §4.4 niet. Sneller vraagt een andere vorm van de vraag, met eigen metingen. | [z1.md](../z1.md), Open |
| Een index voor alleen een datumfilter op foto's | een index op (`ftype`, `taken_day`) is een schemawijziging: een nieuwe migratie voor SQLite, buiten dit pakket. De p95 van de filters haalt de poort. | [z1.md](../z1.md), Open |
| a0, Open 7 (`gen` telt een andere mime zonder nieuwe inhoud niet) | komt in de app niet voor (a0.md zegt het zelf); X3 kan het nakijken als het bestand per eigenaar verandert. | [a0.md](../a0.md), Open 7 |
| a0, Open 8 en 18 (wie een versie maakte) | vraagt een kolom (`face_scan.version`), dus een migratie. `test_een_versie_van_een_link_terugzetten` legt het gedrag van nu vast. | [a0.md](../a0.md), Open 8 en 18 |
| a0, Open 11 (het checkpoint bij elke start) | werkt: met een lezer open probeert het tot 10 s en start de app daarna gewoon. Anders starten is een risico zonder een gemeten probleem. | [a0.md](../a0.md), Open 11 |
| a0, Open 13 (`python -m app.cli` start in ±3,7 s) | alleen het commando, niet de app; een andere volgorde van imports in `cli.py` raakt een bestand dat meer pakketten van 0.8.2 veranderen. | [a0.md](../a0.md), Open 13 |
| a0, Open 16 (het geheugen met `temp_store=2` per route) | meten vraagt echte gegevens en de LXC; de sandbox zegt daar niets over. | [a0.md](../a0.md), Open 16 |
| Een lijst om een verborgen map of dag terug te halen, na de melding | de API kan het (`GET` en `DELETE /photos/memories/hide`), maar de web heeft er geen plek voor; dat is een nieuw scherm en een keuze voor Jonas. Tot dan: Ongedaan maken in de melding. | dit document |
| "Misschien ook Anna", "?" in de viewer, "Opnieuw zoeken" en "Mislukte opnieuw" in een e2e | niet in de lijst van P8; de toetsen staan in `faces.test.ts`, de API in `api/tests/people/faces/`. | [toetsenbord-x2.md](../toetsenbord-x2.md) |
| Een e2e voor "Een map verbergen…" | niet in de lijst van P8. `memories.test.ts` toetst welke map mag; het verbergen zelf toetst `api/tests/photos/test_memories.py`. | dit document |

## Commits

Op `wip/i3-fase6-rest` (begin `debd1d96`), in deze volgorde:

| Commit | Wat |
|---|---|
| `45f80aa3` | GPS: 60,0 seconden is de volgende minuut |
| `4f6864a8` | OCR: een upload van een inhoud die er al was, leest de foto voor de nieuwe eigenaar |
| `2f53a424` | Web: Dubbel in het hoofdmenu |
| `b2591101` | Web: de viewer is 100% breed, niet 100vw |
| `3abfd3bc` | Web: `/beheer` luistert naar `search.progress` |
| `82988365` | Web: wie een PDF alleen mag lezen, ziet de rij OCR zonder knop |
| `7bd9d0cf` | Web: een map verbergen in de herinneringen |
| `fb6db5af` | Web: na de laatste "Meer laden" de focus op het eerste nieuwe gezicht |

De drie e2e-tests en deze notities kwamen er bij de samenvoeging van golf 3 bij, in één commit
op `claude/stappenplan-uitwerken-lu4uv3` ("P8: the e2e keyboard tests and the notes"); zie
[w3.md](w3.md).

## Controles

De controles van P8 na de samenvoeging (ruff, de snelle controles van de web met het budget,
gerichte pytest op SQLite en PostgreSQL 16) staan in [w3.md](w3.md). De e2e-tests draaien in
CI: de sandbox installeert geen browsers voor Playwright.
