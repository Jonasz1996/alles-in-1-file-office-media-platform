"""De groepen van GET /duplicates (bouwontwerp fase 6, Keuzes 34–36 en 4a; Z3). Alleen eigen,
levende bestanden: elke vraag hier heeft own_live (tests/search/test_own_live.py).

- Precies gelijk (Keuze 34): dezelfde blob_id bij twee of meer eigen, levende bestanden, met de
  vraag van Keuze 4a (GROUP BY op ix_node_owner_blob). Een groep per inhoud.
- Bijna gelijk (Keuze 36): de paren uit dup_pair waarvan beide inhouden een eigen, levend
  bestand hebben (de vorm met twee keer EXISTS van S10; de vorm met IN mine is een
  kruisproduct), en daaruit de klieken: de paren op volgorde van afstand ("zeker" vóór "lijkt"
  bij gelijke afstand); een inhoud komt alleen bij een groep als hij met elk lid een paar is,
  en zit in hooguit één groep. Nooit "verbonden via een ander". Het niveau van een groep is dat
  van zijn zwakste paar, dist de grootste afstand erin.
- Tekst telt mee (Keuze 35), ook bij het lezen: tekst die na de pHash kwam (OCR), maakt een
  paar "zeker" alsnog "lijkt" als beide inhouden tekst hebben en die anders is.
- Een groep die de gebruiker "niet gelijk" noemde (dup_ignore, op zijn sleutel), komt niet
  terug; een groep die erbij krijgt of verliest, heeft een andere sleutel en komt wel terug.

De sleutel van een groep is de sha256 van zijn gesorteerde shas (phash.group_key). Nooit iets
vooraf gekozen: een groep is alleen een lijst; weggooien gaat via de prullenbak.
"""

from __future__ import annotations

import base64
import binascii
import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.dupes import phash
from app.models import BlobText, DupIgnore, DupPair, Node, User
from app.search.live import own_live

PAGE = 50
Kind = Literal["exact", "near"]
_LEVELS = {"zeker": 0, "lijkt": 1}


@dataclass(frozen=True)
class Group:
    """Een groep; rank is zijn plek in de volgorde: precies gelijk met de meeste bestanden
    eerst, bijna gelijk "zeker" eerst en dan de kleinste afstand; daarna de sleutel."""

    key: str
    kind: Kind
    level: str | None
    dist: int | None
    shas: tuple[str, ...]
    rank: tuple[int, int, str]


def cliques(pairs: Iterable[tuple[str, str, int, str]]) -> list[Group]:
    """De klieken uit paren (a, b, afstand, niveau), zonder database (Keuze 36)."""
    near: dict[str, dict[str, tuple[int, str]]] = {}
    ordered = sorted(pairs, key=lambda p: (p[2], _LEVELS.get(p[3], 1), p[0], p[1]))
    for a, b, dist, level in ordered:
        near.setdefault(a, {})[b] = (dist, level)
        near.setdefault(b, {})[a] = (dist, level)
    member: dict[str, list[str]] = {}
    groups: list[list[str]] = []
    for a, b, _dist, _level in ordered:
        ga, gb = member.get(a), member.get(b)
        if ga is None and gb is None:
            group = [a, b]
            groups.append(group)
            member[a] = member[b] = group
        elif gb is None and ga is not None and all(m in near[b] for m in ga):
            ga.append(b)
            member[b] = ga
        elif ga is None and gb is not None and all(m in near[a] for m in gb):
            gb.append(a)
            member[a] = gb
        # Beide al in een groep (dezelfde of een andere), of niet met elk lid: niets
    out = []
    for group in groups:
        links = [near[x][y] for i, x in enumerate(group) for y in group[i + 1 :]]
        level = "zeker" if all(lv == "zeker" for _d, lv in links) else "lijkt"
        shas = tuple(sorted(group))
        key = phash.group_key(shas)
        dist = max(d for d, _lv in links)
        out.append(Group(key, "near", level, dist, shas, (_LEVELS[level], dist, key)))
    return out


