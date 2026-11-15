# Runbook: de analyse (gezichten en zoeken op beeld)

De container `analyse` (fase 6, §3.11) zoekt gezichten (X2) en zet foto's om in vectoren om op te
zoeken met woorden of vanuit een foto (X3). In 0.8.0 staat hij uit; hij komt met 0.8.1. Het deel
gezichten en de container zelf schrijft X2; hieronder staat het deel van X3a.

## Zoeken op beeld (X3)

Golf 5 (X3a) bouwt de tokenizer, de omzetter van SigLIP 2 en de CI-job `beeld-model`. Op de LXC
verandert er nog niets: `install.sh` haalt SigLIP 2 niet op en zet niets om; dat komt in golf 6
(X3b, de functies voor `BEELD` in `install.sh`). Wat er gebouwd is en wat nog open staat:
[docs/fase-6/x3a.md](../fase-6/x3a.md).

### De job `beeld-model`

De job haalt het echte model (`google/siglip2-base-patch32-256`, ±1,55 GB) en de 120 foto's van de
kwaliteitsset, zet het model twee keer om en draait de poorten van Keuze 83. Hij draait alleen:

- bij een push of PR die `app/analyse/worker/omzetten/`, `app/analyse/tokenizer.py`,
  `app/analyse/onnxw.py` of `app/analyse/models.lock` verandert (`git diff --name-only`);
- of als de tekst van de PR een regel `beeld-model: ja` heeft.

Er is geen knop om hem met de hand te starten en geen cache. Draait hij niet, dan zegt het blok
`=== kort ===` aan het eind van het log "beeld-model: draaien nee" met de reden.

Het blok `=== kort ===` staat aan het eind van het log, met per regel:

- `BZ1 <host>: bereikbaar (HTTP …)` of `niet bereikbaar (…)`, voor huggingface.co,
  commons.wikimedia.org, upload.wikimedia.org en images-assets.nasa.gov;
- `BZ2 …`: de commit, de tijd en maxrss van elke omzetting (grens 1,5 GB), of twee omzettingen
  dezelfde sha256 geven, en of ze kloppen met `models.lock`;
- `BZ3`, `BZ4`, `BZ6`, `BZ7`: de uitkomsten van de tests, met `goed` of `FOUT`;
- `UITSLAG …`: waarom de job rood is, als er een voorstel ligt;
- per pytest-stap de laatste regel.

Het hele verslag staat bij de run als artifact `beeld-model` (`kort.txt`, `bz3.json`, `bz4.json`,
`bz6.json`, `bz7.json`, de voorstellen en de logs van de omzetter, en `uit1/meta.json`).

### Na de eerste run: de regels vastzetten

Zolang `app/analyse/models.lock` geen regels van SigLIP heeft, of
`api/tests/analyse/sets/beeld.tsv` ontbreekt, stelt de job ze voor en is hij rood met
"models.lock mist de regels van SigLIP" en "beeld.tsv ontbreekt". Dat is de bedoeling. De job zet
zelf nooit iets in het repo; een mens doet dat, zo:

1. Kijk in `=== kort ===` of BZ1 bij huggingface.co en Commons kon. Kon dat niet, dan gaat de rest
   naar de LXC (X3b: `beeld-kwaliteit`, `beeld-bench`, trust-on-first-use).
2. Kijk of BZ2 zegt "dezelfde sha256 voor alle 5 bestanden" en of maxrss onder de 1,5 GB bleef.
   Zo niet: niets vastzetten, eerst de omzetter nakijken.
3. Haal het artifact `beeld-model` op. `voorstel-models.lock` heeft tien regels: vijf bronnen
   (`siglip2-b32-256/bron/<naam>` met de URL op één vaste commit) en vijf uitkomsten
   (`siglip2-b32-256/<naam>` met url `-`). Kijk na: de commit in de URL's is dezelfde als in het
   blok, `model.safetensors` is ±1,51 GB en `tokenizer.json` ±34,4 MB.
4. Kijk `voorstel-beeld.tsv` door: 120 foto's (12 categorieën × 5, de 6 vaste foto's van de
   combinaties, 60 andere), elk met sleutel, categorieën, licentie (CC0 of publiek domein),
   sha256, bytes, URL op upload.wikimedia.org en de bestandspagina op Commons. Staat er toch een
   herkenbaar persoon op een foto, vervang die regel door een andere foto van Commons met dezelfde
   sleutel-soort, categorie en licentie, met zijn eigen sha256 en grootte. Weglaten kan niet: de
   controle van de set eist die aantallen (`beeld_model.py`, `lees_set`; `test_beeld_sets.py`).
5. Zet in één commit:
   - de tien regels onder de regels van de gezichten in `app/analyse/models.lock`, met een
     opmerking erboven (de commit, Apache-2.0, de licentie in `licenties/`);
   - `voorstel-beeld.tsv` als `api/tests/analyse/sets/beeld.tsv`;
   - de nieuwe exacte lijst in `api/tests/analyse/test_models_lock.py`
     (`test_de_twee_modellen_van_gezichten` kent nu alleen de twee regels van de gezichten).
6. Push. De job draait dan weer (de lock is veranderd), nu streng: een andere sha256 of grootte
   is rood. Groen is BZ2 en BZ4 gehaald: dan kan de go/no-go van X3 (Keuze 73) beslist worden.

Let op bij stap 5: `install.sh` (X2) haalt elke regel van `models.lock` met een URL op zodra de
analyse aan staat. Met de bronregels erin zou dat ook de 1,51 GB van `model.safetensors` zijn,
ook zonder zoeken op beeld. Zet de regels daarom pas als `install.sh` de regels onder `*/bron/`
overslaat (X3b), of samen met die wijziging (x3a.md, Open 1).

### De omzetter met de hand draaien (alleen om te testen)

In de repo, met de extra analyse (`UV_PROJECT_ENVIRONMENT=.venv-analyse uv sync --frozen --extra
analyse`), en een map met de vijf bronbestanden:

```bash
cd api
BOOT="import runpy,sys;sys.path.insert(0,sys.argv.pop(1));runpy.run_module('app.analyse.worker.omzetten.siglip',run_name='__main__',alter_sys=True)"
UV_PROJECT_ENVIRONMENT=.venv-analyse uv run --no-sync python -I -c "$BOOT" "$PWD" --bron /pad/naar/bron --uit /lege/map
```

Dat is dezelfde opdrachtregel als die van de dienst `analyse-omzetten` (`OMZET_BOOT`) en van de
job.

Op stdout komen de regels voor `models.lock`, op stderr de voortgang, de tijd en maxrss. Code 2
met één zin is een bron of map die niet klopt (de uitvoermap blijft leeg); code 1 is een fout in
de omzetter.
