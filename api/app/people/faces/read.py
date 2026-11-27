"""Gezichten lezen voor de API (fase 6, X2b): de stand, voorstellen, groepen en de gezichten van
één foto. Alleen de eigenaar, alleen eigen, levende foto's (own_live). Nooit een vector of een
score naar buiten: sure zegt alleen of het voorstel ruim boven de drempel zit (Keuze 66).

Snel bij 60.000 gezichten (test_faces_perf.py): de tellingen komen uit de indexen van 0011
(ix_face_new, ix_face_open, ix_face_cluster, ix_fp_person, ix_face_scan_todo), zonder join op
node per gezicht; wat own_live laat vallen (een foto in de prullenbak, ook via een map erboven)
gaat er daarna af (_gone_by, _counts_sql). Een lijst van gezichten leest per persoon of groep
alleen de eerste paar uit de index, met de join op node en own_live per gezicht.

Open is "zonder ja". Een gezicht met een ja heeft nooit een voorstel of een groep: confirm,
links (een kader wordt ja) en face_rows.write_group houden dat zo, dus de voorstellen en de
groepen zijn vanzelf open.

Volgorde: voorstellen de beste eerst (suggest_score, dan rowid); groepen de grootste eerst en
daarin de nieuwste eerst (rowid aflopend: de volgorde van invoegen; created_at staat niet in de
index). Een cursor is de plek in de lijst (een getal als tekst); een verkeerde cursor begint
vooraan.

PostgreSQL (0.8.2, P1 pg-sql): dezelfde vragen in de vorm van PostgreSQL, met dezelfde uitkomst.
De volgorde van invoegen is daar face.seq (dbdialect.FaceSeq; migratie 0013), een score zonder
waarde staat achteraan zoals in SQLite (NULLS LAST), en de hints voor SQLite (+kolom, CROSS JOIN)
vallen weg. Op SQLite blijft de tekst van elke vraag precies wat hij was
(tests/test_pg_dialect.py).
"""

from __future__ import annotations

import functools
import uuid
from collections import Counter
from typing import Any

from sqlalchemy import Integer, TextClause, Uuid, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app import dbdialect, face_rows
from app.models import Face, FaceOwner, FacePerson, Node, Person
from app.people.faces import drempels
from app.people.faces_types import (
    FACES_PAGE,
    FaceGroup,
    FaceGroups,
    FaceOut,
    FacePage,
    FacesCount,
    FacesPhotos,
    FaceSuggest,
    PersonSuggestions,
    SuggestionsByPerson,
)
from app.search.live import OWN_LIVE_SQL, own_live
from app.search.types import PersonRef

PEOPLE_PAGE = 60
GROUPS_PAGE = 30
SAMPLE = 6
GROUP_FACES = 12
SCAN_STATES = ("wait", "busy", "done", "failed", "skip")
ROWID = dbdialect.FaceSeq()  # face.rowid op SQLite, face.seq op PostgreSQL


def offset(cursor: str | None) -> int:
    try:
        found = int(cursor or 0)
    except ValueError:
        return 0
    return max(0, found)


def _next(start: int, got: int, page: int) -> str | None:
    return str(start + page) if got > page else None


def _me(db: Any, value: Any) -> Any:
    """Een uuid als parameter van een vraag als tekst: SQLite bewaart hem als 32 hex, PostgreSQL
    als uuid (asyncpg wil dan het uuid zelf)."""
    if isinstance(value, uuid.UUID) and not dbdialect.is_pg(db):
        return value.hex
    return value


