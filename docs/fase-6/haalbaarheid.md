# Fase 6: haalbaarheid (metingen)

> Bij het bouwontwerp, revisie 3 ([bouwontwerp.md](bouwontwerp.md)). Gemeten op 3 november 2026 in
> de sandbox: eerst voor het ontwerp (H1–H8 en de proef van ontwerp B), daarna om de kritiek op
> revisie 1 na te lopen (V1–V10). Voor revisie 3 kwamen gezichten en beeld erbij: de spike B1–B7,
> groeperen en wissen van gezichten, de proeven van de critici (K1, K2) en de installatie met
> `--target` (zie "Revisie 3: gezichten en beeld"). Er staan geen databases, beelden, modellen of
> datasets in het repo. De scripts en de uitkomsten staan in scratch (`fase6/` en `fase6-beeld/`),
> niet in het repo.

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

## Revisie 3: gezichten en beeld

Jonas koos op 3 november 2026 voor "alles", met gezichten en zonder tegels van buiten. Voor X2
(gezichten) en X3 (zoeken op beeld) is daarna gemeten in drie rondes: de spike B1–B7, twee
metingen voor gezichten (groeperen en wissen), en de proeven van de twee critici van revisie 3
(K1, K2). Bij het schrijven van revisie 3 zijn een deel van die proeven opnieuw gedraaid; dat
staat er per proef bij. Daarnaast is de installatie met `--target /opt/analyse` geprobeerd.

`$B` is de scratchmap `fase6-beeld/` (niet in het repo). De scripts en kleine uitkomsten staan
daar: `$B/spike/` (B1–B7, met `verslag.md`), `$B/gezichten/`, `$B/kritiek1/`, `$B/kritiek2/` en
`$B/r3/`.

| | |
|---|---|
| Machine | dezelfde sandbox als hierboven (4 vCPU, AVX-512 en VNNI) |
| Python | 3.12.3 op de host, 3.12.16 in `python:3.12-slim` |
| Pakketten | numpy 2.5.4 (scipy-openblas 0.3.34), onnxruntime 1.31.1, Pillow 13.0.0 (staat al in `api/uv.lock`), in een eigen venv in `$B/spike/venv`, nooit in `api/.venv` |
| Zwaar werk | `taskset -c 0-2`; op één core `taskset -c 0` met `OPENBLAS_NUM_THREADS=1` |

De VNNI van de sandbox maakt int8 snel. Of de CPU van Jonas dat heeft, is onbekend (BZ9).

## B1. numpy en onnxruntime in een eigen venv (Keuze 57)

**Vraag.** Wat kosten numpy en onnxruntime, en werkt de import, ook offline in het image?

```sh
cd $B/spike
pip download numpy onnxruntime -d b1/wheels --only-binary=:all:
python3.12 -m venv venv && venv/bin/pip install --no-index --find-links b1/wheels numpy onnxruntime
du -sh venv/lib/python3.12/site-packages/*
docker run --rm --network none -v "$PWD/b1":/b1:ro python:3.12-slim …   # dezelfde wheels, offline
```

**Uitkomst.**

- Vijf wheels, samen 40 MB: numpy 2.5.4 (16,7 MB), onnxruntime 1.31.1 (23,8 MB), protobuf 7.36.2,
  flatbuffers 25.12.19 en packaging 26.3.
- Geïnstalleerd ±140 MB (numpy 43 MB, numpy.libs 27 MB, onnxruntime 68 MB); in
  `python:3.12-slim` 153 MB.
- Import: numpy 91–122 ms, onnxruntime 37–57 ms, RSS 44–53 MB.
- onnxruntime meldt twee providers: `AzureExecutionProvider` en `CPUExecutionProvider`. Het
  ontwerp kiest altijd alleen de tweede, en controleert dat (K1-12).

**Gevolg.** De extra `analyse` heeft precies vijf pakketten; R24 en de poort ≤ +160 MB in de job
`images`.

## B2. Zelf een klein ONNX-model maken (testen zonder echte gewichten)

**Vraag.** Kunnen tests hun eigen modellen bouwen, zonder download en zonder gewichten in het
repo?

```sh
cd $B/spike
python3 -I scripts/b2_bouw.py b2/modellen          # alleen de standaardbibliotheek (onnxw.py)
venv/bin/python -I scripts/b2_draai.py b2/modellen
```

