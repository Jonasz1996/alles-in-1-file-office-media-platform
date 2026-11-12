"""Opnamedatum (app/photodate.py): zwevende lokale tijd, stempelen bij neerzetten, kopiëren
en versie terugzetten, en de datum uit de inhoud naar alle bestanden met die inhoud."""

import hashlib
import uuid
from datetime import UTC, datetime, timedelta, timezone

import pytest
from sqlalchemy import select, update

from app import photodate, previews, render_meta
from app.models import Blob, Node, Upload
from app.storage import BlobStore
from tests.factories import create_file, create_user, maker
from tests.test_files import put, root
from tests.test_previews import picture
from tests.test_uploads import create, send

TZ = "Europe/Amsterdam"
SUMMER = datetime(2024, 7, 1, 22, 30, tzinfo=UTC)  # in Amsterdam al 2 juli, 00:30


def ms(moment: datetime) -> int:
    return int(moment.timestamp() * 1000)


def naive(value: datetime | None) -> datetime | None:
    """SQLite geeft tijden zonder tijdzone terug."""
    return value.replace(tzinfo=None) if value is not None else None


async def node_row(settings, node_id) -> Node:
    async with maker(settings)() as session:
        return await session.get(Node, uuid.UUID(str(node_id)))


# Zuivere functies


def test_wall_clock_en_dag():
    local = photodate.wall_clock(SUMMER, TZ)
    assert local == datetime(2024, 7, 2, 0, 30, tzinfo=UTC)
    assert photodate.day_of(local) == 20240702
    # Zonder tijdzone: UTC
    assert photodate.wall_clock(SUMMER.replace(tzinfo=None), TZ) == local
    # In de winter een uur verschil
    assert photodate.wall_clock(datetime(2024, 1, 15, 8, 0, tzinfo=UTC), TZ).hour == 9
    # Een onbekende tijdzone: UTC, geen fout
    assert photodate.wall_clock(SUMMER, "Nergens/Niets") == SUMMER


def test_is_media_en_stamp_values():
    assert photodate.is_media("image/jpeg") and photodate.is_media("VIDEO/mp4")
    assert not photodate.is_media("text/plain") and not photodate.is_media(None)
    assert photodate.stamp_values("text/plain", SUMMER, SUMMER, TZ) == {
        "taken_at": None,
        "taken_day": None,
    }
    # Uit de inhoud: al een zwevende tijd, zoals hij is
    content = datetime(2020, 5, 1, 10, 0, tzinfo=UTC)
    assert photodate.stamp_values("image/png", content, SUMMER, TZ) == {
        "taken_at": content,
        "taken_day": 20200501,
    }
    # Anders de terugval, als kloktijd in TZ
    assert photodate.stamp_values("image/png", None, SUMMER, TZ)["taken_day"] == 20240702


def test_split_capture_gooit_gps_weg():
    meta = {
        "width": 10,
        "taken_at": "2020-05-01T10:00:00",
        "taken_kind": "local",
        "gps": {"lat": 52.1, "lon": 5.1},
        "exif": {"model": "X"},
    }
    capture = photodate.split_capture(meta)
    assert capture == {"taken_at": "2020-05-01T10:00:00", "taken_kind": "local"}
    assert meta == {"width": 10, "exif": {"model": "X"}}
    assert photodate.split_capture({}) == {"taken_at": None, "taken_kind": None}


def test_captured_at_utc_en_lokaal():
    # Lokaal (EXIF): de kloktijd zoals hij is, ook met een offset erbij
    local = {"taken_at": "2020-05-01T10:00:00+05:00", "taken_kind": "local"}
    assert photodate.captured_at(local, TZ) == datetime(2020, 5, 1, 10, 0, tzinfo=UTC)
    # UTC (video): omgezet naar de kloktijd in TZ
    utc = {"taken_at": "2024-07-01T22:30:00", "taken_kind": "utc"}
    assert photodate.captured_at(utc, TZ) == datetime(2024, 7, 2, 0, 30, tzinfo=UTC)
    # Onzin en jaren buiten 1900-2100: geen datum
    assert photodate.captured_at({"taken_at": "gisteren"}, TZ) is None
    assert photodate.captured_at({"taken_at": "1850-01-01T00:00:00"}, TZ) is None
    assert photodate.captured_at({"taken_at": None}, TZ) is None


def test_from_ms():
    assert photodate.from_ms(ms(SUMMER)) == SUMMER
    assert photodate.from_ms(str(ms(SUMMER))) == SUMMER
    assert photodate.from_ms(None) is None
    assert photodate.from_ms("x") is None
    assert photodate.from_ms(10**20) is None


# In de database