def _gone_nodes(rule: str = OWN_LIVE_SQL, dialect: str = dbdialect.SQLITE) -> str:
    """SQL: de nodes van :me die de regel (OWN_LIVE_SQL) laat vallen. Gezichten en face_scan
    staan alleen op bestanden (de triggers van 0011), dus voor hen is dat dead = 1: de
    prullenbak, ook via een map erboven. Gezocht vanaf de wortels in de prullenbak
    (ix_node_trash, dan ix_node_mpath per wortel; CROSS JOIN houdt die volgorde vast, en +
    houdt SQLite van ix_node_owner_taken af), zoals DEAD_SUBTREE de boom afbakent. Alleen de
    eigen wortels: een boom heeft één eigenaar (een nieuwe node krijgt die van zijn map,
    routers/uploads.py en routers/files.py; verplaatsen naar een andere eigenaar geeft
    cross_owner), dus de prullenbak van een ander kost hier niets. PostgreSQL kent geen CROSS
    JOIN met ON en geen unaire + op uuid: daar een gewone JOIN zonder hints."""
    end = "substr(t.mpath, 1, length(t.mpath) - 1) || '0'"
    if dialect != dbdialect.SQLITE:
        return (
            "SELECT d.id FROM node t JOIN node d"
            f" ON d.mpath >= t.mpath AND d.mpath < {end}"
            " WHERE t.deleted_at IS NOT NULL AND t.owner_id = :me AND d.owner_id = :me"
            f" AND NOT ({rule.format(n='d')})"
        )
    return (
        "SELECT d.id FROM node t CROSS JOIN node d"
        f" ON d.mpath >= t.mpath AND d.mpath < {end}"
        " WHERE t.deleted_at IS NOT NULL AND +t.owner_id = :me AND +d.owner_id = :me"
        f" AND NOT ({rule.format(n='d')})"
    )


async def _gone_by(
    db: AsyncSession, owner: uuid.UUID, key: str, rule: str = OWN_LIVE_SQL
) -> Counter[Any]:
    """Wat er van de tellingen uit de indexen af moet: per voorstel (key suggest_id) of per groep
    (key cluster) de gezichten (hidden 0) op eigen foto's die de regel (OWN_LIVE_SQL, die elke
    aanroeper zelf noemt) laat vallen."""
    kind = Uuid if key == "suggest_id" else Integer
    dialect = dbdialect.name_of(db)
    plus = "+" if dialect == dbdialect.SQLITE else ""
    rows = await db.execute(
        text(
            f"SELECT g.{key} AS k, count(*) AS n FROM face g"
            f" WHERE g.node_id IN ({_gone_nodes(rule, dialect)})"
            f" AND {plus}g.owner_id = :me AND {plus}g.hidden = 0 AND g.{key} IS NOT NULL"
            f" GROUP BY g.{key}"
        ).columns(k=kind),
        {"me": _me(db, owner)},
    )
    return Counter({k: n for k, n in rows})


async def _names(
    db: AsyncSession, owner: uuid.UUID, ids: set[uuid.UUID], *, shown: bool = False
) -> dict[uuid.UUID, str]:
    """De namen van eigen personen; shown: alleen wie niet verborgen is."""
    if not ids:
        return {}
    q = select(Person.id, Person.name).where(Person.owner_id == owner, Person.id.in_(ids))
    if shown:
        q = q.where(Person.hidden == 0)
    return dict((r.id, r.name) for r in await db.execute(q))


async def faces_out(
    db: AsyncSession, owner: uuid.UUID, rows: list[Any], known: dict[uuid.UUID, str] | None = None
) -> list[FaceOut]:
    """rows: Face-achtige rijen (id, node_id, x, y, w, h, suggest_id, suggest_score, suggest_why)
    plus person (de ja of None). known: namen die de aanroeper al heeft (geen vraag meer)."""
    names = dict(known or {})
    wanted = {r.person for r in rows if r.person} | {r.suggest_id for r in rows if r.suggest_id}
    names.update(await _names(db, owner, wanted - set(names)))
    out = []
    for r in rows:
        person = PersonRef(id=r.person, name=names[r.person]) if r.person in names else None
        suggest = None
        if person is None and r.suggest_id in names and r.suggest_why in ("sim", "tag"):
            sure = r.suggest_why == "sim" and (r.suggest_score or 0) >= drempels.SURE
            suggest = FaceSuggest(
                person_id=r.suggest_id, name=names[r.suggest_id], sure=sure, why=r.suggest_why
            )
        out.append(
            FaceOut(
                id=r.id, node_id=r.node_id, box=(r.x, r.y, r.w, r.h), person=person, suggest=suggest
            )
        )
    return out


