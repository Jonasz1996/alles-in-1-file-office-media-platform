"""Delen: met een gebruiker of groep (lezen of schrijven), en publieke links (bekijken of alleen
uploaden, met wachtwoord en vervaldatum). Een album (kind "album") deel je op dezelfde manier,
maar alleen om te bekijken: schrijven geeft 422 album_read_only, een uploadlink 422
upload_folder_only. Alleen de eigenaar deelt; wie wat mag, rekent
app/access.py bij elk verzoek uit. Publieke links openen gaat via app/routers/public.py.

Gebeurtenissen (na de commit): share.created, share.changed en share.deleted met
{share_id, node_id} naar wie de deling krijgt, kreeg of had (bij een groep: alle leden).
Voor links niet: daar is niemand om het te vertellen.
"""

import secrets
import uuid
from collections import defaultdict
from datetime import UTC, datetime
from typing import Annotated, Literal

import anyio.to_thread
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.exc import StaleDataError

from app import access, errors, events
from app.db import Session
from app.models import Group, GroupMember, Node, Share, User
from app.routers import uploads
from app.routers.files import GUEST, NodeOut, OwnerRef, Utc, describe, get_node, is_trashed, to_utc
from app.security import CurrentUser, RateLimiter, new_password_hash
from app.text import fold

router = APIRouter(prefix="/api/v1", tags=["delen"])
MIN_LINK_PASSWORD = 6
MAX_LABEL = 100
# Tegen het afschrapen van alle namen en e-mailadressen; gewoon zoeken haalt dit nooit
directory_limiter = RateLimiter(
    60,
    60,
    {"message": "Even rustig aan met zoeken; probeer het zo opnieuw", "code": "rate_limited"},
)


class ShareUser(BaseModel):
    id: uuid.UUID
    display_name: str
    email: str


class ShareGroup(BaseModel):
    id: uuid.UUID
    name: str
    member_count: int


class ShareLink(BaseModel):
    url: str
    label: str | None
    has_password: bool


class ShareOut(BaseModel):
    id: uuid.UUID
    node_id: uuid.UUID
    type: Literal["user", "group", "link"]
    # user en group: read of write; link: read of upload
    permission: str
    created_at: Utc
    expires_at: Utc | None
    user: ShareUser | None = None
    group: ShareGroup | None = None
    link: ShareLink | None = None


class NewShare(BaseModel):
    user_id: uuid.UUID | None = None
    group_id: uuid.UUID | None = None
    permission: Literal["read", "write"]
    expires_at: datetime | None = None

    @model_validator(mode="after")
    def one_target(self) -> "NewShare":
        if (self.user_id is None) == (self.group_id is None):
            raise ValueError("Kies precies één persoon of groep")
        return self


class NewLink(BaseModel):
    permission: Literal["read", "upload"]
    password: str | None = Field(default=None, max_length=200)
    expires_at: datetime | None = None
    label: str | None = Field(default=None, max_length=200)


class ShareChange(BaseModel):
    permission: Literal["read", "write", "upload"] | None = None
    # null haalt de vervaldatum weg; weglaten laat hem staan
    expires_at: datetime | None = None
    # Alleen links: "" haalt het wachtwoord weg
    password: str | None = Field(default=None, max_length=200)
    # Alleen links: "" haalt het label weg
    label: str | None = Field(default=None, max_length=200)


class SharedItem(BaseModel):
    share_id: uuid.UUID
    node: NodeOut
    permission: str
    via: Literal["user", "group"]
    group: ShareGroup | None = None
    owner: OwnerRef
    created_at: Utc


class DirectoryUser(BaseModel):
    id: uuid.UUID
    display_name: str
    email: str


class Directory(BaseModel):
    users: list[DirectoryUser]
    groups: list[ShareGroup]


def error(status: int, message: str, code: str) -> HTTPException:
    """Oude vorm (groups.py gebruikte hem); nieuwe code roept errors.fail zelf aan."""
    return errors.fail(status, code, message)


def no_guests(user: User) -> None:
    if user.role == "guest":
        raise errors.fail(403, **GUEST)