**Uitkomst.** Drie nepmodellen (de vormen van SFace, YuNet en een tekstmodel), gebouwd zonder
numpy. Twee keer bouwen geeft dezelfde sha256. onnxruntime en numpy rekenen hetzelfde, tot
6,33·10⁻⁸. Een token buiten de woordenlijst geeft een nette fout. De vorm van YuNet draait op
640×480 tot 1024×768 in 1,9–2,9 ms.

**Gevolg.** Laag 2 van de tests (X2, "Tests") en de nep-worker gebruiken dit.

## B3. Waar de gewichten vandaan komen

**Vraag.** Welke bronnen zijn bereikbaar, voor `install.sh` en voor CI?

```sh
cd $B/spike && sh scripts/b3_reach.sh > b3/reach.txt   # curl --max-time 10 per URL
```

**Uitkomst.**

| Host | Uitkomst |
|---|---|
| huggingface.co, cdn-lfs.huggingface.co, hf-mirror.com | 000: de proxy van de sandbox weigert |
| upload.wikimedia.org, nasa.gov, images-assets.nasa.gov | 000 |
| github.com, codeload.github.com | 400 of 403 |
| raw.githubusercontent.com | 200, maar voor een LFS-bestand alleen de pointer (131 B) |
| media.githubusercontent.com (LFS van opencv_zoo) | 200, het echte bestand (YuNet: 232.589 B) |
| pypi.org, registry.npmjs.org | 200 |

YuNet en SFace zijn gedownload vanaf commit `48b10fa505aeb51fbd127fc60a5c660040e823b7` van
opencv_zoo; de sha256 van elk bestand is gelijk aan die in de LFS-pointer.

**Gevolg.** `install.sh` downloadt op de host, met sha256, naar `MODELLEN_ROOT` (Keuze 58). Of
een runner van GitHub bij huggingface.co kan, is **niet aangetoond** (BZ1, R41).

## B4. Licenties

**Vraag.** Mogen de gewichten privé en commercieel gebruikt worden?

Gelezen in de bestanden zelf (`$B/spike/b4/`) en op de pagina's van de modellen:

| Model | Licentie van de gewichten | Oordeel |
|---|---|---|
| YuNet (opencv_zoo) | MIT ("All files in this directory are licensed under MIT License") | mag; de MIT-tekst moet mee (K1-13) |
| SFace (opencv_zoo) | Apache-2.0 | mag |
| SigLIP 2 B/32-256 (`google/siglip2-base-patch32-256`) | "License: apache-2.0" | mag |
| InsightFace | "for non-commercial research only" | valt af |
| MobileCLIP (Apple) | "exclusively for Research Purposes" | valt af |
| nllb-clip-base-siglip | cc-by-nc-4.0 | valt af |
| clip-ViT-B-32-multilingual-v1 | tekstkant Apache-2.0, het beeldmodel is OpenAI CLIP ("out of scope") | valt af |

- De int8bq-varianten van YuNet en SFace draaien niet in onnxruntime 1.31.1. Het ontwerp gebruikt
  YuNet 2026may en SFace 2021dec in fp32.
- Een int8-versie van SigLIP 2 bestaat niet als download; de omzetter maakt hem zelf (Keuze 73).
- **Rechtgezet bij revisie 3 (K1-14).** De spike noemde de meetlat voor B5,
  `@lenml/tokenizer-gemma` 3.7.2, "MIT". npm meldt **Apache-2.0**. Beide labels zijn van de maker
  van het npm-pakket; de woordenlijst zelf komt van Google, en onder welke voorwaarden die valt, is
  niet nagekeken. Dat bestand komt nooit in het repo, in CI of in de docs. BZ3 vergelijkt alleen
  met het bestand van SigLIP 2 zelf.
- De herkomst van de **trainingsdata** van YuNet, SFace en SigLIP 2 is niet nagekeken (K1-13,
  R36).

## B5. De tokenizer zonder `transformers`

**Vraag.** Kan een Nederlandse zoekvraag tokens worden met alleen de standaardbibliotheek?

```sh
cd $B/spike
venv-meetlat/bin/python -I scripts/b5_vergelijk.py <tokenizer.json> scripts   # meetlat: tokenizers 0.23.3
```

