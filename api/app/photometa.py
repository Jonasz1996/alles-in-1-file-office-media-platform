"""De metadata van foto's en video's (alsnog) lezen: een achtergrondtaak die in stukken van 200
blobs door alles loopt wat nog gelezen moet worden.

Eén keten (job-type "photometa", key "photometa:backfill") in twee fasen; elke run pakt het
volgende stuk (op sha, na payload["after"]), leest het in één renderproces (previews.render met
mode "meta": alleen de headers) en zet dan zijn opvolger klaar met jobs.chain. Minder dan 200
gevonden: de fase is klaar.

- Fase "blobs": een afbeelding of video met een oude Blob.meta_version, of zonder blob_meta.
  Per blob de opnamedatum en EXIF (photodate.set_blob_meta en retime_nodes, alleen bij een oude
  meta_version: een nieuwe upload heeft die al van de preview-taak), en sinds VERSION 2 (fase 6,
  Z3) blob_meta (camera, objectief, gps 1/0/NULL; Keuze 25 en 26) en de plek in photo_geo voor
  elk bestand met die inhoud, per node van de eigenaar, alleen als die eigenaar locatie niet op
  "uit" heeft (gecontroleerd in dezelfde statement; Keuze 25a en 28). Een plek die er al stond,
  krijgt de nieuwe waarden; geen plek meer: de rijen weg.
- Fase "nodes": een bestand zonder rij in photo_geo terwijl zijn inhoud een plek heeft
  (blob_meta.gps = 1) en zijn eigenaar locatie niet op "uit" heeft: een kopie door een ander,
  een andere eigenaar, een eigenaar die weer "mij" koos. De plek staat nergens per blob (alleen
  óf er een is), dus de inhoud wordt opnieuw gelezen; alleen ontbrekende rijen komen erbij.

Aan het eind van de keten: kwam er intussen een afbeelding bij die nog gelezen moet worden (een
upload terwijl de keten liep, achter de plek waar hij was), dan begint hij opnieuw. Alleen
afbeeldingen: die zijn na één keer lezen altijd klaar, dus dat houdt op; een video kan "later"
blijven (zie onder).

Wat de keten klaarzet: enqueue_backfill bij het starten (als er iets te doen is), de haak na
preview.ready (een nieuwe upload, na DELAY, zodat een import één keten geeft), een kopie
(search.hooks.photo_copy) en locatie weer "mij" (app/geo/wire.py). Er loopt er nooit meer dan
één: enqueue ontdubbelt tegen een keten die klaarstaat of loopt.

Nieuwe uploads lezen hun datum in de preview-taak (render.py roept render_meta aan) en die zet
meta_version al; de plek leest alleen deze keten (nooit voor de preview, P6). Het schrijven
gebeurt onder BlobStore.lock, net als het neerzetten van een preview (previews._store): zo
overschrijft de een nooit het preview_meta dat de ander net schreef. De plaats (places.sqlite)
zoeken we vóór het schrijven op, in een thread.

Sinds fase 6 (Z3, Keuzes 35 en 36) rekent fase blobs ook de pHash van elke afbeelding zonder
blob_hash van photo_hash.VERSION, in hetzelfde renderproces (uit het voorbeeld van 256 px, of
het origineel met draft()), zoekt de kandidaten, doet de tweede controle van die paren in een
tweede renderproces (in stukken van PAIRS) en schrijft blob_hash en dup_pair in dezelfde
transactie als de rest (app/photo_hash.py).

Mislukt een run definitief (het proces crasht steeds op hetzelfde bestand), dan zet on_dead
meta_version voor dat stuk toch op VERSION, blob_meta.gps op NULL (onbekend), blob_hash op
"none" (geen hash), en gaat de keten verder: geen eindeloze lus.

Met media aan (fase 5) leest de mediaworker de video's. Is die weg, druk of zonder ffmpeg, dan
geeft render_meta {"error", "later"} en blijft de blob over (integratie, punt 3): de
housekeeping van media (runtime.PHOTOMETA, eens per 5 minuten en alleen met een gezonde
worker) roept enqueue_backfill dan weer aan. Media uit: zoals 0.6.0.
"""