def _face_cols(owner: uuid.UUID, live: Any = own_live, *, by_node: bool = False):
    """De kolommen van een gezicht met zijn ja; live is de regel voor eigen, levende foto's
    (own_live), die elke aanroeper zelf noemt. by_node: de vraag is per foto; dan zonder index op
    de eigenaar (face_rows.no_index), zodat ix_face_node loopt."""
    ja = (
        select(FacePerson.person_id)
        .where(FacePerson.face_id == Face.id, FacePerson.verdict == "ja")
        .scalar_subquery()
    )
    cols = (
        Face.id,
        Face.node_id,
        Face.x,
        Face.y,
        Face.w,
        Face.h,
        Face.suggest_id,
        Face.suggest_score,
        Face.suggest_why,
        Face.cluster,
        ROWID.label("r"),
        ja.label("person"),
    )
    return (
        select(*cols)
        .join(Node, Node.id == Face.node_id)
        .where(
            (face_rows.no_index(Face.owner_id) if by_node else Face.owner_id) == owner,
            live(Node, owner),
            Face.hidden == 0,
        )
    )


@functools.cache
def _firsts_sql(
    key: str, n: int, rule: str = OWN_LIVE_SQL, dialect: str = dbdialect.SQLITE
) -> TextClause:
    """De eerste paar gezichten van n personen (key suggest_id: de beste eerst) of n groepen (key
    cluster: de nieuwste eerst) in één vraag: UNION ALL, en elk deel loopt zijn index af
    (ix_face_open of ix_face_cluster) met de join op node en de regel (OWN_LIVE_SQL) per gezicht,
    tot zijn LIMIT. Als tekst en per n één keer gemaakt: 60 keer _face_cols kost meer dan de
    vraag zelf. PostgreSQL: face.seq voor rowid, een score zonder waarde achteraan (zoals in
    SQLite) en een naam voor elk deel (AS p0, p1, ...)."""
    seq = dbdialect.face_seq_sql(dialect, "f")
    last = "" if dialect == dbdialect.SQLITE else " NULLS LAST"
    alias = "" if dialect == dbdialect.SQLITE else " AS p{i}"
    order, limit = {
        "suggest_id": (f"f.suggest_score DESC{last}, {seq}", SAMPLE),
        "cluster": (f"{seq} DESC", GROUP_FACES),
    }[key]
    part = (
        "SELECT * FROM (SELECT f.id, f.node_id, f.x, f.y, f.w, f.h, f.suggest_id,"
        f" f.suggest_score, f.suggest_why, f.cluster, {seq} AS r, (SELECT p.person_id"
        " FROM face_person p WHERE p.face_id = f.id AND p.verdict = 'ja') AS person"
        " FROM face f JOIN node n ON n.id = f.node_id"
        f" WHERE f.owner_id = :me AND {rule.format(n='n')} AND f.hidden = 0"
        f" AND f.{key} = :k{{i}} ORDER BY {order} LIMIT {limit}){alias}"
    )
    return text(" UNION ALL ".join(part.format(i=i) for i in range(n))).columns(
        id=Uuid, node_id=Uuid, suggest_id=Uuid, person=Uuid
    )


async def _firsts(db: AsyncSession, owner: uuid.UUID, key: str, values: list[Any]) -> list[Any]:
    if not values:
        return []
    params = {f"k{i}": _me(db, v) for i, v in enumerate(values)}
    sql = _firsts_sql(key, len(values), OWN_LIVE_SQL, dbdialect.name_of(db))
    return list(await db.execute(sql, {"me": _me(db, owner), **params}))