**Uitkomst.** 2.181 zinnen (Nederlandse zoekvragen, willekeurige zinnen, Unicode uit veel
schriften, randgevallen):

- `encode()` tegen de bibliotheek `tokenizers`: 0 verschillen;
- kleine letters met `str.lower()` op de hele tekst: 1 verschil (`'ΣΟΦΙΑ ΟΔΟΣ'`, de slot-sigma);
  per teken: 0 verschillen;
- de hele SigLIP-vorm (kleine letters, afkappen, `<eos>`, opvullen tot 64): 0 verschillen;
- laden 1,35 s en 245 MB maxrss voor een JSON van 17,5 MB; encode mediaan 0,11 ms.

**Wat dit niet bewijst.** Gemeten met het tokenizer-bestand van Gemma (de meetlat van B4), niet
met dat van SigLIP 2 (34,4 MB), want huggingface.co was dicht. Of die gelijk zijn, is onbekend.
Dat doet BZ3 in CI. De wegwerp-venv met `tokenizers` is gewist.

## B6. Rekenkosten

**Vraag.** Wat kost een foto voor gezichten en voor beeld, en wat kost een zoekvraag, op één
core?

```sh
cd $B/spike
OPENBLAS_NUM_THREADS=1 taskset -c 0 venv/bin/python -I scripts/b6_gezichten.py <modellen> <foto> 1
venv/bin/python -I scripts/b6_jpeg.py
venv/bin/python -I scripts/b6_proxy.py build|run …      # SigLIP 2 met willekeurige gewichten, dezelfde vormen
OPENBLAS_NUM_THREADS=1 taskset -c 0 venv/bin/python -I scripts/b6_vec.py 100000 768 5
```

**Uitkomst** (mediaan, één thread):

| Stap | Tijd |
|---|---|
| YuNet 2026may op 1024×640 | 38,0–41,0 ms |
| SFace fp32, per gezicht | 52,1–59,5 ms |
| NMS en uitlijnen (5 punten) | 1,2–1,4 ms |
| beeldmodel B/32-256 int8 | 66,3–76,5 ms |
| tekstmodel (256k tokens) int8 | 62,4–62,7 ms (p95 69–74) |
| zoeken over 100.000 vectoren (768, float32) | 35,3–41,9 ms (p95 40,6–50,6) |
| een JPEG van 12 MP decoderen en verkleinen (synthetisch) | 192–245 ms; met `draft` 75–137 ms |

- Eén testfoto (`messi5.jpg` van OpenCV): één gezicht, score 0,915. Dat zegt niets over de
  kwaliteit tussen personen: er waren geen foto's van meer mensen.
- In puur Python kost één zoekvraag over 100.000 vectoren 5,3–10,7 s. Daarom zoekt de container,
  niet de app.
- Een volle int8-versie van het echte model is **niet** gemeten; de proxy heeft dezelfde vormen,
  niet dezelfde gewichten (BZ4).

**Gevolg.** De kosten per foto zijn na K2-04 opnieuw gerekend: zie "De proeven van de kritiek".

## B7. Vectoren als BLOB in SQLite

**Vraag.** Hoeveel ruimte kost een vector per foto en per gezicht?

```sh
cd $B/spike && taskset -c 0-2 python3.12 -I scripts/b7_sqlite.py b7/db 100000 150000
# ook in python:3.12-slim (SQLite 3.46.1); de groottes waren gelijk aan die op 3.45.1
```

**Uitkomst** (page size 4096, na `VACUUM`):

| Wat | Bestand | Per rij |
|---|---|---|
| 100.000 × 768 float32 | 410,6 MB | 4.106 B |
| **100.000 × 768 float16** | **205,3 MB** | 2.053 B |
| 100.000 × 768 int8 + schaal | 82,1 MB | 821 B |
| 150.000 gezichten (vak, score, 128 × float32, index) | 89,7 MB | 598 B |

float16 kost in de recall niets (B6: recall@50 1,0 als hij in het geheugen float32 wordt).

**Gevolg.** `blob_embed.vec` is float16 (Keuze 76); de ruimte staat in §3.1a en bij Keuze 84.

## Gezichten groeperen (`groep.py`, Keuze 62)

**Vraag.** Wat kost groeperen van gezichtsvectoren met numpy op één core?

