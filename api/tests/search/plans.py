"""assert_plan: het plan van een vraag vastzetten (bouwontwerp fase 6, §3.6; Keuze 4 en 4a).

    from tests.search.plans import assert_plan
    assert_plan(conn, sql, args, require_first="fts_node VIRTUAL TABLE")

Kijkt naar EXPLAIN QUERY PLAN en faalt met het hele plan erbij als:

- de eerste stap van de buitenste lus niet `require_first` bevat (bijvoorbeeld: begin bij FTS,
  of bij ix_node_live_mod);
- een regel uit `require` nergens staat;
- iets uit `forbid` ergens staat (standaard AUTOMATIC: een index die SQLite zelf bouwt, per
  vraag opnieuw);
- een SEARCH een van de voorwaarden uit `forbid_index_terms` gebruikt (standaard
  `parent_id=?`: een boom aflopen per map in plaats van één bereik op het pad).

conn: een sqlite3-verbinding of een synchrone SQLAlchemy-verbinding (exec_driver_sql). De plannen
hangen af van de versie van SQLite en van ANALYZE: test daarom met en zonder ANALYZE, en draai ze
ook in python:3.12-slim (SQLite 3.46.1, e2e/slim.sh).

pg_stappen: het plan van PostgreSQL (EXPLAIN (FORMAT JSON)) voor een test die op PostgreSQL een
eigen verwachting heeft. PostgreSQL kiest bij een paar rijen zelf de volgorde van de tabellen;
vast staat wat de vraag aanraakt, op welke voorwaarde, en dat elke tabel een index heeft die bij
een voorwaarde past.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass

FORBID = ("AUTOMATIC",)
FORBID_INDEX_TERMS = ("parent_id=?",)


@dataclass(frozen=True)
class Plan:
    """De regels van EXPLAIN QUERY PLAN: (id, ouder, tekst)."""

    regels: tuple[tuple[int, int, str], ...]

    @property
    def eerste(self) -> str:
        """De eerste SCAN of SEARCH van de buitenste lus."""
        for _id, ouder, tekst in self.regels:
            if ouder == 0 and tekst.startswith(("SCAN ", "SEARCH ")):
                return tekst
        return ""

    def tekst(self) -> str:
        diepte = {0: -1}
        uit = []
        for id_, ouder, tekst in self.regels:
            diepte[id_] = diepte.get(ouder, -1) + 1
            uit.append("  " * diepte[id_] + tekst)
        return "\n".join(uit)

    def __contains__(self, stuk: str) -> bool:
        return any(stuk in tekst for _, _, tekst in self.regels)


def plan_van(conn, sql: str, args: Sequence | dict = ()) -> Plan:
    vraag = "EXPLAIN QUERY PLAN " + sql
    if hasattr(conn, "exec_driver_sql"):
        rijen = conn.exec_driver_sql(vraag, args if args else ()).all()
    else:
        rijen = conn.execute(vraag, args).fetchall()
    return Plan(tuple((r[0], r[1], r[3]) for r in rijen))


def assert_plan(
    conn,
    sql: str,
    args: Sequence | dict = (),
    *,
    require_first: str | None = None,
    require: Sequence[str] = (),
    forbid: Sequence[str] = FORBID,
    forbid_index_terms: Sequence[str] = FORBID_INDEX_TERMS,
) -> Plan:
    """Zie bovenaan. Geeft het plan terug (voor meer eisen in de test zelf)."""
    plan = plan_van(conn, sql, args)
    fouten = []
    if require_first is not None and require_first not in plan.eerste:
        fouten.append(f"de eerste stap is {plan.eerste!r}, niet {require_first!r}")
    fouten += [f"{stuk!r} ontbreekt" for stuk in require if stuk not in plan]
    fouten += [f"{stuk!r} staat erin" for stuk in forbid if stuk in plan]
    for _, _, tekst in plan.regels:
        if tekst.startswith("SEARCH "):
            fouten += [
                f"zoekt met {term!r}: {tekst}" for term in forbid_index_terms if term in tekst
            ]
    if fouten:
        raise AssertionError("; ".join(fouten) + "\n" + plan.tekst())
    return plan


def pg_stappen(conn, sql: str, args: Sequence | dict = ()) -> list[dict]:
    """De stappen van het plan van PostgreSQL, de buitenste eerst (conn: een synchrone
    SQLAlchemy-verbinding in een transactie). Met enable_seqscan uit, alleen voor deze vraag:
    dan blijft een Seq Scan alleen staan als geen index past bij een voorwaarde op die tabel."""
    conn.exec_driver_sql("SET LOCAL enable_seqscan = off")
    try:
        plan = conn.exec_driver_sql("EXPLAIN (FORMAT JSON) " + sql, args if args else ()).scalar()
    finally:
        conn.exec_driver_sql("SET LOCAL enable_seqscan = on")
    if isinstance(plan, str):
        plan = json.loads(plan)
    stappen: list[dict] = []
    open_ = [plan[0]["Plan"]]
    while open_:
        stap = open_.pop(0)
        stappen.append(stap)
        open_ += stap.get("Plans", [])
    return stappen
