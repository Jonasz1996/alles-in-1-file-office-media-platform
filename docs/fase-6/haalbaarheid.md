# Fase 6: haalbaarheid (metingen)

> Bij het bouwontwerp, revisie 2 ([bouwontwerp.md](bouwontwerp.md)). Gemeten op 3 november 2026 in
> de sandbox: eerst voor het ontwerp (H1–H8 en de proef van ontwerp B), daarna om de kritiek op
> revisie 1 na te lopen (V1–V10). Er staan geen databases, beelden, modellen of datasets in het
> repo. De scripts en de uitkomsten staan in scratch (`fase6/`), niet in het repo.

Dit stuk zet de getallen bij elkaar waar het ontwerp op leunt. Per meting: de vraag, het
commando, en wat eruit kwam. Elke tijd is de mediaan van 3 runs, tenzij er iets anders staat.

## De sandbox

| | |
|---|---|
| CPU | Intel(R) Xeon(R) Processor @ 2.80GHz (familie 6, model 85, stepping 7), 4 vCPU, 1 thread per core, onder KVM; AVX2, AVX-512 en VNNI |
| Geheugen | 15 GiB |
| GPU | geen |
| Docker | 29.9.1 |
| Python op de host | 3.12 in `api/.venv` (alleen gelezen), met SQLite 3.53.1; het systeem-Python heeft SQLite 3.45.1 |
| Python in `python:3.12-slim` | 3.12.16, Debian 13, SQLite 3.46.1: dezelfde als het image |
| tesseract | 5.3.4 uit Ubuntu noble, met `eng`, `nld` en `osd` (alleen op de host, voor de meting) |

Regels tijdens het meten: hooguit 3 zware processen tegelijk, samen onder 8 GB, en een proces
alleen stoppen op zijn pid. Andere agents gebruikten de sandbox tegelijk; reken op ±20% ruis.

De CPU van Jonas is onbekend. Een oudere of zuinige CPU kan 1,5–3× trager zijn. Daarom meet
`bench --los` het later op de LXC zelf (Keuze 54).

## De bronnen

| Bron | Wat |
|---|---|
| een boom van 100.000 bestanden | `gen_fts.py`: 30 gebruikers, 5.931 mappen, 20.000 documenten met samen ±235 miljoen tekens (Nederlands en Engels, Zipf, met diakrieten). Gemaakt, niet gedownload |
| 200.000 documenten | `fts_deadline.py … build`: een FTS5-tabel met gemaakte tekst, voor V3 en V4 (78 MB) |
| GeoNames cities1000 | uit de sdist van `reverse_geocoder` 1.5.1 op PyPI (`rg_cities1000.csv`, 144.563 plaatsen), CC BY 4.0 |
| Natural Earth 10m admin-0 | `https://raw.githubusercontent.com/nvkelso/natural-earth-vector/v5.1.2/geojson/ne_10m_admin_0_countries.geojson`: 13.287.234 bytes, sha256 `239eec57ac17f100a11e2536cffc56752c318b50ae765b0918ff7aab4ce8f255`, gzip −9: 4.723.035 bytes. Publiek domein |
| beelden | gemaakt met Pillow: foto's met EXIF en GPS, varianten (kleiner, JPEG opnieuw, bijgesneden, gedraaid), 150 brieven op één sjabloon, 150 chatschermen, 150 bijna egale beelden |
| een scan | `ocr/scan.pdf`: een A4-pagina zonder tekstlaag (2.482 × 3.509 px op 300 dpi), gemaakt met Pillow |

## De hulpscripts

Alle scripts gebruiken de standaardbibliotheek, en waar nodig Pillow en pypdfium2 uit `api/.venv`
(alleen gelezen). Er is niets geïnstalleerd in `api/.venv` en niets veranderd in
`pyproject.toml` of `package.json`. `$S` is de scratchmap `fase6/`, `$R` het repo.

- `$S/proef/`: de eerste proef (H1–H8): `gen_fts.py`, `bench_fts.py`, `fts_allroots.py`,
  `bench_extract.py`, `pdf_render.py`, `bench_ocr.py`, `deskew.py`, `bench_photo.py`,
  `bench_geo.py`, `bench_geo_rtree.py`.
