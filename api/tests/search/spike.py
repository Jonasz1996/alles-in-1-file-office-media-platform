"""Hulpmiddelen voor de spike van fase 6 (bouwontwerp §3.7): een database op head zonder de
event loop (een gewone sqlite3-verbinding via SQLAlchemy), een vaste verzameling teksten om
snel een grote index te vullen, en de controle dat de index gelijk is aan een herbouw.

- head_db(path): migreert een nieuwe database naar head, synchroon;
- vul_snel(conn, ...): zet nodes en tekst neer met de cursor van de herbouw op 0 (de triggers
  houden num, pad en dead bij, maar schrijven niet naar FTS), en herbouwt daarna beide
  FTS-tabellen in één keer. Alleen voor een testdatabase waar niemand anders schrijft;
- zoekbench(): tools/zoekbench/bench.py als module (S9), voor zijn boom en zijn SQL;
- herbouw_verschil(path): de integrity-check van FTS5, en de index (fts_node en fts_name) naast
  een volledige herbouw van dezelfde database, term voor term (fts5vocab, soort instance).
  Leeg: gelijk.
"""

from __future__ import annotations

import functools
import importlib.util
import random
import sqlite3
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

import sqlalchemy as sa
from alembic import command

from app.migrate import alembic_config

BENCH = Path(__file__).resolve().parents[3] / "tools" / "zoekbench" / "bench.py"
TZ = "Europe/Amsterdam"
NU = datetime(2026, 11, 3, 9, 0, tzinfo=UTC)

# Woorden voor de tekst: eerst gewone (Zipf: "de" staat in bijna elk document), dan een lange
# staart van verzonnen woorden die met "ver", "ont" of "be" beginnen. Zo heeft het voorvoegsel
# "ver" duizenden termen, zoals in een echte Nederlandse index (V3: "ver"* is de dure vraag).
GEWOON = (
    "de het een en van in is dat op te zijn met voor niet aan er om ook als bij of door naar "
    "factuur offerte contract vergadering notulen rapport verslag begroting jaarverslag "
    "belasting klant project planning budget kosten datum bijlage onderwerp groet levering "
    "betaling huur woning school vakantie reis hotel energie salaris recept"
).split()
_LETTERGREPEN = (
    "ba be bo da de di ga go ka ke ko la le lo ma me mo na ne no pa pe ra re ro sa se".split()
)


def woordenschat(seed: int = 6, staart: int = 4000) -> list[str]:
    rng = random.Random(seed)
    out, gezien = list(GEWOON), set(GEWOON)
    while len(out) < len(GEWOON) + staart:
        begin = rng.choice(("ver", "ver", "ont", "be"))
        w = begin + "".join(rng.choice(_LETTERGREPEN) for _ in range(rng.randint(2, 4)))
        if w not in gezien:
            gezien.add(w)
            out.append(w)
    return out


def zipf(n: int, s: float = 1.07) -> list[float]:
    acc, out = 0.0, []
    for r in range(1, n + 1):
        acc += 1.0 / r**s
        out.append(acc)
    return out