```sh
cd $B/gezichten
OPENBLAS_NUM_THREADS=1 taskset -c 2 ../spike/venv/bin/python -I scripts/groep.py 5000 512 3
OPENBLAS_NUM_THREADS=1 taskset -c 2 ../spike/venv/bin/python -I scripts/groep.py 20000 512 2
OPENBLAS_NUM_THREADS=1 taskset -c 2 ../spike/venv/bin/python -I scripts/groep.py 60000 512 2
```

**Uitkomst** (`uit/groep2_*.json`; synthetische vectoren, 128 × float32):

| Gezichten | Buren | Groepen | Voorstellen | maxrss |
|---|---|---|---|---|
| 5.000 | 0,19–0,25 s | 0,11 s | 0,03–0,04 s | 60 MB |
| 20.000 | 3,5 s | 0,5–0,8 s | 0,45–0,56 s | 122 MB |
| 60.000 | 31,8–33,6 s | 2,0 s | 3,9–4,3 s | 285 MB |

- De eerste vorm (blokken van 1.024, zonder drempel vooraf) kostte bij 60.000 74–76 s en 1.376 MB
  (`uit/groep_60000.json`).
- Op synthetische data: zuiverheid 1,0, recall 0,986–0,989, 0–5 foute voorstellen. Dat zegt
  **niets** over echte gezichten.
- Tijdens de meting draaide er ook een pytest op de machine.

**Gevolg.** `GROUP_MAX` is 60.000 (K2-06); het geheugen van de container staat in §3.11.

## Gezichten wissen (`wissen.py`, Keuze 64)

**Vraag.** Staan de bytes van een gewiste vector nog in `platform.db` of in de WAL?

```sh
cd $B/gezichten
python3 -I scripts/wissen.py <lege map>     # SQLite 3.45.1; daarna hetzelfde in python:3.12-slim (3.46.1)
```

**Uitkomst** (3.000 rijen in WAL-modus; een derde met een merkteken, 32.000 merktekens; beide
versies gelijk, `uit/wissen_host.txt` en `uit/wissen_slim.txt`):

| Manier | in `.db` | in `-wal` | Checkpoint (busy, log, klaar) |
|---|---|---|---|
| gewone `DELETE`, PASSIVE | 0 | 96 | 0, 444, 444 |
| `secure_delete` en TRUNCATE | 0 | 0 | 0, 0, 0 |
| hetzelfde, met een open lezer | 32.000 | 96 | 1, 444, 0 |
| na het sluiten van de lezer, opnieuw TRUNCATE | 0 | 0 | 0, 0, 0 |

**Gevolg.** Wissen gaat met `secure_delete`, en TRUNCATE wordt herhaald tot hij lukt. Dat
gebeurt op een eigen verbinding met `busy_timeout` 0 (K2-08, zie hieronder).

## De proeven van de kritiek (K1, K2)

De critici draaiden hun proeven zelf. Bij het schrijven van revisie 3 zijn de goedkope proeven
opnieuw gedraaid (`$B/r3/na/`); dat staat er bij. Wat niet opnieuw gedraaid is, staat er ook bij.

### K1-01. Een reservekopie zonder gezichten, via het geheugen

```sh
python3 -I $B/kritiek1/backup/kopie.py <bron.db> <kopie.db>
```

5.000 foto's met een merkteken in elke vector. Eerst `backup()` naar `:memory:`, daar
`DELETE FROM face`, dan `VACUUM INTO` naar schijf. **Opnieuw gedraaid:** "kopie 94208 B;
merktekens: 0; nodes: 5000; face-rijen: 0". De criticus zag met `strace` ook 0 tijdelijke
bestanden op schijf. **Gevolg:** Keuze 52 en 64; de reservekopie heeft geen gezichten.

### K1-03. SQLite sorteert in een tijdelijk bestand

```sh
N=20000 strace -f -y -e trace=write,pwrite64 -s 1200 python3 -I $B/kritiek1/tempstore/spill.py <t.db> default
N=20000 strace -f -y -e trace=write,pwrite64 -s 1200 python3 -I $B/kritiek1/tempstore/spill.py <t.db> memory
```