async def file_faces(db: AsyncSession, owner: uuid.UUID, node: uuid.UUID) -> list[FaceOut]:
    rows = list(
        await db.execute(
            _face_cols(owner, own_live, by_node=True)
            .where(Face.node_id == node)
            .order_by(Face.x, Face.id)
            .limit(200)
        )
    )
    return await faces_out(db, owner, rows)


async def _cluster_sizes(
    db: AsyncSession, owner: uuid.UUID, rule: str = OWN_LIVE_SQL
) -> Counter[int]:
    """Hoeveel open, levende gezichten elke groep heeft (ix_face_cluster, min wat de regel laat
    vallen; een gezicht met een ja heeft geen groep)."""
    sizes: Counter[int] = Counter(
        dict(
            (int(c), int(n))
            for c, n in await db.execute(
                select(Face.cluster, func.count())
                .where(Face.owner_id == owner, Face.hidden == 0, Face.cluster.is_not(None))
                .group_by(Face.cluster)
            )
        )
    )
    sizes.subtract(await _gone_by(db, owner, "cluster", rule))
    return sizes


@functools.cache
def _counts_sql(rule: str = OWN_LIVE_SQL, dialect: str = dbdialect.SQLITE) -> TextClause:
    """De stand in één vraag (een rondgang, alleen getallen): per soort (k) de tellingen uit de
    indexen, en wat de regel (OWN_LIVE_SQL) daarvan laat vallen. De nodes die wegvallen (gone)
    één keer, als MATERIALIZED. k: 1 face_scan per state, 2 daarvan weg; 3 open gezichten per
    groep; 4 de gezichten die weg zijn, per groep (ook zonder groep: a NULL), met hoeveel daarvan
    een ja hebben; 5 alle gezichten, 6 alle ja's; 7 wiping.

    PostgreSQL: IN (SELECT id FROM gone) voor IN gone, geen +, sum() over een getal in plaats van
    een waar/onwaar, en a overal als tekst (UNION ALL wil één soort per kolom)."""
    states = ", ".join(f"'{s}'" for s in SCAN_STATES)
    ja = "EXISTS (SELECT 1 FROM face_person p WHERE p.face_id = g.id AND p.verdict = 'ja')"
    if dialect != dbdialect.SQLITE:
        return text(
            f"WITH gone(id) AS MATERIALIZED ({_gone_nodes(rule, dialect)})"
            " SELECT 1 AS k, state AS a, count(*) AS n, 0 AS ja FROM face_scan"
            f" WHERE state IN ({states}) AND owner_id = :me GROUP BY state"
            " UNION ALL SELECT 2, state, count(*), 0 FROM face_scan"
            " WHERE node_id IN (SELECT id FROM gone) AND owner_id = :me GROUP BY state"
            " UNION ALL SELECT 3, CAST(cluster AS TEXT), count(*), 0 FROM face"
            " WHERE owner_id = :me AND hidden = 0 AND cluster IS NOT NULL GROUP BY cluster"
            f" UNION ALL SELECT 4, CAST(g.cluster AS TEXT), count(*), sum(CAST({ja} AS INTEGER))"
            " FROM face g WHERE g.node_id IN (SELECT id FROM gone) AND g.owner_id = :me"
            " AND g.hidden = 0 GROUP BY g.cluster"
            " UNION ALL SELECT 5, NULL, count(*), 0 FROM face WHERE owner_id = :me AND hidden = 0"
            " UNION ALL SELECT 6, NULL, count(*), 0 FROM face_person"
            " WHERE owner_id = :me AND verdict = 'ja'"
            " UNION ALL SELECT 7, CAST(wiping AS TEXT), 0, 0 FROM face_owner WHERE owner_id = :me"
        )
    return text(
        f"WITH gone(id) AS MATERIALIZED ({_gone_nodes(rule)})"
        " SELECT 1 AS k, state AS a, count(*) AS n, 0 AS ja FROM face_scan"
        f" WHERE state IN ({states}) AND owner_id = :me GROUP BY state"
        " UNION ALL SELECT 2, state, count(*), 0 FROM face_scan"
        " WHERE node_id IN gone AND +owner_id = :me GROUP BY state"
        " UNION ALL SELECT 3, cluster, count(*), 0 FROM face"
        " WHERE owner_id = :me AND hidden = 0 AND cluster IS NOT NULL GROUP BY cluster"
        f" UNION ALL SELECT 4, g.cluster, count(*), sum({ja}) FROM face g"
        " WHERE g.node_id IN gone AND +g.owner_id = :me AND +g.hidden = 0 GROUP BY g.cluster"
        " UNION ALL SELECT 5, NULL, count(*), 0 FROM face WHERE owner_id = :me AND hidden = 0"
        " UNION ALL SELECT 6, NULL, count(*), 0 FROM face_person"
        " WHERE owner_id = :me AND verdict = 'ja'"
        " UNION ALL SELECT 7, wiping, 0, 0 FROM face_owner WHERE owner_id = :me"
    )


