"""Nooit een vector naar buiten (fase 6, K1-16, Keuze 68; X2b).

Met echte gezichten in de database (de verzegelde vec en de vector zelf als merktekens) roept de
test elke GET-route uit de OpenAPI aan (ook /beheer, de viewer en de publieke routes), als Eva,
als de beheerder en zonder inlog, en de routes van gezichten die iets doen. Nergens staan de
bytes, base64 of hex van een vec of een vector, of de getallen van een vector; geen antwoord van
gezichten of personen heeft een veld vec, embedding, vector of score. Ook niet in de logs
(caplog, ook DEBUG) en niet in de gebeurtenissen (events.publish).
"""

from __future__ import annotations

import base64
import json
import logging
import re
import struct
import uuid

import anyio
import pytest
from sqlalchemy import select

from app import events
from app.analyse import wire
from app.main import app as fastapi_app
from app.models import Face
from app.people.faces import crypto, group, state
from tests.factories import create_user, maker
from tests.people.faces.conftest import aan, draai, foto
from tests.test_photos import login

pytestmark = pytest.mark.anyio

A = (0.10, 0.10, 0.20, 0.25)
B = (0.60, 0.20, 0.20, 0.25)
F = "/api/v1/people/faces"
# Een stroom die openblijft (server-sent events): geen antwoord om te lezen
SKIP = {"/api/v1/events"}
FORBIDDEN_KEYS = {"vec", "embedding", "vector", "score", "suggest_score"}


def forms(data: bytes) -> list[bytes]:
    """De vormen waarin bytes in een antwoord of log kunnen staan."""
    part = data[16:40]
    return [
        part,
        base64.b64encode(data)[8:40],
        base64.urlsafe_b64encode(data)[8:40],
        data.hex()[16:64].encode(),
        data.hex().upper()[16:64].encode(),
        repr(part)[2:40].encode(),
    ]


def numbers(vec: bytes) -> list[bytes]:
    """De eerste getallen van een vector zoals json of repr ze schrijft."""
    values = struct.unpack(f"<{wire.FACE_DIM}f", vec)[:3]
    return [repr(v).encode() for v in values] + [f"{values[0]:.6f}".encode()]


def keys_of(value) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {k for v in value.values() for k in keys_of(v)}
    if isinstance(value, list):
        return {k for v in value for k in keys_of(v)}
    return set()


@pytest.fixture
async def wereld(client, settings, nep, admin):
    async with maker(settings)() as session:
        user = await create_user(session, "eva@example.nl")
        await session.commit()
    ids = {"eva": user.id, "h_admin": admin[0]}
    ids["fotos"] = [
        await foto(settings, nep, user.id, f"{i}.jpg", [(A, 1), (B, 2)]) for i in range(4)
    ]
    await aan(settings, user.id)
    await draai(settings)
    ids["h"] = await login(client, "eva@example.nl")
    return ids


async def test_geen_vector_in_een_antwoord_een_log_of_een_gebeurtenis(
    client, settings, wereld, caplog, monkeypatch
):
    h = wereld["h"]
    published: list[str] = []
    real_publish = events.publish

    def spy(user_ids, type, data):
        published.append(json.dumps({"type": type, "data": data}, default=str))
        return real_publish(user_ids, type, data)

    monkeypatch.setattr(events, "publish", spy)
    # people.changed hooguit eens per 10 s: de scan van de fixture telt niet mee
    monkeypatch.setattr(state, "_last_event", {})
    caplog.set_level(logging.DEBUG)

    # Oma op de gezichten van persoon 1, een groep (full) en voorstellen
    async with maker(settings)() as session:
        rows = list(await session.scalars(select(Face).order_by(Face.node_id, Face.x)))
    first = rows[0]
    r = await client.post(f"{F}/confirm", json={"faces": [str(first.id)], "name": "Oma"}, headers=h)
    assert r.status_code == 204
    await group.run_group(settings, wereld["eva"], "full")
    await group.run_group(settings, wereld["eva"], "suggest")
    oma = (await client.get("/api/v1/people", headers=h)).json()["people"][0]["id"]
    groups = (await client.get(f"{F}/groups", headers=h)).json()["groups"]

    # De merktekens: elke vec zoals hij in de database staat, en de vector erin
    key = crypto.load_key(settings, wereld["eva"])
    async with maker(settings)() as session:
        rows = list(await session.scalars(select(Face).where(Face.vec.is_not(None))))
    assert len(rows) == 8
    marks: list[bytes] = []
    for f in rows:
        plain = crypto.open_vec(key, f.id, f.vec)
        assert plain is not None
        marks += forms(f.vec) + forms(plain) + numbers(plain)

    values = {
        "node_id": str(wereld["fotos"][0]),
        "face_id": str(rows[0].id),
        "person_id": oma,
        "key": groups[0]["key"] if groups else "1:0",
        "size": "256",
        "sha": "0" * 64,
        "n": "1",
        "i": "1",
    }
    calls: list[tuple[str, str, dict | None, dict | None]] = []
    for path, ops in fastapi_app.openapi()["paths"].items():
        if "get" not in ops or path in SKIP:
            continue
        filled = re.sub(r"{(\w+)}", lambda m: values.get(m.group(1), str(uuid.uuid4())), path)
        for who in (h, wereld["h_admin"], None):
            calls.append(("GET", filled, None, who))
    for path in (f"{F}/suggestions?person={oma}", f"{F}/suggestions", f"{F}/status"):
        calls.append(("GET", path, None, h))
    other = str(rows[-1].id)
    calls += [
        ("POST", f"{F}/reject", {"faces": [other], "person_id": oma}, h),
        ("POST", f"{F}/hide", {"faces": [other]}, h),
        ("POST", f"{F}/reset", {"faces": [other]}, h),
        ("POST", f"/api/v1/files/{wereld['fotos'][1]}/faces/rescan", None, h),
    ]

    answered: set[str] = set()
    for method, path, body, who in calls:
        with anyio.fail_after(10):
            r = await client.request(method, path, json=body, headers=who)
        if r.status_code < 400:
            answered.add(f"{method} {path}")
        content = r.content
        for m in marks:
            assert m not in content, (method, path)
        if "json" in r.headers.get("content-type", "") and content:
            payload = r.json()
            if "/faces" in path or "/people" in path or "/admin/search" in path:
                assert not keys_of(payload) & FORBIDDEN_KEYS, (path, keys_of(payload))
    # De routes met gezichten antwoordden echt (geen lijst vol 404's)
    node, face = values["node_id"], values["face_id"]
    must = {
        f"GET {F}/status",
        f"GET {F}/suggestions",
        f"GET {F}/suggestions?person={oma}",
        f"GET {F}/groups",
        f"GET {F}/groups/{values['key']}",
        f"GET {F}/{face}/crop",
        f"GET /api/v1/files/{node}/faces",
        f"GET /api/v1/files/{node}",
        "GET /api/v1/people",
        f"GET /api/v1/people/{oma}/photos",
        "GET /api/v1/admin/search",
        "GET /api/v1/me/photo-settings",
        f"POST {F}/hide",
        f"POST {F}/reset",
        f"POST /api/v1/files/{wereld['fotos'][1]}/faces/rescan",
    }
    assert must <= answered, must - answered
    await draai(settings)

    logged = "\n".join(
        f"{rec.getMessage()} {rec.args!r} {rec.exc_text or ''}" for rec in caplog.records
    ).encode()
    assert caplog.records
    for m in marks:
        assert m not in logged
    assert published and any("people.changed" in p for p in published)
    text = "\n".join(published).encode()
    for m in marks:
        assert m not in text
