"""De index herbouwen, herstellen en controleren, zonder de schrijver vast te houden (bouwontwerp
fase 6, Keuze 12). Z1; app.cli search-rebuild, search-triggers en search-check roepen dit aan.

- rebuild(): nooit één statement ('rebuild' van FTS5 houdt de schrijver minuten vast, en
  busy_timeout is 5 s). Eerst 'delete-all' en cursor = 0 in één transactie, dan per CHUNK nums
  een korte transactie met een commit; een stuk is ook hooguit CHUNK_TEXT bytes tekst (minstens
  één node), en na elk stuk komt eerst PAUSE een andere schrijver aan de beurt (review golf 1,
  H3). Elk stuk begint met een schrijfactie (UPDATE search_state
  ... RETURNING cursor) en leest pas daarna :hi en search_seq.next: met pysqlite opent een SELECT
  geen transactie, en dan zou het laatste stuk een node missen die er net bij kwam (spike S2).
  De triggers schrijven intussen alleen voor num <= cursor (schema.in_); wat daarboven
  verandert, neemt een volgend stuk mee. Het laatste stuk zet cursor op NULL in dezelfde
  transactie. Daarna is de index gelijk aan een volledige herbouw. Staat cursor al (een herbouw
  die stopte), dan gaat hij daar verder. Tijdens een herbouw zoekt de app met LikeFallback
  (engine.engine_for, Keuze 10).
- restore_triggers(): de triggers precies zoals schema.TRIGGERS (of TRIGGERS_NO_FTS), en wat ze
  bijhouden opnieuw uit de bron: num, mpath en dead, de foto-rijen (dead en taken_at van
  photo_geo; photo_ocr, photo_geo en photo_person van een andere eigenaar dan die van de node
  weg, Keuze 25a). In dezelfde transactie begint de herbouw (delete-all, cursor 0): na een
  ontbrekende trigger klopt de index niet meer met de bron (R5). search-triggers draait daarna
  altijd rebuild().
- check(): vergelijkt n willekeurige nodes met de bron. Per node het aantal woorden per kolom
  (docsize) van fts_node en fts_name naast dat van dezelfde tekst in een FTS5-tabel in het
  geheugen (een eigen sqlite3-verbinding, met dezelfde tokenizers; nooit een tabel in de
  database van de app, Keuze 14), en één willekeurig woord van de node via MATCH met zijn rowid
  (de uitdrukking van parse(), zoals overal). Ook nums in de index
  zonder node, en mpath en dead naast die van de ouder. integrity-check van FTS5 alleen met
  full: die leest alles.
"""

from __future__ import annotations

import asyncio
import random
import sqlite3
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from app.search import schema
from app.search.query import parse

CHUNK = 2000
# H3: een stuk is ook nooit meer dan zoveel bytes tekst (body en ocr), minstens één node: 2000
# nodes met elk een tekst van 1 MB hielden de schrijver anders seconden vast. 4 MiB is lokaal
# ±200 ms per stuk.
CHUNK_TEXT = 4 * 1024 * 1024
# Na elk stuk even niets: een upload die op de schrijver wacht, probeert het in de busy-handler van
# SQLite pas na 1 tot 100 ms opnieuw. Zonder pauze pakt het volgende stuk de schrijver eerst, en
# wacht die upload meer stukken lang (review golf 1, H3).
PAUSE = 0.1
# Zoveel nodes per keer in het geheugen bij check() (een tekst is tot 1 MB)
BATCH = 100

# --- De herbouw in stukken ---------------------------------------------------------------------


async def _has_fts(conn: AsyncConnection) -> bool:
    found = await conn.exec_driver_sql(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'fts_node'"
    )
    return found.scalar() is not None


