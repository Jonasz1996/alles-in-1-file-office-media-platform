"""De zoekmotoren per database, op één plek (release 0.8.2, P3; docs/fase-6/i3/pg-tests-ci.md).

SQLite heeft twee motoren: fts (de FTS5-index van 0010, FtsEngine) en like (LikeFallback).
PostgreSQL heeft tot golf 3 alleen like: 0010 maakt daar geen index (Keuze 76); de eigen index
van PostgreSQL komt met 0014 (P5). Daarom, voor elke test:

- params("fts", "like"): de motoren voor een fixture of een parametrize, in de volgorde die de
  test al had. Op PostgreSQL zonder fts: die variant bestaat daar dan niet (hij wordt niet
  overgeslagen, hij wordt niet gemaakt).
- needs_fts: een marker voor een test die de FTS-index zelf nodig heeft (de index lezen,
  herbouwen, een vraag in de index afbreken). Op PostgreSQL overgeslagen, met REASON.
- DEFAULT: de motor die de app zonder keuze neemt (fts op SQLite, like op PostgreSQL).
- check_index(conn): in een test die na het werk zegt "boom en index kloppen", de controle van
  de FTS-tabellen (migratie.check_fts) waar er een FTS-index is. Op PostgreSQL is er tot golf 3
  geen index om te controleren; de rest van die test loopt daar wel.

Golf 3 zet de index van PostgreSQL hier aan, op één plek: PG_FTS (en zo nodig de naam van die
motor in params). Elke test die zo niet op PostgreSQL draait, staat met naam in
docs/fase-6/i3/pg-tests-ci.md.
"""

from __future__ import annotations

import os

import pytest

from tests.search.migratie import check_fts

PG = bool(os.environ.get("PLATFORM_TEST_DATABASE_URL"))
# Golf 3 (P5): True zodra PostgreSQL een eigen zoekindex heeft (0014)
PG_FTS = False
FTS = not PG or PG_FTS
REASON = "de FTS-index; die heeft PostgreSQL pas met golf 3 (P5, migratie 0014)"
DEFAULT = "fts" if FTS else "like"

needs_fts = pytest.mark.skipif(not FTS, reason=REASON)


def params(*names: str) -> list[str]:
    """names zonder fts als deze database geen FTS-index heeft."""
    return [name for name in names if name != "fts" or FTS]


def check_index(conn) -> None:
    """migratie.check_fts als deze database een FTS-index heeft (voor conn.run_sync)."""
    if FTS:
        check_fts(conn)