@functools.cache
def zoekbench():
    """tools/zoekbench/bench.py als module: hij staat los van de app, niet in een pakket."""
    spec = importlib.util.spec_from_file_location("zoekbench", BENCH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["zoekbench"] = mod
    spec.loader.exec_module(mod)
    return mod


def head_db(path: Path, *, fts5: bool | None = None) -> str:
    """Een nieuwe database op head, zonder event loop. Geeft de url voor aiosqlite."""
    cfg = alembic_config(f"sqlite+aiosqlite:///{path}")
    engine = sa.create_engine(f"sqlite:///{path}")
    with engine.begin() as conn:
        conn.exec_driver_sql("PRAGMA journal_mode=WAL")
        cfg.attributes["connection"] = conn
        cfg.attributes["timezone"] = TZ
        if fts5 is not None:
            cfg.attributes["search_fts5"] = fts5
        command.upgrade(cfg, "head")
    engine.dispose()
    return f"sqlite+aiosqlite:///{path}"


def gebruiker(conn: sqlite3.Connection, naam: str) -> tuple[str, str]:
    """Een gebruiker met zijn hoofdmap; geeft (id, id van de hoofdmap) als hex."""
    user, root = uuid.uuid4().hex, uuid.uuid4().hex
    conn.execute(
        'INSERT INTO "user" (id, email, display_name, password_hash, role, quota_bytes, '
        "root_id, created_at) VALUES (?, ?, ?, 'x', 'user', 0, ?, ?)",
        (user, f"{naam}@example.nl", naam, root, NU.isoformat()),
    )
    node(conn, root, None, user, "", "dir")
    return user, root


def node(
    conn: sqlite3.Connection,
    node_id: str,
    parent: str | None,
    owner: str,
    name: str,
    kind: str = "file",
    *,
    mime: str | None = None,
    blob: str | None = None,
    modified: str | None = None,
    deleted: bool = False,
) -> None:
    t = modified or NU.isoformat()
    conn.execute(
        "INSERT INTO node (id, parent_id, owner_id, name, kind, mime, size, blob_id, "
        "created_at, modified_at, deleted_at, search_name) "
        "VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?)",
        (node_id, parent, owner, name, kind, mime, blob, t, t, t if deleted else None,
         name.lower()),
    )  # fmt: skip


def vul_snel(conn: sqlite3.Connection, doe) -> None:
    """Draait doe(conn) met de cursor van de herbouw op 0: de triggers zetten num, pad en dead,
    maar schrijven niets naar FTS. Daarna beide FTS-tabellen in één keer opnieuw, en de cursor
    weer op NULL (zoals de migratie het doet)."""
    conn.execute("UPDATE search_state SET cursor = 0 WHERE id = 1")
    doe(conn)
    conn.execute("INSERT INTO fts_node(fts_node) VALUES ('rebuild')")
    conn.execute("INSERT INTO fts_name(fts_name) VALUES ('rebuild')")
    conn.execute("UPDATE search_state SET cursor = NULL WHERE id = 1")
    conn.commit()


def teksten(n: int, woorden: int, seed: int = 6) -> list[str]:
    """n teksten van ongeveer zoveel woorden, met een vaste seed."""
    rng = random.Random(seed)
    voc = woordenschat(seed)
    cw = zipf(len(voc))
    return [" ".join(rng.choices(voc, cum_weights=cw, k=woorden)) for _ in range(n)]


def _vocab(conn: sqlite3.Connection) -> dict[str, list[tuple]]:
    out = {}
    for table in ("fts_node", "fts_name"):
        conn.execute(f"DROP TABLE IF EXISTS temp.v_{table}")
        conn.execute(
            f"CREATE VIRTUAL TABLE temp.v_{table} USING fts5vocab(main, {table}, instance)"
        )
        out[table] = conn.execute(
            f"SELECT term, doc, col, offset FROM temp.v_{table} ORDER BY 1, 2, 3, 4"
        ).fetchall()
    return out


def herbouw_verschil(path: Path, *, termen: bool = True) -> dict[str, str]:
    """Leest de database (wat gecommit is) in het geheugen, doet de integrity-check van FTS5 en
    vergelijkt de index met een volledige herbouw. Geeft per tabel wat er niet klopt; leeg als
    alles gelijk is. (Mist de index een rij, dan zegt de integrity-check "malformed".)
    termen=False: alleen de integrity-check (bij 100.000 nodes kost de vergelijking per term
    minuten)."""
    src = sqlite3.connect(path)
    mem = sqlite3.connect(":memory:")
    try:
        src.backup(mem)
    finally:
        src.close()
    verschil = {}
    try:
        for table in ("fts_node", "fts_name"):
            try:
                mem.execute(f"INSERT INTO {table}({table}, rank) VALUES ('integrity-check', 1)")
            except sqlite3.DatabaseError as e:
                verschil[f"{table} integrity-check"] = str(e)
        if not termen:
            return verschil
        voor = _vocab(mem)
        mem.execute("INSERT INTO fts_node(fts_node) VALUES ('rebuild')")
        mem.execute("INSERT INTO fts_name(fts_name) VALUES ('rebuild')")
        na = _vocab(mem)
    finally:
        mem.close()
    for table in voor:
        a, b = set(voor[table]), set(na[table])
        if a != b:
            verschil[table] = f"{len(a - b)} alleen in de index, {len(b - a)} alleen na herbouw"
    return verschil