- `$S/design-b/`: de proef van ontwerp B: `proef_zoek.py` (`build`, `query`, `churn`),
  `migratie.py`, `grootmap.py`, `prefix3.py`. De uitkomsten staan ernaast als JSON.
- `$S/critic/` en `$S/critic-sec/`: de scripts van de critici (`design_sql.py`, `churn_*.py`,
  `fts_deadline.py`, `geo_border.py`, `phash_docs.py`).
- `$S/verify/`: de scripts van deze revisie (`gaps.py`, `delall.py`, `phash_fix.py`,
  `pip_cost.py`, `ocr_chain.py`, `r2_sql.py`, `gencol.py`), met hun uitvoer als `.txt`.

## De getallen uit de eerste proef

Deze getallen komen uit de proef vóór revisie 1. Het ontwerp leunt erop; de commando's staan
hieronder.

| # | Wat | Uitkomst | Waar in het ontwerp |
|---|---|---|---|
| H2 | Zoeken met FTS5 en een pad als toegangsfilter, 100.000 bestanden | p95 17–42 ms, max 76–110 ms per profiel; een closure-tabel max 248 ms, de recursieve CTE van nu max 282 ms | Keuze 1, 2, 4 |
| H2 | Zoeken op een stuk van een naam met `trigram` | 2,3 ms, tegen 26,6 ms nu | Keuze 6, 9 (ronde C) |
| H2 | Een plan dat bij `node` begint en per rij `MATCH` doet | 107 s | Keuze 4 (`CROSS JOIN`), R2 |
| H3 | Tekst uit een PDF-pagina met pypdfium2 | 2,2 ms per pagina; Office 1–70 ms per bestand | Keuze 17, §3.10 |
| H3 | Een billion laughs in expat | stopt pas na 0,2–0,6 s; daarom de lezer die `DOCTYPE` weigert | Keuze 17, 25 |
| H4 | OCR met tesseract, A4 op 300 dpi, één core | 2,1–3,3 s per pagina, 70–155 MB RSS; lineair met processen | Keuze 22 |
| H4 | Eigen deskew in Pillow | 150–380 ms per pagina; zonder deskew breekt OCR vanaf ±7° | Keuze 22 |
| H5 | EXIF en GPS lezen | 0,24 ms per foto | Keuze 25, §3.10 |
| H5 | pHash in puur Python, en zoeken met 4 stukken van 16 bits | 0,9 ms per foto; 0,6 ms per zoektocht in 100.000 hashes; recall bij bijsnijden 0,71 en draaien 0,85 (afstand 10) | Keuze 35 |
| H6 | De dichtstbijzijnde plaats uit cities1000 in een R*Tree | 15 MB, 0,057 ms per foto, gelijk aan brute force | Keuze 29 |
| H8 | Ruimte per 100.000 bestanden (20.000 met tekst) | index ±120 MB, tekst ±257 MB, naamindexen ±15 MB | Keuze 1, R8 |

Commando's (in `$R/api`, of met `api/.venv/bin/python` voor
`$R/api/.venv/bin/python`):

```sh
uv run python $S/proef/gen_fts.py $S/data/fts_base.db
uv run python $S/proef/bench_fts.py build $S/data/fts_base.db $S/data/fts_w1.db
uv run python $S/proef/bench_fts.py query $S/data/fts_w1.db 3
uv run python $S/proef/fts_allroots.py $S/data/fts_w1.db
docker run --rm --name f6-fts -v $S:/w python:3.12-slim python /w/proef/bench_fts.py query /w/data/fts_w1.db 3 5,20
PYTHONPATH=$PWD uv run python $S/proef/bench_extract.py $S/data/extract
OMP_THREAD_LIMIT=1 api/.venv/bin/python -I $S/proef/pdf_render.py $S/data/extract/twintig.pdf
cd $S/data && python3 -I $S/proef/bench_ocr.py $S/data
cd $S/data && api/.venv/bin/python -I $S/proef/deskew.py ocr/rotated7.png ocr/rotated7_fixed.png
uv run python $S/proef/bench_photo.py $S/data/photos gen      # daarna: hash, index
api/.venv/bin/python -I $S/proef/bench_geo_rtree.py $S/data/dl-geo/reverse_geocoder-1.5.1/reverse_geocoder/rg_cities1000.csv $S/data/places.db
```