async def _start(conn: AsyncConnection, what: str) -> None:
    """Begint een herbouw als er geen loopt: cursor 0 (eerst schrijven, dan weet geen andere
    herbouw het niet), dan beide FTS-tabellen leeg."""
    started = await conn.exec_driver_sql(
        "UPDATE search_state SET cursor = 0, started_at = ?, what = ? "
        "WHERE id = 1 AND cursor IS NULL RETURNING cursor",
        (datetime.now(UTC).isoformat(), what),
    )
    if started.first() is not None:
        await conn.exec_driver_sql("INSERT INTO fts_node(fts_node) VALUES ('delete-all')")
        await conn.exec_driver_sql("INSERT INTO fts_name(fts_name) VALUES ('delete-all')")


async def begin(conn: AsyncConnection, what: str) -> bool:
    """De cursor van een nieuwe herbouw al zetten, vóór de taak van rebuild() loopt
    (POST /admin/search/rebuild, Z7: een GET direct na de 202 ziet de herbouw dan al). False
    zonder FTS-index. rebuild() gaat daarna verder vanaf deze cursor."""
    if not await _has_fts(conn):
        return False
    await _start(conn, what)
    return True


# De volgende hooguit :chunk nodes na de cursor, met de tekst tot en met elke node opgeteld.
# octet_length leest alleen de kop van de rij, niet de tekst zelf (SQLite 3.43 en later).
_NEXT = (
    "WITH next AS (SELECT num, blob_id FROM node WHERE num > :cur ORDER BY num LIMIT :chunk), "
    "sized AS (SELECT next.num AS num, sum(coalesce({n}(t.body), 0) + coalesce({n}(t.ocr), 0)) "
    "OVER (ORDER BY next.num) AS total "
    "FROM next LEFT JOIN blob_text t ON t.blob_sha = next.blob_id) "
    "SELECT max(CASE WHEN total <= :budget THEN num END), min(num), max(num), count(*) "
    "FROM sized"
)


async def _length(conn: AsyncConnection) -> str:
    version = (await conn.exec_driver_sql("SELECT sqlite_version()")).scalar() or "0"
    parts = tuple(int(p) for p in version.split(".")[:2])
    return "octet_length" if parts >= (3, 43) else "length"


async def _chunk(conn: AsyncConnection, chunk: int, budget: int = CHUNK_TEXT) -> tuple[int, bool]:
    """Eén stuk: de nums (cursor, hi] in beide FTS-tabellen. Geeft hi en of het het laatste
    was. Een stuk is hooguit chunk nodes, en hooguit budget bytes tekst; minstens één node."""
    cur = (
        await conn.exec_driver_sql(
            "UPDATE search_state SET cursor = cursor WHERE id = 1 RETURNING cursor"
        )
    ).scalar()
    if cur is None:  # een andere herbouw maakte hem af
        return 0, True
    last = (await conn.exec_driver_sql("SELECT next - 1 FROM search_seq WHERE id = 1")).scalar()
    fit, first, top, n = (
        await conn.exec_driver_sql(
            _NEXT.format(n=await _length(conn)), {"cur": cur, "chunk": chunk, "budget": budget}
        )
    ).one()
    if n and (fit is None or fit < top):  # de tekst begrenst dit stuk
        hi = first if fit is None else fit
    elif n == chunk:
        hi = top
    else:
        hi = None  # minder dan chunk nodes over: dit is het laatste stuk
    done = hi is None or hi >= last
    if done:
        hi = last
    await conn.exec_driver_sql(
        "INSERT INTO fts_node(rowid, name, extra, body, ocr, meta, pocr) "
        "SELECT num, name, extra, body, ocr, meta, pocr FROM search_src "
        "WHERE num > ? AND num <= ?",
        (cur, hi),
    )
    await conn.exec_driver_sql(
        "INSERT INTO fts_name(rowid, search_name) SELECT num, search_name FROM node "
        "WHERE num > ? AND num <= ?",
        (cur, hi),
    )
    await conn.exec_driver_sql(
        "UPDATE search_state SET cursor = ? WHERE id = 1", (None if done else hi,)
    )
    return hi, done


