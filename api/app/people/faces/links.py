"""Eén regel tussen gezichten en de personen van Z6 (fase 6, Keuze 63; X2b).

- **sync_links.** Een photo_person met source = 'auto' bestaat precies zolang er op die node
  minstens één gezicht met ja voor die persoon is. Is er een ja en nog geen rij, dan komt er een
  met het kader van dat gezicht; is er een rij zonder kader, dan krijgt die het kader. Een rij met
  source = 'hand' raakt X2 nooit aan (ook niet om hem weg te halen).
- **Een kader met de hand** (Z6, put_on met een kader): een gezicht met IoU ≥ 0,5 tegen dat kader
  wordt meteen ja. Z6 heeft daar geen haak voor; daarom kijkt een luisteraar van de ORM (na elke
  flush) naar nieuwe of veranderde rijen van photo_person met source 'hand' en een kader, en doet
  het in dezelfde transactie (in de greenlet van SQLAlchemy, dus synchroon). Alleen als gezichten
  draaien (state.running): met ANALYSE=uit doet hij niets.
- **Een persoon zonder kader** (Z6): staat er op een foto precies één persoon zonder kader en vond
  YuNet precies één gezicht, dan wordt dat gezicht een voorstel met suggest_why = 'tag' (geen ja).

Alleen eigen, levende foto's (own_live).
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import delete, event, func, insert, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session as OrmSession

from app import face_rows
from app.models import Face, FacePerson, Node, PhotoPerson
from app.people.faces import drempels, state
from app.search.live import own_live

Box = tuple[float, float, float, float]


def iou(a: Box, b: Box) -> float:
    """De overlap van twee kaders (x, y, w, h; 0–1): doorsnede gedeeld door vereniging."""
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    w = min(ax + aw, bx + bw) - max(ax, bx)
    h = min(ay + ah, by + bh) - max(ay, by)
    if w <= 0 or h <= 0:
        return 0.0
    inter = w * h
    union = aw * ah + bw * bh - inter
    return inter / union if union > 0 else 0.0


async def sync_links(
    db: AsyncSession, owner: uuid.UUID, node: uuid.UUID, person: uuid.UUID
) -> None:
    """Houd de rij photo_person (person, node) met source 'auto' gelijk aan de ja's op die node
    (nog niet gecommit)."""
    box = (
        await db.execute(
            select(Face.x, Face.y, Face.w, Face.h)
            .join(FacePerson, FacePerson.face_id == Face.id)
            .join(Node, Node.id == Face.node_id)
            .where(
                Face.node_id == node,
                FacePerson.person_id == person,
                FacePerson.verdict == "ja",
                own_live(Node, owner),
            )
            .order_by(Face.created_at, Face.id)
            .limit(1)
        )
    ).first()
    row = (
        await db.execute(
            select(PhotoPerson.source, PhotoPerson.x)
            .join(Node, Node.id == PhotoPerson.node_id)
            .where(
                PhotoPerson.node_id == node,
                PhotoPerson.person_id == person,
                own_live(Node, owner),
            )
        )
    ).first()
    if box is not None:
        if row is None:
            await db.execute(
                insert(PhotoPerson).values(
                    person_id=person,
                    node_id=node,
                    owner_id=owner,
                    x=box.x,
                    y=box.y,
                    w=box.w,
                    h=box.h,
                    source="auto",
                    created_at=state.now(),
                )
            )
        elif row.x is None:
            await db.execute(
                update(PhotoPerson)
                .where(PhotoPerson.node_id == node, PhotoPerson.person_id == person)
                .values(x=box.x, y=box.y, w=box.w, h=box.h)
            )
    elif row is not None and row.source == "auto":
        await db.execute(
            delete(PhotoPerson).where(
                PhotoPerson.node_id == node,
                PhotoPerson.person_id == person,
                PhotoPerson.source == "auto",
            )
        )


# --- een kader met de hand, en de tag ------------------------------------------------------------


def _boxes_to_ja(conn: Any, owner: uuid.UUID, node: uuid.UUID) -> None:
    """Synchroon (in de flush): elk kader van photo_person op deze node maakt het gezicht met de
    grootste IoU ≥ 0,5 ja, als dat gezicht nog geen ja heeft."""
    live = select(Node.id).where(Node.id == node, own_live(Node, owner))
    if conn.execute(live).first() is None:
        return
    faces = list(
        conn.execute(
            select(Face.id, Face.x, Face.y, Face.w, Face.h).where(
                Face.node_id == node,
                face_rows.no_index(Face.owner_id) == owner,
                Face.hidden == 0,
            )
        )
    )
    if not faces:
        return
    taken = set(
        conn.execute(
            select(FacePerson.face_id).where(
                FacePerson.face_id.in_([f.id for f in faces]), FacePerson.verdict == "ja"
            )
        ).scalars()
    )
    boxes = conn.execute(
        select(
            PhotoPerson.person_id, PhotoPerson.x, PhotoPerson.y, PhotoPerson.w, PhotoPerson.h
        ).where(PhotoPerson.node_id == node, PhotoPerson.x.is_not(None))
    )
    for person, x, y, w, h in boxes:
        has = conn.execute(
            select(FacePerson.face_id)
            .join(Face, Face.id == FacePerson.face_id)
            .where(
                Face.node_id == node,
                FacePerson.person_id == person,
                FacePerson.verdict == "ja",
            )
        ).first()
        if has is not None:
            continue
        best, best_iou = None, drempels.IOU
        for f in faces:
            if f.id in taken:
                continue
            found = iou((x, y, w, h), (f.x, f.y, f.w, f.h))
            if found >= best_iou:
                best, best_iou = f.id, found
        if best is None:
            continue
        taken.add(best)
        conn.execute(
            delete(FacePerson).where(FacePerson.face_id == best, FacePerson.person_id == person)
        )
        conn.execute(
            insert(FacePerson).values(
                face_id=best, person_id=person, owner_id=owner, verdict="ja", created_at=state.now()
            )
        )
        # Een ja heeft geen voorstel en geen groep (people/faces/read.py telt open uit de indexen)
        conn.execute(
            update(Face)
            .where(Face.id == best)
            .values(suggest_id=None, suggest_score=None, suggest_why=None, cluster=None)
        )


def _tag(conn: Any, owner: uuid.UUID, node: uuid.UUID) -> None:
    """Synchroon: precies één persoon zonder kader en precies één gezicht (zonder ja): dat gezicht
    krijgt die persoon als voorstel met why 'tag'."""
    live = select(Node.id).where(Node.id == node, own_live(Node, owner))
    if conn.execute(live).first() is None:
        return
    loose = list(
        conn.execute(
            select(PhotoPerson.person_id).where(
                PhotoPerson.node_id == node, PhotoPerson.x.is_(None)
            )
        ).scalars()
    )
    faces = list(
        conn.execute(
            select(Face.id).where(
                Face.node_id == node,
                face_rows.no_index(Face.owner_id) == owner,
                Face.hidden == 0,
            )
        ).scalars()
    )
    if len(loose) != 1 or len(faces) != 1:
        return
    verdicts = conn.execute(
        select(func.count()).select_from(FacePerson).where(FacePerson.face_id == faces[0])
    ).scalar()
    if verdicts:
        return
    conn.execute(
        update(Face)
        .where(
            Face.id == faces[0],
            or_(Face.suggest_id.is_(None), Face.suggest_why == "tag"),
        )
        .values(suggest_id=loose[0], suggest_score=None, suggest_why="tag")
    )


def reconcile(conn: Any, owner: uuid.UUID, node: uuid.UUID) -> None:
    """Kaders naar ja, dan de tag; synchroon op een Connection of een Session (in de greenlet)."""
    _boxes_to_ja(conn, owner, node)
    _tag(conn, owner, node)


_SEEN = "platform.faces.links"


@event.listens_for(OrmSession, "after_flush")
def _after_flush(session: OrmSession, _ctx: Any) -> None:
    """Nieuwe of veranderde photo_person met de hand (Z6) op een foto: meteen bijwerken, in
    dezelfde transactie. Alleen met gezichten aan op dit platform. Welke rijen dat zijn, leest
    face_rows.hand_links uit de sessie (geen vraag aan de database); reconcile kijkt met own_live
    of de foto eigen en levend is."""
    touched = face_rows.hand_links(session)
    if not touched or session.info.get(_SEEN):
        return
    settings = state.settings_for(session)
    if not state.running(settings):
        return
    session.info[_SEEN] = True
    try:
        conn = session.connection()
        for owner, node in touched:
            reconcile(conn, owner, node)
    finally:
        session.info.pop(_SEEN, None)