**De proef van ontwerp B** (in `python:3.12-slim`, SQLite 3.46.1; `migratie.py <bron.db>
<map van proef_zoek.py> <uit.db>`, `grootmap.py <werk.db> <map>`, `prefix3.py <werk.db> <map>`):

| Wat | Uitkomst | Waar in het ontwerp |
|---|---|---|
| Migratie 0010 op 104.696 nodes (paden, `dead`, FTS `rebuild` van de namen) | 2,82 s (paden 1,74 s, FTS 0,76 s); 0 paden fout; `integrity-check` ok | Keuze 2, §3.1 |
| Een map van 5.276 nodes verplaatsen | 55,7 ms (terug 66,2 ms); naar de prullenbak 31,3 ms, terug 39,7 ms | Keuze 11 |
| Een gebruiker met 10.911 nodes wissen via de FK-cascade, in één keer | 3.060,8 ms | Keuze 11 (daarom per 2.000) |
| Zoeken met snippets, zonder HTTP | p95 45,3–54,0 ms (1–20 delingen), 79,8 ms ("alles", 105.931 nodes); max 136,0 ms | §4.4 |
| `prefix='3'` op `fts_node` | `ver*` 59 → 28 ms, +62 MB | §4.4 |

## V1. De SQL van §3.1 van revisie 1 (C1, C2)

**Vraag.** Draait de SQL van revisie 1 letterlijk? En wat doen een upsert en `INSERT OR IGNORE`
met een external-content-index?

```sh
cd $R/api && .venv/bin/python $S/critic/design_sql.py
cd $S && P=$R/api/.venv/bin/python; $P critic/churn_upsert.py; $P critic/churn_orignore.py; $P critic/churn_plain.py
cd $S && docker run --rm --network none -v $PWD/critic:/w:ro python:3.12-slim python -I /w/churn_plain.py
cd $S && docker run --rm --network none -v $PWD/critic:/w:ro python:3.12-slim python -I /w/churn_upsert.py
```

**Uitkomst.**

- Revisie 1 letterlijk: `insert blob_text FAILED: no such column: blob_id`. De view had die kolom
  niet.
- Met `n.blob_id AS blob_id` en 3.000 willekeurige wijzigingen met alleen gewone schrijfacties
  (`churn_plain.py`): `integrity-check` ok, paden en `dead` kloppen, op 3.53.1 en 3.46.1.
- Dezelfde reeks met `INSERT … ON CONFLICT DO UPDATE` (`churn_upsert.py`) of `INSERT OR IGNORE`
  (`churn_orignore.py`) op `blob_text`: `database disk image is malformed`, op beide versies.

**Gevolg.** De view krijgt `blob_id`; upserts en `OR IGNORE` zijn verboden, met een wachter
(Keuze 3).

## V2. Een renderproces starten (C3)

**Vraag.** Wat kost het om per blob een nieuw Python-proces te starten?

```sh
cd $R/api && .venv/bin/python - <<EOF
import subprocess, time, resource, sys
for label, code in (("met app.render", "import sys; sys.path.insert(0, '$PWD'); import app.render"), ("kaal", "pass")):
    for i in range(3):
        r0 = resource.getrusage(resource.RUSAGE_CHILDREN)
        t = time.perf_counter(); subprocess.run([sys.executable, "-I", "-c", code], check=True); w = (time.perf_counter() - t) * 1000
        r1 = resource.getrusage(resource.RUSAGE_CHILDREN)
        print(f"{label}: wall {w:.0f} ms, cpu {(r1.ru_utime+r1.ru_stime-r0.ru_utime-r0.ru_stime)*1000:.0f} ms, maxrss {r1.ru_maxrss//1024} MB")
EOF
```

(`/usr/bin/time` is er niet; daarom `resource.getrusage`.)

**Uitkomst.** Met `import app.render`: 112–115 ms CPU per proces, ±19 MB RSS. Kaal: 24–33 ms.

**Gevolg.** Batches: één kind voor tot 16 blobs in de worker, ±7 ms start per blob (Keuze 17).

## V3. De tijdsgrens (S4, C4)

