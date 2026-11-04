"""Personen op foto's (bouwontwerp fase 6, §3.4; Z6). Stubs van Z0: 501 not_built. Alleen eigen
personen: een id van een ander geeft 404 not_found.
"""

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Query
from pydantic import BeforeValidator

from app.people.types import (
    AddPersonIn,
    FilePeople,
    MergeIn,
    People,
    Person,
    PersonIn,
    PersonPatch,
    PersonPhotos,
)
from app.routers.stubs import not_built
from app.security import CurrentUser

router = APIRouter(prefix="/api/v1", tags=["foto's"])

# ?hidden=1 komt als tekst binnen; Literal[0, 1] alleen neemt geen "1" (dan altijd 422)
Hidden = Annotated[Literal[0, 1], BeforeValidator(int)]


@router.get("/people")
async def list_people(user: CurrentUser, hidden: Hidden = 0) -> People:
    raise not_built("Z6")


@router.post("/people", status_code=201)
async def create_person(body: PersonIn, user: CurrentUser) -> Person:
    raise not_built("Z6")


@router.patch("/people/{person_id}")
async def change_person(person_id: uuid.UUID, body: PersonPatch, user: CurrentUser) -> Person:
    raise not_built("Z6")


@router.post("/people/{person_id}/merge")
async def merge_person(person_id: uuid.UUID, body: MergeIn, user: CurrentUser) -> Person:
    raise not_built("Z6")


@router.delete("/people/{person_id}", status_code=204)
async def delete_person(person_id: uuid.UUID, user: CurrentUser) -> None:
    raise not_built("Z6")


@router.get("/people/{person_id}/photos")
async def person_photos(
    person_id: uuid.UUID,
    user: CurrentUser,
    cursor: Annotated[str | None, Query(max_length=200)] = None,
) -> PersonPhotos:
    raise not_built("Z6")


@router.get("/files/{node_id}/people")
async def file_people(node_id: uuid.UUID, user: CurrentUser) -> FilePeople:
    raise not_built("Z6")


@router.post("/files/{node_id}/people", status_code=201)
async def add_file_person(node_id: uuid.UUID, body: AddPersonIn, user: CurrentUser) -> None:
    raise not_built("Z6")


@router.delete("/files/{node_id}/people/{person_id}", status_code=204)
async def remove_file_person(node_id: uuid.UUID, person_id: uuid.UUID, user: CurrentUser) -> None:
    raise not_built("Z6")
