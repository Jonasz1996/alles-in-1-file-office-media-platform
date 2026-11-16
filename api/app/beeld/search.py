"""GET /search?mode=image: zoeken op wat er op een foto staat (bouwontwerp fase 6, X3; Keuze 78,
79; X3b).

Het verloop (Keuze 78): 0. beeld aan bij de beheerder en bij jou, anders 409 beeld_off; 1. de
grenzen (limits.py: 429 search_busy, een nieuwe vraag stopt de oude); 2. de cache van 60 s
(cache.py); 3. de fd van je eigen bestand (vecfile.py); 4. rank in de container met een eigen
klok van PLATFORM_BEELD_DEADLINE_MS (anyio.fail_after), met de vraag óf de num van "Meer zoals
deze" (nooit een vector, K1-08); 5. de SQL met json_each: alleen eigen, levende foto's
(OWN_LIVE_SQL en ftype image) en de filters van het register, op de volgorde van rank; 6. de
pagina en de tweede controle (access.second_check via engine.hits); 7. SearchPage met mode image
en match image, zonder snippet en zonder score.

Is de klok op, de container weg of het model er niet, dan Fallback(reden): de router geeft dan de
treffers van gewoon zoeken (Keuze 9) met fallback slow, down of no_model.

Een drempel (MIN_TEKST, MIN_FOTO) op sigmoid(scale · cos + bias): onder de grens valt een treffer
weg. BZ4 gaf geen grens die goed van niet goed scheidt (de kans van een goede treffer heeft p5
0,0; de cosinus van "Meer zoals deze" overlapt tussen zelfde en andere categorie), dus beide 0:
elke treffer telt. tests/beeld/test_beeld_drempels.py houdt ze vast.

Nooit in een log of een antwoord: de vraag, de vector, de score (K1-16).
"""

from __future__ import annotations

import logging
import math
import os
import time
import uuid
from collections import deque
from typing import Any, Literal

import anyio
from sqlalchemy import bindparam, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.sql.selectable import Join

from app import beeld_rows, errors
from app.analyse import client, monitor, wire
from app.analyse.wire import Code, Op
from app.beeld import cache as bcache
from app.beeld import jobs as bjobs
from app.beeld import limits, vecfile
from app.beeld import types as vt
from app.config import Settings
from app.models import BeeldOwner, BlobEmbed, Node, User
from app.search import engine as engines
from app.search import filters
from app.search.live import own_live
from app.search.types import BeeldHour, IndexProgress, SearchPage, SearchParams

log = logging.getLogger("platform.beeld")

Reason = Literal["slow", "down", "no_model"]
# BZ4 (x3a.md, "CI op 3a9b07d" en "CI op 669f03c"): geen grens scheidt goed van niet goed, dus 0
MIN_TEKST = 0.0
MIN_FOTO = 0.0
SQL_MS = 40  # de klok van Z1 op stap 5
HOUR_S = 3600.0


class Fallback(Exception):
    """Terug naar gewoon zoeken (Keuze 9), met de reden voor SearchPage.fallback."""

    def __init__(self, reason: Reason) -> None:
        super().__init__(reason)
        self.reason: Reason = reason


# --- het afgelopen uur (voor /beheer) ------------------------------------------------------------

_hour: deque[tuple[float, float, str | None]] = deque()


def record(ms: float, fallback: str | None) -> None:
    at = time.monotonic()
    _hour.append((at, ms, fallback))
    while _hour and at - _hour[0][0] > HOUR_S:
        _hour.popleft()


def hour() -> BeeldHour:
    at = time.monotonic()
    rows = [r for r in _hour if at - r[0] <= HOUR_S]
    times = sorted(r[1] for r in rows if r[2] is None)
    p95 = times[min(len(times) - 1, math.ceil(0.95 * len(times)) - 1)] if times else None
    return BeeldHour(
        queries=len(rows),
        p95_ms=None if p95 is None else round(p95, 1),
        slow=sum(r[2] == "slow" for r in rows),
        down=sum(r[2] == "down" for r in rows),
        no_model=sum(r[2] == "no_model" for r in rows),
    )


# --- de vraag ------------------------------------------------------------------------------------


def _bad_filter() -> Exception:
    return errors.fail(400, "search_bad_filter", "Deze filter kan niet bij zoeken op beeld")


