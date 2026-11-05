"""De sleutel van TMDB, versleuteld in de tabel setting (Keuze 40).

- Opslag: `Setting "media_secrets"`, als `{"tmdb": {"v": 1, "nonce", "ct", "set_at", "ok"}}`.
  AES-GCM (256 bit) met als sleutel `HKDF-SHA256(app-geheim, info="platform media secrets
  v1")`, en de naam van het geheim als associated data. Dat beschermt tegen een gelekte kopie
  van `platform.db` zonder het app-geheim; tegen iemand met de hele `/data` niet.
- Opslaan (`save_tmdb`) test de sleutel eerst (`GET /3/configuration`). Werkt hij niet:
  ProviderAuth (422 media_provider_auth) en er wordt niets bewaard. Is TMDB onbereikbaar:
  ProviderUnavailable, ook dan niets bewaard (wie niet kan testen, bewaart niet).
- Een latere 401 (`mark_tmdb(ok=False)`): de sleutel blijft staan met `ok: false`; TMDB gaat dicht
  (`usable()` is False) tot er een nieuwe sleutel is. /beheer toont media_provider_auth.
- De sleutel staat nooit in een antwoord (alleen `status()`: `{"set", "set_at", "ok"}`), nooit
  in een log, nooit in een adres (alleen in de kop `Authorization: Bearer` naar TMDB) en nooit in
  een foutmelding: elke fout hier is een vaste tekst.

De app leest de rij opnieuw bij elke vraag (`load`, een opzoeking op de primaire sleutel); het
ontsleutelen gebeurt één keer per versie. Zo ziet de app ook een sleutel die een ander proces
(de cli, of de e2e) zette. `tmdb_key()` (het contract van M0) geeft wat de laatste `load` las.
"""

from __future__ import annotations

import base64
import binascii
import datetime as dt
import hmac
import os
import re
from collections.abc import Awaitable, Callable
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.media.providers.base import ProviderAuth
from app.models import Setting

SETTING = "media_secrets"
INFO = b"platform media secrets v1"
VERSION = 1
TMDB = "tmdb"
# Een "API Read Access Token" van TMDB is een JWT: base64url met punten. Alles daarbuiten (een
# spatie, een regeleinde) gaat nooit naar buiten: het zou in een kop en in een foutmelding van
# httpx belanden.
KEY_FORMAT = re.compile(r"^[A-Za-z0-9._~+/=-]{16,1000}$")
# De korte "API Key" (v3) van TMDB: 32 hex. Die werkt alleen als ?api_key=… in het adres, en dat
# doet het platform nooit (de sleutel zou in elke log van een adres staan); als Bearer geeft TMDB
# er 401 op. Daarom geweigerd zonder verzoek naar buiten (review golf 5, K2)
V3_KEY = re.compile(r"^[0-9a-fA-F]{32}$")

Check = Callable[[str], Awaitable[None]]


def _aead(settings: Settings) -> AESGCM:
    key = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=INFO).derive(
        settings.secret().encode()
    )
    return AESGCM(key)


def seal(settings: Settings, name: str, value: str) -> dict[str, Any]:
    """Een geheim versleuteld, met een nieuwe nonce."""
    nonce = os.urandom(12)
    ct = _aead(settings).encrypt(nonce, value.encode(), name.encode())
    return {
        "v": VERSION,
        "nonce": base64.b64encode(nonce).decode(),
        "ct": base64.b64encode(ct).decode(),
    }


def unseal(settings: Settings, name: str, entry: Any) -> str | None:
    """Het geheim, of None (geen, een andere versie, of niet te ontsleutelen: een ander
    app-geheim, of een andere naam)."""
    if not isinstance(entry, dict) or entry.get("v") != VERSION:
        return None
    try:
        nonce = base64.b64decode(str(entry["nonce"]), validate=True)
        ct = base64.b64decode(str(entry["ct"]), validate=True)
        return _aead(settings).decrypt(nonce, ct, name.encode()).decode()
    except (KeyError, ValueError, binascii.Error, InvalidTag, UnicodeDecodeError):
        return None


class _Memory:
    """Wat de laatste load las: de sleutel en zijn staat."""

    def __init__(self) -> None:
        self.ct: str | None = None
        self.key: str | None = None
        self.set_at: str | None = None
        self.ok: bool | None = None

    def clear(self) -> None:
        self.ct = self.key = self.set_at = None
        self.ok = None


_memory = _Memory()


def _iso(value: dt.datetime) -> str:
    return value.astimezone(dt.UTC).isoformat().replace("+00:00", "Z")


async def _entry(session: AsyncSession) -> tuple[Setting | None, dict[str, Any] | None]:
    row = await session.get(Setting, SETTING, populate_existing=True)
    value = row.value if row is not None and isinstance(row.value, dict) else {}
    entry = value.get(TMDB)
    return row, entry if isinstance(entry, dict) else None