async def exact(db: AsyncSession, user: User) -> list[Group]:
    """Precies gelijk: elke inhoud met twee of meer eigen, levende bestanden (Keuze 4a)."""
    n = func.count().label("n")
    rows = await db.execute(
        select(Node.blob_id, n)
        .where(own_live(Node, user.id))
        .group_by(Node.blob_id)
        .having(func.count() > 1)
    )
    out = []
    for sha, count in rows:
        if sha:
            key = phash.group_key([sha])
            out.append(Group(key, "exact", None, None, (sha,), (-count, 0, key)))
    out.sort(key=lambda g: g.rank)
    return out


async def near_pairs(db: AsyncSession, user: User) -> list[tuple[str, str, int, str]]:
    """De paren tussen eigen, levende bestanden (de vorm van S10: twee keer EXISTS)."""
    na, nb = aliased(Node), aliased(Node)
    rows = await db.execute(
        select(DupPair.a, DupPair.b, DupPair.dist, DupPair.level).where(
            exists().where(na.blob_id == DupPair.a, own_live(na, user.id)),
            exists().where(nb.blob_id == DupPair.b, own_live(nb, user.id)),
        )
    )
    return [(a, b, dist, level) for a, b, dist, level in rows]


async def _text_keys(db: AsyncSession, shas: Sequence[str]) -> dict[str, str | None]:
    """{sha: hash van de gevouwen tekst}, alleen voor deze inhoud (die al eigen en levend is)."""
    out: dict[str, str | None] = {}
    for i in range(0, len(shas), 200):
        rows = await db.execute(
            select(BlobText.blob_sha, BlobText.body, BlobText.ocr).where(
                BlobText.blob_sha.in_(shas[i : i + 200])
            )
        )
        out.update({sha: phash.text_key(body, ocr) for sha, body, ocr in rows})
    return out


async def near(db: AsyncSession, user: User) -> list[Group]:
    """Bijna gelijk: de klieken uit de paren van eigen, levende foto's (Keuze 36)."""
    pairs = await near_pairs(db, user)
    sure = sorted({sha for a, b, _d, level in pairs if level == "zeker" for sha in (a, b)})
    texts = await _text_keys(db, sure) if sure else {}
    checked = [
        (a, b, dist, "lijkt" if phash.text_differs(texts.get(a), texts.get(b)) else level)
        for a, b, dist, level in pairs
    ]
    found = cliques(checked)
    found.sort(key=lambda g: g.rank)
    return found


async def ignored(db: AsyncSession, user: User) -> set[str]:
    rows = await db.scalars(select(DupIgnore.key).where(DupIgnore.user_id == user.id))
    return set(rows)


async def groups(db: AsyncSession, user: User, kind: Kind) -> list[Group]:
    """Alle groepen van deze soort, zonder wat de gebruiker "niet gelijk" noemde."""
    found = await exact(db, user) if kind == "exact" else await near(db, user)
    skip = await ignored(db, user)
    return [g for g in found if g.key not in skip]


async def members(db: AsyncSession, user: User, shas: Iterable[str]) -> list[Node]:
    """De eigen, levende bestanden met deze inhoud."""
    todo = sorted(set(shas))
    nodes: list[Node] = []
    for i in range(0, len(todo), 500):
        nodes += list(
            await db.scalars(
                select(Node).where(own_live(Node, user.id), Node.blob_id.in_(todo[i : i + 500]))
            )
        )
    return nodes


def encode_cursor(group: Group) -> str:
    """De cursor na deze groep: zijn soort en plek in de volgorde (base64url van JSON)."""
    raw = json.dumps([group.kind, list(group.rank)], separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(text: str, kind: Kind) -> tuple[int, int, str] | None:
    """De plek uit een cursor van deze soort, of None als hij niet klopt."""
    try:
        value = json.loads(base64.urlsafe_b64decode(text + "=" * (-len(text) % 4)))
    except (binascii.Error, ValueError, UnicodeDecodeError):
        return None
    if not (isinstance(value, list) and len(value) == 2 and value[0] == kind):
        return None
    rank = value[1]
    if not (isinstance(rank, list) and len(rank) == 3):
        return None
    a, b, key = rank
    ints = all(isinstance(v, int) and not isinstance(v, bool) for v in (a, b))
    if not ints or not isinstance(key, str) or not is_key(key):
        return None
    return (a, b, key)


def is_key(text: str) -> bool:
    """Een sleutel van een groep: 64 tekens hex, kleine letters."""
    return len(text) == 64 and all(c in "0123456789abcdef" for c in text)