def check_params(q: str, like: uuid.UUID | None, p: SearchParams) -> None:
    """Toegestaan: q (1–200 tekens) óf like, type alleen image, de filters van Keuze 43; niet
    after (400 search_bad_filter)."""
    if p.after is not None or any(t != "image" for t in p.types):
        raise _bad_filter()
    if bool(q.strip()) == (like is not None):
        raise _bad_filter()
    if len(q) > 200:
        raise errors.fail(400, "search_query_too_long", "Je zoekvraag is te lang", max_words=10)


async def _like_num(db: AsyncSession, user: User, like: uuid.UUID) -> int:
    """De num van een eigen, levende foto met een vector. Van een ander of niet te zien: 404
    (geen verschil met "bestaat niet"); eigen foto zonder vector: 409 beeld_no_vector."""
    n = Node.__table__
    row = (
        await db.execute(
            select(n.c.num, n.c.blob_id).where(
                n.c.id == like, own_live(n, user.id), n.c.ftype == "image"
            )
        )
    ).first()
    if row is None:
        raise errors.fail(404, "not_found", "Niet gevonden")
    has = await db.scalar(
        select(func.count()).select_from(BlobEmbed).where(BlobEmbed.blob_sha == row.blob_id)
    )
    if not has:
        raise errors.fail(409, "beeld_no_vector", "Van deze foto is nog geen beeld bekend")
    return int(row.num)


def _min_for(text: str | None) -> float:
    return MIN_TEKST if text is not None else MIN_FOTO


def _keep(hits: list[bcache.Ranked], text: str | None) -> list[bcache.Ranked]:
    """De drempel op sigmoid(scale · cos + bias); met 0 telt alles."""
    floor = _min_for(text)
    found = monitor.status()
    beeld = found.caps.beeld if found.caps is not None else None
    if floor <= 0 or beeld is None:
        return hits
    out = []
    for hit in hits:
        z = beeld.scale * (hit[1] / 10_000) + beeld.bias
        if 1 / (1 + math.exp(-max(-60.0, min(60.0, z)))) >= floor:
            out.append(hit)
    return out


async def _rank(
    settings: Settings, user: User, gen: int, text: str | None, num: int | None
) -> list[bcache.Ranked]:
    """Stap 3 en 4: de fd van het bestand en rank, binnen de klok. Bij elke treffer het begin
    van de sha van de inhoud waarmee hij in het bestand staat (uit dezelfde fd, §3.9)."""
    found = monitor.status()
    if found.state in ("off", "down"):
        raise Fallback("down")
    if not bjobs.ready(found):
        raise Fallback("no_model")
    if not vecfile.is_current(settings.beeld_dir, user.id):
        raise Fallback("slow")  # nog geen bestand, of van een oude versie: het komt zo
    fd = vecfile.open_for_worker(settings.beeld_dir, user.id)
    if fd is None:
        raise Fallback("slow")
    try:
        args = wire.RankArgs(owner=user.id.hex, gen=gen, text=text, num=num, k=settings.beeld_top_k)
        data = await _call(settings, args, fd)
        hits = [(int(a), int(b)) for a, b in wire.RankData.model_validate(data).hits]
        prefix = vecfile.prefixes(fd, [h[0] for h in hits])
    except (OSError, ValueError):
        raise Fallback("slow") from None
    finally:
        os.close(fd)
    return [(n, s, prefix[n].hex()) for n, s in hits if n in prefix]


async def _call(settings: Settings, args: wire.RankArgs, fd: int) -> Any:
    try:
        with anyio.fail_after(settings.beeld_deadline_ms / 1000):
            return await client.call(settings, Op.RANK, args, fds=[fd])
    except TimeoutError:
        raise Fallback("slow") from None
    except (client.AnalyseDown, client.AnalyseBusy):
        raise Fallback("down") from None
    except client.AnalyseFailed as exc:
        if exc.code == Code.MODEL_MISSING:
            raise Fallback("no_model") from None
        if args.num is not None and exc.code == Code.BAD_REQUEST and exc.detail == vt.NO_NUM:
            raise errors.fail(
                409, "beeld_no_vector", "Van deze foto is nog geen beeld bekend"
            ) from None
        log.warning("beeld: rank mislukt (%s)", exc.code)
        raise Fallback("slow") from None