def future(value: datetime | None) -> datetime | None:
    """Een vervaldatum in UTC, en niet in het verleden."""
    if value is None:
        return None
    value = to_utc(value)
    if value <= datetime.now(UTC):
        raise errors.fail(422, "expires_past", "De vervaldatum ligt in het verleden")
    return value


def clean_label(value: str | None) -> str | None:
    value = (value or "").strip()
    if len(value) > MAX_LABEL:
        raise errors.fail(
            422, "label_too_long", f"Een label mag hooguit {MAX_LABEL} tekens hebben", max=MAX_LABEL
        )
    return value or None


async def password_hash_of(password: str | None) -> str | None:
    """None of "" = geen wachtwoord. argon2, zoals gebruikerswachtwoorden (in een thread)."""
    if not password:
        return None
    if len(password) < MIN_LINK_PASSWORD:
        raise errors.fail(
            422,
            "password_too_short",
            f"Een wachtwoord voor een link heeft minstens {MIN_LINK_PASSWORD} tekens",
            min=MIN_LINK_PASSWORD,
        )
    return await new_password_hash(password)


ALBUM_READ_ONLY = "Een album kun je alleen delen om te bekijken"


async def shareable(session: AsyncSession, user: User, node_id: uuid.UUID) -> Node:
    """Een map, bestand of album van user dat gedeeld mag worden: niet de hoofdmap, niet in de
    prullenbak. Een album staat buiten de boom (parent_id NULL, maar geen hoofdmap) en wordt
    alleen gedeeld om te bekijken."""
    no_guests(user)
    node = await get_node(session, user, node_id, "owner")
    if node.kind == "album":
        return node
    if node.parent_id is None:
        raise errors.fail(422, "root", "Je hoofdmap kun je niet delen; deel een map erin")
    if await is_trashed(session, node):
        raise errors.fail(422, "trashed", "Wat in de prullenbak ligt, kun je niet delen")
    return node


async def member_counts(session: AsyncSession, group_ids: set[uuid.UUID]) -> dict[uuid.UUID, int]:
    if not group_ids:
        return {}
    rows = await session.execute(
        select(GroupMember.group_id, func.count())
        .where(GroupMember.group_id.in_(group_ids))
        .group_by(GroupMember.group_id)
    )
    return dict(rows.all())


async def shares_out(session: AsyncSession, shares: list[Share]) -> list[ShareOut]:
    """ShareOut voor deze delingen, met twee queries voor de namen."""
    user_ids = {s.grantee_id for s in shares if s.grantee_id}
    group_ids = {s.group_id for s in shares if s.group_id}
    people = {}
    if user_ids:
        rows = await session.execute(
            select(User.id, User.display_name, User.email).where(User.id.in_(user_ids))
        )
        people = {
            r.id: ShareUser(id=r.id, display_name=r.display_name, email=r.email) for r in rows
        }
    groups = {}
    if group_ids:
        counts = await member_counts(session, group_ids)
        rows = await session.execute(select(Group.id, Group.name).where(Group.id.in_(group_ids)))
        groups = {
            r.id: ShareGroup(id=r.id, name=r.name, member_count=counts.get(r.id, 0)) for r in rows
        }
    result = []
    for s in shares:
        out = ShareOut(
            id=s.id,
            node_id=s.node_id,
            type="link" if s.token else "group" if s.group_id else "user",
            permission=s.permission,
            created_at=s.created_at,
            expires_at=s.expires_at,
        )
        if s.token:
            out.link = ShareLink(
                url=f"/s/{s.token}", label=s.label, has_password=s.password_hash is not None
            )
        elif s.group_id:
            out.group = groups.get(s.group_id)
        else:
            out.user = people.get(s.grantee_id)
        result.append(out)
    return result


async def grantees(session: AsyncSession, share: Share) -> set[uuid.UUID]:
    """Wie deze deling krijgt: de gebruiker, of alle leden van de groep. Niemand bij een link."""
    if share.grantee_id is not None:
        return {share.grantee_id}
    if share.group_id is not None:
        members = await session.scalars(
            select(GroupMember.user_id).where(GroupMember.group_id == share.group_id)
        )
        return set(members.all()) - {share.owner_id}
    return set()


