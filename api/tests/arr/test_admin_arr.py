"""Mediabeheer op /beheer: Prowlarr en qBittorrent koppelen (fase 11, stap 2)."""

from __future__ import annotations

import json
import logging

import httpx
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.arr import prowlarr
from app.arr import settings as arr_settings
from app.db import get_engine
from app.models import Setting
from tests.arr.test_qbit import FakeQBit
from tests.test_shares import person

pytestmark = pytest.mark.anyio

BASE = "/api/v1/admin/arr"
KEY = "prowlarr-sleutel-HEEL-GEHEIM"
PASSWORD = "qbit-wachtwoord-HEEL-GEHEIM"
INDEXERS = [
    {"id": 1, "name": "Site B", "enable": True, "protocol": "torrent", "privacy": "public"},
    {"id": 2, "name": "Usenet", "enable": True, "protocol": "usenet", "privacy": "private"},
    {"id": 3, "name": "Uit", "enable": False, "protocol": "torrent", "privacy": "public"},
    {"id": 4, "name": "Site A", "enable": True, "protocol": "torrent", "privacy": "private"},
]


@pytest.fixture
def links(monkeypatch):
    """Nep-Prowlarr op prowlarr:9696 en nep-qBittorrent op qbittorrent:8080."""
    fake_qbit = FakeQBit(password=PASSWORD)
    state = {"prowlarr_up": True}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "qbittorrent":
            return fake_qbit(request)
        if request.url.host == "prowlarr":
            if not state["prowlarr_up"]:
                raise httpx.ConnectError("weg")
            if request.headers.get("x-api-key") != KEY:
                return httpx.Response(401)
            if request.url.path == "/api/v1/indexer":
                return httpx.Response(200, text=json.dumps(INDEXERS))
        return httpx.Response(404)

    monkeypatch.setattr(
        arr_settings,
        "http_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    return fake_qbit, state


async def test_only_admins(client, admin, links):
    headers, _ = admin
    user, _ = await person(client, headers, "gezin@example.nl")
    for method, path in [
        ("GET", BASE),
        ("PUT", f"{BASE}/prowlarr"),
        ("POST", f"{BASE}/qbittorrent/test"),
        ("DELETE", f"{BASE}/qbittorrent"),
    ]:
        r = await client.request(method, path, headers=user, json={"url": "http://x:1"})
        assert r.status_code == 403, (method, path)


async def test_prowlarr_save_test_forget(client, admin, links, caplog):
    headers, _ = admin
    caplog.set_level(logging.DEBUG)
    body = {"url": "http://prowlarr:9696/", "key": KEY}
    r = await client.put(f"{BASE}/prowlarr", json=body, headers=headers)
    assert r.status_code == 200, r.text
    assert [i["name"] for i in r.json()["indexers"]] == ["Site A", "Site B"]  # geen usenet, uit

    view = (await client.get(BASE, headers=headers)).json()
    assert view["prowlarr"] == {"url": "http://prowlarr:9696", "key_set": True}
    # Opnieuw opslaan zonder sleutel houdt de oude
    r = await client.put(f"{BASE}/prowlarr", json={"url": "http://prowlarr:9696"}, headers=headers)
    assert r.status_code == 200
    r = await client.post(f"{BASE}/prowlarr/test", headers=headers)
    assert r.status_code == 200 and len(r.json()["indexers"]) == 2

    _, state = links
    state["prowlarr_up"] = False
    r = await client.post(f"{BASE}/prowlarr/test", headers=headers)
    assert r.status_code == 503 and r.json()["detail"]["code"] == "arr_link_unreachable"

    assert KEY not in caplog.text and KEY not in json.dumps(view)
    r = await client.delete(f"{BASE}/prowlarr", headers=headers)
    assert r.status_code == 204
    r = await client.post(f"{BASE}/prowlarr/test", headers=headers)
    assert r.status_code == 404


async def test_prowlarr_wrong_key_saves_nothing(client, admin, links):
    headers, _ = admin
    r = await client.put(
        f"{BASE}/prowlarr", json={"url": "http://prowlarr:9696", "key": "fout"}, headers=headers
    )
    assert r.status_code == 422 and r.json()["detail"]["code"] == "arr_link_auth"
    assert (await client.get(BASE, headers=headers)).json()["prowlarr"]["key_set"] is False
    r = await client.put(f"{BASE}/prowlarr", json={"url": "file:///etc/x"}, headers=headers)
    assert r.status_code == 422


async def test_qbit_save_and_secret_is_sealed(client, admin, links, settings):
    headers, _ = admin
    fake, _ = links
    body = {
        "url": "http://qbittorrent:8080",
        "username": "admin",
        "password": PASSWORD,
        "save_path": "/downloads/platform",
    }
    r = await client.put(f"{BASE}/qbittorrent", json=body, headers=headers)
    assert r.status_code == 200, r.text
    assert r.json() == {"version": "v5.0.2"}
    assert fake.categories == {"platform": "/downloads/platform"}

    view = (await client.get(BASE, headers=headers)).json()["qbittorrent"]
    assert view["password_set"] is True and view["username"] == "admin"
    assert PASSWORD not in json.dumps(view)

    async with async_sessionmaker(get_engine(settings))() as session:
        row = await session.get(Setting, arr_settings.SETTING)
        assert PASSWORD not in json.dumps(row.value)

    r = await client.post(f"{BASE}/qbittorrent/test", headers=headers)
    assert r.status_code == 200
    r = await client.put(f"{BASE}/qbittorrent", json={**body, "password": "fout"}, headers=headers)
    assert r.status_code == 422 and r.json()["detail"]["code"] == "arr_link_auth"
    # het oude wachtwoord staat er nog
    r = await client.post(f"{BASE}/qbittorrent/test", headers=headers)
    assert r.status_code == 200


def test_torznab_base() -> None:
    assert prowlarr.torznab_base("http://prowlarr:9696/", 7) == "http://prowlarr:9696/7/api"
    with pytest.raises(prowlarr.ProwlarrError):
        prowlarr.torznab_base("http://p:1/?x=1", 1)
