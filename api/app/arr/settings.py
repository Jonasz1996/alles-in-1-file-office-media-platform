"""De koppelingen van mediabeheer: Prowlarr en qBittorrent (fase 11, stap 2).

Opslag in de tabel setting (geen migratie nodig), rij `arr_settings`:

    {"prowlarr": {"url": "...", "key": <geheim>},
     "qbittorrent": {"url": "...", "username": "...", "password": <geheim>,
                     "category": "platform", "save_path": "/downloads"}}

Een geheim is versleuteld zoals de sleutel van TMDB (`media.providers.secrets.seal`, AES-GCM met
een sleutel uit het app-geheim), met als naam `arr.prowlarr` of `arr.qbittorrent`. Een geheim
staat nooit in een antwoord: `view()` zegt alleen of het er is. Wie opslaat zonder nieuw geheim,
houdt het oude.

Opslaan test eerst de verbinding (wie niet kan testen, bewaart niet), net als bij TMDB.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.arr import prowlarr, qbit
from app.config import Settings
from app.media.providers.secrets import seal, unseal
from app.models import Setting

SETTING = "arr_settings"
DEFAULT_CATEGORY = "platform"
DEFAULT_SAVE_PATH = "/downloads"


@dataclass(frozen=True)
class ProwlarrConf:
    url: str
    key: str


@dataclass(frozen=True)
class QBitConf:
    url: str
    username: str
    password: str
    category: str = DEFAULT_CATEGORY
    save_path: str = DEFAULT_SAVE_PATH


def http_client() -> httpx.AsyncClient:
    """De client naar Prowlarr en qBittorrent (tests vervangen deze functie)."""
    return httpx.AsyncClient(trust_env=False)


async def _raw(session: AsyncSession) -> dict[str, Any]:
    row = await session.get(Setting, SETTING)
    return dict(row.value) if row is not None and isinstance(row.value, dict) else {}


async def _write(session: AsyncSession, value: dict[str, Any]) -> None:
    row = await session.get(Setting, SETTING)
    if row is None:
        session.add(Setting(key=SETTING, value=value))
    else:
        row.value = value
    await session.commit()


async def load_prowlarr(session: AsyncSession, settings: Settings) -> ProwlarrConf | None:
    entry = (await _raw(session)).get("prowlarr")
    if not isinstance(entry, dict) or not entry.get("url"):
        return None
    key = unseal(settings, "arr.prowlarr", entry.get("key"))
    return ProwlarrConf(entry["url"], key) if key else None


async def load_qbit(session: AsyncSession, settings: Settings) -> QBitConf | None:
    entry = (await _raw(session)).get("qbittorrent")
    if not isinstance(entry, dict) or not entry.get("url"):
        return None
    password = unseal(settings, "arr.qbittorrent", entry.get("password"))
    if password is None:
        return None
    return QBitConf(
        url=entry["url"],
        username=str(entry.get("username", "")),
        password=password,
        category=str(entry.get("category") or DEFAULT_CATEGORY),
        save_path=str(entry.get("save_path") or DEFAULT_SAVE_PATH),
    )


async def view(session: AsyncSession, settings: Settings) -> dict[str, Any]:
    """Wat /beheer toont: adressen en namen, nooit een geheim."""
    raw = await _raw(session)
    p = raw.get("prowlarr") if isinstance(raw.get("prowlarr"), dict) else {}
    q = raw.get("qbittorrent") if isinstance(raw.get("qbittorrent"), dict) else {}
    return {
        "prowlarr": {
            "url": p.get("url"),
            "key_set": unseal(settings, "arr.prowlarr", p.get("key")) is not None,
        },
        "qbittorrent": {
            "url": q.get("url"),
            "username": q.get("username"),
            "password_set": unseal(settings, "arr.qbittorrent", q.get("password")) is not None,
            "category": q.get("category") or DEFAULT_CATEGORY,
            "save_path": q.get("save_path") or DEFAULT_SAVE_PATH,
        },
    }


async def check_prowlarr(conf: ProwlarrConf) -> list[prowlarr.Indexer]:
    async with http_client() as client:
        return await prowlarr.indexers(client, conf.url, conf.key)


async def check_qbit(conf: QBitConf) -> str:
    async with http_client() as client:
        q = qbit.QBit(client, conf.url, conf.username, conf.password)
        version = await q.version()
        await q.ensure_category(conf.category, conf.save_path)
        return version


async def save_prowlarr(
    session: AsyncSession, settings: Settings, url: str, key: str | None
) -> list[prowlarr.Indexer]:
    url = prowlarr.check_base(url)
    if not key:
        old = await load_prowlarr(session, settings)
        if old is None:
            raise prowlarr.ProwlarrError("de API-sleutel ontbreekt")
        key = old.key
    found = await check_prowlarr(ProwlarrConf(url, key))
    await store_prowlarr(session, settings, ProwlarrConf(url, key))
    return found


async def store_prowlarr(session: AsyncSession, settings: Settings, conf: ProwlarrConf) -> None:
    """Bewaren zonder test (arr-koppel, vóór de container draait)."""
    raw = await _raw(session)
    raw["prowlarr"] = {
        "url": prowlarr.check_base(conf.url),
        "key": seal(settings, "arr.prowlarr", conf.key),
    }
    await _write(session, raw)


async def save_qbit(
    session: AsyncSession,
    settings: Settings,
    *,
    url: str,
    username: str,
    password: str | None,
    category: str,
    save_path: str,
) -> str:
    if not password:
        old = await load_qbit(session, settings)
        if old is None:
            raise qbit.QBitError("het wachtwoord ontbreekt")
        password = old.password
    conf = QBitConf(url.rstrip("/"), username, password, category, save_path)
    version = await check_qbit(conf)
    await store_qbit(session, settings, conf)
    return version


async def store_qbit(session: AsyncSession, settings: Settings, conf: QBitConf) -> None:
    """Bewaren zonder test (arr-koppel, vóór de container draait)."""
    raw = await _raw(session)
    raw["qbittorrent"] = {
        "url": conf.url.rstrip("/"),
        "username": conf.username,
        "password": seal(settings, "arr.qbittorrent", conf.password),
        "category": conf.category,
        "save_path": conf.save_path,
    }
    await _write(session, raw)


async def forget(session: AsyncSession, which: str) -> None:
    raw = await _raw(session)
    raw.pop(which, None)
    await _write(session, raw)