def announce(user_ids: set[uuid.UUID], type: str, share_id: uuid.UUID, node_id: uuid.UUID):
    if user_ids:
        events.publish(user_ids, type, {"share_id": str(share_id), "node_id": str(node_id)})


async def own_share(session: AsyncSession, user: User, share_id: uuid.UUID) -> Share:
    share = await session.get(Share, share_id, populate_existing=True)
    if share is None or share.owner_id != user.id:
        raise errors.fail(404, "share_not_found", "Deling niet gevonden")
    return share


@router.post("/files/{node_id}/shares", status_code=201)
async def create_share(
    node_id: uuid.UUID, body: NewShare, user: CurrentUser, session: Session
) -> ShareOut:
    """Delen met één gebruiker of één groep, om te bekijken (read) of te bewerken (write).
    Alleen de eigenaar. Bestaat er al een deling met die persoon of groep, dan 409: pas die
    aan met PATCH /shares/{id}."""
    node = await shareable(session, user, node_id)
    if node.kind == "album" and body.permission != "read":
        raise errors.fail(422, "album_read_only", ALBUM_READ_ONLY)
    expires_at = future(body.expires_at)
    if body.user_id is not None:
        if body.user_id == user.id:
            raise errors.fail(422, "self", "Met jezelf delen hoeft niet: het is al van jou")
        if await session.get(User, body.user_id) is None:
            raise errors.fail(404, "user_not_found", "Gebruiker niet gevonden")
        taken = Share.grantee_id == body.user_id
    else:
        if await session.get(Group, body.group_id) is None:
            raise errors.fail(404, "group_not_found", "Groep niet gevonden")
        taken = Share.group_id == body.group_id
    if await session.scalar(select(Share.id).where(Share.node_id == node.id, taken)):
        raise errors.fail(409, "already_shared", "Al gedeeld met deze persoon of groep")
    share = Share(
        node_id=node.id,
        owner_id=user.id,
        grantee_id=body.user_id,
        group_id=body.group_id,
        permission=body.permission,
        expires_at=expires_at,
    )
    session.add(share)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        # Intussen definitief weg: de map, de gebruiker of de groep; anders was iemand anders
        # net eerder met dezelfde deling
        if await session.get(Node, node_id) is None:
            raise errors.fail(404, "not_found", "Niet gevonden") from None
        if body.user_id is not None and await session.get(User, body.user_id) is None:
            raise errors.fail(404, "user_not_found", "Gebruiker niet gevonden") from None
        if body.group_id is not None and await session.get(Group, body.group_id) is None:
            raise errors.fail(404, "group_not_found", "Groep niet gevonden") from None
        raise errors.fail(409, "already_shared", "Al gedeeld met deze persoon of groep") from None
    announce(await grantees(session, share), "share.created", share.id, share.node_id)
    [out] = await shares_out(session, [share])
    return out


@router.post("/files/{node_id}/links", status_code=201)
async def create_link(
    node_id: uuid.UUID, body: NewLink, user: CurrentUser, session: Session
) -> ShareOut:
    """Een publieke link: bekijken (read) of alleen uploaden (upload, alleen voor mappen),
    met een wachtwoord en vervaldatum als je wilt. Een map of bestand kan meerdere links
    hebben. Het adres is /s/<token>."""
    node = await shareable(session, user, node_id)
    if body.permission == "upload" and node.kind != "dir":
        raise errors.fail(422, "upload_folder_only", "Een uploadlink kan alleen voor een map")
    expires_at = future(body.expires_at)
    label = clean_label(body.label)
    password_hash = await password_hash_of(body.password)
    share = Share(
        node_id=node.id,
        owner_id=user.id,
        token=secrets.token_urlsafe(32),
        permission=body.permission,
        password_hash=password_hash,
        expires_at=expires_at,
        label=label,
    )
    session.add(share)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()  # intussen definitief verwijderd
        raise errors.fail(404, "not_found", "Niet gevonden") from None
    [out] = await shares_out(session, [share])
    return out