async def rebuild(
    engine: AsyncEngine,
    *,
    chunk: int = CHUNK,
    after_chunk: Callable[[int], Awaitable[None]] | None = None,
    what: str = "alles",
    pause: float = PAUSE,
) -> list[float]:
    """De herbouw in stukken; geeft de duur per stuk in ms. after_chunk(cursor) na de commit van
    elk stuk behalve het laatste (voor tests en de voortgang)."""
    async with engine.begin() as conn:
        if not await _has_fts(conn):
            return []
        await _start(conn, what)
    times: list[float] = []
    while True:
        start = time.perf_counter()
        async with engine.begin() as conn:
            hi, done = await _chunk(conn, chunk)
        times.append((time.perf_counter() - start) * 1000)
        if done:
            return times
        if after_chunk is not None:
            await after_chunk(hi)
        await asyncio.sleep(pause)


async def progress(engine: AsyncEngine) -> tuple[int, int] | None:
    """(cursor, hoogste num) als er een herbouw loopt, anders None."""
    async with engine.connect() as conn:
        row = (
            await conn.exec_driver_sql(
                "SELECT s.cursor, q.next - 1 FROM search_state s, search_seq q "
                "WHERE s.id = 1 AND q.id = 1"
            )
        ).first()
    if row is None or row[0] is None:
        return None
    return int(row[0]), int(row[1])


# --- De triggers terugzetten -------------------------------------------------------------------

# mpath en dead opnieuw uit parent_id en deleted_at (zoals de migratie, schema.FILL), alleen
# waar het verschilt: een UPDATE van mpath of dead start geen trigger
_TREE = (
    "WITH RECURSIVE t(id, mpath, dead) AS (\n"
    "  SELECT id, '/' || num || '/', deleted_at IS NOT NULL FROM node WHERE parent_id IS NULL\n"
    "  UNION ALL\n"
    "  SELECT n.id, t.mpath || n.num || '/', t.dead OR n.deleted_at IS NOT NULL\n"
    "  FROM node n JOIN t ON n.parent_id = t.id)\n"
    "UPDATE node SET mpath = t.mpath, dead = t.dead FROM t\n"
    "WHERE node.id = t.id AND (node.mpath IS NOT t.mpath OR node.dead IS NOT t.dead)"
)
# De foto-rijen horen bij een bestand van de eigenaar van de node (Keuze 25a, node_au_owner)
_PHOTO_OWNER = tuple(
    f"DELETE FROM {table} WHERE NOT EXISTS (SELECT 1 FROM node n "
    f"WHERE n.id = {table}.node_id AND n.owner_id = {table}.owner_id)"
    for table in ("photo_ocr", "photo_geo", "photo_person")
)
_GEO = (
    "UPDATE photo_geo SET (dead, taken_at) =\n"
    "  (SELECT n.dead, n.taken_at FROM node n WHERE n.id = photo_geo.node_id)\n"
    "WHERE dead IS NOT (SELECT n.dead FROM node n WHERE n.id = photo_geo.node_id)\n"
    "   OR taken_at IS NOT (SELECT n.taken_at FROM node n WHERE n.id = photo_geo.node_id)"
)


async def _changes(conn: AsyncConnection, sql: str) -> int:
    """Hoeveel rijen sql veranderde (rowcount is -1 bij een statement dat met WITH begint)."""
    await conn.exec_driver_sql(sql)
    return int((await conn.exec_driver_sql("SELECT changes()")).scalar() or 0)


@dataclass
class Restored:
    dropped: list[str] = field(default_factory=list)
    created: list[str] = field(default_factory=list)
    numbered: int = 0  # nodes zonder num die er een kregen
    tree: int = 0  # nodes met een ander mpath of dead
    photo_rows: int = 0  # foto-rijen weg (andere eigenaar) of bijgewerkt (dead, taken_at)


