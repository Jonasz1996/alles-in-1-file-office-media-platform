# tools/geo: de bronnen voor plaatsnamen en landen

Bouwontwerp fase 6, Keuze 29. Het platform zet bij een foto met GPS het land en de dichtstbijzijnde
plaats, offline. Daarvoor zijn twee bestanden nodig, elk met een vaste sha256:

| Bestand | Waar vandaan | Licentie | Bytes | sha256 |
|---|---|---|---|---|
| `ne_10m_admin_0_countries.geojson` | Natural Earth 10m admin-0, tag v5.1.2 (`raw.githubusercontent.com/nvkelso/natural-earth-vector`) | publiek domein | 13.287.234 | `239eec57…f255` |
| `reverse_geocoder-1.5.1.tar.gz` | de sdist van reverse_geocoder 1.5.1 op PyPI; alleen `rg_cities1000.csv` erin (GeoNames cities1000) | CC BY 4.0 (GeoNames) | 2.246.559 | `2a2e781b…c75c` |

De volledige URL's en sha256 staan in `haal.py` en in `api/app/geo/load.py`; een test
(`api/tests/search/test_geo_load.py`) eist dat ze gelijk zijn.

- Het pakket `reverse_geocoder` is data, geen afhankelijkheid: het wordt nooit geïnstalleerd of
  geïmporteerd. `geo-load` leest de CSV met `tarfile` uit het archief, zonder uitpakken.
- De bestanden komen nooit in de repository.

## Ophalen

```sh
python3 -I tools/geo/haal.py --naar /pad/naar/geo           # wat ontbreekt ophalen
python3 -I tools/geo/haal.py --naar /pad/naar/geo --check   # alleen controleren
```

Op de server zet `install.sh` ze in `DATA_ROOT/geo/` (Z7; `PLAATSEN=uit` in `.env` slaat het
over). Daarna bouwt de app bij de start `places.sqlite` (`python -m app.cli geo-load` doet
hetzelfde met de hand). Klopt een sha256 niet of is een bestand kapot, dan zijn er geen
plaatsnamen en start de app gewoon.

## Tests

`api/tests/geo/test_places.py` (marker `geo`) gebruikt de echte bronnen uit de map in
`PLATFORM_GEO_TEST_SRC`, standaard `~/.cache/platform-geo`. CI haalt ze daar eerst met dit script
(job `api`). Zonder de bronnen faalt de test met de reden; hij slaat nooit stil over.

## Bronvermelding

Op de kaart en op `/over`: "Plaatsnamen: GeoNames, CC BY 4.0. Landen: Natural Earth (publiek
domein)." Natural Earth 10m is bij een grens op ±1 km nauwkeurig en heeft de enclaves van
Baarle-Hertog niet: daar zegt het platform Nederland. Provincienamen zijn Engels.
