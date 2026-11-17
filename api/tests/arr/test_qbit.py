"""qBittorrent aansturen tegen een nep-qBittorrent (fase 11, stap 1)."""

from __future__ import annotations

import json
from urllib.parse import parse_qs

import httpx
import pytest

from app.arr.qbit import QBit, QBitError, magnet_hash, to_torrent

pytestmark = pytest.mark.anyio


class FakeQBit:
    """Genoeg van de web-API v2 van qBittorrent om de client te testen."""

    def __init__(self, password: str = "goed") -> None:
        self.password = password
        self.sessions: set[str] = set()
        self.logins = 0
        self.categories: dict[str, str] = {}
        self.torrents: list[dict] = []
        self.added: list[dict] = []

    def expire(self) -> None:
        self.sessions.clear()

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        form = (
            {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
            if request.headers.get("content-type", "").startswith("application/x-www")
            else {}
        )
        if path == "/api/v2/auth/login":
            self.logins += 1
            if form.get("username") == "admin" and form.get("password") == self.password:
                sid = f"sid{self.logins}"
                self.sessions.add(sid)
                return httpx.Response(200, text="Ok.", headers={"set-cookie": f"SID={sid}"})
            return httpx.Response(200, text="Fails.")
        if request.headers.get("cookie", "").removeprefix("SID=") not in self.sessions:
            return httpx.Response(403, text="Forbidden")
        if path == "/api/v2/app/version":
            return httpx.Response(200, text="v5.0.2")
        if path == "/api/v2/torrents/createCategory":
            if form["category"] in self.categories:
                return httpx.Response(409)
            self.categories[form["category"]] = form["savePath"]
            return httpx.Response(200)
        if path == "/api/v2/torrents/editCategory":
            self.categories[form["category"]] = form["savePath"]
            return httpx.Response(200)
        if path == "/api/v2/torrents/add":
            if b"multipart" in request.headers.get("content-type", "").encode():
                self.added.append({"file": True, "body": request.content})
                return httpx.Response(200, text="Ok.")
            if form.get("urls") == "kapot":
                return httpx.Response(200, text="Fails.")
            self.added.append(form)
            return httpx.Response(200, text="Ok.")
        if path == "/api/v2/torrents/info":
            tag = request.url.params.get("tag")
            rows = [t for t in self.torrents if tag is None or tag in t["tags"].split(",")]
            return httpx.Response(200, text=json.dumps(rows))
        if path == "/api/v2/torrents/delete":
            gone = set(form["hashes"].split("|"))
            self.torrents = [t for t in self.torrents if t["hash"].lower() not in gone]
            return httpx.Response(200)
        return httpx.Response(404)


async def make(fake: FakeQBit, password: str = "goed"):
    client = httpx.AsyncClient(transport=httpx.MockTransport(fake))
    return client, QBit(client, "http://qbittorrent:8080/", "admin", password)


async def test_login_version_and_relogin() -> None:
    fake = FakeQBit()
    client, qbit = await make(fake)
    async with client:
        assert await qbit.version() == "v5.0.2"
        fake.expire()  # qBittorrent herstart: de sessie is weg
        assert await qbit.version() == "v5.0.2"
        assert fake.logins == 2


async def test_wrong_password_never_leaks() -> None:
    fake = FakeQBit()
    client, qbit = await make(fake, password="HEEL-GEHEIM")
    async with client:
        with pytest.raises(QBitError, match="weigert") as info:
            await qbit.version()
    assert "HEEL-GEHEIM" not in str(info.value)


async def test_category_add_list_delete() -> None:
    fake = FakeQBit()
    client, qbit = await make(fake)
    async with client:
        await qbit.ensure_category("platform", "/downloads/platform")
        await qbit.ensure_category("platform", "/downloads/ander")  # bestaat: aanpassen
        assert fake.categories == {"platform": "/downloads/ander"}

        await qbit.add("magnet:?xt=urn:btih:" + "a" * 40, tag="platform-7", category="platform")
        assert fake.added[0]["tags"] == "platform-7" and fake.added[0]["category"] == "platform"
        await qbit.add(torrent_file=b"d4:infod4:name1:xee", tag="platform-8", category="platform")
        assert fake.added[1]["file"]
        with pytest.raises(QBitError, match="weigert de torrent"):
            await qbit.add("kapot", tag="t", category="platform")
        with pytest.raises(ValueError):
            await qbit.add(tag="t", category="platform")

        fake.torrents = [
            {
                "hash": "A" * 40,
                "name": "Film",
                "state": "stalledDL",
                "progress": 0.4,
                "tags": "platform-7",
                "category": "platform",
                "size": 10,
            },
            {
                "hash": "b" * 40,
                "name": "Ander",
                "state": "uploading",
                "progress": 1,
                "tags": "iets-anders",
                "category": "",
            },
        ]
        mine = await qbit.torrents(tag="platform-7")
        assert [(t.hash, t.stage) for t in mine] == [("a" * 40, "vastgelopen")]
        await qbit.delete(["a" * 40], files=True)
        assert await qbit.torrents(tag="platform-7") == []


async def test_unreachable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("nee")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        qbit = QBit(client, "http://qbittorrent:8080", "admin", "x")
        with pytest.raises(QBitError, match="niet bereikbaar"):
            await qbit.version()


def test_bad_base_url() -> None:
    with pytest.raises(QBitError):
        QBit(httpx.AsyncClient(), "ftp://x", "a", "b")


def test_magnet_hash() -> None:
    assert magnet_hash("magnet:?xt=urn:btih:" + "AB" * 20 + "&dn=x") == "ab" * 20
    # base32 (32 tekens) wordt hex
    assert magnet_hash("magnet:?xt=urn:btih:" + "A" * 32) == "00" * 20
    assert magnet_hash("magnet:?dn=x") is None


def test_stages() -> None:
    stages = {
        s: to_torrent({"state": s}).stage
        for s in ("metaDL", "downloading", "stalledDL", "stoppedDL", "stalledUP", "error", "nieuw")
    }
    assert stages == {
        "metaDL": "metadata",
        "downloading": "downloaden",
        "stalledDL": "vastgelopen",
        "stoppedDL": "gestopt",
        "stalledUP": "klaar",
        "error": "fout",
        "nieuw": "wachten",
    }
