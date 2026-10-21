"""Groepen (beheer op /beheer): om in één keer met iedereen erin te delen. Wie lid wordt of
eruit gaat, ziet dat meteen terug: toegang wordt bij elk verzoek opnieuw berekend."""

import asyncio
import uuid
from itertools import batched

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import delete, insert, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import Session
from app.models import Group, GroupMember, User
from app.routers import uploads
from app.routers.shares import announce_members, error, group_shares
from app.security import AdminUser
from app.text import fold

router = APIRouter(prefix="/api/v1", tags=["beheer"])
MAX_GROUP_NAME = 100
MAX_MEMBERS = 10_000

# Eén wijziging aan groepen tegelijk: of een naam (zonder hoofdletters en accenten) vrij is,
# en wie er lid is, moet vastliggen tot de wijziging erin staat
_lock = asyncio.Lock()


class GroupMemberOut(BaseModel):
    id: uuid.UUID
    display_name: str
    email: str


class GroupOut(BaseModel):
    id: uuid.UUID
    name: str
    member_count: int
    members: list[GroupMemberOut]


class GroupName(BaseModel):
    # De echte grens (MAX_GROUP_NAME) geldt na het weghalen van spaties eromheen
    name: str = Field(min_length=1, max_length=300)

    @field_validator("name")
    @classmethod
    def valid_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Geef de groep een naam")
        if len(value) > MAX_GROUP_NAME:
            raise ValueError(f"Een groepsnaam mag hooguit {MAX_GROUP_NAME} tekens hebben")
        return value


class Members(BaseModel):
    user_ids: list[uuid.UUID] = Field(max_length=MAX_MEMBERS)


def name_taken() -> HTTPException:
    return error(409, "Er bestaat al een groep met deze naam", "name_taken")


async def check_free(session: AsyncSession, name: str, skip: uuid.UUID | None = None) -> None:
    """Hoofdletters en accenten tellen niet mee (in Python: lower() in SQLite kent alleen
    ASCII)."""
    rows = await session.execute(select(Group.id, Group.name))
    if any(fold(other) == fold(name) and group_id != skip for group_id, other in rows):
        raise name_taken()


async def get_group(session: AsyncSession, group_id: uuid.UUID) -> Group:
    group = await session.get(Group, group_id, populate_existing=True)
    if group is None:
        raise HTTPException(404, "Groep niet gevonden")
    return group


async def groups_out(session: AsyncSession, groups: list[Group]) -> list[GroupOut]:
    """Met de leden erbij, in één query."""
    ids = [g.id for g in groups]
    members: dict[uuid.UUID, list[GroupMemberOut]] = {g: [] for g in ids}
    if ids:
        rows = await session.execute(
            select(GroupMember.group_id, User.id, User.display_name, User.email)
            .join(User, User.id == GroupMember.user_id)
            .where(GroupMember.group_id.in_(ids))
        )
        for group_id, user_id, name, email in rows:
            members[group_id].append(GroupMemberOut(id=user_id, display_name=name, email=email))
    result = []
    for g in groups:
        people = sorted(members[g.id], key=lambda m: (fold(m.display_name), m.email))
        result.append(GroupOut(id=g.id, name=g.name, member_count=len(people), members=people))
    return result


@router.get("/groups")
async def list_groups(_: AdminUser, session: Session) -> list[GroupOut]:
    groups = sorted((await session.scalars(select(Group))).all(), key=lambda g: fold(g.name))
    return await groups_out(session, groups)


@router.post("/groups", status_code=201)
async def create_group(body: GroupName, _: AdminUser, session: Session) -> GroupOut:
    await session.commit()  # niet met een databaseverbinding op het slot wachten
    async with _lock:
        await check_free(session, body.name)
        group = Group(name=body.name)
        session.add(group)
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            raise name_taken() from None
    [out] = await groups_out(session, [group])
    return out


@router.patch("/groups/{group_id}")
async def rename_group(
    group_id: uuid.UUID, body: GroupName, _: AdminUser, session: Session
) -> GroupOut:
    await session.commit()  # niet met een databaseverbinding op het slot wachten
    async with _lock:
        group = await get_group(session, group_id)
        await check_free(session, body.name, skip=group.id)
        group.name = body.name
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            raise name_taken() from None
    [out] = await groups_out(session, [group])
    return out


@router.delete("/groups/{group_id}", status_code=204)
async def delete_group(group_id: uuid.UUID, _: AdminUser, session: Session) -> None:
    """De delingen met deze groep gaan mee weg (de leden houden wat ze via een andere weg
    hebben)."""
    await session.commit()  # niet met een databaseverbinding op het slot wachten
    async with _lock:
        group = await get_group(session, group_id)
        shares = await group_shares(session, group.id)
        members = set(
            (
                await session.scalars(
                    select(GroupMember.user_id).where(GroupMember.group_id == group.id)
                )
            ).all()
        )
        await session.delete(group)
        await session.commit()
    for owner_id, lost in announce_members(shares, set(), members).items():
        await uploads.release(session, owner_id, lost)


@router.put("/groups/{group_id}/members")
async def set_members(
    group_id: uuid.UUID, body: Members, _: AdminUser, session: Session
) -> GroupOut:
    """Vervangt de leden door deze gebruikers."""
    wanted = set(body.user_ids)
    await session.commit()  # niet met een databaseverbinding op het slot wachten
    async with _lock:
        group = await get_group(session, group_id)
        if wanted:
            known = set((await session.scalars(select(User.id).where(User.id.in_(wanted)))).all())
            if known != wanted:
                raise error(422, "Een of meer gebruikers bestaan niet", "unknown_user")
        current = set(
            (
                await session.scalars(
                    select(GroupMember.user_id).where(GroupMember.group_id == group.id)
                )
            ).all()
        )
        added, removed = wanted - current, current - wanted
        try:
            for chunk in batched(removed, 500):
                await session.execute(
                    delete(GroupMember).where(
                        GroupMember.group_id == group.id, GroupMember.user_id.in_(chunk)
                    )
                )
            # Rechtstreeks in stukken, en tussendoor de anderen even aan de beurt: één
            # ORM-object per lid houdt de event loop bij duizenden leden te lang bezig
            for chunk in batched(added, 500):
                await session.execute(
                    insert(GroupMember), [{"group_id": group.id, "user_id": u} for u in chunk]
                )
                await asyncio.sleep(0)
            await session.commit()
        except IntegrityError:
            await session.rollback()  # een gebruiker of de groep is intussen weg
            raise HTTPException(404, "Groep of gebruiker niet gevonden") from None
        shares = await group_shares(session, group.id)
    for owner_id, lost in announce_members(shares, added, removed).items():
        await uploads.release(session, owner_id, lost)
    [out] = await groups_out(session, [group])
    return out