@router.get("/files/{node_id}/shares")
async def list_shares(node_id: uuid.UUID, user: CurrentUser, session: Session) -> list[ShareOut]:
    """Delingen met gebruikers en groepen en de links van deze map of dit bestand (alleen
    voor de eigenaar), oudste eerst."""
    no_guests(user)
    node = await get_node(session, user, node_id, "owner")
    shares = (
        await session.scalars(
            select(Share).where(Share.node_id == node.id).order_by(Share.created_at, Share.id)
        )
    ).all()
    return await shares_out(session, list(shares))


@router.patch("/shares/{share_id}")
async def change_share(
    share_id: uuid.UUID, body: ShareChange, user: CurrentUser, session: Session
) -> ShareOut:
    """Recht, vervaldatum (null = nooit), en voor links ook wachtwoord ("" = geen) en label
    aanpassen. Een ander wachtwoord (of geen) sluit wie de link al open had: die moet hem
    opnieuw openen."""
    share = await own_share(session, user, share_id)
    is_link = share.token is not None
    given = body.model_fields_set
    if body.permission is not None:
        allowed = ("read", "upload") if is_link else ("read", "write")
        if body.permission not in allowed:
            raise errors.fail(422, "bad_permission", "Dit recht past niet bij deze deling")
        if body.permission == "write":
            node = await session.get(Node, share.node_id)
            if node is not None and node.kind == "album":
                raise errors.fail(422, "album_read_only", ALBUM_READ_ONLY)
        if body.permission == "upload":
            node = await session.get(Node, share.node_id)
            if node is None or node.kind != "dir":
                raise errors.fail(
                    422, "upload_folder_only", "Een uploadlink kan alleen voor een map"
                )
    if not is_link and ({"password", "label"} & given):
        raise errors.fail(422, "link_only", "Een wachtwoord of label hoort alleen bij een link")
    expires_at = future(body.expires_at) if "expires_at" in given else share.expires_at
    password_hash = share.password_hash
    if "password" in given:
        password_hash = await password_hash_of(body.password)
    label = clean_label(body.label) if "label" in given else share.label
    # Na het hashen opnieuw lezen: het kan intussen weg zijn
    share = await own_share(session, user, share_id)
    before = share.permission
    if body.permission is not None:
        share.permission = body.permission
    share.expires_at = expires_at
    share.password_hash = password_hash
    share.label = label
    try:
        await session.commit()
    except StaleDataError:
        await session.rollback()  # net ingetrokken (bijv. in een ander tabblad)
        raise errors.fail(404, "share_not_found", "Deling niet gevonden") from None
    if is_link and before == "upload" and share.permission != "upload":
        await uploads.release_link(session, share.owner_id, share.id)
    if not is_link:
        receivers = await grantees(session, share)
        announce(receivers, "share.changed", share.id, share.node_id)
        if before == "write" and share.permission == "read":
            await uploads.release(session, share.owner_id, receivers)
    [out] = await shares_out(session, [share])
    return out


@router.delete("/shares/{share_id}", status_code=204)
async def delete_share(share_id: uuid.UUID, user: CurrentUser, session: Session) -> None:
    """Deling of link intrekken. Wie het via deze deling zag, kan er meteen niet meer bij (ook
    niet met een downloadlink die al uit was). Lopende uploads via een link gaan mee weg."""
    share = await own_share(session, user, share_id)
    receivers = await grantees(session, share)
    node_id, owner_id = share.node_id, share.owner_id
    await session.delete(share)
    await session.commit()
    announce(receivers, "share.deleted", share_id, node_id)
    await uploads.release(session, owner_id, receivers)


