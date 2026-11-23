"""De rijen van gezichten schrijven die ook bij bestanden in de prullenbak horen (fase 6, X2b).

Zoals app/photo_rows.py: dit zijn schrijvers per eigenaar die bij "uit", "niet benoemde wissen",
een nieuwe versie of samenvoegen ook de rijen van bestanden in de prullenbak raken. own_live zou
daar juist rijen laten staan (een vector in de prullenbak is ook een vector). Daarom staan ze niet
onder app/people/, waar de wachter van tests/search/test_own_live.py elke functie die foto's leest
aan own_live houdt. Wat gezichten leest om te tonen of te groeperen, staat daar wel, met own_live.

Ook hier: de tellingen voor /beheer, voor de hele server en nooit per gebruiker (Keuze 67).

Nooit een vector, een sleutel of een eigenaar in het log: alleen aantallen.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import and_, bindparam, delete, exists, func, insert, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app import dbdialect
from app.models import Face, FaceOwner, FacePerson, FaceScan, PhotoPerson, PhotoPref, User

CHUNK = 2000  # rijen per transactie bij wissen (zoals Keuze 11)


def no_index(col: Any) -> Any:
    """De kolom met een unaire + ervoor: SQLite gebruikt voor die term geen index. Voor een vraag
    per foto (node_id) of per id, met ook de eigenaar erin: dan loopt ix_face_node of de sleutel.
    Met een gewone face.owner_id = :me kiest SQLite (ook 3.46.1, zonder sqlite_stat1) ix_face_new,
    de gedeeltelijke index op owner_id met hidden = 0, en leest dan alle gezichten van de eigenaar:
    bij 60.000 gezichten 50 ms per vraag, en in een batch per foto, met het schrijfslot. Net zo
    face_scan.state naast node_id IN (...) (anders ix_face_scan_todo: alles wat wacht).
    Alleen op SQLite: PostgreSQL kent geen unaire + op uuid of tekst en krijgt de kolom zelf
    (dbdialect.NoIndex; op SQLite dezelfde tekst als ervoor)."""
    return dbdialect.NoIndex(col)


async def has_rows(db: AsyncSession, owner: uuid.UUID) -> bool:
    """Heeft deze eigenaar nog iets van gezichten (een rij in face, face_scan of face_owner)?"""
    for model in (Face, FaceScan, FaceOwner):
        found = await db.scalar(select(model.owner_id).where(model.owner_id == owner).limit(1))
        if found is not None:
            return True
    return False


async def has_sealed(db: AsyncSession, owner: uuid.UUID) -> bool:
    """Staat er een versleutelde vector van deze eigenaar (face.vec niet NULL), ook van een foto
    in de prullenbak? Dan hoort daar een sleutel bij: crypto.ensure_key maakt dan geen nieuwe
    (0.8.2, P4). Een verborgen gezicht heeft geen vec (hidden = 1), dus ix_face_new."""
    found = await db.scalar(
        select(Face.id)
        .where(Face.owner_id == owner, Face.hidden == 0, Face.vec.is_not(None))
        .limit(1)
    )
    return found is not None


async def key_owners(db: AsyncSession) -> list[uuid.UUID]:
    """Wie een sleutel nodig heeft: gezichten aan (photo_pref.faces), geen wisronde
    (face_owner.wiping), en minstens één versleutelde vector. Bij "uit" en een verbod gaat de
    sleutel eerst weg en wist de wisronde de rijen daarna: die eigenaren horen er niet bij."""
    on = await db.scalars(select(PhotoPref.user_id).where(PhotoPref.faces == "aan"))
    wiping = set(await db.scalars(select(FaceOwner.owner_id).where(FaceOwner.wiping == 1)))
    return [o for o in sorted(on) if o not in wiping and await has_sealed(db, o)]


async def lock_pref(db: AsyncSession, owner: uuid.UUID) -> str | None:
    """De schakelaar gezichten van owner (photo_pref.faces, None zonder rij), met de rij vast tot
    de commit: op PostgreSQL het rijslot, op SQLite het schrijfslot (een UPDATE zonder
    verandering; photo_pref heeft geen triggers). Zo kan niemand gezichten aanzetten terwijl
    sleutels-check --opruimen de sleutel weghaalt."""
    found = await db.execute(
        update(PhotoPref)
        .where(PhotoPref.user_id == owner)
        .values(faces=PhotoPref.faces)
        .returning(PhotoPref.faces)
    )
    return found.scalar_one_or_none()


async def user_gone(db: AsyncSession, owner: uuid.UUID) -> bool:
    """Bestaat deze gebruiker niet (meer)? Dan kan niemand zijn gezichten nog aanzetten."""
    return await db.scalar(select(User.id).where(User.id == owner)) is None


async def owners_with_rows(db: AsyncSession) -> list[uuid.UUID]:
    """Eigenaren met rijen van gezichten (voor de ronde van elk uur: wie uit staat en nog rijen
    heeft, krijgt een wisronde)."""
    found: set[uuid.UUID] = set()
    for model in (Face, FaceScan, FaceOwner):
        found.update(await db.scalars(select(model.owner_id).distinct()))
    return sorted(found)


async def wipe_owner_chunk(db: AsyncSession, owner: uuid.UUID, n: int = CHUNK) -> int:
    """Eén stuk van scope all: eerst face_person, dan face, dan face_scan van deze eigenaar;
    hooguit n rijen. Geeft hoeveel er weg zijn (0: klaar, dan mag face_owner weg)."""
    ids = list(
        await db.scalars(select(FacePerson.face_id).where(FacePerson.owner_id == owner).limit(n))
    )
    if ids:
        await db.execute(
            delete(FacePerson).where(FacePerson.owner_id == owner, FacePerson.face_id.in_(ids))
        )
        return len(ids)
    ids = list(await db.scalars(select(Face.id).where(Face.owner_id == owner).limit(n)))
    if ids:
        await db.execute(delete(Face).where(Face.id.in_(ids)))
        return len(ids)
    ids = list(
        await db.scalars(select(FaceScan.node_id).where(FaceScan.owner_id == owner).limit(n))
    )
    if ids:
        await db.execute(delete(FaceScan).where(FaceScan.node_id.in_(ids)))
        return len(ids)
    return 0


async def drop_owner_row(db: AsyncSession, owner: uuid.UUID) -> None:
    await db.execute(delete(FaceOwner).where(FaceOwner.owner_id == owner))


async def hide_unnamed_chunk(db: AsyncSession, owner: uuid.UUID, n: int = CHUNK) -> int:
    """Eén stuk van scope unnamed: open gezichten (niet verborgen, zonder ja) worden verborgen,
    zonder vector, voorstel en groep; hun afwijzingen gaan weg. Geeft hoeveel."""
    ja = select(FacePerson.face_id).where(FacePerson.verdict == "ja")
    ids = list(
        await db.scalars(
            select(Face.id)
            .where(Face.owner_id == owner, Face.hidden == 0, Face.id.not_in(ja))
            .limit(n)
        )
    )
    if not ids:
        return 0
    await hide_rows(db, ids)
    return len(ids)


async def hide_rows(db: AsyncSession, ids: Sequence[uuid.UUID]) -> None:
    """Niet tonen: vec, voorstel en groep NULL, hidden 1, en de oordelen over die gezichten weg
    (ja en nee). De aanroeper houdt photo_person gelijk (links.sync_links)."""
    if not ids:
        return
    await db.execute(
        update(Face)
        .where(Face.id.in_(ids))
        .values(
            vec=None, hidden=1, suggest_id=None, suggest_score=None, suggest_why=None, cluster=None
        )
    )
    await db.execute(delete(FacePerson).where(FacePerson.face_id.in_(ids)))


async def reset_busy(db: AsyncSession, before: datetime | None = None) -> int:
    """busy terug naar wait: bij de start (een crash van de app laat busy achter) en in de ronde
    van elk uur (busy ouder dan before)."""
    where = [FaceScan.state == "busy"]
    if before is not None:
        where.append(FaceScan.updated_at < before)
    done = await db.execute(update(FaceScan).where(*where).values(state="wait"))
    return int(getattr(done, "rowcount", 0) or 0)


async def drop_version_chunk(
    db: AsyncSession, owner: uuid.UUID, version: int, at: datetime, n: int = CHUNK
) -> int:
    """Eén stuk van een nieuwe versie: de gezichten met hidden 0 van een oude versie weg (de
    verborgen blijven, zonder vector), en face_scan van een oude versie terug naar wait. Geeft
    hoeveel rijen er veranderden."""
    ids = list(
        await db.scalars(
            select(Face.id)
            .where(Face.owner_id == owner, Face.hidden == 0, Face.version != version)
            .limit(n)
        )
    )
    if ids:
        await db.execute(delete(Face).where(Face.id.in_(ids)))
        return len(ids)
    nodes = list(
        await db.scalars(
            select(FaceScan.node_id)
            .where(FaceScan.owner_id == owner, FaceScan.version != version)
            .limit(n)
        )
    )
    if nodes:
        await db.execute(
            update(FaceScan)
            .where(FaceScan.node_id.in_(nodes))
            .values(
                state="wait",
                version=version,
                tries=0,
                code=None,
                faces=0,
                updated_at=at,
                queued_at=at,
            )
        )
    return len(nodes)


async def move_verdicts(
    db: AsyncSession, owner: uuid.UUID, source: uuid.UUID, into: uuid.UUID
) -> list[uuid.UUID]:
    """Samenvoegen (Keuze 63), vóór de bron weg is: ja van de bron gaat naar het doel (een nee
    van het doel op dat gezicht vervalt); nee van de bron gaat mee, behalve waar het doel al een
    oordeel heeft; suggest_id gaat naar het doel. Geeft de nodes met een ja van de bron (voor
    sync_links)."""
    rows = list(
        await db.execute(
            select(FacePerson.face_id, FacePerson.verdict, Face.node_id)
            .join(Face, Face.id == FacePerson.face_id)
            .where(FacePerson.owner_id == owner, FacePerson.person_id == source)
        )
    )
    into_rows = dict(
        (r.face_id, r.verdict)
        for r in await db.execute(
            select(FacePerson.face_id, FacePerson.verdict).where(
                FacePerson.owner_id == owner, FacePerson.person_id == into
            )
        )
    )
    nodes: set[uuid.UUID] = set()
    for face_id, verdict, node_id in rows:
        if verdict == "ja":
            nodes.add(node_id)
            if into_rows.get(face_id) is not None:
                await db.execute(
                    delete(FacePerson).where(
                        FacePerson.face_id == face_id, FacePerson.person_id == into
                    )
                )
            await db.execute(
                update(FacePerson)
                .where(FacePerson.face_id == face_id, FacePerson.person_id == source)
                .values(person_id=into)
            )
        elif face_id not in into_rows:
            await db.execute(
                update(FacePerson)
                .where(FacePerson.face_id == face_id, FacePerson.person_id == source)
                .values(person_id=into)
            )
    await db.execute(
        update(Face)
        .where(Face.owner_id == owner, Face.suggest_id == source)
        .values(suggest_id=into)
    )
    return sorted(nodes)


async def server_counts(db: AsyncSession) -> dict[str, Any]:
    """De aantallen voor /beheer, voor de hele server (Keuze 67): gebruikers met gezichten aan,
    gezichten, waarvan benoemd, wachtend, mislukt per code, en of er een wisronde loopt."""
    users_on = await db.scalar(
        select(func.count()).select_from(PhotoPref).where(PhotoPref.faces == "aan")
    )
    faces = await db.scalar(select(func.count()).select_from(Face).where(Face.hidden == 0))
    named = await db.scalar(
        select(func.count()).select_from(FacePerson).where(FacePerson.verdict == "ja")
    )
    waiting = await db.scalar(
        select(func.count())
        .select_from(FaceScan)
        .where(or_(FaceScan.state == "wait", FaceScan.state == "busy"))
    )
    failed = dict(
        (code or "", n)
        for code, n in await db.execute(
            select(FaceScan.code, func.count())
            .where(FaceScan.state == "failed")
            .group_by(FaceScan.code)
        )
    )
    wiping = await db.scalar(
        select(func.count()).select_from(FaceOwner).where(FaceOwner.wiping == 1)
    )
    return {
        "users_on": int(users_on or 0),
        "faces": int(faces or 0),
        "named": int(named or 0),
        "waiting": int(waiting or 0),
        "failed_by_code": {k: int(v) for k, v in failed.items()},
        "wiping": int(wiping or 0),
    }


async def done_since(db: AsyncSession, since: datetime) -> int:
    found = await db.scalar(
        select(func.count())
        .select_from(FaceScan)
        .where(and_(FaceScan.state.in_(("done", "skip")), FaceScan.updated_at >= since))
    )
    return int(found or 0)


async def retry_failed(db: AsyncSession, at: datetime) -> int:
    """/beheer "Mislukte opnieuw" (RebuildWhat faces): failed terug naar wait, met 0 pogingen."""
    done = await db.execute(
        update(FaceScan)
        .where(FaceScan.state == "failed")
        .values(state="wait", tries=0, code=None, queued_at=at, updated_at=at)
    )
    return int(getattr(done, "rowcount", 0) or 0)


async def insert_faces(db: AsyncSession, rows: list[dict[str, Any]]) -> None:
    if rows:
        await db.execute(insert(Face), rows)


async def write_group(
    db: AsyncSession,
    owner: uuid.UUID,
    rows: list[dict[str, Any]],
    *,
    full: bool,
    first: bool,
    at: datetime,
) -> None:
    """Eén stuk (≤ CHUNK) van de uitkomst van group: voorstellen (why sim; een tag blijft staan
    als er geen voorstel is), en bij full de groep. De eerste statement schrijft (face_owner), dus
    de transactie heeft meteen het schrijfslot. Bij full en het eerste stuk: run + 1 en alle
    oude groepen van deze eigenaar weg."""
    dialect = dbdialect.of(db)
    await db.execute(
        dialect.insert(FaceOwner)
        .values(owner_id=owner, run=0, wiping=0)
        .on_conflict_do_nothing(index_elements=["owner_id"])
    )
    if first:
        values: dict[str, Any] = {"suggested_at": at}
        if full:
            values.update(run=FaceOwner.run + 1, grouped_at=at)
            await db.execute(
                update(Face)
                .where(Face.owner_id == owner, Face.cluster.is_not(None))
                .values(cluster=None)
            )
        await db.execute(update(FaceOwner).where(FaceOwner.owner_id == owner).values(**values))
    # Core op de tabel (executemany met bindparams): een ORM-update met een lijst van rijen is
    # "bulk UPDATE by primary key" en wil de id als kolomnaam
    t = Face.__table__
    fp = FacePerson.__table__
    where = [t.c.id == bindparam("b_id"), t.c.owner_id == owner, t.c.hidden == 0]
    # Een gezicht met een ja krijgt geen voorstel en geen groep, ook niet als de ja kwam nadat
    # group las: ja is nooit open (people/faces/read.py telt open uit de indexen)
    no_ja = ~exists().where(fp.c.face_id == t.c.id, fp.c.verdict == "ja")
    sim = [r for r in rows if r["suggest_id"] is not None]
    none = [{"b_id": r["b_id"]} for r in rows if r["suggest_id"] is None]
    if sim:
        await db.execute(
            update(t)
            .where(*where, no_ja)
            .values(
                suggest_id=bindparam("b_suggest"),
                suggest_score=bindparam("b_score"),
                suggest_why="sim",
            ),
            [
                {"b_id": r["b_id"], "b_suggest": r["suggest_id"], "b_score": r["suggest_score"]}
                for r in sim
            ],
        )
    if none:
        await db.execute(
            update(t)
            .where(*where, or_(t.c.suggest_why.is_(None), t.c.suggest_why == "sim"))
            .values(suggest_id=None, suggest_score=None, suggest_why=None),
            none,
        )
    if full:
        await db.execute(
            update(t).where(*where, no_ja).values(cluster=bindparam("b_cluster")),
            [{"b_id": r["b_id"], "b_cluster": r["cluster"]} for r in rows],
        )


async def old_version(db: AsyncSession, version: int) -> bool:
    """Is er een face_scan van een andere versie (dan loopt faces.plan met scope version)?"""
    found = await db.scalar(select(FaceScan.node_id).where(FaceScan.version != version).limit(1))
    return found is not None


def open_faces() -> Any:
    """De voorwaarde "open": een gezicht zonder ja (voor een where op Face)."""
    return Face.id.not_in(select(FacePerson.face_id).where(FacePerson.verdict == "ja"))


async def has_waiting(db: AsyncSession, owner: uuid.UUID | None = None) -> bool:
    """Wacht er een foto op een scan (wait, zonder code), voor iedereen of voor één eigenaar?"""
    q = select(FaceScan.node_id).where(FaceScan.state == "wait", FaceScan.code.is_(None))
    if owner is not None:
        q = q.where(FaceScan.owner_id == owner)
    return (await db.scalar(q.limit(1))) is not None


async def waiting_count(db: AsyncSession) -> int:
    found = await db.scalar(
        select(func.count()).select_from(FaceScan).where(FaceScan.state.in_(("wait", "busy")))
    )
    return int(found or 0)


async def retry_no_preview(db: AsyncSession) -> None:
    """De sweep: no_preview (de 1024 was er nog niet) opnieuw proberen."""
    await db.execute(update(FaceScan).where(FaceScan.code == "no_preview").values(code=None))


async def set_scan(
    db: AsyncSession, node: uuid.UUID, values: dict[str, Any], *, only_busy: bool = False
) -> None:
    """De stand van één foto in face_scan (na de scan, of terug na wijken of een timeout)."""
    where = [FaceScan.node_id == node]
    if only_busy:
        where.append(FaceScan.state == "busy")
    await db.execute(update(FaceScan).where(*where).values(**values))


async def new_since_full(db: AsyncSession) -> list[uuid.UUID]:
    """De eigenaren (zonder wisronde) met een gezicht van na hun laatste full, of nog zonder full:
    voor de ronde van elk uur (group.full_due). Per eigenaar één vraag langs ix_face_new."""
    rows = select(FaceOwner.owner_id, FaceOwner.grouped_at).where(FaceOwner.wiping == 0)
    found = []
    for owner, last in (await db.execute(rows)).all():
        faces = select(Face.id).where(Face.owner_id == owner, Face.hidden == 0)
        if last is not None:
            faces = faces.where(Face.created_at > last)
        if await db.scalar(faces.limit(1)) is not None:
            found.append(owner)
    return found


async def queue_quiet(db: AsyncSession, owner: uuid.UUID) -> bool:
    """Wacht er voor deze eigenaar niets meer (geen wait of busy)? Dan mag een volle groepering."""
    found = await db.scalar(
        select(FaceScan.node_id)
        .where(FaceScan.owner_id == owner, FaceScan.state.in_(("wait", "busy")))
        .limit(1)
    )
    return found is None


def hand_links(session: Any) -> list[tuple[uuid.UUID, uuid.UUID]]:
    """(eigenaar, node) van de photo_person met source 'hand' die deze flush nieuw of veranderd
    zijn: uit de sessie zelf, zonder vraag aan de database (links._after_flush)."""
    found = {
        (obj.owner_id, obj.node_id)
        for obj in (*session.new, *session.dirty)
        if isinstance(obj, PhotoPerson) and obj.source == "hand"
    }
    return sorted(found)