@pytest.mark.anyio
async def test_on_blob_meta_naar_alle_bestanden_met_die_inhoud(settings):
    async with maker(settings)() as session:
        owner = await create_user(session, "a@example.nl")
        data = b"foto"
        fallback = datetime(2024, 1, 1, tzinfo=UTC)
        a = await create_file(session, owner, name="a.jpg", data=data, mime="image/jpeg",
                              taken_at=fallback)  # fmt: skip
        b = await create_file(session, owner, name="b.jpg", data=data, mime="image/jpeg",
                              taken_at=fallback)  # fmt: skip
        # Zelfde inhoud, maar niet op de tijdlijn (geen taken_day): blijft zo
        c = await create_file(session, owner, name="c.bin", data=data, mime="application/x")
        blob = await session.get(Blob, hashlib.sha256(data).hexdigest())
        blob.preview_meta = {"sizes": [256]}
        capture = {"taken_at": "2020-05-01T10:00:00", "taken_kind": "local"}
        await photodate.on_blob_meta(session, blob, capture, {"model": "X"}, TZ)
        await session.commit()
        ids = a.id, b.id, c.id

    taken = datetime(2020, 5, 1, 10, 0)
    for node_id in ids[:2]:
        node = await node_row(settings, node_id)
        assert (naive(node.taken_at), node.taken_day) == (taken, 20200501)
    other = await node_row(settings, ids[2])
    assert (other.taken_at, other.taken_day) == (None, None)
    async with maker(settings)() as session:
        blob = await session.get(Blob, hashlib.sha256(b"foto").hexdigest())
    assert naive(blob.taken_at) == taken
    assert blob.meta_version == render_meta.VERSION
    assert blob.preview_meta == {"sizes": [256], "exif": {"model": "X"}}


@pytest.mark.anyio
async def test_zonder_datum_in_de_inhoud_blijft_de_terugval(settings):
    async with maker(settings)() as session:
        owner = await create_user(session, "a@example.nl")
        fallback = datetime(2024, 1, 1, 12, 0, tzinfo=UTC)
        a = await create_file(session, owner, name="a.jpg", data=b"f", mime="image/jpeg",
                              taken_at=fallback)  # fmt: skip
        blob = await session.get(Blob, a.blob_id)
        await photodate.on_blob_meta(session, blob, {"taken_at": None}, None, TZ)
        await session.commit()
    node = await node_row(settings, a.id)
    assert naive(node.taken_at) == naive(fallback)
    assert blob.preview_meta is None  # zonder exif niets erbij


@pytest.mark.anyio
async def test_preview_taak_haalt_de_datum_uit_de_metadata(settings, tmp_path):
    """De haakregels in previews._store: taken_at en gps gaan niet naar preview_meta, de datum
    wel naar de blob en zijn bestanden."""
    async with maker(settings)() as session:
        owner = await create_user(session, "a@example.nl")
        a = await create_file(session, owner, name="a.jpg", data=b"f", mime="image/jpeg",
                              taken_at=datetime(2024, 1, 1, tzinfo=UTC))  # fmt: skip
        await session.commit()
    out = tmp_path / "uit"
    out.mkdir()
    result = {
        "state": "ready",
        "meta": {"width": 1, "taken_at": "2021-03-04T05:06:07", "taken_kind": "local",
                 "gps": {"lat": 1}},
        "sizes": [],
    }  # fmt: skip
    blobs = BlobStore(settings.blob_dir)
    assert await previews._store(maker(settings), blobs, a.blob_id, result, out, TZ)
    async with maker(settings)() as session:
        blob = await session.get(Blob, a.blob_id)
        node = await session.get(Node, a.id)
    assert blob.preview_meta == {"width": 1, "sizes": []}
    assert naive(blob.taken_at) == datetime(2021, 3, 4, 5, 6, 7)
    assert node.taken_day == 20210304


@pytest.mark.anyio
async def test_stempel_bij_uploaden_met_mtime(client, admin, settings):
    headers, _ = admin
    root_id = (await root(client, headers))["folder"]["id"]
    r = await put(client, headers, root_id, "a.png", picture("PNG"), mtime=ms(SUMMER))
    assert r.status_code == 201, r.text
    node = await node_row(settings, r.json()["id"])
    assert (naive(node.taken_at), node.taken_day) == (datetime(2024, 7, 2, 0, 30), 20240702)

    # Zonder mtime: nu, als kloktijd in de tijdzone van de server
    r = await put(client, headers, root_id, "b.png", picture("PNG", color="blue"))
    node = await node_row(settings, r.json()["id"])
    today = photodate.wall_clock(datetime.now(UTC), settings.timezone)
    assert node.taken_day in {photodate.day_of(today), photodate.day_of(today - timedelta(1))}

    # Geen afbeelding of video: niet op de tijdlijn
    r = await put(client, headers, root_id, "c.txt", b"tekst", mtime=ms(SUMMER))
    node = await node_row(settings, r.json()["id"])
    assert (node.taken_at, node.taken_day) == (None, None)