class _CrossJoin(Join):
    """JOIN als CROSS JOIN: dan houdt SQLite de volgorde aan (de top van json_each buiten, elke
    node erbinnen met ix_node_num; Keuze 14). Zonder dit koos SQLite bij weinig statistiek soms
    alle foto's van de eigenaar (ix_node_owner_blob) en zocht daarin de top."""

    inherit_cache = True


@compiles(_CrossJoin)
def _cross_join(element: _CrossJoin, compiler: Any, **kw: Any) -> str:
    joined = compiler.visit_join(element, **kw)
    return joined.replace(" JOIN ", " CROSS JOIN ", 1)


def _select(p: SearchParams, user: User) -> Any:
    """Stap 5: de top als één JSON-parameter, eigen en levend, ftype image, de filters."""
    r = func.json_each(bindparam("nums")).table_valued("key", "value").alias("r")
    n = engines.alias()
    sel = (
        select(n.c.id, n.c.owner_id, n.c.num, n.c.blob_id)
        .select_from(_CrossJoin(r, n, n.c.num == r.c.value))
        .where(own_live(n, user.id), n.c.ftype == "image", text(beeld_rows.origin_ok("n")))
        .order_by(r.c.key)
    )
    return filters.apply_all(sel, n, p, user)


async def search(
    db: AsyncSession,
    user: User,
    settings: Settings,
    p: SearchParams,
    *,
    q: str,
    like: uuid.UUID | None,
) -> SearchPage:
    """Stap 0–7. Fallback(reden) als de router gewoon moet zoeken."""
    import json

    if user.role == "guest":
        raise errors.fail(403, "guest", "Gasten hebben geen eigen foto's")
    check_params(q, like, p)
    if not await bjobs.admin_on(db) or not await bjobs.user_on(db, user.id):
        raise errors.fail(409, "beeld_off", "Zoeken op beeld staat uit")
    text = q.strip() or None
    num = await _like_num(db, user, like) if like is not None else None
    started = time.perf_counter()
    bjobs.searched(user.id)
    owner = await db.get(BeeldOwner, user.id, populate_existing=True)
    gen = owner.built_gen if owner is not None else 0
    current = vecfile.is_current(settings.beeld_dir, user.id)
    if owner is not None and (owner.gen > owner.built_gen or not current):
        await bjobs.queue_vec(db, user.id, settings, soon=True)
        await db.commit()
    key = bcache.key(user.id, text, num, gen)
    hits = bcache.get(key)
    if hits is None:
        try:
            async with limits.admit(user.id, settings):
                await db.commit()  # geen verbinding vasthouden tijdens de tocht naar de container
                hits = _keep(await _rank(settings, user, gen, text, num), text)
        except limits.Replaced:
            return SearchPage(
                results=[], more=False, offset=p.offset, partial=True, engine="fts", mode="image"
            )
        except Fallback as exc:
            record((time.perf_counter() - started) * 1000, exc.reason)
            raise
        bcache.put(key, hits)
    conn = await db.connection()
    rows = (
        await engines.run(
            conn,
            _select(p, user).params(nums=json.dumps([h[0] for h in hits])),
            SQL_MS,
            "beeld",
        )
    ).rows
    # §3.9: een node die sinds het bestand andere inhoud kreeg, nooit met de oude vector
    same = {h[0]: h[2] for h in hits}
    rows = [r for r in rows if str(r.blob_id or "")[: vt.SHA_BYTES * 2] == same.get(r.num)]
    page_rows = rows[p.offset : p.offset + p.limit]
    results = await engines.hits(db, user, page_rows, None)
    for hit in results:
        hit.match = "image"
        hit.snippet = None
    done = await _progress(db, user.id)
    record((time.perf_counter() - started) * 1000, None)
    return SearchPage(
        results=results,
        more=len(rows) > p.offset + p.limit,
        offset=p.offset,
        ranked=True,
        engine="fts",
        mode="image",
        indexing=None if done is None else IndexProgress(done=done),
    )


async def _progress(db: AsyncSession, user_id: uuid.UUID) -> float | None:
    from app.beeld import admin

    return await admin.progress(db, user_id)