20.000 vectoren van 512 B, gesorteerd op een kolom zonder index. `pragma compile_options` geeft
`TEMP_STORE=1`, en `pragma temp_store` is 0: standaard naar een bestand. **Opnieuw gedraaid:**
41.232 schrijfacties naar een ontkoppeld `etilqs_*`-bestand; in de eerste 1.200 bytes van 36.058
daarvan staat het merkteken (de criticus zag het met een langere uitsnede in alle 41.232). Met
`PRAGMA temp_store=MEMORY`: 0 schrijfacties. **Gevolg:** `temp_store=MEMORY` in de connect-haak
(A0) en een test per verbinding (Keuze 64).

### K1-04. Twee processen met dezelfde uid lezen elkaars bestanden

In `python:3.12-slim` met `--user 10003 --cap-drop ALL --security-opt no-new-privileges
--read-only` las een broerproces via `/proc/<pid>/fd` de memfd van een ander
("werk leest via /proc: b'VECTOR-VAN-EVA'"). `/usr/local/bin/python3.12` is 0755, dus na `exec`
dumpbaar. **Niet opnieuw gedraaid.** **Gevolg:** elk kind doet eerst
`prctl(PR_SET_DUMPABLE, 0)`; de test in laag 2 eist `EACCES` (Keuze 56). Aparte uid's per kind
zijn afgewezen (§6 van het bouwontwerp).

### K1-05. `compose run` erft de sleutel en de socket

```sh
cd $B/kritiek1/composerun
ANALYSE_WORKER_KEY=<nep> docker compose -p r3-proef-run -f compose.yaml run --rm --no-deps analyse \
  python -I -c "import os; print('PLATFORM_ANALYSE_WORKER_KEY' in os.environ, os.path.ismount('/run/analyse'))"
```

**Opnieuw gedraaid:** "sleutel in de omgeving: True", "socketmap gekoppeld: True". **Gevolg:** een
eigen dienst `analyse-omzetten` zonder sleutel, socket en `/models` (Keuze 73).

### K1-06. De app plant een symlink, root volgt hem

In een map van uid 10001 een symlink `modellen/face.onnx` naar een bestand van root (0600).
**Opnieuw gedraaid** (in scratch, met een eigen doelbestand): na `cp` en `chmod 644` als root op
`modellen/face.onnx` is het doelbestand 0644 en is de inhoud vervangen. `[ -L ]` ziet de symlink
wel. **Gevolg:** `MODELLEN_ROOT` buiten `$DATA_ROOT`, elke map van root, symlinks weigeren, en een
test die er een plant (Keuze 58).

### K2-04. De kosten per foto, met de echte bron

```sh
OPENBLAS_NUM_THREADS=1 taskset -c 1 $B/spike/venv/bin/python -I $B/kritiek2/meet.py $B/kritiek2/model/yunet.onnx
OPENBLAS_NUM_THREADS=1 taskset -c 1 $B/spike/venv/bin/python -I $B/kritiek2/meet2.py
```

| Stap | Criticus (2 runs) | Opnieuw gedraaid (2 runs) |
|---|---|---|
| YuNet 2026may 1024×640 | 29,7–33,6 ms | 24,3–28,3 ms |
| YuNet 2026may 1024×768 | 40,3–42,9 ms | 36,3–37,4 ms |
| WebP 1024×768 decoderen (±140 kB) | 20,8–23,0 ms | 20,0–21,5 ms |
| van 1024×768 naar 256×256 | 8,8 ms | 8,6–8,7 ms |
| WebP 256×192 decoderen | 2,4 ms | 2,3–2,4 ms |

