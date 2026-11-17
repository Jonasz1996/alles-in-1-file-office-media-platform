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
"""

from __future__ import annotations

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
