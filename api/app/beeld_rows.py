"""De rijen van zoeken op beeld schrijven en onderhouden (bouwontwerp fase 6, X3; Keuzes 69, 76,
82; X3b).

Zoals app/photo_rows.py (Z3) en app/face_rows.py (X2b): dit zijn de schrijvers en het onderhoud,
per inhoud of per eigenaar. Ze raken ook de rijen van foto's in de prullenbak en kijken per inhoud
naar elke eigenaar met beeld aan, dus zonder own_live: het bestand per eigenaar bevat ook de foto's
in de prullenbak (Keuze 76; de vraag filtert met own_live, Keuze 78), en uit en wissen halen ook
hun vectoren weg. own_live zou daar juist rijen laten staan of missen. Daarom staan ze niet onder
app/beeld/, waar de wachter van tests/search/test_own_live.py elke functie die foto's leest aan
own_live houdt. Wat foto's leest om te zoeken of te tonen (app/beeld/search.py) staat daar wel,
met own_live.

- **write_embed()** is de enige schrijver van blob_embed (zoals write_text, Keuze 3): eerst een
  SELECT, dan een gewone INSERT of één UPDATE. In dezelfde transactie gaat beeld_owner.gen + 1
  voor elke eigenaar van een beeldbestand met die sha (gewone INSERT … WHERE NOT EXISTS en UPDATE,
  zoals de triggers van 0012). tests/beeld/test_embed_step.py houdt dat vast met een AST-test.
- **Keuze 69** als SQL: on_users_sql (wie beeld aan heeft) en origin_ok (zelf geüpload, of door
  iemand met beeld aan; via een link nooit).
- **De stap embed**: welke inhoud een eigenaar met beeld aan nog wil (wanted), en wie daarna een
  nieuw bestand krijgt (owners).
- **Het bestand per eigenaar**: file_rows, de rijen van één eigenaar, ook in de prullenbak.
- **Aan en uit**: de inhoud van iemands foto's (mine_sql), of een ander die nog wil
  (wanted_exists), de eigenaren in wier map hij foto's zette (bump_others), en wat bij uit weg mag
  (unwanted_of, delete_embeds).
- **Wissen** door de beheerder: delete_all.
- **De tellingen voor /beheer** (totals): voor de hele server, nooit per gebruiker.

Nooit een vector, een sha of een eigenaar in een log: alleen aantallen.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import bindparam, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app import analysis
from app.analysis import Step
from app.beeld import types as vt
from app.models import BlobEmbed, now
from app.prefs import PHOTO_PREFS

# --- Keuze 69 ------------------------------------------------------------------------------------


def on_users_sql() -> str:
    """De gebruikers met beeld aan, als subquery. NULL in photo_pref.beeld is de standaard van
    PHOTO_PREFS (Vraag 11: uit); zo verandert een ander antwoord alleen de code."""
    if PHOTO_PREFS["beeld"] == "aan":
        return (
            'SELECT u.id FROM "user" AS u LEFT JOIN photo_pref AS pp ON pp.user_id = u.id '
            "WHERE pp.beeld IS NULL OR pp.beeld = 'aan'"
        )
    return "SELECT pp.user_id FROM photo_pref AS pp WHERE pp.beeld = 'aan'"


def origin_ok(n: str = "n") -> str:
    """Keuze 69 voor beeld, als voorwaarde op node {n}: geen rij in node_origin (Vraag 9), of
    niet via een link en neergezet door de eigenaar zelf of door iemand met beeld aan."""
    return (
        f"(NOT EXISTS (SELECT 1 FROM node_origin AS o WHERE o.node_id = {n}.id) "
        f"OR EXISTS (SELECT 1 FROM node_origin AS o WHERE o.node_id = {n}.id "
        f"AND o.via_link = 0 AND o.created_by IS NOT NULL "
        f"AND (o.created_by = {n}.owner_id OR o.created_by IN ({on_users_sql()}))))"
    )


# --- write_embed (Keuze 76) ----------------------------------------------------------------------

_BUMP_NEW = text(
    "INSERT INTO beeld_owner (user_id, gen, built_gen, built_rows) "
    "SELECT DISTINCT n.owner_id, 0, 0, 0 FROM node AS n "
    "WHERE n.blob_id = :sha AND n.kind = 'file' AND n.ftype = 'image' "
    "AND NOT EXISTS (SELECT 1 FROM beeld_owner AS b WHERE b.user_id = n.owner_id)"
)
_BUMP = text(
    "UPDATE beeld_owner SET gen = gen + 1 WHERE user_id IN (SELECT n.owner_id FROM node AS n "
    "WHERE n.blob_id = :sha AND n.kind = 'file' AND n.ftype = 'image')"
)


async def write_embed(db: AsyncSession, sha: str, vec: bytes, model: str = vt.MODEL) -> None:
    """De vector van een inhoud (768 × float16, L2 = 1), in de transactie van de aanroeper (die
    commit; één korte transactie per blob). De enige schrijver van blob_embed."""
    if len(vec) != vt.DIM * 2:
        raise ValueError("een vector heeft dim × 2 bytes")
    version = analysis.VERSIONS[Step.EMBED]
    found = await db.scalar(select(BlobEmbed.id).where(BlobEmbed.blob_sha == sha))
    values = {"model": model, "version": version, "vec": vec, "updated_at": now()}
    if found is None:
        await db.execute(BlobEmbed.__table__.insert().values(blob_sha=sha, **values))
    else:
        await db.execute(
            BlobEmbed.__table__.update().where(BlobEmbed.__table__.c.id == found).values(**values)
        )
    await db.execute(_BUMP_NEW, {"sha": sha})
    await db.execute(_BUMP, {"sha": sha})


# --- de stap embed (Keuze 77) --------------------------------------------------------------------


def _in(sql: str) -> Any:
    return text(sql).bindparams(bindparam("shas", expanding=True))


def _wanted_sql() -> str:
    return (
        "SELECT DISTINCT n.blob_id FROM node AS n WHERE n.blob_id IN :shas "
        "AND n.kind = 'file' AND n.ftype = 'image' AND n.dead = 0 "
        f"AND n.owner_id IN ({on_users_sql()}) AND {origin_ok()}"
    )


def _owners_sql() -> str:
    return (
        "SELECT DISTINCT n.owner_id FROM node AS n WHERE n.blob_id IN :shas "
        f"AND n.kind = 'file' AND n.ftype = 'image' AND n.owner_id IN ({on_users_sql()})"
    )


async def wanted(db: AsyncSession, shas: Sequence[str]) -> set[str]:
    """De inhoud uit shas die een eigenaar met beeld aan nog wil: een levend beeldbestand van hem,
    zelf geüpload of door iemand met beeld aan (Keuze 69)."""
    return set((await db.execute(_in(_wanted_sql()), {"shas": list(shas)})).scalars())


async def owners(db: AsyncSession, shas: Sequence[str]) -> list[Any]:
    """De eigenaren met beeld aan van een beeldbestand met deze inhoud (ook in de prullenbak):
    hun bestand moet opnieuw."""
    return list((await db.execute(_in(_owners_sql()), {"shas": list(shas)})).scalars().all())


# --- het bestand per eigenaar (Keuze 76) ---------------------------------------------------------


def file_rows(me_param: str = ":me") -> str:
    """FROM en WHERE van de rijen van één eigenaar: elk beeldbestand van hem met een vector van
    versie :v en lengte :size, ook in de prullenbak (de vraag filtert met own_live, Keuze 78),
    zonder wat Keuze 69 uitsluit."""
    return (
        "FROM node AS n JOIN blob_embed AS e ON e.blob_sha = n.blob_id "
        f"WHERE n.owner_id = {me_param} AND n.kind = 'file' AND n.ftype = 'image' "
        f"AND e.version = :v AND length(e.vec) = :size AND {origin_ok()}"
    )


# --- aan, uit en wissen (Keuze 82, K1-08) --------------------------------------------------------


def mine_sql() -> str:
    """De inhoud van de beeldbestanden van :me, en van de beeldbestanden die :me in de map van
    een ander zette (Keuze 69: die horen bij zijn keuze); ook in de prullenbak."""
    return (
        "SELECT n.blob_id FROM node AS n WHERE n.kind = 'file' AND n.ftype = 'image' "
        "AND n.blob_id IS NOT NULL AND (n.owner_id = :me OR EXISTS (SELECT 1 FROM node_origin "
        "AS o WHERE o.node_id = n.id AND o.created_by = :me AND o.via_link = 0))"
    )


def wanted_exists(sha: str) -> str:
    """Een eigenaar met beeld aan wil deze inhoud nog (Keuze 69), ook in de prullenbak."""
    return (
        f"EXISTS (SELECT 1 FROM node AS n WHERE n.blob_id = {sha} AND n.kind = 'file' "
        f"AND n.ftype = 'image' AND n.owner_id IN ({on_users_sql()}) "
        f"AND {origin_ok()})"
    )


_OTHERS = (
    "SELECT DISTINCT n.owner_id FROM node AS n JOIN node_origin AS o ON o.node_id = n.id "
    "WHERE o.created_by = :me AND o.via_link = 0 AND n.owner_id != :me "
    "AND n.kind = 'file' AND n.ftype = 'image'"
)


async def bump_others(db: AsyncSession, user_id: uuid.UUID) -> list[Any]:
    """De eigenaren in wier map deze gebruiker foto's zette: gen + 1 (Keuze 69: of die foto's
    meetellen, hangt van zijn keuze af). Geeft die eigenaren, voor hun nieuwe bestand."""
    await db.execute(
        text(f"UPDATE beeld_owner SET gen = gen + 1 WHERE user_id IN ({_OTHERS})"),
        {"me": user_id.hex},
    )
    return list((await db.execute(text(_OTHERS), {"me": user_id.hex})).scalars().all())


async def unwanted_of(db: AsyncSession, user_id: uuid.UUID) -> list[str]:
    """Bij uit (K1-08): de inhoud met een vector van zijn foto's (ook die hij bij een ander
    neerzette, en die in de prullenbak) die niemand met beeld aan nog wil."""
    return list(
        (
            await db.execute(
                text(
                    f"SELECT e.blob_sha FROM blob_embed AS e WHERE e.blob_sha IN "
                    f"({mine_sql()}) AND NOT {wanted_exists('e.blob_sha')}"
                ),
                {"me": user_id.hex},
            )
        )
        .scalars()
        .all()
    )


async def delete_embeds(db: AsyncSession, shas: Sequence[str]) -> None:
    """De vectoren van deze inhoud weg (een stuk van hooguit CHUNK, in de transactie van de
    aanroeper)."""
    await db.execute(BlobEmbed.__table__.delete().where(BlobEmbed.blob_sha.in_(list(shas))))


async def delete_all(db: AsyncSession) -> None:
    """Alle vectoren weg (wissen door de beheerder, Keuze 82)."""
    await db.execute(BlobEmbed.__table__.delete())


# --- /beheer (Keuze 82) --------------------------------------------------------------------------


async def totals(db: AsyncSession) -> tuple[int, int]:
    """(rijen in blob_embed, bytes aan vectoren) voor de hele server; nooit per gebruiker."""
    rows, vec_bytes = (
        await db.execute(
            select(func.count(), func.coalesce(func.sum(func.length(BlobEmbed.vec)), 0))
        )
    ).one()
    return int(rows), int(vec_bytes)
