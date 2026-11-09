"""De pHash en de paren van bijna-duplicaten schrijven (bouwontwerp fase 6, Keuzes 35 en 36; Z3).

In de keten van photometa (app/photometa.py), per stuk van 200 blobs, in vier stappen:

1. het renderproces rekent de pHash van elke afbeelding (render_meta.run_meta met "hash");
2. candidates() zoekt per nieuwe hash de kandidaten: inhoud die minstens één stuk van 16 bits
   (h0–h3, op dezelfde plek) deelt en hooguit NEAR bits verschilt; in de database (blob_hash, via
   ix_hash_0..3) en binnen het stuk zelf. Per inhoud hooguit MAX_CANDIDATES, de dichtste eerst;
3. het renderproces doet de tweede controle van die paren uit de twee voorbeelden (pairs);
4. write() zet in de transactie van photometa blob_hash (ok, flat of none) en de paren in
   dup_pair met hun niveau: "zeker" alleen met pHash ≤ 6, de tweede controle, en geen andere
   tekst (blob_text, gevouwen; Keuze 35). Een inhoud die opnieuw gehasht is, verliest eerst zijn
   oude paren.

Dit zijn schrijvers per inhoud, voor iedereen (een pHash is geen plek of persoon), zoals
app/photo_rows.py; daarom staan ze niet onder app/dupes/, waar de wachter van
tests/search/test_own_live.py elke functie die foto's leest aan own_live houdt. Wat de groepen
toont (GET /duplicates) staat daar wel, met own_live.

Een paar schrijft alleen als beide blobs er nog zijn, in dezelfde statement (INSERT … SELECT …
WHERE EXISTS): een blob die tussen het lezen en het schrijven werd opgeruimd, geeft zo geen fout
op de foreign key. Upserts zijn ON CONFLICT DO UPDATE, nooit REPLACE (Keuze 3).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import Float, Integer, String, bindparam, delete, exists, literal, or_, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.dupes import phash
from app.models import Blob, BlobHash, BlobText, DupPair

# Omhoog als de hash anders gerekend wordt: photometa rekent dan alles opnieuw
VERSION = 1
MAX_CANDIDATES = 20  # per inhoud, de dichtste eerst
SCAN = 2000  # rijen per stuk van 16 bits die we hooguit bekijken
STATES = ("ok", "flat", "none")

_HASH = BlobHash.__table__
_PAIR = DupPair.__table__
_BLOB = Blob.__table__


@dataclass(frozen=True)
class Hashed:
    """De hash van één inhoud uit het renderproces."""

    sha: str
    state: str
    value: int | None  # zonder teken; None als state niet ok is


def parse(sha: str, answer: Any) -> Hashed:
    """Het antwoord van render_meta voor één inhoud, nagekeken (een ander proces: onvertrouwd)."""
    if isinstance(answer, dict) and answer.get("state") == "ok":
        value = answer.get("phash")
        if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= phash.MASK:
            return Hashed(sha, "ok", value)
        return Hashed(sha, "none", None)
    if isinstance(answer, dict) and answer.get("state") == "flat":
        return Hashed(sha, "flat", None)
    return Hashed(sha, "none", None)


def _shares_piece(a: int, b: int) -> bool:
    return any(x == y for x, y in zip(phash.pieces(a), phash.pieces(b), strict=True))


async def candidates(db: AsyncSession, hashed: Sequence[Hashed]) -> dict[tuple[str, str], int]:
    """{(a, b): afstand} met a < b: de kandidaat-paren van deze nieuwe hashes (Keuze 35)."""
    new = {h.sha: h.value for h in hashed if h.state == "ok" and h.value is not None}
    found: dict[str, list[tuple[int, str]]] = {sha: [] for sha in new}
    for sha, value in new.items():
        p0, p1, p2, p3 = phash.pieces(value)
        rows = await db.execute(
            select(BlobHash.blob_sha, BlobHash.phash)
            .where(
                BlobHash.state == "ok",
                or_(BlobHash.h0 == p0, BlobHash.h1 == p1, BlobHash.h2 == p2, BlobHash.h3 == p3),
            )
            .limit(SCAN)
        )
        for other, other_value in rows:
            if other in new or other_value is None:
                continue  # wat in dit stuk zit, vergelijken we hieronder met zijn nieuwe hash
            dist = phash.hamming(value, other_value)
            if dist <= phash.NEAR:
                found[sha].append((dist, other))
    shas = sorted(new)
    for i, a in enumerate(shas):
        for b in shas[i + 1 :]:
            if not _shares_piece(new[a], new[b]):
                continue
            dist = phash.hamming(new[a], new[b])
            if dist <= phash.NEAR:
                found[a].append((dist, b))
                found[b].append((dist, a))
    pairs: dict[tuple[str, str], int] = {}
    for sha, near in found.items():
        for dist, other in sorted(near)[:MAX_CANDIDATES]:
            pairs[(min(sha, other), max(sha, other))] = dist
    return pairs


async def looks(db: AsyncSession, shas: Iterable[str]) -> dict[str, str | None]:
    """{sha: mime} van de inhoud die (nog) bestaat, voor de tweede controle."""
    out: dict[str, str | None] = {}
    todo = sorted(set(shas))
    for i in range(0, len(todo), 500):
        rows = await db.execute(
            select(Blob.sha256, Blob.mime).where(Blob.sha256.in_(todo[i : i + 500]))
        )
        out.update({sha: mime for sha, mime in rows})
    return out


async def text_keys(db: AsyncSession, shas: Iterable[str]) -> dict[str, str | None]:
    """{sha: hash van de gevouwen tekst of None} (Keuze 35: tekst telt mee)."""
    out: dict[str, str | None] = {}
    todo = sorted(set(shas))
    for i in range(0, len(todo), 200):
        rows = await db.execute(
            select(BlobText.blob_sha, BlobText.body, BlobText.ocr).where(
                BlobText.blob_sha.in_(todo[i : i + 200])
            )
        )
        out.update({sha: phash.text_key(body, ocr) for sha, body, ocr in rows})
    return out


def levels(
    pairs: Mapping[tuple[str, str], int],
    measured: Mapping[tuple[str, str], dict[str, Any]],
    texts: Mapping[str, str | None],
) -> list[dict[str, Any]]:
    """De rijen voor dup_pair: afstand, MAD64 (NO_MAD als het niet te meten was) en niveau."""
    rows = []
    for (a, b), dist in sorted(pairs.items()):
        m = measured.get((a, b))
        differs = phash.text_differs(texts.get(a), texts.get(b))
        level = phash.level(dist, phash.check_ok(m), differs)
        if level is None:
            continue
        mad = m.get("mad") if isinstance(m, dict) else None
        if not isinstance(mad, int | float) or isinstance(mad, bool) or mad < 0:
            mad = phash.NO_MAD
        rows.append({"p_a": a, "p_b": b, "p_dist": dist, "p_mad": float(mad), "p_level": level})
    return rows


def parse_pairs(answer: Any) -> dict[tuple[str, str], dict[str, Any]]:
    """De metingen van de tweede controle uit het renderproces, per (a, b) met a < b."""
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for item in answer if isinstance(answer, list) else []:
        if not isinstance(item, dict) or item.get("error"):
            continue
        a, b = str(item.get("a", "")), str(item.get("b", ""))
        out[(min(a, b), max(a, b))] = item
    return out


def _hash_row(h: Hashed) -> dict[str, Any]:
    if h.state == "ok" and h.value is not None:
        p0, p1, p2, p3 = phash.pieces(h.value)
        value: int | None = phash.signed(h.value)
    else:
        value = p0 = p1 = p2 = p3 = None
    return {"blob_sha": h.sha, "state": h.state, "phash": value, "h0": p0, "h1": p1, "h2": p2,
            "h3": p3, "version": VERSION}  # fmt: skip


async def write(db: AsyncSession, hashed: Sequence[Hashed], rows: Sequence[dict[str, Any]]) -> None:
    """blob_hash voor deze inhoud, en de paren (rows van levels()): eerst de oude paren van wat
    opnieuw gehasht is weg, dan de nieuwe, elk alleen als beide blobs er nog zijn."""
    if hashed:
        cols = ("state", "phash", "h0", "h1", "h2", "h3", "version")
        excluded = sqlite_insert(BlobHash.__table__).excluded
        await db.execute(
            sqlite_insert(BlobHash.__table__).on_conflict_do_update(
                index_elements=[_HASH.c.blob_sha], set_={c: excluded[c] for c in cols}
            ),
            [_hash_row(h) for h in hashed],
        )
        shas = [h.sha for h in hashed]
        for i in range(0, len(shas), 400):
            part = shas[i : i + 400]
            await db.execute(delete(_PAIR).where(or_(_PAIR.c.a.in_(part), _PAIR.c.b.in_(part))))
    if not rows:
        return
    a, b = bindparam("p_a", type_=String), bindparam("p_b", type_=String)
    sel = select(
        a,
        b,
        bindparam("p_dist", type_=Integer),
        bindparam("p_mad", type_=Float),
        bindparam("p_level", type_=String),
    ).where(exists().where(_BLOB.c.sha256 == a), exists().where(_BLOB.c.sha256 == b))
    excluded = sqlite_insert(DupPair.__table__).excluded
    await db.execute(
        sqlite_insert(DupPair.__table__)
        .from_select(["a", "b", "dist", "mad", "level"], sel)
        .on_conflict_do_update(
            index_elements=[_PAIR.c.a, _PAIR.c.b],
            set_={c: excluded[c] for c in ("dist", "mad", "level")},
        ),
        list(rows),
    )


def not_hashed() -> Any:
    """Een blob zonder blob_hash van deze VERSION (voor de todo van photometa)."""
    return ~exists().where(BlobHash.blob_sha == Blob.sha256, BlobHash.version >= VERSION)


async def mark_none(db: AsyncSession, shas: Sequence[str]) -> None:
    """Na de laatste mislukte poging van photometa: deze afbeeldingen krijgen state none (geen
    hash, niet opnieuw proberen); wat al een hash van deze VERSION had, houdt die."""
    if not shas:
        return
    await db.execute(
        sqlite_insert(BlobHash.__table__)
        .from_select(
            ["blob_sha", "state", "version"],
            select(_BLOB.c.sha256, literal("none"), literal(VERSION))
            .where(_BLOB.c.sha256.in_(list(shas)), _BLOB.c.mime.like("image/%"))
            .where(~exists().where(_HASH.c.blob_sha == _BLOB.c.sha256, _HASH.c.version >= VERSION)),
        )
        .on_conflict_do_update(
            index_elements=[_HASH.c.blob_sha],
            set_={
                "state": "none",
                "phash": None,
                "h0": None,
                "h1": None,
                "h2": None,
                "h3": None,
                "version": VERSION,
            },  # fmt: skip
        )
    )