async def load(session: AsyncSession, settings: Settings) -> str | None:
    """De sleutel van TMDB uit de database (en in het geheugen voor `tmdb_key`)."""
    _, entry = await _entry(session)
    if entry is None:
        _memory.clear()
        return None
    ct = str(entry.get("ct") or "")
    if ct != _memory.ct:
        _memory.key = unseal(settings, TMDB, entry)
        _memory.ct = ct
    set_at = entry.get("set_at")
    ok = entry.get("ok")
    _memory.set_at = set_at if isinstance(set_at, str) else None
    _memory.ok = ok if isinstance(ok, bool) else None
    return _memory.key


def tmdb_key() -> str | None:
    """De sleutel van TMDB zoals de laatste `load` hem las (het contract van M0)."""
    return _memory.key


def status_memory() -> dict[str, Any]:
    """Wat de laatste load las, zonder de database (synchroon, voor providers.status)."""
    return {"set": _memory.key is not None, "set_at": _memory.set_at, "ok": _memory.ok}


def usable() -> bool:
    """Is er een sleutel die (nog) werkt? Na een 401 niet meer, tot een nieuwe sleutel."""
    return _memory.key is not None and _memory.ok is not False


async def status(session: AsyncSession, settings: Settings) -> dict[str, Any]:
    """Voor /beheer (M10): `{"set", "set_at", "ok"}`, nooit de sleutel."""
    key = await load(session, settings)
    return {"set": key is not None, "set_at": _memory.set_at, "ok": _memory.ok}


async def _write(session: AsyncSession, entry: dict[str, Any] | None) -> None:
    row, _ = await _entry(session)
    value = dict(row.value) if row is not None and isinstance(row.value, dict) else {}
    if entry is None:
        value.pop(TMDB, None)
    else:
        value[TMDB] = entry
    if row is None:
        session.add(Setting(key=SETTING, value=value))
    else:
        row.value = value
    await session.commit()


def _default_check(settings: Settings) -> Check:
    async def check(key: str) -> None:
        from app.media.providers import tmdb

        await tmdb.check_key(key, settings)

    return check


async def save_tmdb(
    session: AsyncSession,
    settings: Settings,
    key: str,
    *,
    check: Check | None = None,
    now: dt.datetime | None = None,
) -> None:
    """Een nieuwe sleutel: eerst testen, dan versleuteld bewaren (commit). ProviderAuth (werkt
    niet, of geen geldige vorm) of ProviderUnavailable (niet te testen): niets bewaard. Tijdens
    het testen staat er geen transactie open (regel 8)."""
    key = key.strip()
    if not KEY_FORMAT.match(key):
        raise ProviderAuth("tmdb: de sleutel heeft niet de vorm van een token")
    if V3_KEY.match(key):
        raise ProviderAuth("tmdb: dit is de korte API Key; neem het API Read Access Token")
    await session.rollback()
    await (check or _default_check(settings))(key)
    entry = {**seal(settings, TMDB, key), "set_at": _iso(now or dt.datetime.now(dt.UTC))}
    entry["ok"] = True
    await _write(session, entry)
    _memory.clear()
    await load(session, settings)


async def forget_tmdb(session: AsyncSession) -> None:
    """De sleutel weg (DELETE /api/v1/admin/media/tmdb)."""
    await _write(session, None)
    _memory.clear()


async def mark_tmdb(
    session: AsyncSession, settings: Settings, ok: bool, *, used: str | None = None
) -> None:
    """De sleutel werkt (niet) meer: na een 401 dicht tot een nieuwe sleutel, na een geslaagde
    test weer open. `used`: de sleutel waarmee het verzoek ging. Is de bewaarde sleutel intussen
    een andere (Jonas bewaarde een nieuwe terwijl een verzoek met de oude liep), dan zegt een
    late 401 niets over de nieuwe: niets veranderen (review golf 5, C7)."""
    _, entry = await _entry(session)
    if entry is not None and used is not None:
        current = unseal(settings, TMDB, entry)
        if current is None or not hmac.compare_digest(current.encode(), used.encode()):
            await load(session, settings)
            return
    if entry is not None and entry.get("ok") is not ok:
        await _write(session, {**entry, "ok": ok})
    await load(session, settings)


async def verify_tmdb(
    session: AsyncSession, settings: Settings, *, check: Check | None = None
) -> None:
    """De knop "Testen" (POST /api/v1/admin/media/tmdb/test): ProviderAuth zonder sleutel of als
    hij niet werkt (dan ok: false), ProviderUnavailable als TMDB onbereikbaar is."""
    key = await load(session, settings)
    if key is None:
        raise ProviderAuth("tmdb: er is geen sleutel")
    await session.rollback()  # regel 8: geen transactie open tijdens het testen
    try:
        await (check or _default_check(settings))(key)
    except ProviderAuth:
        await mark_tmdb(session, settings, False, used=key)
        raise
    await mark_tmdb(session, settings, True, used=key)


def reset() -> None:
    """Tests: het geheugen leeg."""
    _memory.clear()