async def restore_triggers(engine: AsyncEngine) -> Restored:
    """De triggers zoals schema.py, wat ze bijhouden opnieuw, en het begin van een herbouw; alles
    in één transactie. Daarna rebuild()."""
    out = Restored()
    async with engine.begin() as conn:
        fts = await _has_fts(conn)
        want = schema.TRIGGERS if fts else schema.TRIGGERS_NO_FTS
        have = dict(
            (
                await conn.exec_driver_sql(
                    "SELECT name, sql FROM sqlite_master WHERE type = 'trigger'"
                )
            ).all()
        )
        for name, sql in have.items():
            if want.get(name) != sql:
                await conn.exec_driver_sql(f'DROP TRIGGER "{name}"')
                out.dropped.append(name)
        for name, sql in want.items():
            if have.get(name) != sql:
                await conn.exec_driver_sql(sql)
                out.created.append(name)
        # Een node zonder num (gemaakt zonder node_ai): een nummer uit de teller
        await conn.exec_driver_sql(
            "UPDATE search_seq SET next = max(next, coalesce((SELECT max(num) FROM node), 0) + 1) "
            "WHERE id = 1"
        )
        missing = [
            row[0] for row in await conn.exec_driver_sql("SELECT id FROM node WHERE num IS NULL")
        ]
        for node_id in missing:
            await conn.exec_driver_sql("UPDATE search_seq SET next = next + 1 WHERE id = 1")
            await conn.exec_driver_sql(
                "UPDATE node SET num = (SELECT next - 1 FROM search_seq WHERE id = 1) WHERE id = ?",
                (node_id,),
            )
        out.numbered = len(missing)
        out.tree = await _changes(conn, _TREE)
        for sql in (*_PHOTO_OWNER, _GEO):
            out.photo_rows += await _changes(conn, sql)
        if fts:
            # De herbouw begint hier, ook als er al een liep: alles opnieuw
            await conn.exec_driver_sql("UPDATE search_state SET cursor = NULL WHERE id = 1")
            await _start(conn, "triggers")
    return out


# --- Controleren -------------------------------------------------------------------------------


@dataclass
class Checked:
    """Wat check() vond. bad: (num, wat) per verschil; nums, geen namen of tekst."""

    sampled: int = 0
    bad: list[tuple[int, str]] = field(default_factory=list)
    integrity: list[str] = field(default_factory=list)
    rebuilding: bool = False

    @property
    def ok(self) -> bool:
        return not self.bad and not self.integrity and not self.rebuilding


_COLS = ("name", "extra", "body", "ocr", "meta", "pocr")