import shutil
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import anyio
import anyio.to_thread
from sqlalchemy import and_, exists, or_, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import jobs, photo_hash, photodate, previews, render_meta
from app.config import Settings
from app.db import get_engine
from app.models import Blob, BlobMeta, Node, PhotoGeo, PhotoPref
from app.storage import BlobStore

TYPE = "photometa"
KEY = "photometa:backfill"
BATCH = 200
BLOBS, NODES = "blobs", "nodes"
# Na preview.ready: zo lang wachten, zodat een import van honderden foto's één keten geeft
DELAY = timedelta(seconds=5)
MEDIA = or_(Blob.mime.like("image/%"), Blob.mime.like("video/%"))
IMAGE = Blob.mime.like("image/%")
# Paren per renderproces voor de tweede controle (het antwoord blijft ruim onder OUTPUT_BYTES)
PAIRS = 1000


def _maker(settings: Settings) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(get_engine(settings), expire_on_commit=False)


def _no_meta():
    return ~exists().where(BlobMeta.blob_sha == Blob.sha256)


def _todo():
    """Blobs die (opnieuw) gelezen moeten worden: een oude meta_version, nog geen blob_meta (een
    nieuwe upload: de preview-taak zet meta_version al, maar leest geen plek), of een afbeelding
    zonder blob_hash van photo_hash.VERSION (Keuze 35)."""
    return (
        or_(
            Blob.meta_version < render_meta.VERSION,
            _no_meta(),
            and_(IMAGE, photo_hash.not_hashed()),
        ),
        MEDIA,
    )


def _missing():
    """Inhoud met een plek (blob_meta.gps = 1) waarvan een bestand nog geen rij in photo_geo heeft,
    terwijl zijn eigenaar locatie niet op "uit" heeft."""
    return exists().where(
        Node.blob_id == Blob.sha256,
        Node.kind == "file",
        Node.owner_id.is_not(None),
        ~exists().where(PhotoGeo.node_id == Node.id),
        ~exists().where(PhotoPref.user_id == Node.owner_id, PhotoPref.location == "uit"),
    )


def _nodes_todo():
    return (
        exists().where(BlobMeta.blob_sha == Blob.sha256, BlobMeta.gps == 1),
        MEDIA,
        _missing(),
    )


async def _batch(
    session: AsyncSession, after: str, fase: str = BLOBS
) -> list[tuple[str, str | None]]:
    where = _todo() if fase == BLOBS else _nodes_todo()
    rows = await session.execute(
        select(Blob.sha256, Blob.mime)
        .where(Blob.sha256 > after, *where)
        .order_by(Blob.sha256)
        .limit(BATCH)
    )
    return [(sha, mime) for sha, mime in rows]


async def _render(settings: Settings, request: dict[str, Any]) -> dict[str, Any]:
    """Eén renderproces in mode meta, met een eigen werkmap."""
    blobs = BlobStore(settings.blob_dir)
    tmp = previews.root(blobs) / ".tmp"
    await anyio.Path(tmp).mkdir(parents=True, exist_ok=True)
    work = Path(await anyio.to_thread.run_sync(lambda: tempfile.mkdtemp(dir=tmp)))
    try:
        request = {
            "mode": "meta",
            "max_pixels": settings.preview_max_pixels,
            "memory_mb": settings.preview_memory_mb,
            **request,
        }
        return await previews.render(settings, request, work)
    finally:
        with anyio.CancelScope(shield=True):
            await anyio.to_thread.run_sync(shutil.rmtree, work, True)


async def _read(settings: Settings, items: list[dict[str, Any]]) -> dict[str, Any]:
    """Metadata van deze bestanden uit één renderproces."""
    if not items:
        return {}
    result = await _render(settings, {"items": items})
    found = result.get("results")
    if not isinstance(found, dict):
        raise jobs.JobError("Het metadataproces gaf een onverwacht antwoord")
    return found


def _thumb(blobs: BlobStore, sha: str) -> str:
    return str(previews.directory(blobs, sha) / f"{previews.THUMB}.webp")


async def _need_hash(session: AsyncSession, batch: list[tuple[str, str | None]]) -> set[str]:
    """De afbeeldingen in dit stuk zonder blob_hash van photo_hash.VERSION."""
    rows = await session.scalars(
        select(Blob.sha256).where(
            Blob.sha256.in_([sha for sha, _mime in batch]), IMAGE, photo_hash.not_hashed()
        )
    )
    return set(rows)


