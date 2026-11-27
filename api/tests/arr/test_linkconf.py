"""qBittorrent en Prowlarr vooraf instellen: arr-koppel (fase 11, stap 2b)."""

from __future__ import annotations

import base64
import hashlib

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.arr import cli as arr_cli
from app.arr import linkconf
from app.arr import settings as arr_settings
from app.db import get_engine

pytestmark = pytest.mark.anyio

EXISTING = """[Application]
FileLogger\\Enabled=true

[Preferences]
General\\Locale=nl
WebUI\\Port=9999
"""


def test_qbit_hash_format_matches_qbittorrent() -> None:
    salt = b"\x01" * 16
    stored = linkconf.qbit_hash("geheim", salt)
    key = hashlib.pbkdf2_hmac("sha512", b"geheim", salt, 100_000, dklen=64)
    assert (
        stored == f"@ByteArray({base64.b64encode(salt).decode()}:{base64.b64encode(key).decode()})"
    )
    assert linkconf.qbit_check("geheim", stored)
    assert not linkconf.qbit_check("ander", stored)


def test_qbit_conf_keeps_other_settings() -> None:
    text = linkconf.qbit_conf(
        EXISTING, username="platform", password="pw", save_path="/downloads", salt=b"\0" * 16
    )
    assert "FileLogger\\Enabled=true" in text and "General\\Locale=nl" in text
    assert "WebUI\\Port=8080" in text and "WebUI\\Port=9999" not in text
    assert "WebUI\\Username=platform" in text
    assert "[LegalNotice]\nAccepted=true" in text
    assert "Session\\TempPath=/downloads/bezig" in text
    assert "pw" not in text.replace("Password_PBKDF2", "")
    # twee keer geeft hetzelfde (met hetzelfde zout)
    again = linkconf.qbit_conf(
        text, username="platform", password="pw", save_path="/downloads", salt=b"\0" * 16
    )
    assert again == text


def test_prowlarr_conf() -> None:
    key = "0123456789abcdef0123456789abcdef"
    fresh = linkconf.prowlarr_conf("", key)
    assert linkconf.read_prowlarr_key(fresh) == key
    old = "f" * 32
    full = f"<Config>\n  <Port>9696</Port>\n  <ApiKey>{old}</ApiKey>\n</Config>"
    assert linkconf.read_prowlarr_key(linkconf.prowlarr_conf(full, key)) == key
    assert "<Port>9696</Port>" in linkconf.prowlarr_conf(full, key)
    with pytest.raises(ValueError):
        linkconf.prowlarr_conf("", "<x/>")


async def test_koppel_is_idempotent_and_never_prints_secrets(client, settings, capsys):
    lines = await arr_cli.koppel(
        settings, "http://qbittorrent:8080", "http://prowlarr:9696", "/downloads"
    )
    assert lines == [
        "qBittorrent: gekoppeld als gebruiker platform",
        "Prowlarr: nieuwe API-sleutel gezet",
        "Prowlarr: gekoppeld",
        "Lidarr: nieuwe API-sleutel gezet",
        "Lidarr: gekoppeld",
    ]
    data = settings.data_dir
    conf = (data / "arr/qbittorrent/qBittorrent/qBittorrent.conf").read_text()
    xml = (data / "arr/prowlarr/config.xml").read_text()
    assert (data / "import" / "torrents").is_dir()
    assert (data / "import" / "lidarr").is_dir()

    async with async_sessionmaker(get_engine(settings))() as session:
        q = await arr_settings.load_qbit(session, settings)
        p = await arr_settings.load_prowlarr(session, settings)
    stored = conf.split("WebUI\\Password_PBKDF2=", 1)[1].splitlines()[0]
    assert linkconf.qbit_check(q.password, stored)
    assert p.key == linkconf.read_prowlarr_key(xml) and p.url == "http://prowlarr:9696"
    for line in lines:
        assert q.password not in line and p.key not in line

    again = await arr_cli.koppel(
        settings, "http://qbittorrent:8080", "http://prowlarr:9696", "/downloads"
    )
    assert again == [
        "qBittorrent: al gekoppeld, niets veranderd",
        "Prowlarr: al gekoppeld, niets veranderd",
        "Lidarr: al gekoppeld, niets veranderd",
    ]
    assert (data / "arr/qbittorrent/qBittorrent/qBittorrent.conf").read_text() == conf