**Vraag.** Stopt een progress-handler een FTS5-vraag op tijd? Wat geeft een vraag die stopt
terug? En werkt `interrupt()` vanuit een timer?

```sh
cd $S && timeout 400 $R/api/.venv/bin/python critic-sec/fts_deadline.py verify/dl.db build
cd $S/verify && $R/api/.venv/bin/python -I gaps.py dl.db
cd $S/verify && docker run --rm --network none -v $PWD:/w python:3.12-slim python -I /w/gaps.py /w/dl.db
```

**Uitkomst** (200.000 documenten):

| Vraag | Zonder grens | Met grens |
|---|---|---|
| `ver*`, gerangschikt (`ORDER BY bm25`) | 1.881 ms (3.53.1), 2.307 ms (3.46.1) | progress-handler elke 1.000 stappen: grootste gat 144 ms (3.53.1) en 201 ms (3.46.1); elke 100 stappen: 192 en 165 ms |
| `ver*`, gerangschikt | — | `interrupt()` na 20 ms: gestopt na 21–26 ms, 0 rijen |
| `ver*`, alleen rowid, zonder `ORDER BY` | 916 ms | gestopt bij de grens: 0 rijen (FTS5 voegt eerst alle lijsten samen) |
| `"de"`, zonder `ORDER BY` | 31 rijen in 0,2 ms | — |
| `interrupt()` als er geen vraag loopt | — | de volgende vraag loopt gewoon |

**Gevolg.** `interrupt()` uit een timer; de rondes A, B en C (Keuze 9).

## V4. Herbouwen in stukken (S13)

**Vraag.** Wat kost `delete-all` en een herbouw in stukken van 2.000 op een external-content-index?

```sh
cd $S/verify && $R/api/.venv/bin/python -I delall.py dl.db
cd $S/verify && docker run --rm --network none -v $PWD:/w python:3.12-slim python -I /w/delall.py /w/dl.db
```

**Uitkomst** (200.000 rijen, 78 MB):

| | 3.53.1 | 3.46.1 |
|---|---|---|
| `rebuild` in één keer | 3,59 s | 4,01 s |
| `delete-all` | 17 ms | 250 ms |
| in stukken van 2.000, samen | 4,6 s | 6,0 s |
| langste stuk | 101 ms | 142 ms |
| `integrity-check` daarna | ok | ok |

**Gevolg.** De herbouw in stukken met een cursor (Keuze 12, §3.1).

## V5. Bijna-duplicaten (S1)

**Vraag.** Geeft pHash ≤ 6 echt "bijna zeker gelijk"? En welke tweede controle scheidt echte
varianten van andere bestanden?

```sh
cd $R/api && .venv/bin/python -I $S/verify/phash_fix.py $S/proef
```

**Uitkomst.**

- Spreiding van 32 × 32 grijs: bijna egale beelden 0,0 (max); foto's ≥ 6,5 (p1 10,1); brieven
  p5 23,4; chatschermen p5 44,4.
- 150 brieven op één sjabloon: 1.076 paren van verschillende brieven op pHash ≤ 6; dHash min 0,
  p50 8; MAD64 min 3,94, p5 4,83. 150 chatschermen: 0 paren. 150 egale beelden: 11.175 paren op
  pHash ≤ 6 (allemaal afstand 0).
- Echte varianten (pHash p95 | dHash max | MAD64 max):

| Variant | Foto | Brief |
|---|---|---|
| half zo groot | 2 \| 8 \| 0,92 | 2 \| 5 \| 2,18 |
| JPEG opnieuw | 4 \| 10 \| 1,04 | 2 \| 8 \| 1,08 |
| 3–8% bijgesneden | 18 \| 18 \| 14,91 | 20 \| 13 \| 19,31 |
| ±4° gedraaid | 12 \| 17 \| 9,83 | 22 \| 17 \| 19,38 |

**Gevolg.** Geen hash voor vlakke beelden (spreiding < 3); "zeker" alleen met verhouding binnen
2%, dHash ≤ 10 en MAD64 ≤ 3,0; groepen als klieken (Keuze 35, 36).

## V6. Het land bij een grens (S10)

**Vraag.** Hoe vaak geeft de dichtstbijzijnde plaats het verkeerde land? En wat kost een
punt-in-polygoon-test?

