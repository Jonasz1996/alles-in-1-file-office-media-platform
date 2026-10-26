# Fase 3 — Web-app

Status: **klaar** (versie 0.5.0, 26 oktober 2026).

## Doel

Een complete web-app bovenop de API van fase 2: een snelle bestandsbrowser voor grote mappen,
uploads van mappen, viewers voor foto's, video, PDF en kantoordocumenten, een fototijdlijn met
albums, en beheer, account, delen, prullenbak en versies in de app zelf. Nederlands en Engels,
licht en donker thema, en als PWA te installeren. Zonder nieuwe bibliotheken (B19).

## Wat er is

- **P0 — Fundament:** een vaste code (met params) bij elke API-fout, migratie 0006 met alle
  schemawijzigingen van fase 3, de contracten van de API (prullenbak in bulk, opnamedatum,
  geblokkeerde accounts, taal per account), de eigen router, i18n, UI-bouwstenen (virtuele
  lijst, menu's, selectie, meldingen, sneltoetsen) en het e2e-harnas ([e2e.md](e2e.md)).
- **P1 — Schil, PWA en prestaties:** navigatie, inloggen met 2FA, het thema zonder flits, de PWA
  (alleen de schil in de service worker, offline-balk, melding bij een nieuwe versie),
  voorgecomprimeerde bestanden met een strikte CSP, het groottebudget en de meetscripts
  ([p1.md](p1.md)).
- **P2 — Bestandsbrowser:** lijst en raster, ook met 10.000 items in een map (in pagina's
  geladen, gevirtualiseerd), live bijwerken, selecteren met muis en toetsenbord, bulkacties
  (verplaatsen, kopiëren, naar de prullenbak met Ongedaan maken, zip), contextmenu, knippen en
  plakken in de app, slepen naar mappen en het pad, en zoeken ([p2.md](p2.md)).
- **P3 — Uploads:** één uploadlade voor de hele app: mappen uploaden (knop en slepen), drie
  tegelijk, pauzeren en hervatten, onderbroken uploads na herladen, en één vraag per batch over
  dubbele namen (Vervangen, Beide houden, Overslaan) ([p3.md](p3.md)).
- **P4 — Viewers en preview-pijplijn:** één viewer voor elk soort bestand: afbeeldingen met zoom
  en EXIF-paneel (HEIC, HEIF en TIFF via een WebP van 2048 px), PDF-pagina's die de server
  rendert, video en audio in de browser zelf, tekst en code met kleuren, CSV, archieven, en een
  eigen lezer voor docx, xlsx, pptx, odt, ods en odp. Oude previews worden opnieuw gemaakt
  ([p4.md](p4.md)).
- **P5 — Foto's:** EXIF en opnamedatum (met een backfill voor bestaande foto's en video's), de
  tijdlijn op `/fotos` (ook bij 50.000 foto's), albums, en albums delen met personen, groepen
  en via een link ([p5.md](p5.md)).
- **P6 — Beheer, account, delen, prullenbak en versies:** `/beheer` met zes tabbladen
  (overzicht, gebruikers met blokkeren en verwijderen, opslag, taken, groepen, beveiliging), de
  accountpagina (opslag, taal, thema, apparaten), delen met een vervaldatum en van een
  selectie, "Gedeeld door mij", de prullenbak in bulk en versies uploaden met voortgang
  ([p6.md](p6.md)).
- **Integratie (§5.4):** de bevindingen tussen de pakketten verholpen; `check:i18n --strict` en
  het budget als harde poort in CI; het verhaal van §5.4 als één e2e-test op één server
  ([integratie.md](integratie.md)); alle prestatiepoorten en de echte Lighthouse
  ([prestaties.md](prestaties.md)).

## Klaar als

| Criterium (stappenplan) | Bewijs |
|---|---|
| Lighthouse performance > 90 | echte Lighthouse 13.5.0: `/` 96 en `/s/<link>` 95 (mobiel), `/map/<10k>` 100 (desktop); de eigen meting binnen 3 punten ([prestaties.md](prestaties.md)) |
| Eerste weergave < 1,5 s op LAN | `/`, `/map/<10k>` en `/s/<link>`, koud en warm, ruim onder de poort ([prestaties.md](prestaties.md)) |
| Alle acties ook via het toetsenbord | de checklists [toetsenbord-p1](toetsenbord-p1.md) t/m [p6](toetsenbord-p6.md): p1 t/m p5 in `e2e/test_browser_keyboard.py` en stap 13 van het verhaal, p6 met alleen het toetsenbord in `e2e/test_toetsenbord_p6.py` (de tabbladen en Groepen in `test_admin`) |
| 10.000 bestanden in één map scrollen soepel | lijst, raster en tijdlijn (50.000 foto's) bij 1× en 4× CPU binnen de poort in de volledige runs op `8321db6` en `0661462` (3 van 3); bij 4× niet in elke losse run: op de opgeleverde code haalde één van de drie volledige runs alle poorten, en de lijst en de tijdlijn bij 4× een deel van de losse runs (zie de bekende grenzen hieronder en [prestaties.md](prestaties.md)) |

## Documenten

- [bouwontwerp.md](bouwontwerp.md): het ontwerp (revisie 2, leidend); revisie 1 in
  [bouwontwerp-r1.md](bouwontwerp-r1.md). De keuzes staan als B19–B30 in
  [beslissingen](../fase-0/beslissingen.md).
- Per pakket: [p1](p1.md), [p2](p2.md), [p3](p3.md), [p4](p4.md), [p5](p5.md), [p6](p6.md), elk
  met zijn bekende beperkingen.
- Sneltoetsen: [toetsenbord-p1](toetsenbord-p1.md), [p2](toetsenbord-p2.md),
  [p3](toetsenbord-p3.md), [p4](toetsenbord-p4.md), [p5](toetsenbord-p5.md),
  [p6](toetsenbord-p6.md).
- Testen en meten: [e2e.md](e2e.md) (het harnas), [integratie.md](integratie.md) (het verhaal
  van §5.4), [prestaties.md](prestaties.md) met [prestaties.json](prestaties.json).

```sh
cd api && uv run pytest -q                                          # API
cd web && npm test && npm run build && npm run budget               # web
cd api && uv run --with playwright pytest ../e2e -m "not perf"      # e2e, na npm run build
cd api && uv run --with playwright python ../e2e/perf/run_all.py    # prestatiepoorten
```

## Bekende grenzen

Niet in fase 3:

- Geen tekstlaag in PDF's (zoeken of selecteren in de viewer): fase 6. "Openen in nieuw
  tabblad" geeft de viewer van de browser.
- Geen transcoding: wat de browser niet afspeelt (in de Chromium van de sandbox H.264 en AAC),
  krijgt een eerlijke melding en Downloaden (fase 5).
- Geen GPS of locatie: niet gelezen, niet bewaard, in geen enkel antwoord (fase 6).
- doc, xls, ppt en rtf hebben geen voorbeeld; kantoordocumenten zonder opmaak, afbeeldingen,
  formules of grafieken.
- Slepen naar de navigatiebalk (bijv. naar Prullenbak) is niet gebouwd; slepen gaat naar mappen
  in de lijst en in het pad.
- Het systeemklembord doet niet mee: knippen, kopiëren en plakken werken binnen de app.
- Een nieuwe versie uploaden (Versies…) is niet hervatbaar.

Zo werkt het nu:

- Wie een album gedeeld kreeg of via een link opent, krijgt geen live `preview.ready` voor de
  foto's erin (die gebeurtenissen volgen de mappenboom van de eigenaar): een nieuwe thumbnail
  zie je bij de volgende keer laden, en een preview die nog gemaakt wordt, vraagt de viewer een
  minuut lang elke 3 s opnieuw op.
- Oude video's, GIF's en tekstbestanden krijgen codec, animatie en tekencodering pas bij een
  latere nieuwe preview; tot dan probeert de viewer af te spelen, gaat hij uit van het
  mime-type en raadt hij de codering in de browser.
- Een album via een publieke link heeft geen zip en geen gegevenspaneel; downloaden gaat per
  foto.
- De opslagbalk op `/beheer` meet alleen de lokale node (standalone is er één).
- De reden bij `listener_bind` (bijv. "poort bezet") komt van het besturingssysteem en blijft
  in die taal; de rest van de melding is vertaald.
- Previewadressen (256, 1024 en 2048 px) zijn ondertekend op de inhoud, niet op de gebruiker,
  zodat de browser ze kan cachen. Na het intrekken van een deling, het weghalen van een link of
  een blokkade werkt een al uitgegeven adres dus nog hooguit 12 uur, alleen voor de inhoud van
  toen. PDF-pagina's en kantoorinhoud controleren bij elk verzoek ([p4.md](p4.md)).
- Wie herlaadt terwijl de app na het weer online komen zijn sessie vernieuwt, kan uitgelogd
  raken: de afgebroken refresh telt na `REFRESH_RACE` (5 s) als hergebruik van het token. Zo
  werkt de detectie van een gestolen token, bewust ([integratie.md](integratie.md)).
- Terugzetten uit de prullenbak in één verzoek met een map én iets wat erin lag: de map komt
  eerst terug, en het item gaat terug in die map, niet naar de hoofdmap.
- Wie de app in het Engels opent, laadt bij het opstarten de Engelse woordenboeken erbij: één
  rondgang extra (in Lighthouse op mobiel ruim 1 s meer Speed Index; de score blijft > 90).

Meten en testen:

- De scrollpoort bij 4× CPU slaagt niet altijd voor de lijst en de tijdlijn. Hoe vaak wel,
  hangt sterk af van hoe druk de VM is. De lijst haalde hem bij `0661462` in 46 van 61 losse
  runs en later, met dezelfde lijstcode, in 3 tot 8 van 20. De tijdlijn ging in `ac4914d` van 6
  naar 13 van 20 (20 runs per build, afwisselend). Het doel van 18 van 20 is voor geen van
  beide gehaald. Volgens CDP-traces is het meeste werk van de app: lay-out en tekenen van
  nieuwe rijen, in de lijst vooral hun tekst. In de lijst kwam ongeveer een derde van de lange
  taken alleen door het gedrang in de VM boven 50 ms. Garbage collection speelde geen rol, en
  steal van de host was 1–3 %. Van de drie `run_all.py` op `25eadb9` haalden er twee alle
  poorten. In de andere faalde de lijst bij 4× (p95 50 ms, één taak van 51 ms)
  ([prestaties.md](prestaties.md), "Scrollen bij 4× nader bekeken" en "Slotcontrole op
  `25eadb9`").
- Bij de slotcontrole van de integratie (`c6221f0`) was één koude eerste weergave van
  `/map/<10k>` 8,5 s. De oorzaak is niet gevonden. Achtergrondtaken zijn het niet, want in
  `run_all.py` lopen er geen. Wel hield de backfill van de fotometadata het schrijfslot van
  SQLite zo lang vast dat `/auth/refresh` erop wachtte: na een update was de eerste weergave
  1,0–1,3 s zolang de backfill liep. Dat is opgelost in `25eadb9` (nu 0,65–0,8 s). In 120
  koude runs van die proef en 15 in de drie `run_all.py` kwam de 8,5 s niet terug.
- Ingelogd (`/map/<10k>`) meldt Lighthouse "The page loaded too slowly to finish within the
  time limit": de live-stroom `/api/v1/events` blijft open, dus het netwerk wordt nooit stil.
  Alle metingen zijn er wel (score 100).
- De harde acceptatie is gedaan met de Lighthouse-CLI 13.5.0 in de sandbox (throttling
  `devtools`, de profielen van §4.3), niet met het DevTools-paneel in een gewone Chrome (B30).
- Een pagina die zijn meetpunt (`app:ready:<pagina>`) niet zet, krijgt in `e2e/perf` de status
  "not yet" en telt dan niet als fout (zo ontworpen voor de wave). Nu zet elke pagina zijn
  meetpunt; er een fout van maken is een wijziging in het harnas.
- Niet in de e2e: mp4/H.264 afspelen (deze Chromium heeft geen H.264, alleen de melding is
  getest), een TIFF in de viewer (wel in `api/tests/test_viewer.py`), installeren als app
  (headless kan dat niet), echt netwerkverlies (de test zet de browser offline) en de service
  worker over https (alleen `localhost`).
- De build meldt één `INEFFECTIVE_DYNAMIC_IMPORT` voor `src/api.ts`: de dynamische import in
  `i18n/index.ts` voorkomt een importcyclus en verandert de bundel niet.

Meer per pakket staat onder "Bekende beperkingen" of "Bekende grenzen" in [p1](p1.md) t/m
[p6](p6.md).