async def _pairs(settings: Settings, hashed: list[photo_hash.Hashed]) -> list[dict[str, Any]]:
    """De paren van deze nieuwe hashes met hun niveau (Keuze 35): kandidaten zoeken, dan de
    tweede controle in het renderproces, en de tekst erbij."""
    blobs = BlobStore(settings.blob_dir)
    async with _maker(settings)() as session:
        pairs = await photo_hash.candidates(session, hashed)
        if not pairs:
            return []
        shas = {sha for pair in pairs for sha in pair}
        alive = await photo_hash.looks(session, shas)
        texts = await photo_hash.text_keys(session, shas)
    measured: dict[tuple[str, str], dict[str, Any]] = {}
    todo = [pair for pair in sorted(pairs) if pair[0] in alive and pair[1] in alive]
    for i in range(0, len(todo), PAIRS):
        part = todo[i : i + PAIRS]
        looks = {
            sha: {"thumb": _thumb(blobs, sha), "source": str(blobs.path(sha))}
            for pair in part
            for sha in pair
        }
        answer = await _render(settings, {"items": [], "pairs": part, "looks": looks})
        measured.update(photo_hash.parse_pairs(answer.get("pairs")))
    return photo_hash.levels(pairs, measured, texts)


def _gps(meta: Any) -> int | None:
    """blob_meta.gps uit het antwoord van render_meta: 1 met een bruikbare plek, 0 als er gelezen
    is en er geen is, None als het onbekend is (een video zonder de sleutel location)."""
    from app import photo_rows as rows

    if not isinstance(meta, dict) or "location" not in meta:
        return None
    return 1 if rows.valid(meta["location"]) is not None else 0


def _meta_row(sha: str, meta: Any) -> dict[str, Any]:
    """De rij van blob_meta voor dit antwoord (fout of weg: alles onbekend)."""
    if not isinstance(meta, dict) or "error" in meta:
        return {"blob_sha": sha, "camera": None, "lens": None, "gps": None}
    exif = meta.get("exif") if isinstance(meta.get("exif"), dict) else None
    camera, lens = render_meta.camera(exif)
    return {"blob_sha": sha, "camera": camera, "lens": lens, "gps": _gps(meta)}


async def _places(settings: Settings, found: dict[str, Any]) -> list[tuple[str, Any, Any]]:
    """(sha, plek, plaats) voor elke inhoud met een plek; de plaats uit places.sqlite, in een
    thread (zonder places.sqlite: geen plaats en geen land)."""
    from app import photo_rows as rows
    from app.geo import places

    spots = [(sha, spot) for sha, meta in found.items() if (spot := rows.valid(meta)) is not None]
    if not spots:
        return []

    def lookup() -> list[tuple[str, Any, Any]]:
        db = places.get(settings)
        out = []
        for sha, spot in spots:
            place = places.NIETS if db is None else db.lookup(spot.lat, spot.lon)
            out.append((sha, spot, place))
        return out

    return await anyio.to_thread.run_sync(lookup)


async def _read_batch(
    settings: Settings, batch: list[tuple[str, str | None]], need: set[str] | None = None
) -> tuple[set[str], dict[str, Any]]:
    """Lees dit stuk; need: de afbeeldingen die ook hun pHash krijgen."""
    blobs = BlobStore(settings.blob_dir)
    present = {
        sha: mime
        for sha, mime in batch
        if await anyio.Path(blobs.path(sha)).is_file()  # weg op schijf: niets te lezen
    }
    items = []
    for sha, mime in present.items():
        item: dict[str, Any] = {"sha": sha, "source": str(blobs.path(sha)), "mime": mime}
        if need and sha in need:
            item.update(hash=True, thumb=_thumb(blobs, sha))
        items.append(item)
    return set(present), await _read(settings, items)