async def counts(db: AsyncSession, owner: uuid.UUID) -> tuple[FacesPhotos, FacesCount, bool]:
    """De stand uit de indexen, min wat own_live (OWN_LIVE_SQL) laat vallen: één vraag
    (_counts_sql); de stand wordt vaak gevraagd (p95 ≤ 50 ms bij 60.000 gezichten)."""
    states: Counter[str] = Counter()
    sizes: Counter[int] = Counter()
    found = named = 0
    wiping = False
    sql = _counts_sql(OWN_LIVE_SQL, dbdialect.name_of(db))
    for k, a, n, ja in await db.execute(sql, {"me": _me(db, owner)}):
        if k == 1:
            states[a] += n
        elif k == 2:
            states[a] -= n
        elif k == 3:
            sizes[int(a)] += n
        elif k == 4:
            found, named = found - n, named - ja
            if a is not None:
                sizes[int(a)] -= n
        elif k == 5:
            found += n
        elif k == 6:
            named += n
        else:
            wiping = bool(int(a))  # PostgreSQL: als tekst (zie _counts_sql)
    photos = FacesPhotos(
        total=sum(states.values()),
        done=states["done"],
        waiting=states["wait"] + states["busy"],
        failed=states["failed"],
        skipped=states["skip"],
    )
    faces = FacesCount(
        found=found,
        named=named,
        open=found - named,
        groups=sum(1 for n in sizes.values() if n >= drempels.MIN_GROUP),
    )
    return photos, faces, wiping


async def suggestions(
    db: AsyncSession, owner: uuid.UUID, cursor: str | None
) -> SuggestionsByPerson:
    start = offset(cursor)
    per: Counter[uuid.UUID] = Counter(
        dict(
            (r.suggest_id, r.n)
            for r in await db.execute(
                select(Face.suggest_id, func.count().label("n"))
                .where(Face.owner_id == owner, Face.hidden == 0, Face.suggest_id.is_not(None))
                .group_by(Face.suggest_id)
            )
        )
    )
    per.subtract(await _gone_by(db, owner, "suggest_id", OWN_LIVE_SQL))
    names = await _names(db, owner, {p for p, n in per.items() if n > 0}, shown=True)
    order = sorted(((p, n) for p, n in per.items() if n > 0 and p in names),
                   key=lambda pn: (-pn[1], pn[0].hex))  # fmt: skip
    page = order[start : start + PEOPLE_PAGE + 1]
    shown = page[:PEOPLE_PAGE]
    rows = await _firsts(db, owner, "suggest_id", [person for person, _n in shown])
    rows.sort(key=lambda r: (-(r.suggest_score or 0), r.r))
    faces = await faces_out(db, owner, rows, names)
    sample: dict[uuid.UUID, list[FaceOut]] = {}
    for r, face in zip(rows, faces, strict=True):
        sample.setdefault(r.suggest_id, []).append(face)
    people = [
        PersonSuggestions(
            person=PersonRef(id=person, name=names[person]),
            count=n,
            sample=sample.get(person, []),
        )
        for person, n in shown
    ]
    return SuggestionsByPerson(people=people, cursor=_next(start, len(page), PEOPLE_PAGE))


