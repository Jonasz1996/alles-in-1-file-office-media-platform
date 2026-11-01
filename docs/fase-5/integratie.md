# Fase 5 — De integratie

Stap 4 van §5 van het [bouwontwerp](bouwontwerp.md) (revisie 2, leidend). Dit document groeit
per deel van de integratie. Deel 1 (dit deel) werkte de punten van "Naar de integratie" in
[m10.md](m10.md) af (1 en 3–8), en liep de stukken "Open" van [m0.md](m0.md) tot en met
[m10.md](m10.md) na op punten met de integratie als eigenaar, of zonder eigenaar en klein. Punt 2
(een grens voor `media/subs` en `media/trick`) hoort bij stap 3, punt 9 (Keuze 42 op echte namen)
bij de metingen, en punt 10 (Vraag 7) blijft bij Jonas.

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

- **Punt 2 van "Naar de integratie"** (een grens voor `media/subs` en `media/trick`): stap 3,
  samen met de standaarden van `MediaConfig` op de metingen.
- **Punt 9** (Keuze 42 op echte namen): de metingen.
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

## Metingen

*Volgt in een later deel van de integratie* (onder andere punt 9: Keuze 42 op echte namen).

## Standaarden

*Volgt in een later deel van de integratie* (de standaarden van `MediaConfig` op de metingen,
met punt 2 van "Naar de integratie").

## Installatietest en terugrollen

*Volgt in een later deel van de integratie.*

## Wat alleen CI of de LXC kan bewijzen

*Volgt; uit dit deel:*

- de sprites en ondertitels opnieuw na "Mislukte opnieuw" met de ffmpeg 7.1.5 van het image;
- een ffprobe die in de container niet start (ENOEXEC, EACCES): hier alleen met een leeg
  programma op PATH van de lokale worker;
- Downloaden op `/kijken` met Chrome for Testing in `e2e-media` (hier met Chromium 141).