def _memory_sizes(
    rows: Sequence[tuple], name_rows: Sequence[tuple[int, str]], rng: random.Random
) -> tuple[dict, dict, dict]:
    """docsize van fts_node en fts_name voor deze rijen, en per num een willekeurig woord van de
    node, in een FTS5-tabel in het geheugen met dezelfde tokenizers."""
    con = sqlite3.connect(":memory:")
    try:
        con.execute(
            "CREATE VIRTUAL TABLE m USING fts5(name, extra, body, ocr, meta, pocr, "
            "tokenize='unicode61 remove_diacritics 2')"
        )
        con.execute("CREATE VIRTUAL TABLE mn USING fts5(search_name, tokenize='trigram')")
        con.execute("CREATE VIRTUAL TABLE mv USING fts5vocab(m, 'instance')")
        con.executemany(
            "INSERT INTO m(rowid, name, extra, body, ocr, meta, pocr) VALUES (?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        con.executemany("INSERT INTO mn(rowid, search_name) VALUES (?, ?)", name_rows)
        node = dict(con.execute("SELECT id, sz FROM m_docsize"))
        name = dict(con.execute("SELECT id, sz FROM mn_docsize"))
        words: dict[int, str] = {}
        seen: dict[int, int] = {}
        for term, doc in con.execute("SELECT term, doc FROM mv"):
            seen[doc] = seen.get(doc, 0) + 1
            if rng.randrange(seen[doc]) == 0:  # elk woord van de node even vaak
                words[doc] = term
        return node, name, words
    finally:
        con.close()


async def check(
    engine: AsyncEngine, *, sample: int = 1000, full: bool = False, seed: int | None = None
) -> Checked:
    out = Checked()
    rng = random.Random(seed)
    async with engine.connect() as conn:
        if not await _has_fts(conn):
            return out
        if (
            await conn.exec_driver_sql("SELECT cursor FROM search_state WHERE id = 1")
        ).scalar() is not None:
            out.rebuilding = True
            return out
        nums = [row[0] for row in await conn.exec_driver_sql("SELECT num FROM node")]
        if None in nums:
            out.bad.append((0, f"{nums.count(None)} node(s) zonder num"))
            nums = [n for n in nums if n is not None]
        picked = sorted(rng.sample(nums, min(sample, len(nums))))
        out.sampled = len(picked)
        # nums in de index zonder node: uit beide docsize-tabellen
        for table in ("fts_node_docsize", "fts_name_docsize"):
            ghosts = await conn.exec_driver_sql(
                f"SELECT d.id FROM {table} d LEFT JOIN node n ON n.num = d.id "
                "WHERE n.id IS NULL LIMIT 100"
            )
            out.bad += [(row[0], f"{table}: geen node") for row in ghosts]
        for start in range(0, len(picked), BATCH):
            part = picked[start : start + BATCH]
            marks = ", ".join("?" * len(part))
            rows = list(
                await conn.exec_driver_sql(
                    f"SELECT num, {', '.join(_COLS)} FROM search_src WHERE num IN ({marks})",
                    tuple(part),
                )
            )
            names = list(
                await conn.exec_driver_sql(
                    f"SELECT num, search_name FROM node WHERE num IN ({marks})", tuple(part)
                )
            )
            have_node = {
                row[0]: row[1]
                for row in await conn.exec_driver_sql(
                    f"SELECT id, sz FROM fts_node_docsize WHERE id IN ({marks})", tuple(part)
                )
            }
            have_name = {
                row[0]: row[1]
                for row in await conn.exec_driver_sql(
                    f"SELECT id, sz FROM fts_name_docsize WHERE id IN ({marks})", tuple(part)
                )
            }
            want_node, want_name, words = _memory_sizes(
                [tuple(r) for r in rows], [tuple(r) for r in names], rng
            )
            for num in part:
                if have_node.get(num) != want_node.get(num):
                    out.bad.append((num, "fts_node"))
                if have_name.get(num) != want_name.get(num):
                    out.bad.append((num, "fts_name"))
            for num, term in words.items():
                # De uitdrukking komt ook hier van parse() (test_search_no_raw.py)
                parsed = parse(term + " ")  # met een spatie: geen voorvoegsel
                if parsed is None:
                    continue
                hit = await conn.exec_driver_sql(
                    "SELECT 1 FROM fts_node WHERE fts_node MATCH ? AND rowid = ?",
                    (parsed.match, num),
                )
                if hit.first() is None:
                    out.bad.append((num, "fts_node: woord"))
            tree = await conn.exec_driver_sql(
                "SELECT n.num FROM node n LEFT JOIN node p ON p.id = n.parent_id "
                f"WHERE n.num IN ({marks}) AND (n.mpath IS NOT coalesce(p.mpath, '/') || n.num "
                "|| '/' OR n.dead IS NOT (coalesce(p.dead, 0) OR n.deleted_at IS NOT NULL))",
                tuple(part),
            )
            out.bad += [(row[0], "mpath of dead") for row in tree]
    if full:
        for table in ("fts_node", "fts_name"):
            try:
                async with engine.begin() as conn:
                    await conn.exec_driver_sql(
                        f"INSERT INTO {table}({table}, rank) VALUES ('integrity-check', 1)"
                    )
            except Exception as exc:  # de melding van SQLite is de uitkomst
                out.integrity.append(f"{table}: {type(exc).__name__}")
    return out