@router.get("/shared-with-me")
async def shared_with_me(user: CurrentUser, session: Session) -> list[SharedItem]:
    """Wat anderen met jou (of een groep van jou) delen: alleen het bovenste (een gedeelde
    map in een andere gedeelde map telt niet apart), niet wat verlopen is of in de prullenbak
    ligt. Eén regel per map of bestand, met het hoogste recht. Op naam."""
    roots = await access.shared_roots(session, user.id)
    if not roots:
        return []
    nodes = {
        n.id: n
        for n in (
            await session.scalars(select(Node).where(Node.id.in_([r.node_id for r in roots])))
        ).all()
    }
    roots = [r for r in roots if r.node_id in nodes]
    described = await describe(
        session,
        user,
        [nodes[r.node_id] for r in roots],
        {r.node_id: r.permission for r in roots},
        roots={r.node_id for r in roots},
    )
    group_ids = {r.share.group_id for r in roots if r.share.group_id}
    groups = {}
    if group_ids:
        counts = await member_counts(session, group_ids)
        rows = await session.execute(select(Group.id, Group.name).where(Group.id.in_(group_ids)))
        groups = {
            r.id: ShareGroup(id=r.id, name=r.name, member_count=counts.get(r.id, 0)) for r in rows
        }
    items = [
        SharedItem(
            share_id=r.share.id,
            node=out,
            permission=r.permission,
            via="group" if r.share.group_id else "user",
            group=groups.get(r.share.group_id) if r.share.group_id else None,
            owner=out.owner,
            created_at=r.share.created_at,
        )
        for r, out in zip(roots, described, strict=True)
    ]
    items.sort(key=lambda i: (fold(i.node.name), str(i.node.id)))
    return items


@router.get("/directory")
async def directory(
    user: CurrentUser,
    session: Session,
    q: Annotated[str, Query(min_length=1, max_length=100)],
) -> Directory:
    """Gebruikers en groepen om mee te delen: hooguit 10 van elk waarvan de naam (of het
    e-mailadres) q bevat, hoofdletters en accenten tellen niet. Nooit jezelf."""
    no_guests(user)
    directory_limiter.check(str(user.id))
    needle = fold(q.strip())
    if not needle:
        return Directory(users=[], groups=[])
    people = (
        await session.execute(
            select(User.id, User.display_name, User.email).where(User.id != user.id)
        )
    ).all()
    all_groups = (await session.execute(select(Group.id, Group.name))).all()
    # Zonder accenten vergelijken kan SQLite niet; in een thread, zodat duizenden namen de
    # event loop (en daarmee elk ander verzoek) niet ophouden
    found, matching = await anyio.to_thread.run_sync(_directory_matches, needle, people, all_groups)
    counts = await member_counts(session, {g.id for g in matching})
    return Directory(
        users=[DirectoryUser(id=p.id, display_name=p.display_name, email=p.email) for p in found],
        groups=[
            ShareGroup(id=g.id, name=g.name, member_count=counts.get(g.id, 0)) for g in matching
        ],
    )


def _directory_matches(needle: str, people: list, groups: list) -> tuple[list, list]:
    """Hooguit 10 gebruikers en 10 groepen waarvan de naam (of het e-mailadres) needle
    bevat, op naam."""
    found = sorted(
        (p for p in people if needle in fold(p.display_name) or needle in fold(p.email)),
        key=lambda p: (fold(p.display_name), p.email),
    )[:10]
    matching = sorted((g for g in groups if needle in fold(g.name)), key=lambda g: fold(g.name))
    return found, matching[:10]


async def group_shares(session: AsyncSession, group_id: uuid.UUID) -> list[Share]:
    return list((await session.scalars(select(Share).where(Share.group_id == group_id))).all())


def announce_members(
    shares: list[Share], added: set[uuid.UUID], removed: set[uuid.UUID]
) -> dict[uuid.UUID, set[uuid.UUID]]:
    """Na een wijziging in een groep: share.created naar wie erbij kwam en share.deleted naar
    wie eruit ging, voor elke deling met die groep. Geeft per eigenaar wie er (misschien)
    schrijfrecht kwijt is."""
    lost: dict[uuid.UUID, set[uuid.UUID]] = defaultdict(set)
    for share in shares:
        announce(added - {share.owner_id}, "share.created", share.id, share.node_id)
        announce(removed - {share.owner_id}, "share.deleted", share.id, share.node_id)
        lost[share.owner_id] |= removed
    return lost
