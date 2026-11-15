"""Duplicaten (bouwontwerp fase 6, Keuzes 34–36 en §3.4; Z3). Alleen eigen, levende bestanden; een
gast heeft geen eigen bestanden (403 guest, zoals /photos).

- GET /duplicates?kind=exact|near&cursor=: de groepen (app/dupes/groups.py), per PAGE, in een
  vaste volgorde; de cursor is de plek van de laatste groep. Per groep elk eigen, levend bestand
  met die inhoud: de node zoals in een lijst, het pad zoals bij een treffer van zoeken, de
  grootte en de opnamedatum. Bij exact zijn level en dist null. Er staat nooit iets vooraf
  gekozen: opruimen gaat via de bestaande batchroute van de prullenbak.
- POST /duplicates/ignore {"key"}: "niet gelijk", in dup_ignore (204, ook een tweede keer). Een
  sleutel die nu geen groep van jou is (of geen sleutel): 400 dupes_bad_key.
"""

from typing import Annotated, Literal

from fastapi import APIRouter, Query
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app import errors
from app.db import Session
from app.dupes import groups
from app.geo.types import DupeGroup, DupeIgnoreIn, DupeItem, DupePage
from app.models import DupIgnore
from app.routers.files import describe
from app.routers.photos import owner_only
from app.search.like import paths
from app.security import CurrentUser

router = APIRouter(prefix="/api/v1", tags=["foto's"])


@router.get("/duplicates")
async def duplicates(
    user: CurrentUser,
    session: Session,
    kind: Literal["exact", "near"] = "exact",
    cursor: Annotated[str | None, Query(max_length=200)] = None,
) -> DupePage:
    owner_only(user)
    after = None
    if cursor is not None:
        after = groups.decode_cursor(cursor, kind)
        if after is None:
            raise errors.fail(422, "invalid_input", "Ongeldige invoer", field="query.cursor")
    found = await groups.groups(session, user, kind)
    if after is not None:
        found = [g for g in found if g.rank > after]
    page = found[: groups.PAGE]
    files = await groups.members(session, user, {sha for g in page for sha in g.shas})
    by_sha: dict[str, list] = {}
    for f in files:
        by_sha.setdefault(f.blob_id, []).append(f)
    where = await paths(session, files)
    described = {out.id: out for out in await describe(session, user, files, "owner")}
    out = []
    for g in page:
        # Groot eerst, dan de oudste; niets gekozen
        items = sorted(
            (f for sha in g.shas for f in by_sha.get(sha, [])),
            key=lambda f: (-(f.size or 0), f.created_at, str(f.id)),
        )
        if len(items) < 2:
            continue  # intussen weg (een ander verzoek): geen groep meer
        out.append(
            DupeGroup(
                key=g.key,
                kind=g.kind,
                level=g.level,  # type: ignore[arg-type]
                dist=g.dist,
                items=[
                    DupeItem(
                        node=described[f.id],
                        path=where.get(f.id, []),
                        size=f.size or 0,
                        taken_at=f.taken_at,
                    )
                    for f in items
                ],
            )
        )
    more = len(found) > groups.PAGE
    return DupePage(groups=out, cursor=groups.encode_cursor(page[-1]) if more else None)


@router.post("/duplicates/ignore", status_code=204)
async def ignore(body: DupeIgnoreIn, user: CurrentUser, session: Session) -> None:
    owner_only(user)
    key = body.key
    known = groups.is_key(key) and (
        key in await groups.ignored(session, user)
        or any(g.key == key for g in await groups.exact(session, user))
        or any(g.key == key for g in await groups.near(session, user))
    )
    if not known:
        raise errors.fail(400, "dupes_bad_key", "Deze groep bestaat niet (meer)")
    await session.execute(
        sqlite_insert(DupIgnore.__table__).on_conflict_do_nothing(),
        [{"user_id": user.id, "key": key}],
    )
    await session.commit()