async def _run_blobs(settings: Settings, after: str) -> str | None:
    """Fase blobs: één stuk. Geeft de sha om na verder te gaan, of None als de fase klaar is."""
    from app import photo_rows as rows

    maker = _maker(settings)
    async with maker() as session:
        batch = await _batch(session, after, BLOBS)
        need = await _need_hash(session, batch) if batch else set()
    if not batch:
        return None
    present, results = await _read_batch(settings, batch, need)
    usable = {
        sha: meta.get("location")
        for sha, meta in results.items()
        if sha in present and isinstance(meta, dict) and "error" not in meta
    }
    located = await _places(settings, usable)
    hashed = [
        photo_hash.parse(sha, (results.get(sha) or {}).get("hash") if sha in present else None)
        for sha in sorted(need)
    ]
    pair_rows = await _pairs(settings, hashed)
    blobs = BlobStore(settings.blob_dir)
    async with blobs.lock:
        async with maker() as session:
            # Eerst alles lezen en uitrekenen, dan schrijven in een paar statements. SQLite
            # heeft één schrijver: zolang deze transactie schrijft, wacht elk ander schrijvend
            # verzoek (inloggen, /auth/refresh bij het openen van de app) op het slot. Per blob
            # lezen en schrijven hield dat slot een halve seconde per stuk vast.
            found = {
                blob.sha256: blob
                for blob in await session.scalars(
                    select(Blob)
                    .where(Blob.sha256.in_([sha for sha, _mime in batch]))
                    .execution_options(populate_existing=True)
                )
            }
            taken: list[tuple[str, datetime]] = []
            metas: list[dict[str, Any]] = []
            done: set[str] = set()
            for sha, _mime in batch:
                blob = found.get(sha)
                if blob is None:
                    continue  # opgeruimd
                meta = results.get(sha)
                if isinstance(meta, dict) and meta.get("later"):
                    # Media aan en de worker weg, druk of zonder ffmpeg (integratie, punt 3):
                    # niet als gelezen markeren; enqueue_backfill pakt hem later weer op
                    continue
                readable = sha in present and isinstance(meta, dict) and "error" not in meta
                metas.append(_meta_row(sha, meta if readable else None))
                done.add(sha)
                if blob.meta_version >= render_meta.VERSION:
                    continue  # de preview-taak was al zo ver: alleen blob_meta en de plek
                if not readable:
                    # Niet te lezen: niet opnieuw proberen, en de datum die er is laten staan
                    blob.meta_version = render_meta.VERSION
                    continue
                meta = dict(meta)
                capture = photodate.split_capture(meta)
                exif = meta.get("exif") if isinstance(meta.get("exif"), dict) else None
                at = photodate.set_blob_meta(blob, capture, exif, settings.timezone)
                if at is not None:
                    taken.append((sha, at))
            await session.flush()
            await photodate.retime_nodes(session, taken)
            await rows.set_meta(session, metas)
            # De plek per node (Keuze 25a, 28); wat nu geen plek heeft, verliest zijn rijen
            with_spot = [item for item in located if item[0] in done]
            await rows.write(session, with_spot, replace=True)
            spotted = {sha for sha, _spot, _place in with_spot}
            gone = [m["blob_sha"] for m in metas if m["gps"] == 0]
            await rows.clear(session, [sha for sha in gone if sha not in spotted])
            # De pHash en de paren (Keuze 35); wat intussen opgeruimd is, niet
            await photo_hash.write(session, [h for h in hashed if h.sha in found], pair_rows)
            await session.commit()
    return batch[-1][0] if len(batch) == BATCH else None


async def _run_nodes(settings: Settings, after: str) -> str | None:
    """Fase nodes: één stuk. Alleen rijen die ontbreken; een inhoud zonder (bruikbare) plek of
    die niet te lezen is, krijgt gps 0 of NULL en komt dus niet terug."""
    from app import photo_rows as rows

    maker = _maker(settings)
    async with maker() as session:
        batch = await _batch(session, after, NODES)
    if not batch:
        return None
    present, results = await _read_batch(settings, batch)
    usable = {
        sha: meta.get("location")
        for sha, meta in results.items()
        if sha in present and isinstance(meta, dict) and "error" not in meta
    }
    located = await _places(settings, usable)
    async with BlobStore(settings.blob_dir).lock:
        async with maker() as session:
            metas = []
            for sha, _mime in batch:
                meta = results.get(sha)
                if isinstance(meta, dict) and meta.get("later"):
                    continue
                readable = sha in present and isinstance(meta, dict) and "error" not in meta
                metas.append(_meta_row(sha, meta if readable else None))
            await rows.set_meta(session, metas)
            await rows.write(session, located, replace=False)
            await session.commit()
    return batch[-1][0] if len(batch) == BATCH else None


