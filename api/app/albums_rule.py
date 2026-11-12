"""Slimme albums: een album met een regel (bouwontwerp fase 6, Keuze 42; Z6).

- De regel is een zoekvraag zonder pagina (SearchRule): q en dezelfde filters als zoeken, uit
  het register (filters.apply_all). Hij past alleen op eigen, levende foto's en video's met een
  opnamedag (own_live), hooguit MAX_ITEMS; meer is album_rule_too_many.
- De verversing zet wat past erbij met source 'rule' en shared = album_rule.share_new (gelezen
  als ze schrijft, niet vóór het zoeken), en haalt alleen 'rule'-items weg die levend zijn en
  niet meer passen. Een item in de prullenbak blijft
  lid (het album toont het niet, zoals in 0.7.0); zet je het terug, dan staat het er weer.
  Items met de hand blijven altijd. Wat de eigenaar uit een album met een regel haalt, komt in
  album_skip: de regel zet het niet terug (een item met de hand dat ook past evenmin).
- Delen: een item met de hand is altijd gedeeld (shared 1); een 'rule'-item krijgt share_new
  (standaard 0). access._via_albums, public._in_album en de routes van albums tonen anderen
  alleen shared 1. confirm(True) zet share_new op 1 en alle 'rule'-items op shared 1;
  confirm(False) laat ze zoals ze zijn (alleen voor de eigenaar). Een andere regel zonder
  antwoord zet share_new weer op 0: "ja" gold voor de regel van toen.
- Wie het album verversen laat (de route, elke nacht, 60 s na een reeks uploads): zie
  routers/albums_rule.py. Hier staat alleen wat met de database gebeurt; committen doet de
  aanroeper.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import and_, bindparam, case, delete, exists, func, literal_column, or_, select
from sqlalchemy import update as sql_update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app import errors
from app.config import Settings
from app.models import AlbumItem, AlbumRule, AlbumSkip, Node, Person, Tag, User
from app.people.types import AlbumRuleInfo
from app.routers.files import get_live_dir, to_utc
from app.search.engine import FTS_NODE, LIKE, engine_for
from app.search.filters import DATED, FILTERS, apply_all
from app.search.like import check_library
from app.search.live import own_live
from app.search.query import MAX_WORDS, QueryTooLong, parse
from app.search.types import SearchParams, SearchRule
from app.text import fold

MAX_ITEMS = 10_000
TOO_MANY = "album_rule_too_many"
# De filters van een ander pakket (zoals in routers/search.LATER): zonder aanmelding kan een
# regel ze niet gebruiken
LATER = (("persons", "person"), ("place", "place"), ("country", "country"), ("camera", "camera"))
_CHUNK = 500


def now() -> datetime:
    return datetime.now(UTC)


def load(text: str) -> SearchRule:
    return SearchRule.model_validate_json(text)


def dump(rule: SearchRule) -> str:
    """Zoals de web hem stuurt (de namen van §3.4), zonder wat leeg is."""
    return rule.model_dump_json(by_alias=True, exclude_defaults=True)


def invalid() -> HTTPException:
    return errors.fail(400, "album_rule_invalid", "Met deze regel kan het album niet gevuld worden")


def too_many() -> HTTPException:
    return errors.fail(
        400, TOO_MANY, "Deze regel past op te veel foto's voor één album", max=MAX_ITEMS
    )


async def check(db: AsyncSession, user: User, rule: SearchRule) -> None:
    """400 album_rule_invalid als de regel niets zegt (geen q en geen filter), een filter heeft
    dat (nog) niet bestaat, of iets noemt dat niet van jou is of dat je niet ziet: een map
    (in, een eigen map die niet in de prullenbak ligt), een bibliotheek, een tag, een persoon."""
    try:
        parsed = parse(rule.q)
    except QueryTooLong:
        raise invalid() from None
    if parsed is None and not rule.has_filter():
        raise invalid()
    for field, name in LATER:
        value = getattr(rule, field)
        if (value is not None if field == "place" else bool(value)) and name not in FILTERS:
            raise invalid()
    if rule.in_ is not None:
        try:
            found = await get_live_dir(db, user, rule.in_, "read")
        except HTTPException:
            raise invalid() from None
        if not found.owner:
            raise invalid()
    if rule.library is not None:
        try:
            await check_library(db, user, rule.library)
        except HTTPException:
            raise invalid() from None
    if rule.tags:
        mine = await db.scalar(
            select(func.count()).where(Tag.id.in_(set(rule.tags)), Tag.owner_id == user.id)
        )
        if mine != len(set(rule.tags)):
            raise invalid()
    if rule.persons:
        mine = await db.scalar(
            select(func.count()).where(Person.id.in_(set(rule.persons)), Person.owner_id == user.id)
        )
        if mine != len(set(rule.persons)):
            raise invalid()


def params_of(rule: SearchRule) -> SearchParams:
    try:
        return SearchParams.model_validate(rule.model_dump())
    except ValidationError:  # kan niet: SearchParams is SearchRule met meer velden
        raise invalid() from None


async def matches(
    db: AsyncSession, settings: Settings, owner: User, album_id: uuid.UUID, rule: SearchRule
) -> list[uuid.UUID]:
    """De eigen, levende foto's en video's (met een opnamedag) waar de regel op past, zonder
    album_skip, nieuwste eerst; hooguit MAX_ITEMS + 1 (meer is te veel). q zoals de motor:
    met de index op hele woorden (fts_node) of op stukken van de naam; zonder de index elk
    woord in de naam (zoals LikeFallback)."""
    n = Node.__table__.alias("n")
    sel = select(n.c.id).where(
        own_live(n, owner.id),
        n.c.ftype.in_(DATED),
        n.c.taken_day.is_not(None),
        ~exists().where(AlbumSkip.album_id == album_id, AlbumSkip.node_id == n.c.id),
    )
    sel = apply_all(sel, n, params_of(rule), owner)
    if rule.q.strip():
        engine = await engine_for(settings, await db.connection())
        if engine is LIKE:
            words = fold(rule.q).split()[:MAX_WORDS]
            sel = sel.where(*(n.c.search_name.contains(w, autoescape=True) for w in words))
        else:
            parsed = parse(rule.q)
            if parsed is not None:
                whole = n.c.num.in_(
                    select(FTS_NODE.c.rowid).where(
                        literal_column("fts_node").op("MATCH")(
                            bindparam("rule_match", parsed.match)
                        )
                    )
                )
                pieces = and_(*(func.instr(n.c.search_name, w) > 0 for w in parsed.words))
                sel = sel.where(or_(whole, pieces))
    sel = sel.order_by(n.c.taken_day.desc(), n.c.taken_at.desc(), n.c.id.desc())
    return list((await db.scalars(sel.limit(MAX_ITEMS + 1))).all())


@dataclass
class Refreshed:
    added: int = 0
    removed: int = 0
    # Kwam er iets bij of ging er iets af dat anderen zien (shared 1)?
    shared_changed: bool = False
    too_many: bool = False


async def _live_rule_items(
    db: AsyncSession, owner: User, album_id: uuid.UUID, candidates: list[uuid.UUID]
) -> set[uuid.UUID]:
    """Welke van deze 'rule'-items nog levend en van de eigenaar zijn (own_live)."""
    found: set[uuid.UUID] = set()
    for i in range(0, len(candidates), _CHUNK):
        part = candidates[i : i + _CHUNK]
        rows = await db.scalars(
            select(AlbumItem.node_id)
            .join(Node, Node.id == AlbumItem.node_id)
            .where(
                AlbumItem.album_id == album_id,
                AlbumItem.node_id.in_(part),
                own_live(Node, owner.id),
            )
        )
        found.update(rows.all())
    return found


async def refresh(
    db: AsyncSession, settings: Settings, owner: User, album_id: uuid.UUID
) -> Refreshed | None:
    """Het album bij zijn regel brengen (niet gecommit). None: er is geen regel (meer). Past
    de regel op meer dan MAX_ITEMS, dan verandert er niets aan de items en krijgt de regel de
    code album_rule_too_many (te zien voor de eigenaar)."""
    row = await db.get(AlbumRule, album_id, populate_existing=True)
    if row is None:
        return None
    text = row.rule
    found = await matches(db, settings, owner, album_id, load(text))
    # Eerst schrijven, dan share_new lezen (review golf 2, veilig V2): de UPDATE neemt de
    # schrijfsleutel van SQLite, en wat hij teruggeeft verandert niet meer tot de commit. Een
    # "nee" dat binnenkwam terwijl matches() zocht, telt zo voor wat er nu bij komt. Staat er
    # intussen een andere regel (of geen), dan doet deze verversing niets: wie de regel zette,
    # verving het album al.
    share_new = await db.scalar(
        sql_update(AlbumRule)
        .where(AlbumRule.album_id == album_id, AlbumRule.rule == text)
        .values(refreshed_at=now())
        .returning(AlbumRule.share_new)
        .execution_options(synchronize_session=False)
    )
    if share_new is None:
        return None
    await db.refresh(row)
    if len(found) > MAX_ITEMS:
        row.code = TOO_MANY
        return Refreshed(too_many=True)
    present = {
        node_id: (source, shared)
        for node_id, source, shared in (
            await db.execute(
                select(AlbumItem.node_id, AlbumItem.source, AlbumItem.shared).where(
                    AlbumItem.album_id == album_id
                )
            )
        ).all()
    }
    wanted = set(found)
    adding = [i for i in found if i not in present]
    gone = [i for i, (source, _) in present.items() if source == "rule" and i not in wanted]
    stale = await _live_rule_items(db, owner, album_id, gone) if gone else set()
    result = Refreshed(added=len(adding), removed=len(stale))
    if adding:
        at = now()
        # Zonder fout als een verversing of de eigenaar het intussen ook toevoegde
        await db.execute(
            sqlite_insert(AlbumItem).on_conflict_do_nothing(),
            [
                {
                    "album_id": album_id,
                    "node_id": i,
                    "added_by": owner.id,
                    "added_at": at,
                    "source": "rule",
                    "shared": share_new,
                }
                for i in adding
            ],
        )
        result.shared_changed = bool(share_new)
    if stale:
        ids = list(stale)
        for i in range(0, len(ids), _CHUNK):
            await db.execute(
                delete(AlbumItem).where(
                    AlbumItem.album_id == album_id, AlbumItem.node_id.in_(ids[i : i + _CHUNK])
                )
            )
        result.shared_changed = result.shared_changed or any(present[i][1] for i in stale)
    row.count = len(found)
    row.code = None
    return result


async def save(
    db: AsyncSession, album_id: uuid.UUID, rule: SearchRule, share_new: bool | None
) -> AlbumRule:
    """De regel van dit album zetten of vervangen (niet gecommit). share_new None: een nieuwe of
    andere regel krijgt 0, dezelfde regel houdt wat hij had. Een "ja" gold voor wat de regel van
    toen erbij zou zetten; een andere regel vindt andere foto's die er al zijn, dus de vraag
    opnieuw (review golf 2, veilig V1). Wat al gedeeld is, blijft gedeeld."""
    text = dump(rule)
    row = await db.get(AlbumRule, album_id, populate_existing=True)
    if row is None:
        row = AlbumRule(album_id=album_id, rule=text, share_new=0, count=0)
        db.add(row)
    elif row.rule != text:
        row.rule = text
        if share_new is None:
            row.share_new = 0
    await db.flush()
    if share_new is not None:
        await confirm(db, row, share_new)
    return row


async def confirm(db: AsyncSession, row: AlbumRule, share_new: bool) -> int:
    """Het antwoord op de vraag van Keuze 42 (niet gecommit). Ja: share_new 1 en elk
    'rule'-item gedeeld; nee: share_new 0 en de items blijven zoals ze zijn. Geeft hoeveel
    items nu gedeeld werden."""
    row.share_new = int(share_new)
    row.confirmed_at = now()
    if not share_new:
        return 0
    result = await db.execute(
        sql_update(AlbumItem)
        .where(
            AlbumItem.album_id == row.album_id,
            AlbumItem.source == "rule",
            AlbumItem.shared == 0,
        )
        .values(shared=1)
    )
    return int(result.rowcount or 0)


async def remove(db: AsyncSession, album_id: uuid.UUID) -> bool:
    """De regel weg (niet gecommit); de items van de regel blijven, met hun shared, en
    album_skip blijft ook (komt er later weer een regel, dan blijft weg wat je weghaalde)."""
    result = await db.execute(delete(AlbumRule).where(AlbumRule.album_id == album_id))
    return bool(result.rowcount)


async def skip(db: AsyncSession, album_id: uuid.UUID, node_ids: list[uuid.UUID]) -> None:
    """Wat de eigenaar uit een album met een regel haalt: de regel zet het niet terug."""
    if not node_ids or await db.get(AlbumRule, album_id) is None:
        return
    await db.execute(
        sqlite_insert(AlbumSkip)
        .values([{"album_id": album_id, "node_id": i} for i in node_ids])
        .on_conflict_do_nothing()
    )


async def by_hand(db: AsyncSession, album_id: uuid.UUID, node_ids: list[uuid.UUID]) -> int:
    """De eigenaar zet deze items met de hand in het album (niet gecommit): weg uit album_skip,
    en een 'rule'-item dat er al stond wordt een item met de hand, dus gedeeld zoals elk item
    met de hand. Geeft hoeveel 'rule'-items zo met de hand werden."""
    if not node_ids:
        return 0
    await db.execute(
        delete(AlbumSkip).where(AlbumSkip.album_id == album_id, AlbumSkip.node_id.in_(node_ids))
    )
    result = await db.execute(
        sql_update(AlbumItem)
        .where(
            AlbumItem.album_id == album_id,
            AlbumItem.node_id.in_(node_ids),
            AlbumItem.source == "rule",
        )
        .values(source="hand", shared=1)
    )
    return int(result.rowcount or 0)


async def info(db: AsyncSession, album_ids: list[uuid.UUID]) -> dict[uuid.UUID, AlbumRuleInfo]:
    """De regel per album (alleen voor de eigenaar), voor GET /albums en /albums/{id}."""
    if not album_ids:
        return {}
    rows = list(await db.scalars(select(AlbumRule).where(AlbumRule.album_id.in_(album_ids))))
    if not rows:
        return {}
    # Per album met een regel: de items uit de regel, en hoeveel daarvan niet gedeeld zijn
    counts = {
        album_id: (int(n or 0), int(private or 0))
        for album_id, n, private in await db.execute(
            select(
                AlbumItem.album_id,
                func.count(),
                func.sum(case((AlbumItem.shared == 0, 1), else_=0)),
            )
            .where(
                AlbumItem.album_id.in_([row.album_id for row in rows]),
                AlbumItem.source == "rule",
            )
            .group_by(AlbumItem.album_id)
        )
    }
    return {
        row.album_id: AlbumRuleInfo(
            rule=load(row.rule),
            share_new=bool(row.share_new),
            refreshed_at=to_utc(row.refreshed_at),  # SQLite bewaart geen tijdzone
            count=row.count,
            code=row.code,
            items=counts.get(row.album_id, (0, 0))[0],
            private=counts.get(row.album_id, (0, 0))[1],
        )
        for row in rows
    }


async def private_count(db: AsyncSession, album_id: uuid.UUID) -> int | None:
    """Hoeveel 'rule'-items alleen voor de eigenaar zijn, als het album een regel heeft met
    share_new 0 (voor de vraag in het deeldialoog); anders None."""
    row = await db.get(AlbumRule, album_id)
    if row is None or row.share_new:
        return None
    found = await db.scalar(
        select(func.count()).where(
            AlbumItem.album_id == album_id, AlbumItem.source == "rule", AlbumItem.shared == 0
        )
    )
    return int(found or 0)
