# Fase 5 — De integratie

## In het kort

De integratie (stap 4 van §5 van het [bouwontwerp](bouwontwerp.md)) bracht M0–M10 samen tot
versie 0.7.0, in vier delen:

- **Deel 1, de open punten:** wat de pakketten aan de integratie overlieten, afgewerkt met een
  test per punt; wat bleef liggen, met de reden ([Open punten](#open-punten)). Alle wijzigingen
  aan contracten zijn additief ([Wijzigingen aan contracten](#wijzigingen-aan-contracten)).
- **Deel 2, de metingen:** Ma–Mp en de belastingtest in de sandbox, met de worker op 3 cores
  ([Metingen](#metingen); de getallen in [metingen.md](metingen.md)). Vier van de vijf eisen van
  "Klaar als" gehaald; drie omzettingen tegelijk zonder haperen bijna (na een sprong soms één
  keer 0,1–1,5 s).
- **Deel 3, de standaarden en de installatie:** de standaarden van `MediaConfig` op de metingen
  ([Standaarden](#standaarden)), de installatietest van 0.6.0 naar deze versie en terug
  ([Installatietest en terugrollen](#installatietest-en-terugrollen)), en drie fouten uit de volle
  api-set gerepareerd.
- **Deel 4, afronden:** versie 0.7.0, de README's, het stappenplan, de beslissingen, de lijst
  van alle e2e van media ([e2e.md](e2e.md#alle-e2e-van-media-070)) en dit document, plus één
  gemiste wekker in het opzoeken die de laatste checks vonden
  ([Deel 4: afronden en 0.7.0](#deel-4-afronden-en-070)).

Wat alleen CI of de LXC kan laten zien: [hieronder](#wat-alleen-ci-of-de-lxc-kan-bewijzen); de
lijst voor Jonas op de LXC staat in de [README](README.md#op-de-lxc-stap-5), wat er is en de
grenzen ook daar. De checks per deel staan onderaan.

## Hoe het liep

Stap 4 van §5 van het [bouwontwerp](bouwontwerp.md) (revisie 2, leidend). Dit document groeide
per deel van de integratie. Deel 1 werkte de punten van "Naar de integratie" in
[m10.md](m10.md) af (1 en 3–8), en liep de stukken "Open" van [m0.md](m0.md) tot en met
[m10.md](m10.md) na op punten met de integratie als eigenaar, of zonder eigenaar en klein. Punt 2
(een grens voor `media/subs` en `media/trick`) hoort bij stap 3, punt 9 (Keuze 42 op echte namen)
bij de metingen, en punt 10 (Vraag 7) blijft bij Jonas. Deel 2 mat Ma–Mp van §4.5, de
belastingtest van §4.6 en punt 9 ([Metingen](#metingen), [metingen.md](metingen.md)). Deel 3
zette de standaarden van `MediaConfig` op de metingen, met punt 2 ([Standaarden](#standaarden)),
en draaide de installatietest met terugrollen naar 0.6.0
([Installatietest en terugrollen](#installatietest-en-terugrollen)). Deel 4 maakte er versie
0.7.0 van en werkte de docs af ([Deel 4](#deel-4-afronden-en-070)).

Jonas heeft de beslissingskaarten van fase 5 nog niet beantwoord. De standaarden bleven dus: de
import kopieert, geen TMDB-sleutel en alleen lokale metadata, de providers uit. B39–B48 blijven
voorstel. Elk punt hieronder heeft een test die zonder de fix faalt (nagegaan door de fix terug te
zetten, of doordat de test de nieuwe functie aanroept).

## Open punten

### Gedaan

De nummers zijn die van "Naar de integratie" in [m10.md](m10.md), ook in de boodschappen van
de commits.

- **Punt 1, een mislukte ondertitel of sprite opnieuw proberen** (`8349968`; m5.md, Open "Beheer:").
  "Mislukte opnieuw" op /beheer zet nu ook terug wat blijvend mislukt staat: `trick_state` en
  `subs_state` van `failed` naar `none`, de markeringen `<naam>.failed` onder
  `media/subs/<sha>/` weg (ook die van een los bestand) en een bewaarde mislukte probe weg. De
  analyse van wat in een bibliotheek staat, komt weer in de rij (`jobs.chain`, dezelfde weg als
  Voorbeeldbeelden opruimen). Het overzicht telt die inhouden in `jobs.failed_files`, zodat de
  knop er ook staat zonder mislukte job, met een zin in het Nederlands en Engels. Play zet de
  analyse ook klaar als een video in een bibliotheek nog geen sprites of ondertitels heeft
  (`none`, `pending` na een fout van de worker, of `ready` zonder `trick.vtt`).
  Tests: `test_mislukte_opnieuw_ook_sprites_ondertitels_en_probes`,
  `test_play_zet_de_analyse_klaar_als_de_sprites_ontbreken`, en `retryable` in
  `admin/media.test.ts`.
  *Niet gedaan:* een los bestand krijgt nog steeds geen sprites (Keuze 30 zegt dat zo);
  `failed_files` telt alleen wat in de database staat (een markering van een los bestand niet,
  al haalt de knop haar wel weg); een mislukte stap `keyframes` of `audio_info` telt niet mee.
- **Punt 3, photometa opnieuw na een tijd met de worker weg** (`41d0b5b`; m1.md en m2.md, Open). Met
  media aan en de worker weg, druk of zonder ffmpeg geeft `render_meta` nu `{"error", "later"}`;
  photometa laat zo'n blob op zijn oude `meta_version` staan, en de housekeeping
  `media.photometa` (elke 5 minuten, alleen met een gezonde worker) zet de backfill weer klaar.
  De grens: alleen met een gezonde worker, één keer per 5 minuten, en `jobs.chain` zet er nooit
  twee klaar. Met media uit verandert er niets: `render_meta` geeft nooit "later" en de
  housekeeping staat er niet (`test_media_off.py`). Tests:
  `test_video_met_de_worker_weg_blijft_over_tot_de_worker_er_is`,
  `test_photometa_met_een_worker_zonder_ffmpeg_later`.
- **Punt 4, media in de balk van Opslag** (`707e5d1`; m0.md, afwijking 14). `StorageParts.media`
  (optioneel, standaard `None`) is met media aan de grootte van `<data_dir>/media`. Storage.tsx
  toont het als eigen stuk "Media" (NL en EN) tussen database en overig, en "overig" telt het
  niet meer. Met media uit is het veld `None` en zijn de balk en de legenda die van 0.6.0
  (`format.test.ts`, `test_media_hooks.py`). Het tabblad zit in de chunk van /beheer: de route
  Bestanden bleef 43,6 KB.
- **Punt 5, een probe die niet kan uitvoeren, blijft mislukt** (`8940cac`; m10.md, Open punten).
  `probe.py` geeft bij ENOEXEC, EACCES, EPERM, ENOTDIR, ELOOP en ETXTBSY nu `FFMPEG_MISSING`, en
  bij elke andere OSError bij het starten `INTERNAL`. Geen van beide staat in
  `analysis.FILE_ERRORS`, dus de analyse bewaart niets en probeert het later opnieuw. Daarnaast
  haalt "Mislukte opnieuw" een bewaarde mislukte probe weg (punt 1), dus ook een oude
  `unreadable` van vóór deze fix komt terug. Tests:
  `test_een_ffprobe_die_niet_start_is_geen_kapot_bestand` en, met `-m media`, de echte worker
  met een leeg `ffprobe` op PATH (`test_een_leeg_ffprobe_in_de_worker_bewaart_niets`).
- **Punt 6, de open punten 1–3 van M8** (m8.md; code van M2).
  - **Een haak na een scan** (`98aeacc`): `scanner.after_scan(hook)`; na de commit van een
    afgemaakte ronde roept de scanner elke haak aan met de id van de bibliotheek, zonder
    databaseverbinding, en een fout in een haak houdt de scan niet tegen. M8 gebruikt hem in
    plaats van `hooks.touched` met SOON en twee tikken (m8.md, afwijking 1). De housekeeping van
    5 minuten blijft. Tests: `test_na_een_scan_zet_de_haak_een_taak_klaar`,
    `test_zonder_sleutel_doet_de_haak_niets`, `test_een_fout_in_de_haak_houdt_de_scan_niet_tegen`.
  - **`locked` en `sort_title` in GET …/items/{id}** (`b8799d8`): `detail()` vult ze (`locked`
    alleen met de velden van het venster, zonder de verborgen vergrendeling). Het venster
    Bewerken opent met GET in plaats van een lege PATCH (m8.md, afwijking 7). Tests: GET in
    `test_bewerken_vastzetten_en_vrijgeven` en `test_ontkoppelen`; de e2e van M8 kijkt dat het
    openen een GET is en geen PATCH stuurt.
  - **Online metadata aan zonder bron** (`fe6bdab`): het blok Bibliotheken op /beheer zegt het
    onder de naam (films en series zonder sleutel van TMDB, muziek met MusicBrainz uit), NL en
    EN, met waar je het aanzet. Alleen tekst, geen knop. Test: `missingProvider` in
    `admin/media.test.ts`. *Niet gedaan:* dezelfde zin in het venster Nieuwe bibliotheek (de
    chunk van de mediapagina's; /beheer is de plek waar de sleutel staat).
- **Punt 7, Downloaden op `/kijken` als ffmpeg ontbreekt** (`ada36c6`; m6.md, Open). `failureOffer`
  (`player/session.ts`) zegt per code wat de melding aanbiedt: Downloaden bij
  `media_ffmpeg_missing`, `media_worker_down`, `media_unavailable`, `media_unsupported`,
  `media_hdr`, `media_transcode_failed`, `media_timeout`, `media_busy` en `media_decode`, nooit
  als de toegang weg is; en bij `media_ffmpeg_missing` de zin van Keuze 46 in plaats van de tekst
  van de server. De knop staat met Tab direct na Terug ([toetsenbord-m6.md](toetsenbord-m6.md)).
  De e2e van K7 (`test_de_worker_zonder_ffmpeg`) Tabt op `/kijken` naar Downloaden, drukt Enter
  en vergelijkt de sha256 van de download met het bestand. De speler bleef in zijn chunk (25,4
  van 35 KB).
- **Punt 8, een titel zonder stuurtekens bij de bron** (`8f6d31f`; m2.md, Open). `items._clean` doet wat
  `http.printable` doet: witruimte wordt een spatie, de rest van C0, DEL en C1 valt weg; een
  samenvatting houdt haar regels. `worker/ops/nfo.py` laat ook C1 vallen (dat mag in XML 1.0).
  Tests: `test_stuurtekens_vallen_weg_bij_de_bron` (met fuzz, vaste seed) en
  `test_geen_stuurtekens_in_titel_en_samenvatting`; de fuzz van nfo kijkt nu ook naar
  stuurtekens.
- **Ook: de regroup van muziek na een scan** (`265d2e2`; m9.md, Open en afwijking 8, zonder
  eigenaar en klein). Met de haak van punt 6 zet `music.scanned` na een scan van een
  muziekbibliotheek een regroup klaar (na `DELAY_S`); de eerste vraag na een scan loopt niet meer
  één achter. Test: `test_na_een_scan_staat_er_een_regroup_klaar`.

### Nagekeken, al gedaan

- **`MEDIA_REQUIRE_H264` in `e2e-media-perf`** (m6.md, Open): M10 zette hem al (`4b6c140`);
  [e2e.md](e2e.md) noemt nu beide jobs.
- **Een `.part` van vóór een crash** (m9.md, Open): M10 ruimt hem bij de start op
  (`runtime.clear_sessions`, M0-hotfix 3 in m10.md).
- **`docker exec` zonder `env -i`** (m1.md, Open): het runbook van media zegt het.

### Niet gedaan, met de reden

- **Punt 2 van "Naar de integratie"** (een grens voor `media/subs` en `media/trick`): niet in
  deel 1; gedaan in deel 3 (`12126c2`), zie [Standaarden](#standaarden).
- **Punt 9** (Keuze 42 op echte namen): gemeten in deel 2, zie [Metingen](#metingen). De
  formule bleef zoals hij is; de voorstellen zijn voor Jonas.
- **Punt 10, Vraag 7** (een eigen uid voor de kinderen van de worker): blijft bij Jonas.
- **De MBID van een album bewaren** (m8.md, open punt 6): raakt het groeperen van M9 en
  `write_album` van M8. Er is geen fout te zien: een gekoppeld album wordt niet opnieuw
  opgezocht. Niet klein; voor later.
- **Muziek in de e2e van M8** (m8.md, open punt 7): het opzoeken van muziek is getest in
  `test_media_lookup.py` met de nep in het proces; een e2e is geen klein werk.
- **Een lange "sample" na de probe** (m2.md, Open): de analyse zet geen nieuwe scan klaar. Dat
  vraagt een haak na de probe in `analysis.py` (M0); tot dan pakt de ronde van 6 uur hem op.
- **Een bibliotheek die in een andere verhuist** (m2.md, Open): /beheer zegt het sinds M10
  (`inside`). Zo'n verhuizing weigeren verandert de bestandsroutes van fase 3: een keuze voor
  Jonas, niet hier genomen.
- **Play en de previews delen de plaatsen "kort"** (m4.md, Open; ook m2.md, de voorbeelden van
  M1): of play voorrang krijgt, is
  Keuze 12 en een vraag voor het ontwerp. De speler probeert het na `Retry-After` opnieuw.
- **Sprites van een anamorfe bron** en **de begintijd van een PGS-spoor** (m5.md, Open): het
  eerste verandert de maten van de tegels in `trick.vtt`, het tweede `SubsArgs` (een contract van
  M0). Niet klein.
- **De TS-marge en een vlag `adts`** (m4.md, Open): vragen een index voor TS of een vlag in de
  worker; voor later.
- **"Verder kijken vanaf…" en "Niet gezien"** (m7.md, afwijking 3 en 4): nieuwe knoppen, geen
  fout.
- **Een grens aan het aantal afspeellijsten** (m9.md, Open): als Jonas er een wil.
- **Wat de stukken Open als gedrag beschrijven, zonder fout:** nagelopen en zo gelaten. In m2.md
  twee namen die alleen in hoofdletters verschillen, verplaatsen met dezelfde naam en inhoud, en
  een `tvshow.nfo` die uit de map van een serie verhuist (de ronde van 6 uur). In m4.md en m6.md
  Safari en native HLS (fase 9) en direct play van een klein bestand na intrekken (past bij §4.3
  #10). In m7.md een herstart tussen een nieuwe versie en de job, `JOB_WAIT_S`, de Reporter die
  aan het eind ook "pause" meldt, en twee tabbladen met de grens op ticks. In m9.md een schijf
  achter de titel zonder schijftag (de veiligere lezing) en `web/dist/__e2e-m9/` na een harde
  stop. In m1.md de wezen in de container (pas als een opdracht zelf kinderen maakt).
- **De rest van de stukken Open** had een pakket als eigenaar en is daar al gedaan (zoals de
  knop "cache legen" met `audio/`, een sessie stoppen vanuit /beheer en `WorkerCaps.children`,
  alle van M10), of is van de lead (de tag `fase5-basis`, m0.md).
- **"Lagere kwaliteit" bij `media_transcode_failed`** (Keuze 46; bij deze ronde gezien, stond in
  geen lijst): de melding biedt nu Opnieuw en Downloaden, het menu Kwaliteit van de speler blijft
  bereikbaar. Een eigen knop is werk voor de speler (M6); niet in dit deel.

## Wijzigingen aan contracten

Alles additief, met een test; met media uit verandert er niets.

| Contract | Wat | Test |
| --- | --- | --- |
| `JobCounts` (`app/media/types.py`, `web/src/media/types.ts`) | `failed_files: int = 0` erbij | `test_opnieuw_proberen`, `test_mislukte_opnieuw_ook_sprites_ondertitels_en_probes` |
| `POST /api/v1/admin/media/jobs/retry` | zet ook mislukte sprites, ondertitels en probes terug; `retried` telt die analyses mee | idem |
| `StorageParts` (`app/usage.py`, `web/src/admin/api.ts`) | `media: int \| None = None` erbij; met media uit `None` | `test_media_hooks.py`, `format.test.ts` |
| `probe.py` (M0, bevroren) | een fout bij het uitvoeren is `ffmpeg_missing`, een andere fout bij het starten `internal`; geen van beide wordt bewaard | `test_media_probe.py` |
| `render_meta` (fase 3, met media aan) | `{"error", "later": true}` als de worker weg, druk of zonder ffmpeg is; photometa telt zo'n blob niet als gelezen | `test_photometa.py`, `test_previews_video_worker.py` |
| `scanner.after_scan(hook)` (M2) | nieuw: een haak na een afgemaakte scan; vervangt de haak `lookup.touched` van M8 op `hooks.touched` | `test_media_lookup.py`, `test_media_music.py` |
| `Item.locked`, `Item.sort_title` bij GET …/items/{id} | worden nu gevuld (de velden stonden er al, leeg) | `test_media_match_api.py`, e2e M8 |

Deel 3 veranderde geen contract: de grens voor `subs/` en `trick/` zit in de sweep
(`cache.py`, intern), `CacheClearIn` bleef `sessions | audio | trick`, en de routes van M5 geven
hetzelfde terug (ze zetten alleen de mtime van de map). Ook de drie reparaties uit de checks
([De checks van deel 3](#de-checks-van-deel-3)) zijn intern: `played_s` in het verslag van de
belastingtest houdt zijn betekenis (de gespeelde tijd), alleen telt hij niet meer te veel.

Deel 4 veranderde geen contract: de reparatie in `lookup.scanned` is intern (de haak
`scanner.after_scan` en de taak `media.lookup` blijven gelijk), verder alleen de versie (0.7.0)
en de docs.

## Metingen

Deel 2 mat Ma–Mp van §4.5 en de belastingtest van §4.6 in de sandbox (de worker onder
`taskset -c 0-2`, nooit meer dan 3 ffmpeg, een waakhond op het geheugen), elk 5 keer en de
tijden in e2e 10 keer, plus punt 9 (Keuze 42 op echte namen). Alles staat in
[metingen.md](metingen.md), de getallen in [metingen.json](metingen.json), de scripts in
`api/tests/media/metingen/` (één commando elk; pytest neemt ze niet mee). Mb en Ma met H.264 komen
uit CI (run 41341675278).

Gehaald: Ma, Mb, Me, Mf, Mg, Mh, Mi, Mj, Ml, Mm, Mn, Mo en Mp, en de 3 s van "Klaar als" bij Mc.
Niet gehaald, met de oorzaak, en zonder iets bij te stellen om te slagen:

- **Md, fase a en fase c** (0 keer haperen): na een sprong of een herstart van een producent
  haperde een speler één keer 0,1–1,5 s, in 5 van de 12 runs van fase a (vaker met OnlyOffice
  ernaast) en in 1 van de 5 van fase c; zonder sprong nooit. De startvoorrang van Keuze 26
  geldt alleen tot het eerste segment van een sessie; het voorstel is hem ook na een herstart
  van een producent te geven ([bevinding 3](metingen.md#bevindingen)).
- **Md met een herstart van DS** (§4.6 d): de lopende streams haperden niet, maar een nieuwe
  kijker kreeg 503 in plaats van een lagere trede. Bij `B − 1,0` laat de reserve vanaf `f`
  ±1,07 geen plaats over, en de sandbox had 1,24 en 1,37 (bevinding 7, met een voorstel). De
  speler in de browser wacht en begint als DS klaar is.
- **Mc, direct** haalt de 1,5 s van de tabel niet (2,0 s; 1,5 s op 400 Mbit/s): Chromium zelf
  (bevinding 4).
- **Mk**: de voorspelde kosten liggen 44–59 % boven wat het kost; de tabel van Keuze 24 is een
  bovengrens (bevinding 6).
- **Keuze 42 (punt 9)**: precisie 0,971 en 0,953 (twee manieren van zoeken), 21 en 18 van de 125
  namen naar Nakijken. De foute koppelingen zijn titels die het begin van een andere zijn terwijl
  de goede ontbreekt; het geval van punt 9 zelf ging goed. Geen fout tegen de keuze; twee
  voorstellen voor Jonas, doorgerekend (samen geen foute koppeling).

De treden van Vraag 3 en §4.5 kiest de toelating alleen bij `f` rond 1,0; de sandbox mat
1,22–1,40 en koos lager. Dat kan alleen de LXC laten zien, net als H.264 in de browser buiten CI
en de ffmpeg 7.1.5 van het image.

Drie fouten in onze code, gerepareerd met een test (de derde vonden de checks van dit deel):

- **De reserve van de toelating** (`3420059`): rekende het decoderen van 480 veryfast met `f` in
  plaats van `f_dec` ("alle kosten × `f`, en `d` × `f_dec`", Keuze 26). Met `f_dec` boven `f`
  kreeg de derde kijker 503 met een plaats vrij.
- **De belastingtest buiten de app, fase c** (`bd93489`): 500 als de probe `WorkerBusy` kreeg,
  spelers die na de herstart voor altijd stilstonden, en een speler die bij 503 `media_busy`
  meteen opgaf.
- **De worker en een SIGTERM vlak na zijn start** (`bf2f766`): `serve` zette de handlers voor
  SIGTERM en SIGINT pas nadat de socket er was, en `local.start` is klaar zodra de socket er is.
  Een SIGTERM daartussen stopte de worker met -15, zonder de socket op te ruimen
  (`test_worker_stopt_op_sigterm_zonder_kinderen` faalde zo één keer in `-m media`). Nu staan de
  handlers er eerst. Test: `test_sigterm_meteen_na_de_socket_stopt_de_worker_netjes`, die zonder
  de fix in de timeout loopt.

Geen ervan verandert een contract: `POST …/loadtest/play` gaf al 503 `media_busy` bij een
drukke toelating; nu ook bij een drukke worker, in plaats van 500.

## Standaarden

Deel 3: "het budget en de standaarden van `MediaConfig` op de metingen gezet" (§5 stap 4), voor
de LXC van Jonas: 4 cores, 16 GB, geen GPU, en de worker krijgt er 3. De metingen van deel 2
dragen elke standaard zoals hij was; er is dus geen getal veranderd. Wat wel nieuw is: de grens
voor `media/subs` en `media/trick` (punt 2 van "Naar de integratie", `12126c2`). De grenzen van
het ontwerp (0,5–16 cores, 0–16 omzettingen, 1–10.000 GB) bleven. Alles hieronder staat vast in
`api/tests/test_media_defaults.py`; een andere waarde daar hoort een andere regel hier te krijgen.

| Standaard | Oud | Nieuw | De meting die hem draagt |
| --- | --- | --- | --- |
| `cpu_budget` (Cores voor omzetten) | 3,0 | 3,0 | de worker krijgt 3 cores (`cpus: 3.0` in `compose.media-limits.yaml`, nu ook in de test). Md fase b: 3 transcodes op die 3 cores, 5 van 5, nooit meer dan 3 ffmpeg; Mg: de API op de vierde core met OnlyOffice ernaast p95 ten hoogste 104,5 ms (grens 200) |
| `max_transcodes` (Omzettingen tegelijk) | 3 | 3 | Vraag 3 (geen antwoord: de standaard blijft); Md fase b 5 van 5. Grens: met 3 plaatsen en B 3,0 past de eerste kijker alleen tot `f` 1,60 (de reserve van Keuze 26; `test_tot_welke_f_de_eerste_kijker_past`). De sandbox had 1,22–1,40. Het runbook zegt nu: staat bij Meting een `f` boven 1,6, zet dit op 2 (past tot 2,4). Bevinding 7 (`max_transcodes` uit `f` afleiden) blijft een voorstel |
| `max_user_transcodes` | 2 | 2 | geen meting raakt het; Keuze 26 |
| `public_transcode` | uit | uit | Vraag 5 (geen antwoord) |
| `hdr_1080` | uit | uit | Keuze 27 (`auto` bij HDR blijft 720; 1080 alleen als iemand hem kiest). Mf liep met `hdr_1080` aan: 0 keer haperen in 15 runs, alleen op 1080 superfast (veryfast past niet onder 0,9 × B bij `f` 1,26). Geen reden om hem standaard aan te zetten; dat blijft een keuze op /beheer |
| `cache_gb` (Cache) | 20 | 20 | Md: de cache groeide 28 MB per minuut met 3 sessies (720 + 480 + 480); Keuze 31 rekent ±45 MB per minuut voor 1080p, dus 20 GiB is ±2,5 uur van 3 × 1080p, en een venster van 6,7 GiB per sessie |
| `musicbrainz` | uit | uit | de beslissingskaarten: providers uit |
| subs/ + trick/ samen | geen grens | `cache_gb / 4`: 5 GiB | geen meting van deel 2; de maat komt uit M5: sprites ±1,4 MB per film van 2 uur (`trick.py`), ondertitels klein. 5 GiB houdt de sprites van ±3.500 films; het voorbeeld van Keuze 31 (1.000 films en 5.000 afleveringen) is ±4 GB. Het langst niet gebruikte gaat eerst, nooit van een open sessie |
| `PLATFORM_MEDIA_MAX_TRANSCODES` | 3 | 3 | het plafond is de standaard van /beheer |
| `PLATFORM_MEDIA_JOB_MEMORY_MB` | 1200 | 1200 | Mh: ±200 MB per ffmpeg; 1200 laat marge voor 1080 en de sprites |
| `PLATFORM_MEDIA_MAX_PROCS` | 10 | 10 | Keuze 12; Md had nooit meer dan 3 ffmpeg voor 3 transcodes. De metingen draaiden met 3 (regel 13 van de sandbox), niet de LXC |
| de plaatsen van de worker | transcode `max_transcodes`, remux 6, achtergrond 1, kort 2 | idem | Keuze 12; Mj: knippen kost 0,007 core per stroom, 6 remuxen samen ±0,04 core |
| `mem_limit` / `cpus` van de worker | 3g / 3,0 | 3g / 3,0 | Mh: de worker met al zijn ffmpeg ten hoogste 793 MB. Het commentaar zei "±1,7 GB [gemeten]" (de schatting van het ontwerp); nu de meting |

**Niet veranderd, met de reden:** de kostentabel van Keuze 24 en `REF_CORES` van de bench (Mk
voorspelt 44–59 % te hoog, maar de tabel is met opzet een bovengrens; bevinding 6 is een voorstel
voor Jonas), en `B − 1,0` zolang DS start (bevinding 7, een voorstel). Beide veranderen een keuze
van het ontwerp, geen standaard.

**Met media uit** verandert geen van deze waarden iets: `MediaConfig` werkt alleen met media aan,
en de housekeeping `media.sweep` (met de nieuwe grens) draait dan niet
(`test_met_media_uit_doet_de_sweep_niets`: de vlag uit, en uitgezet op /beheer).

**Punt 2 van "Naar de integratie"** (`12126c2`): de sweep van elke 5 minuten telt de mappen per
blob onder `subs/` en `trick/` en haalt boven `cache_gb / 4` de map weg die het langst niet
gebruikt is (de mtime van de map; een ondertitel of `trick.vtt` halen zet die op nu). Nooit van
een blob die een open sessie speelt, ook niet haar losse ondertitels; nooit een tijdelijke naam
met een punt (`trick._store`, `subtitles._write_file`). Van `trick/` de hele map, en
`trick_state` gaat van `ready` naar `none`: zo klopt het item, en play zet de analyse weer klaar
(`media_play.steps_left`). Van `subs/` alleen de VTT's: een `.failed` blijft (de staat failed),
en een ondertitel komt bij het volgende verzoek terug (202 met `Retry-After`). Los van de grens
van de sessies en `audio/`, die `cache_gb` samen houden zoals Keuze 31 zegt. Opruimen op /beheer
(sessies, voorbeeldbeelden, muziek) bleef zoals het was, en een lopende sessie merkt er niets
van. Tests: `test_de_grens_voor_subs_en_trick` (`test_media_trick.py`, met een echte sessie en de
routes) en vier regels in `test_media_cache.py`; beide falen zonder de wijziging (nagegaan: zonder
de aanroep in de sweep blijft `trick/` staan, zonder het aanraken bij het halen blijft de mtime
oud).

## Installatietest en terugrollen

M10 en Keuze 47: bijwerken van 0.6.0 naar deze versie, en terug met precies de commando's van het
runbook. In de sandbox op `8ffabf3` (de code en docs van dit deel; het harnas uit een eigen
worktree van die commit, zodat niets het script tijdens de run veranderde):

```bash
INSTALL_TEST_REPO=<de repo> INSTALL_TEST_TO=8ffabf3 bash infra/single/tests/test_install_media.sh --stappen
bash infra/single/tests/test_install_media.sh
```

**`--stappen`: 59 van de 59 controles**, ±4 minuten (de builds uit de cache van Docker), het
blok `=== Samenvatting` als laatste, en daarna niets van de test over (containers, volumes,
netwerken, images en de werkmap weg; `python:3.12-slim` kreeg zijn eigen image terug). Het image
kreeg het lege pakket ffmpeg (de sandbox bereikt geen Debian-spiegel), dus stap 2 nam de tak
zonder ffmpeg. Het laagste `MemAvailable` was 13,9 GiB, op `/` bleef ≥ 16 GB vrij. Het functiedeel:
**125 van de 125**.

| Stap | Wat de run liet zien |
| --- | --- |
| 1. 0.6.0 (`263abd74d914a9811901e7570ed410866364f4b5`) installeren, een film uploaden | de app 0.6.0, schema 0007, geen container `media`; de film staat erin; `/api/v1/office/status` zegt `off` (`OFFICE=uit`) |
| 2. bijwerken naar `8ffabf3` | **0008 vanzelf** (de app migreert bij de start; schema 0008); de worker gezond in zijn container, als `media`, zonder netwerk; dezelfde sleutel in de app en de worker en in geen log; de film heel; **office zoals in 0.6.0** (`off`); **media raakt niets aan**: geen bibliotheek, geen import, geen sleutel van TMDB, MusicBrainz uit, en de film in geen bibliotheek; `media-status`: "Media: aan (afgeschermd, ffmpeg ontbreekt)"; `/beheer` toont `media_ffmpeg_missing`; afspelen geeft **503 `media_ffmpeg_missing`** |
| 2b. nog een keer bijwerken (`b6777db`, een commit erbovenop) | één image `platform:local`, de oude id weg, de worker op het nieuwe image, dezelfde sleutel |
| 3. `MEDIA_SANDBOX=uit`, dan weer `aan` | de container weg, de lokale worker op `/beheer`, de sleutel blijft, de film heel; daarna de container terug |
| 4. terug naar 0.6.0 met de commando's van Keuze 47 | `git fetch` en `cat-file -e '263abd74…^{commit}'`, `dc stop platform media`, `db-downgrade 0007` met "ja" ("De database staat nu op revisie 0007."), dan install.sh met `PLATFORM_SRC_REF=263abd74d914a9811901e7570ed410866364f4b5`: de broncode op die commit, schema 0007, de app 0.6.0 (geen `/api/v1/admin/media`), geen container `media`, de eindtekst "Media: uit, want deze versie van het platform heeft nog geen media.", de film heel, de mappen `media` en `import` en het volume `media-run` blijven, één image |

Wat dit deel aan de test veranderde (allemaal in `8ffabf3`):

- **Afspelen zonder ffmpeg** eist nu 503 `media_ffmpeg_missing`. Sinds `8940cac` (punt 5) is een
  ffprobe die niet kan uitvoeren `FFMPEG_MISSING`; de oude controle nam ook 409
  `media_unavailable` aan (afwijking 22 van M10). De run gaf 503.
- **"Media blijft uit tot hij aangezet wordt"**, in de veiligere lezing: Keuze 47 zet media na het
  bijwerken aan (`MEDIA_SANDBOX=aan`), dus de vlag zelf is aan. Wat de test eist, is dat media
  niets doet met de bestanden van Jonas tot hij het zelf gebruikt: geen bibliotheek, geen import,
  geen provider (de beslissingskaarten zonder antwoord). Uitzetten op /beheer en
  `MEDIA_SANDBOX=uit` zijn de schakelaars; stap 3 test de tweede.
- **Office** blijft in deze test uit (`OFFICE=uit`; een DS op de runner zou de test verdubbelen).
  Nu controleert stap 2 dat `/api/v1/office/status` na de update zegt wat 0.6.0 zei. Office aan
  over een update (DS blijft, de documenten worden opgeslagen) bewijst `test_install_update.sh`
  van fase 4; die veranderde in dit deel niet. Afwijking: de opdracht vroeg "het office-deel
  blijft werken" in deze test; hier alleen met office uit.
- **Het functiedeel** controleert dat het runbook en `infra/single/README.md` precies de vijf
  regels van Keuze 47 geven, in die volgorde, met de volledige hash en het volledige adres van
  install.sh, en dat een tekst die `v0.6.0` noemt "als die er is" zegt (nagegaan: zonder de regel
  `dc stop platform media` in de README, of zonder "als die er is", FOUT).

**Het runbook en de README.** `docs/runbooks/media.md` ("Terug naar 0.6.0") had de commando's met
de volledige hash al, maar stap 4 met `…/install.sh` en "na de integratie ook de tag `v0.6.0`".
Nu het volledige adres, en de tag alleen "als die er is" (`git tag -l v0.6.0`): alles werkt met de
hash. `infra/single/README.md` verwees alleen naar het runbook; nu staan de vijf regels er zelf,
met wat "goed" betekent en dat `db-downgrade` om "ja" vraagt. De hash is nagekeken met
`git rev-parse '263abd74d914a9811901e7570ed410866364f4b5^{commit}'` (de commit "e2e echte DS:
herstart-test leest de sessie vóór ready"). Een tag `v0.6.0` bestaat hier niet; die is van de lead.

**Alleen CI (`media-image`, met het echte image en ffmpeg 7.1.5):** stap 1–3 met een werkende
worker: de zelftest `{"ok": true, "sandbox": "on", "problems": []}`, een transcode via curl met
`docker top` (geen ffmpeg in de app-container), en de rooktest van de belastingtest
(`--sessies 1 --duur 30`). CI zet `INSTALL_TEST_ROLLBACK=0`: stap 4 draait alleen hier. Met echte
ffmpeg geeft afspelen geen 503; de nieuwe controles van stap 2 (office, media raakt niets aan)
staan in het gemeenschappelijke deel en lopen daar ook.

## Wat alleen CI of de LXC kan bewijzen

De sandbox heeft Chromium 141 zonder H.264 en AAC, ffmpeg 6.1.1 in plaats van de 7.1.5 van het
image (het image kreeg hier een leeg pakket ffmpeg: de sandbox bereikt geen Debian-spiegel), en
een CPU die 20–40 % trager is dan de referentie. Wat daarom alleen in CI (de jobs `e2e-media`,
`e2e-media-perf` en `media-image`) of op de LXC te bewijzen is, staat hieronder per deel. Voor de
LXC is het samen stap 5: de lijst in de [README](README.md#op-de-lxc-stap-5), de commando's in
het runbook [media.md](../runbooks/media.md).

Uit deel 1:

- de sprites en ondertitels opnieuw na "Mislukte opnieuw" met de ffmpeg 7.1.5 van het image;
- een ffprobe die in de container niet start (ENOEXEC, EACCES): hier alleen met een leeg
  programma op PATH van de lokale worker;
- Downloaden op `/kijken` met Chrome for Testing in `e2e-media` (hier met Chromium 141).

Uit deel 2, de metingen ([metingen.md](metingen.md#wat-alleen-de-lxc-of-ci-kan-laten-zien)):

- de treden van Vraag 3 en §4.5, en een nieuwe kijker tijdens de start van DS (bevinding 7):
  beide hangen aan `f`, en de sandbox is 20–40 % trager dan de referentie;
- fase c met `dc restart platform` achter de proxy, en fase d met de DS van het image;
- de belastingtest met echte films: `sources` wordt aangenomen maar niet gebruikt.

Uit deel 3:

- de installatietest met echte ffmpeg (stap 1–3 in `media-image`): de zelftest, een transcode en
  de rooktest van de belastingtest; stap 4 (terugrollen) draait alleen in de sandbox;
- de grens van `subs/` en `trick/` op een echte bibliotheek: of 5 GiB genoeg is voor de sprites van
  Jonas zonder dat ze steeds opnieuw gemaakt worden (/beheer → Media → Cache toont de maat per
  soort);
- of de standaarden passen bij de CPU van de LXC: `bench` op /beheer. Boven `f` 1,6 past met 3
  omzettingen tegelijk geen kijker meer (het runbook zegt dan: zet het op 2).

Uit deel 4 (de eindlijst van de e2e, [e2e.md](e2e.md#alle-e2e-van-media-070)):

- de vier e2e met H.264 (`test_de_viewer_speelt_h264`,
  `test_h264_speelt_via_de_sessie_en_stopt_na_blokkeren`, `test_h264_uit_hevc_hdr_via_mse`,
  `test_transcode_naar_h264_in_ci`) en Ma met H.264 slaan hier over; in `e2e-media` en
  `e2e-media-perf` moeten ze spelen (`MEDIA_REQUIRE_H264=1`);
- Safari, iPhone (native HLS), Firefox en de tv: niet getest, hier niet en in CI niet. Een film
  op telefoon en laptop staat daarom in de lijst voor de LXC.

## Deel 4: afronden en 0.7.0

Eén reparatie in de code (hieronder), verder de versie en de docs:

- **Versie 0.7.0** in dezelfde bestanden als 0.6.0: `api/app/__init__.py`, `api/pyproject.toml`,
  `api/uv.lock`, `web/package.json` en `web/package-lock.json`.
- **De README's:** de [README](../../README.md) bovenaan de repo heeft "Bijwerken naar 0.7.0
  (media)" en fase 5 in de voortgang; [infra/single/README.md](../../infra/single/README.md) heeft
  media naast office (de derde container, het bijwerken, en office vanaf 0.5.0 in dezelfde run).
  Het terugrollen naar 0.6.0 blijft de vijf regels van Keuze 47 met de volledige commit; de tag
  `v0.6.0` staat er nog niet, en het runbook noemt hem alleen met "als die er is".
- **[README.md](README.md) van fase 5:** bovenaan de stand voor Jonas: wat er is, "Klaar als" met
  de getallen, de grenzen, wat geschrapt of verschoven is en waarom, de vragen, en stap 5 op de
  LXC als lijst. De geschiedenis per pakket staat eronder.
- **Het [stappenplan](../stappenplan.md):** elf punten van fase 5 afgevinkt, elk met de reden
  waar het ontwerp ze veranderde. De belastingtest blijft open: gebouwd en in de sandbox
  gedraaid, maar het aantal voor de node zelf kan alleen de LXC zeggen. Dat is de veilige lezing
  van "per node vastleggen".
- **De [beslissingen](../fase-0/beslissingen.md):** B39–B48 blijven voorstel, met "zo gebouwd en
  de standaard in 0.7.0, maar Jonas heeft nog niet gekozen", en een regel dat ze op hem wachten.
- **[e2e.md](e2e.md):** de lijst van alle 74 e2e van media (68 `media`, 6 `media_perf`) met wat
  elke test bewijst, en de uitslag bij 0.7.0.

**De gemiste wekker in het opzoeken.** De eerste ronde van `-m media` in de e2e gaf één fout:
`test_twintig_films_vanzelf_en_met_de_hand` (M8) had na 50 s 14 van de 20 films gekoppeld; de
laatste zes geüploade stonden nog op `local`. Los ging de test goed (35 s). Geen toeval, maar
een race die past bij wat er stond: in de volle set liep alles trager, zodat de films in twee
scans binnenkwamen. De haak na de tweede scan (`lookup.scanned`) zette de taak met `jobs.enqueue` klaar, en die telt een lopende
taak met dezelfde key mee. De taak van de eerste scan liep nog, had zijn lijst al gelezen (14
films, minder dan `BATCH`, en de scan was toen klaar), en zette geen opvolger. Zo bleven de
nieuwe films lokaal tot de housekeeping, 5 minuten later. Op de LXC gebeurt dat ook bij een
import of een map die in stukken binnenkomt. Nu zet de haak de taak klaar met `jobs.chain`, dat
alleen ontdubbelt tegen een taak die nog klaarstaat: loopt er een, dan komt er een opvolger, en
`media_net` heeft één worker, dus die loopt erna. De nieuwe test
`test_een_scan_terwijl_het_opzoeken_loopt_krijgt_een_opvolger` laat midden in de eerste taak een
film uploaden en scannen; zonder de wijziging bleef die film `local`.

## De checks van deel 1

Op `d80a514` (de code van dit deel plus de docs), in de sandbox:

- api: `ruff check` en `ruff format --check` (ook `../e2e`) schoon; `pytest` 3806 goed, 2
  overgeslagen; `-m media` onder `taskset -c 0-2` 176 goed;
- web: typecheck, 913 tests in 81 bestanden, build, budget (eerste lading 96,4 van 100 KB, de route
  Bestanden 43,6 van 45 KB, de speler 25,4 van 35 KB) en `check:i18n --strict` goed;
- e2e: de set van CI 110 goed; `-m office` 30 goed; `-m media` 64 goed en 4 overgeslagen (H.264,
  dat de Chromium 141 hier niet heeft);
- infra: `test_install_media.sh` (het functiedeel) 110 van 110, `test_install_env.sh` goed,
  shellcheck schoon;
- als nobody (`/tmp/ci-nobody/run.sh`, zoals CI): de twaalf testbestanden die dit deel raakte, 350
  goed. Een eerste ronde in de werkmap gaf 5 fouten die niet van de code zijn: `api/data/jwt.secret`
  is daar 0600 van root (van een eerdere run als root), en `SSL_CERT_FILE` wijst naar een bestand
  onder `/root`. In een kopie van `api/` van nobody en zonder die variabelen waren ze goed.

Op `/` was maar 6–7 GB vrij (de schijf van de sandbox wordt gedeeld). De toelating weigert remux
en transcode onder 10 GiB vrij op `data_dir` (Keuze 31, zie m4.md), dus liepen pytest en de e2e
met `TMPDIR` en `--basetemp` op `/dev/shm`. Dat verandert geen test, alleen waar de tijdelijke
mappen staan.

## De checks van deel 2

Op `bf2f766` (de code van dit deel) plus de scripts en docs van de metingen, in de sandbox:

- api: `uv sync --frozen`; `ruff check` en `ruff format --check` (ook `../e2e` en
  `tests/media/metingen`) schoon; `pytest` 3812 goed, 2 overgeslagen; `-m media` onder
  `taskset -c 0-2` 176 goed; `pytest --collect-only tests/media/metingen` vindt niets (de scripts
  heten niet `test_*`);
- web (niets veranderd in dit deel): typecheck, 913 tests in 81 bestanden, build, budget (eerste
  lading 96,4 van 100 KB, de route Bestanden 43,6 van 45 KB, de speler 25,4 van 35 KB) en
  `check:i18n --strict` goed;
- e2e, onder `taskset -c 0-2`: de set van CI 110 goed; `-m office` 30 goed; `-m media` 64 goed en
  4 overgeslagen (H.264, dat de Chromium 141 hier niet heeft);
- infra: `test_install_media.sh` (het functiedeel) 110 van 110, `test_install_env.sh` goed,
  shellcheck schoon;
- als nobody (`setpriv` zoals `/tmp/ci-nobody/run.sh`, in een kopie van `api/` van nobody en
  zonder `SSL_CERT_FILE`): de vier testbestanden die dit deel raakte (`test_media_admission`,
  `test_media_decide`, `test_media_loadtest`, `test_media_worker_core`), 207 goed en 1
  overgeslagen, en met `-m media` 9 goed.

De eerste ronde van `-m media` gaf één fout: `test_worker_stopt_op_sigterm_zonder_kinderen` zag
-15 in plaats van 0. Dat was een race in de worker, geen toeval; gerepareerd in `bf2f766` (zie
[Metingen](#metingen)), daarna 15 keer na elkaar goed en in deze ronde ook.

Op `/` was 16–17 GB vrij, boven de 10 GiB van Keuze 31: pytest en de e2e liepen zonder de omweg
via `/dev/shm` van deel 1.

## De checks van deel 3

Op `78f0621` (de code van dit deel, met de drie reparaties hieronder) plus deze docs, in de
sandbox:

- api: `uv sync --frozen`; `ruff check` en `ruff format --check` (ook `../e2e`) schoon; `pytest`
  3833 goed, 2 overgeslagen; `-m media` onder `taskset -c 0-2` 176 goed;
- web (niets veranderd in dit deel): typecheck, 919 tests in 81 bestanden, build, budget (eerste
  lading 96,4 van 100 KB, de route Bestanden 43,5 van 45 KB, de speler 25,4 van 35 KB) en
  `check:i18n --strict` goed;
- e2e, onder `taskset -c 0-2`: de set van CI 123 goed; `-m office` 30 goed; `-m media` 64 goed en
  4 overgeslagen (H.264, dat de Chromium 141 hier niet heeft);
- infra: `test_install_media.sh` (het functiedeel) 125 van 125 en `--stappen` 59 van 59
  ([Installatietest en terugrollen](#installatietest-en-terugrollen)), `test_install_env.sh`
  goed, shellcheck schoon;
- als nobody (`setpriv` zoals `/tmp/ci-nobody/run.sh`, in een kopie van `api/` en `infra/` van
  nobody en zonder `SSL_CERT_FILE`): de vijf testbestanden die dit deel raakte
  (`test_media_cache`, `test_media_trick`, `test_media_defaults`, `test_media_loadtest`,
  `test_office_convert`), 130 goed, en met `-m media` 3 goed. De eerste ronde had alleen `api/`
  gekopieerd: `test_het_budget_past_bij_de_cores_van_de_container` leest
  `infra/single/compose.media-limits.yaml` (in CI staat de hele repo er).

Drie eerdere rondes van de hele api-set gaven elk één andere fout, telkens in een test die los
goed ging. Geen toeval: drie oorzaken, geen ervan in wat deel 3 veranderde, elk met een test
gerepareerd.

1. **`test_fase_a_en_b`** (de belastingtest in pytest): "de API 1030,2 ms (p95)" in fase a; los
   37 tot 80 ms. Een spion op `gc.callbacks` in de testbestanden tot en met die module (in de
   volgorde van de hele set) liet zien: het proces heeft dan ruim een miljoen objecten van
   eerdere tests, een volle ronde van de garbage collector kost 0,94 s, en de gemeten vraag die
   erna kwam duurde 961 ms. De fixture `lt` zet de heap nu eerst in `gc.freeze`, zoals de
   tijdtests van subconv al deden (`85b17a4`); het afval van de app zelf telt nog mee
   en de grens van 200 ms blijft. Met de spion daarna: geen gemeten vraag boven 200 ms.
2. **`test_de_speler_telt_haperen`**: `played_s` 10,34 voor een bron van 10 s. De virtuele speler
   zette zijn plek hooguit tot wat binnen was, maar telde de hele tik; een late tik telde zo meer
   dan er gespeeld werd (`691ddd2`). Dat raakte ook het verslag van de echte belastingtest: een
   speler die tegen de rand van zijn buffer zit, kreeg te veel gespeelde tijd. De nieuwe test
   geeft één tik 4,5 s laat: eerst 5,0 s gespeeld voor een segment van 2 s, nu 2,0 s.
3. **`test_een_rij_conversies_loopt_door`** (office, fase 4): één conversie nog `pending` na
   13 s. Een gemiste wekker in `office/convert.py`: de tweede conversie zette zich in de rij en
   sloeg daarna haar uitstel op; kwam het slot precies daartussen vrij, dan zag `_next` haar taak
   nog lopend, haalde haar uit de rij, en bleef het uitstel van 60 s staan (`78f0621`). Nu gaat ze
   pas in de rij als haar taak `queued` is, en is het slot dan al vrij, dan zet ze zelf de
   volgende klaar. De nieuwe test houdt het uitstel vast tot het slot vrij is; zonder de
   wijziging stond de taak daarna 60 s later in de rij.

De laatste ronde (hierboven) liep op `78f0621`. Op `/` bleef 16 GB vrij en het laagste
`MemAvailable` tijdens de runs was 13,5 GiB.

## De checks van deel 4

Op `97a7b94` (versie 0.7.0 en de reparatie in het opzoeken) plus deze docs, in de sandbox:

- api: `uv sync --frozen`; `ruff check` en `ruff format --check` (ook `../e2e`) schoon; `pytest`
  3834 goed, 2 overgeslagen, in 22 min 49 s; `-m media` onder `taskset -c 0-2` 176 goed;
- web (alleen de versie veranderd): typecheck, 919 tests in 81 bestanden, build, budget (eerste
  lading 96,4 van 100 KB, de route Bestanden 43,5 van 45 KB, de speler 25,4 van 35 KB) en
  `check:i18n --strict` (0 meldingen in 214 bestanden) goed;
- e2e, onder `taskset -c 0-2`: de set van CI 123 goed; `-m office` 30 goed; `-m media` 64 goed en
  4 overgeslagen (H.264, dat de Chromium 141 hier niet heeft);
- infra: `test_install_media.sh` (het functiedeel) 125 van 125, `test_install_env.sh` goed,
  shellcheck schoon (`install.sh` en `tests/*.sh`);
- als nobody (`setpriv` zoals `/tmp/ci-nobody/run.sh`, in een kopie van `api/` en `infra/` van
  nobody en zonder `SSL_CERT_FILE`): `test_media_lookup.py` en `test_jobs.py`, 36 goed.

De eerste ronde, op `abc09b0`, gaf één fout: `test_twintig_films_vanzelf_en_met_de_hand` (M8)
hield een film op `local`. Dat was de gemiste wekker in het opzoeken
([hierboven](#deel-4-afronden-en-070)), geen toeval; gerepareerd in `97a7b94`, en de nieuwe
api-test faalde zonder de wijziging. Daarna liepen alle checks hierboven opnieuw, op de laatste
code.

Op `/` bleef 16 GB vrij en het laagste `MemAvailable` tijdens de runs was 13,5 GiB. Hooguit 3
ffmpeg tegelijk (de e2e tellen ze met `ffmpeg_count`).