Met SFace (B6) erbij is een foto met één gezicht ±142–164 ms (55.000 foto's: ±2,2–2,5 core-uur).
Dat is de eerste schatting (±112–126 ms) plus 25–30%; de stappen in de tabel van X2 opgeteld
geven ±130–148 ms. Het ontwerp rekent met het hogere getal.
Beeld uit de preview van 256 px: ±69–80 ms (2,4 ms decoderen plus 66–77 ms model, B6; 55.000
foto's: ±1,1–1,2 core-uur). Het beeld is synthetisch; echte previews kunnen anders uitvallen.
**Gevolg:** de poorten in X2 en X3, en de kosten in de README.

### K2-05. YuNet en een hoogte die geen veelvoud van 32 is

```sh
OPENBLAS_NUM_THREADS=1 taskset -c 0 $B/spike/venv/bin/python -I $B/r3/na/yunet/vorm.py $B/kritiek2/model/yunet.onnx
```

**Opnieuw gedraaid** (sha256 `ebafce4e3c11…`): 1024×683 geeft `[ONNXRuntimeError] … Add node.
Name:'Add_50'`; 1024×704 werkt (33,3 ms), 1024×768 ook (37,3 ms). `render.py` maakt een preview
van 1024 px zonder te vergroten, dus 683 komt echt voor. **Gevolg:** de worker vult het beeld op
tot een veelvoud van 32 (Keuze 60).

### K2-08. Een checkpoint met `busy_timeout` 5000 laat schrijvers wachten

```sh
python3 -I $B/kritiek2/ckpt.py <lege map>
docker run --rm --network none -v $B/kritiek2:/k:ro -v <lege map>:/d python:3.12-slim python -I /k/ckpt.py /d
```

Een lezer houdt een snapshot vast; dan een TRUNCATE en een schrijver. **Opnieuw gedraaid**, op
3.45.1 en 3.46.1 gelijk:

| `busy_timeout` van het checkpoint | Uitkomst (busy, log, klaar) | Duur | De schrijver wachtte |
|---|---|---|---|
| 5000 | 1, 12, 11 | 5.014–5.016 ms | 4.845–4.847 ms |
| 0 | 1, 13, 11 | 0 ms | 1–2 ms |

**Gevolg:** het wissen en het checkpoint van elk uur gebruiken een eigen verbinding met
`busy_timeout` 0 en proberen het later opnieuw (Keuze 64, R26).

### K2-09. `o-rwx` in `/opt/venv` geeft een leeg namespace-pakket

Een map met groep 10003 en `o-rwx`, geïmporteerd als uid 10001 via `setpriv`: "import gelukt:
<module 'fakenp' (namespace)>". Een ontoegankelijke map telt als namespace-portie. **Niet opnieuw
gedraaid**; de proef met `--target` hieronder laat zien dat de oplossing werkt.

### K2-10. Een test die numpy bovenaan importeert, breekt `api` al bij het verzamelen

Een module met de marker `analyse` en een ontbrekende import bovenaan, met `-m 'not analyse'` in
`addopts`: "ERROR collecting … ModuleNotFoundError … Interrupted: 1 error during collection".
Pytest importeert eerst, en kiest pas daarna op marker. **Niet opnieuw gedraaid.** **Gevolg:** de
tests met numpy staan in `api/tests_analyse/`, buiten de `testpaths` van `api`, met een telling in
`api-analyse` (Keuze 57).

## De proef met `--target /opt/analyse` (Keuze 57, K2-09, K2-15)

**Vraag.** Kan de extra buiten `/opt/venv`, met precies vijf pakketten, en zien uid 10001 en 10002
hem dan echt niet?

```sh
cd $B/r3/proef-target        # een kopie van api/pyproject.toml met de extra, en uv.lock
uv export --frozen --no-dev --no-emit-project --no-hashes -o basis.txt
uv export --frozen --no-dev --extra analyse --no-emit-project --no-hashes -o alles.txt
grep -E '^[a-z]' alles.txt | sort > a; grep -E '^[a-z]' basis.txt | sort > b; comm -23 a b > extra.txt
uv pip install --python-version 3.12 --python-platform x86_64-manylinux_2_28 \
  --target opt-analyse --no-deps -r extra.txt
du -sh opt-analyse; du -sh --apparent-size opt-analyse
# een kopie, root:10003 en 0750; dan per uid:
setpriv --reuid <uid> --regid <uid> --clear-groups /usr/bin/python3.12 -I -c \
  "import sys, importlib.util; sys.path.insert(0, '<kopie>'); print(importlib.util.find_spec('numpy'))"
```

**Uitkomst.**

- `extra.txt` heeft precies vijf regels: flatbuffers 25.12.19, numpy 2.5.4, onnxruntime 1.31.1,
  packaging 26.3, protobuf 7.36.2. packaging komt nu alleen via pytest (dev) in de lock; in het
  image is hij nieuw (K2-15).
- `opt-analyse` is 118–121 MB (`du`, met en zonder `--apparent-size`).
- uid 10001 en 10002: `find_spec("numpy")` is `None`, ook met de map zelf in `sys.path`.
- uid 10003: numpy 2.5.4 met `ndarray`, en onnxruntime met de providers `AzureExecutionProvider` en
  `CPUExecutionProvider`.

**Gevolg.** Het blok in de Dockerfile van §3.11, met `test "$(wc -l < /tmp/analyse.txt)" -eq 5`,
en de controle met `find_spec` in de job `images`. Het image zelf is niet gebouwd: dat gebeurt
alleen in CI.

## Wat niet gemeten is

- De CPU van Jonas: dat doet `bench --los` (Keuze 54) in week 1.
- Het openen van 55.000 foto's van de schijf van de LXC: de tijden in §3.10 rekenen alleen het
  werk zelf.
- De vormen van Keuze 4a over HTTP: spike S10 meet ze in Z0.
- De handdruk van Keuze 17 in de echte worker-container: spike S11.
- tesseract in Debian 13 (het image): spike S5, alleen in CI. **Gemeten op 4 november 2026:
  69 MiB**, boven de poort van ≤ +30 MB; zie [spike.md](spike.md#s5-tesseract-in-debian-13) en
  Vraag 15 (Jonas: "Ja, erin").

Bij revisie 3 (gezichten en beeld):

- **Het echte SigLIP 2.** huggingface.co is vanuit de sandbox dicht (B3). Niet gemeten: de
  omzetting met eigen code, de kwaliteit van int8 tegen fp32, de kwaliteit van Nederlandse
  zoekvragen, en het tokenizer-bestand van SigLIP 2 zelf. Dat doen BZ1–BZ4 in de job
  `beeld-model`, of op de LXC met `app.cli beeld-kwaliteit`. De tijden van B6 komen van een model
  met dezelfde vormen en willekeurige gewichten.
- **Echte gezichten.** Er waren geen foto's met meer mensen; NASA en Wikimedia zijn vanuit de
  sandbox dicht. Niet gemeten: de kwaliteit van de voorstellen tussen personen, RGB tegen BGR bij
  SFace, en of "Meer zoals deze" op een portret dezelfde persoon terugvindt (BZ10). Groeperen is
  alleen op synthetische vectoren gemeten.
- **Of een runner van GitHub bij huggingface.co kan** (BZ1): geen bestaande job doet het.
- **De CPU van Jonas**: VNNI of niet (BZ9). Zonder VNNI kan int8 duidelijk trager zijn.
- **Het image met de extra en met tesseract**: niet gebouwd in de sandbox, alleen in CI (job
  `images`). De grootte van de extra (118–121 MB) komt van de proef met `--target`, niet van een
  echt image.
- **De container `analyse` zelf**: het werkmodel met twee kinderen, `RLIMIT_DATA`, de wachter en
  het geheugen samen (BZ5) zijn ontwerp; ze worden gemeten in X2a en X3a.
- **Juridisch**: de herkomst van de trainingsdata (K1-13) en de AVG bij gebruik buiten het gezin
  (K1-11) zijn niet door een jurist nagekeken.
- **K1-04, K2-09 en K2-10** zijn de proeven van de critici; die zijn niet opnieuw gedraaid.

## Opgeruimd

De grote bestanden zijn na de meting uit scratch gewist: de database van 200.000 documenten
(78 MB na het bouwen, 155 MB na de proeven), de GeoJSON van Natural Earth (13 MB), de
wegwerp-venv voor de modellen (307 MB) en de modellen zelf (47 MB). De scripts, de uitvoer als
`.txt` en de kleinere testdata blijven in scratch. Niets daarvan staat in het repo.

Bij revisie 3 zijn gewist: de modellen en de testfoto van de spike, de wegwerp-venv met
`tokenizers`, het npm-pakket met de Gemma-tokenizer, de proxymodellen van SigLIP 2 (1,6 GB), de
databases van de proef met tijdelijke bestanden (K1-03, ±250 MB), de map `opt-analyse` van de
proef met `--target` (±121 MB) en de databases van de nieuwe runs. De venv van de spike
(`$B/spike/venv`, 177 MB) en de wheels van B1 (40 MB) blijven staan voor wie X2a en X3a bouwt.
De proef met `compose run` liet geen container of volume achter.
