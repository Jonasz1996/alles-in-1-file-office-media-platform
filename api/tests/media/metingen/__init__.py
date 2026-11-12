"""De metingen van fase 5 (§4.5 en §4.6 van docs/fase-5/bouwontwerp.md), met de hand.

Geen tests: pytest neemt deze map niet mee (geen `test_*.py`). Elk script is één commando,
vanuit `api/`, en schrijft zijn uitkomst als JSON (met `--uit`). De getallen en hoe ze gelezen
worden staan in docs/fase-5/metingen.md en metingen.json.

    uv run python -m tests.media.metingen.belasting   # Md, Me, Mg, Mh (de belastingtest, §4.6)
    uv run python -m tests.media.metingen.start       # Mb zonder browser, Mk, Mm, Mj
    uv run python -m tests.media.metingen.hdr         # Mf
    uv run python -m tests.media.metingen.scannen     # Ml
    uv run python -m tests.media.metingen.namen       # Mo
    uv run python -m tests.media.metingen.koppelen    # Keuze 42 op echte namen (punt 9)

meet_netwerk.py is een pytest-plugin voor de e2e van Mc (een ander netwerk); zijn commando
staat in zijn docstring.

Elk script start (behalve namen en koppelen) zijn eigen app met media aan, in een tijdelijke
map, met de lokale worker op de cores van de LXC (taskset -c 0-2, regel 14) en
PLATFORM_MEDIA_MAX_PROCS=3 (regel 13); een waakhond stopt alles op pid als MemAvailable onder
1,5 GiB zakt. kit.py heeft wat ze delen.

Op de LXC meet Jonas met wat in het image zit: `bench` (/beheer → Media → Meten) en de
belastingtest (`app.cli media-loadtest`, de knop op /beheer); zie docs/runbooks/media.md. Dat
is hetzelfde werk als belasting.py hier doet: belasting.py start de test in de app via
dezelfde route (POST /api/v1/admin/media/loadtest) en meet alleen de processen eromheen.
"""