```sh
mkdir -p $S/dl-ne10 && cd $S/dl-ne10 && curl -fsS -o ne_10m_admin_0_countries.geojson https://raw.githubusercontent.com/nvkelso/natural-earth-vector/v5.1.2/geojson/ne_10m_admin_0_countries.geojson && sha256sum ne_10m_admin_0_countries.geojson
cd $S && $R/api/.venv/bin/python -I critic-sec/geo_border.py data/dl-geo/reverse_geocoder-1.5.1/reverse_geocoder/rg_cities1000.csv dl-ne10/ne_10m_admin_0_countries.geojson
cd $S/verify && $R/api/.venv/bin/python -I pip_cost.py ../dl-ne10/ne_10m_admin_0_countries.geojson
```

**Uitkomst.**

- 4.000 punten op land rond NL, BE, DE, LU en FR: 164 (4,1%) krijgen via de dichtstbijzijnde
  plaats het verkeerde land (bijvoorbeeld Frisange in LU voor een punt in FR, Zundert in NL voor
  een punt in BE).
- 1.000 punten op de Noordzee: 923 krijgen toch een land.
- Natural Earth 10m: 258 landen, 4.274 delen, 548.471 hoekpunten; als float32 met zlib 3,4 MB.
  Punt-in-polygoon in puur Python, met een kader per ring vooraf: 0,81 ms per punt.
- Kerkrade NL, Herzogenrath DE, Zundert NL, de Noordzee geen land. Baarle-Hertog: NL. De enclaves
  van Baarle staan niet in 10m.

**Gevolg.** Het land uit de polygonen; de plaats alleen in hetzelfde land (Keuze 29).

## V7. De OCR-keten per pagina (C10)

**Vraag.** Wat kost de hele keten op één core, en scheelt PGM tegen PNG?

```sh
cd $R/api && taskset -c 1 .venv/bin/python -I $S/verify/ocr_chain.py $S/ocr/scan.pdf
```

**Uitkomst** (A4, 2.482 × 3.509 px, één core, 3 runs):

| Stap | Tijd |
|---|---|
| PDF-pagina renderen naar grijs (300 dpi) | 170–210 ms |
| als PGM schrijven (8,5 MB) | 6–20 ms |
| als PNG (standaard) | 154–174 ms (niveau 1: 98 ms) |
| OCR `nld+eng`, `--psm 3`, TSV, van stdin | 1.905–2.052 ms (154 woorden, gemiddelde zekerheid 95) |
| OSD | 1.961–2.115 ms |

tesseract leest PGM van stdin.

**Gevolg.** PGM, OSD alleen bij een lage zekerheid, poort ≥ 8 pagina's per minuut per core,
±70–110 minuten per 1.000 pagina's (Keuze 22, Vraag 1).

## V8. Testen op SQLite 3.46.1 in de sandbox (C16, S18)

**Vraag.** Kunnen de tests op dezelfde SQLite als het image draaien, zonder te pushen?

```sh
cd $R && docker run --rm --network none -v $PWD/api:/api:ro -e PYTHONPATH=/api:/api/.venv/lib/python3.12/site-packages python:3.12-slim python -c 'import sys, app.main, sqlite3, pytest; print(sys.version.split()[0], sqlite3.sqlite_version, pytest.__version__); c=sqlite3.connect(":memory:"); c.execute("create virtual table t using fts5(a, tokenize=\"trigram\")"); print("fts5+trigram ok"); print(list(c.execute("select json_each.value from json_each(?)", ["[1,2]"])))'
docker run --rm --network none python:3.12-slim python -I -c 'import sqlite3; c=sqlite3.connect(":memory:"); print(sqlite3.sqlite_version, list(c.execute("""select 1 where "/1/7/" >= (select r.value ->> "$[0]" from json_each(?) r) """, ["[[\"/1/\", \"/10\"]]"])))'
```

**Uitkomst.** Python 3.12.16, SQLite 3.46.1, pytest 9.1.1; `app.main` laadt; `trigram`,
`json_each` en `->>` werken.

**Gevolg.** `e2e/slim.sh` (§4.1); de bereiken boven 64 wortels met `json_each` (Keuze 14).

## V9. In de code nagekeken (C7, C9, C13, S2, S3, S6, S8, S11, S12, S14, S16)