async def _left(settings: Settings) -> bool:
    """Aan het eind van de keten: een afbeelding die er intussen bij kwam en nog gelezen moet
    worden (in een van beide fasen)?"""
    image = Blob.mime.like("image/%")
    async with _maker(settings)() as session:
        found = await session.scalar(select(Blob.sha256).where(image, *_todo()).limit(1))
        if found is None:
            found = await session.scalar(select(Blob.sha256).where(image, *_nodes_todo()).limit(1))
    return found is not None


async def _next(settings: Settings, payload: dict[str, Any]) -> None:
    async with _maker(settings)() as session:
        await jobs.chain(session, TYPE, payload, KEY)
        await session.commit()


async def run(settings: Settings, payload: dict[str, Any]) -> None:
    """Eén run van de keten: hooguit BATCH blobs na payload["after"], in payload["fase"]
    (zonder fase: blobs, zoals vóór VERSION 2). Is fase blobs klaar, dan begint fase nodes in
    dezelfde run."""
    after = str(payload.get("after") or "")
    fase = NODES if payload.get("fase") == NODES else BLOBS
    if fase == BLOBS:
        last = await _run_blobs(settings, after)
        if last is not None:
            await _next(settings, {"after": last, "fase": BLOBS})
            return
        after = ""
    last = await _run_nodes(settings, after if fase == NODES else "")
    if last is not None:
        await _next(settings, {"after": last, "fase": NODES})
        return
    if await _left(settings):
        await _next(settings, {"after": "", "fase": BLOBS})


async def mark_dead(settings: Settings, payload: dict[str, Any], _error: str) -> None:
    """Na de laatste mislukte poging: dat stuk als gelezen markeren (blob_meta.gps onbekend) en
    verder met het volgende, zodat één kapot bestand de keten niet eindeloos laat herhalen."""
    after = str(payload.get("after") or "")
    fase = NODES if payload.get("fase") == NODES else BLOBS
    async with _maker(settings)() as session:
        batch = await _batch(session, after, fase)
        if not batch:
            return
        shas = [sha for sha, _ in batch]
        unknown = [{"blob_sha": sha, "camera": None, "lens": None, "gps": None} for sha in shas]
        if fase == BLOBS:
            await session.execute(
                update(Blob)
                .where(Blob.sha256.in_(shas), Blob.meta_version < render_meta.VERSION)
                .values(meta_version=render_meta.VERSION)
                .execution_options(synchronize_session=False)
            )
            # Wat al een blob_meta had, houdt die
            await session.execute(sqlite_insert(BlobMeta).on_conflict_do_nothing(), unknown)
            await photo_hash.mark_none(session, shas)
        else:
            await session.execute(
                update(BlobMeta)
                .where(BlobMeta.blob_sha.in_(shas))
                .values(gps=None)
                .execution_options(synchronize_session=False)
            )
        if len(batch) == BATCH:
            await jobs.chain(session, TYPE, {"after": batch[-1][0], "fase": fase}, KEY)
        elif fase == BLOBS and await _batch(session, "", NODES):
            await jobs.chain(session, TYPE, {"after": "", "fase": NODES}, KEY)
        await session.commit()


async def enqueue_backfill(settings: Settings, fase: str = BLOBS) -> int:
    """Bij het starten: de backfill klaarzetten als er iets te doen is (in een van beide
    fasen). Geeft het aantal nieuwe taken (0 of 1); loopt of wacht er al een keten, dan geen
    tweede."""
    async with _maker(settings)() as session:
        todo = await session.scalar(select(Blob.sha256).where(*_todo()).limit(1))
        if todo is None:
            todo = await session.scalar(select(Blob.sha256).where(*_nodes_todo()).limit(1))
        if todo is None:
            return 0
        added = await jobs.enqueue(session, TYPE, {"after": "", "fase": fase}, key=KEY)
        await session.commit()
    return 1 if added else 0


async def kick(session: AsyncSession, fase: str = BLOBS, *, delay: timedelta | None = None) -> None:
    """In de transactie van de aanroeper: de keten klaarzetten (na delay), als er niet al een
    klaarstaat of loopt. Die kijkt zelf of er iets te doen is."""
    at = jobs.now() + delay if delay else None
    await jobs.enqueue(session, TYPE, {"after": "", "fase": fase}, key=KEY, run_after=at)


jobs.register(TYPE, run, on_dead=mark_dead)