def _best_first(db: Any, score: Any) -> Any:
    """De hoogste score eerst; zonder score achteraan, zoals SQLite NULL sorteert (PostgreSQL
    zet NULL bij DESC vooraan). De kolom komt van de vraag zelf, die own_live toepast."""
    if dbdialect.is_pg(db):
        return score.desc().nulls_last()
    return score.desc()


async def person_suggestions(
    db: AsyncSession, owner: uuid.UUID, person: uuid.UUID, cursor: str | None, limit: int
) -> FacePage:
    start = offset(cursor)
    rows = list(
        await db.execute(
            _face_cols(owner, own_live)
            .where(Face.suggest_id == person)
            .order_by(_best_first(db, Face.suggest_score), ROWID)
            .offset(start)
            .limit(limit + 1)
        )
    )
    return FacePage(
        faces=await faces_out(db, owner, rows[:limit]), cursor=_next(start, len(rows), limit)
    )


async def run_of(db: AsyncSession, owner: uuid.UUID) -> int:
    return int(await db.scalar(select(FaceOwner.run).where(FaceOwner.owner_id == owner)) or 0)


async def groups(db: AsyncSession, owner: uuid.UUID, cursor: str | None) -> FaceGroups:
    start = offset(cursor)
    run = await run_of(db, owner)
    sizes = await _cluster_sizes(db, owner, OWN_LIVE_SQL)
    order = sorted(((c, n) for c, n in sizes.items() if n >= drempels.MIN_GROUP),
                   key=lambda cn: (-cn[1], cn[0]))  # fmt: skip
    page = order[start : start + GROUPS_PAGE + 1]
    shown = page[:GROUPS_PAGE]
    rows = await _firsts(db, owner, "cluster", [cluster for cluster, _n in shown])
    rows.sort(key=lambda r: -r.r)
    faces = await faces_out(db, owner, rows)
    per: dict[int, list[FaceOut]] = {}
    for r, face in zip(rows, faces, strict=True):
        per.setdefault(r.cluster, []).append(face)
    out = [
        FaceGroup(key=f"{run}:{cluster}", count=n, faces=per.get(cluster, []))
        for cluster, n in shown
    ]
    return FaceGroups(groups=out, cursor=_next(start, len(page), GROUPS_PAGE))


async def group(
    db: AsyncSession, owner: uuid.UUID, run: int, cluster: int, cursor: str | None
) -> FacePage:
    start = offset(cursor)
    rows = list(
        await db.execute(
            _face_cols(owner, own_live)
            .where(Face.cluster == cluster)
            .order_by(ROWID.desc())
            .offset(start)
            .limit(FACES_PAGE + 1)
        )
    )
    return FacePage(
        faces=await faces_out(db, owner, rows[:FACES_PAGE]),
        cursor=_next(start, len(rows), FACES_PAGE),
    )


async def crop_source(db: AsyncSession, owner: uuid.UUID, face_id: uuid.UUID) -> Any:
    """(blob sha, x, y, w, h) van een eigen, levend gezicht, of None."""
    return (
        await db.execute(
            select(Node.blob_id, Face.x, Face.y, Face.w, Face.h)
            .join(Node, Node.id == Face.node_id)
            .where(Face.id == face_id, Face.owner_id == owner, own_live(Node, owner))
        )
    ).first()