Geen meting, wel gelezen in `1130e1b`:

- een album deelt elk item, ook via een publieke link als origineel: `access.py:150–172`,
  `public.py:184–231` en `:316–334`;
- een kopie krijgt de eigenaar van de doelmap: `files.py:1394`;
- de tijdlijn en albums eisen al "levend": `photos.py:153–162`, `albums.py:122–133`;
- de worker draait hetzelfde image met dezelfde `/opt/venv`: `compose.media.yaml:27`,
  `Dockerfile`;
- pagina's van de viewer gaan door een limiter van 2 processen en een cache: `viewer.py`;
- één proces voor http en https: `serve.py`; `busy_timeout` 5 s: `db.py`;
- `SECRET_PARAMS` kent `q`, `bbox` en `place` niet: `main.py`;
- Pillow 13.0.0 leest XMP alleen met defusedxml, en dat zit niet in `uv.lock`;
  `app/office/reader.py` heeft een geharde `sax()`;
- `git show 1130e1b:api/app/cli.py | grep -c backup` geeft 0: 0.7.0 kent `db-backup` niet;
- `install.sh` gebruikt `BRANCH` (standaard `main`) en `PLATFORM_SRC_REF`, en doet
  `up -d --build` in één stap; `compose.yaml` geeft geen build-args door;
- `guard.BINARIES` is `("ffmpeg", "ffprobe")`;
- de paden zijn `web/src/ShareDialog.tsx` en `web/scripts/budget.mjs`.

## V10. De SQL van §3.1 van revisie 2 (C1, C2, C6, S8, S13)

**Vraag.** Klopt de SQL van revisie 2, met de foto-gegevens per node en de herbouw in stukken?
En werkt de virtuele kolom `ftype` met een partiële index?

```sh
cd $S/verify && python3 -I r2_sql.py
cd $S/verify && docker run --rm --network none -v "$PWD":/v:ro python:3.12-slim python -I /v/r2_sql.py
cd $S/verify && $R/api/.venv/bin/python -I r2_sql.py
cd $S/verify && python3 -I gencol.py; docker run --rm --network none -v "$PWD":/v:ro python:3.12-slim python -I /v/gencol.py
```

**Uitkomst** (SQLite 3.45.1, 3.46.1 en 3.53.1, alle drie gelijk):

- de view met `blob_id`, ß naar "ss" en `photo_ocr`; alle triggers met de cursor; alleen gewone
  `INSERT`, `UPDATE` en `DELETE`;
- 3.000 willekeurige wijzigingen (hernoemen, verplaatsen, prullenbak, tekst, OCR in een foto,
  andere eigenaar, nieuwe inhoud, wissen): `integrity-check` van `fts_node` en `fts_name` ok;
- een herbouw in stukken van 50 met 20 wijzigingen na elk stuk, en daarna nog 1.000: ok;
- 0 foto-rijen met een andere eigenaar dan de node; geen enkele fout;
- "strasse" vindt evenveel rijen in FTS als in de gevouwen bron (126 en 126);
- `ftype` als virtuele kolom op 100.000 rijen, met een partiële index: toevoegen ±190 ms, het plan
  gebruikt `ix_node_ftype_mod`, en `DROP COLUMN` werkt na `DROP INDEX`.

**Gevolg.** §3.1 staat zo in revisie 2.

## Wat niet gemeten is

- De CPU van Jonas: dat doet `bench --los` (Keuze 54) in week 1.
- Het openen van 55.000 foto's van de schijf van de LXC: de tijden in §3.10 rekenen alleen het
  werk zelf.
- De vormen van Keuze 4a over HTTP: spike S10 meet ze in Z0.
- De handdruk van Keuze 17 in de echte worker-container: spike S11.
- tesseract in Debian 13 (het image): spike S5, alleen in CI.

## Opgeruimd

De grote bestanden zijn na de meting uit scratch gewist: de database van 200.000 documenten
(78 MB na het bouwen, 155 MB na de proeven), de GeoJSON van Natural Earth (13 MB), de
wegwerp-venv voor de modellen (307 MB) en de modellen zelf (47 MB). De scripts, de uitvoer als
`.txt` en de kleinere testdata blijven in scratch. Niets daarvan staat in het repo.