@pytest.mark.anyio
async def test_bekende_datum_uit_de_inhoud_gaat_voor(client, admin, settings):
    headers, _ = admin
    root_id = (await root(client, headers))["folder"]["id"]
    data = picture("PNG", color="green")
    first = (await put(client, headers, root_id, "a.png", data)).json()
    async with maker(settings)() as session:
        await session.execute(
            update(Blob)
            .where(Blob.sha256 == hashlib.sha256(data).hexdigest())
            .values(taken_at=datetime(2019, 2, 3, 4, 5, tzinfo=UTC))
        )
        await session.commit()
    # Dezelfde inhoud nog eens: de datum uit de inhoud, niet de mtime
    second = (await put(client, headers, root_id, "b.png", data, mtime=ms(SUMMER))).json()
    node = await node_row(settings, second["id"])
    assert (naive(node.taken_at), node.taken_day) == (datetime(2019, 2, 3, 4, 5), 20190203)
    assert first["id"] != second["id"]


@pytest.mark.anyio
async def test_tus_bewaart_lastmodified_en_stempelt(client, admin, settings):
    headers, _ = admin
    root_id = (await root(client, headers))["folder"]["id"]
    data = picture("PNG", color="yellow")
    r = await create(client, headers, root_id, "t.png", len(data), lastmodified=str(ms(SUMMER)))
    assert r.status_code == 201, r.text
    location = r.headers["location"]
    async with maker(settings)() as session:
        upload = await session.scalar(select(Upload))
    assert naive(upload.client_mtime) == naive(SUMMER)

    r = await send(client, headers, location, 0, data)
    node = await node_row(settings, r.headers["platform-node-id"])
    assert node.taken_day is not None  # gestempeld (met de mtime zodra P3 die meegeeft)


@pytest.mark.anyio
async def test_kopie_neemt_de_datum_mee(client, admin, settings):
    headers, _ = admin
    root_id = (await root(client, headers))["folder"]["id"]
    r = await client.post(f"/api/v1/files/{root_id}/folders", json={"name": "Map"}, headers=headers)
    folder = r.json()["id"]
    photo = (await put(client, headers, folder, "a.png", picture("PNG"), mtime=ms(SUMMER))).json()
    r = await client.post(
        f"/api/v1/files/{folder}/copy", json={"parent_id": root_id}, headers=headers
    )
    assert r.status_code == 201, r.text
    async with maker(settings)() as session:
        copied = await session.scalar(
            select(Node).where(Node.name == "a.png", Node.id != uuid.UUID(photo["id"]))
        )
    assert (naive(copied.taken_at), copied.taken_day) == (datetime(2024, 7, 2, 0, 30), 20240702)


@pytest.mark.anyio
async def test_versie_terugzetten_stempelt_met_die_inhoud(client, admin, settings):
    headers, _ = admin
    root_id = (await root(client, headers))["folder"]["id"]
    old = picture("PNG", color="red")
    node = (await put(client, headers, root_id, "a.png", old, mtime=ms(SUMMER))).json()
    later = datetime(2025, 3, 1, 12, 0, tzinfo=UTC)
    r = await put(
        client, headers, root_id, "a.png", picture("PNG", color="blue"),
        conflict="replace", mtime=ms(later),
    )  # fmt: skip
    assert r.status_code == 201, r.text
    assert (await node_row(settings, node["id"])).taken_day == 20250301

    # De oude inhoud kreeg intussen een datum uit EXIF
    async with maker(settings)() as session:
        await session.execute(
            update(Blob)
            .where(Blob.sha256 == hashlib.sha256(old).hexdigest())
            .values(taken_at=datetime(2018, 8, 9, 10, 11, tzinfo=UTC))
        )
        await session.commit()
    versions = (await client.get(f"/api/v1/files/{node['id']}/versions", headers=headers)).json()
    r = await client.post(
        f"/api/v1/files/{node['id']}/versions/{versions[0]['id']}/restore", headers=headers
    )
    assert r.status_code == 200, r.text
    row = await node_row(settings, node["id"])
    assert (naive(row.taken_at), row.taken_day) == (datetime(2018, 8, 9, 10, 11), 20180809)


def test_offset_in_terugval():
    """Een terugval met een andere tijdzone dan UTC telt als echt moment."""
    moment = datetime(2024, 7, 1, 23, 30, tzinfo=timezone(timedelta(hours=1)))  # 22:30 UTC
    assert photodate.stamp_values("image/png", None, moment, TZ)["taken_day"] == 20240702
