"""Metadata van buiten (Keuze 39–44). base.py is het contract (M0); de rest is van M8.

Voor /beheer (M10) en de cli:
- `status()`: per provider of hij ingesteld is, de stroomonderbreker, laatste goed, laatste fout
  en `failing_since` (sinds wanneer hij onbereikbaar is; None als het laatste verzoek lukte).
  Nooit de sleutel.
- `views()`: hetzelfde als `ProviderView` (types.py) voor `MediaAdmin.providers`.
- `notices(now)`: de codes voor de meldingen van monitor.py: `media_provider_auth` (de sleutel
  van TMDB werkt niet meer) en `media_provider_failing` (een ingestelde provider is al 24 uur
  onbereikbaar). Eerder geen melding: offline werkt alles uit lokale gegevens.
- `check(settings, record=...)`: `app.cli media-provider-check [--opnemen]`.

Hier bovenaan niets zwaars: het renderproces van art.py importeert dit pakket.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

PROVIDERS = ("tmdb", "musicbrainz", "caa")
FAILING_AFTER = dt.timedelta(hours=24)
SHAPES = Path(__file__).resolve().parents[4] / "docs" / "fase-5" / "provider-shapes.json"


def _health(name: str) -> dict[str, Any]:
    from app.media.providers import http

    outbound = http.current()
    if outbound is None or name not in outbound.health:
        return http.Health().view()
    return outbound.health[name].view()


def _configured(name: str) -> bool:
    from app.media.providers import musicbrainz, secrets

    if name == "tmdb":
        return secrets.tmdb_key() is not None
    return musicbrainz.enabled()


def status() -> dict[str, dict]:
    """Per provider: ingesteld, of hij werkt, en de staat van http.py (voor /beheer)."""
    from app.media.providers import secrets

    out: dict[str, dict] = {}
    for name in PROVIDERS:
        health = _health(name)
        entry = {"configured": _configured(name), **health}
        if name == "tmdb":
            entry["key_ok"] = secrets.status_memory()["ok"]
            entry["set_at"] = secrets.status_memory()["set_at"]
        out[name] = entry
    return out


def views() -> list:
    """`MediaAdmin.providers` (types.ProviderView): nooit de sleutel, alleen of hij er is."""
    from app.media.types import ProviderView

    out = []
    for name, entry in status().items():
        error = entry.get("last_error")
        if name == "tmdb" and entry.get("key_ok") is False:
            error = "media_provider_auth"
        out.append(
            ProviderView(
                name=name,  # type: ignore[arg-type]
                key_set=bool(entry["configured"]),
                last_ok=entry.get("last_ok"),
                last_error=error,
                breaker=entry.get("breaker", "closed"),
            )
        )
    return out


def unreachable_since(name: str) -> dt.datetime | None:
    """Sinds wanneer deze provider onbereikbaar is (None: het laatste verzoek lukte, of er was
    er nog geen)."""
    return _health(name).get("failing_since")


def notices(now: dt.datetime | None = None) -> list[str]:
    """De codes van de meldingen voor /beheer (monitor.NOTICES): de sleutel werkt niet meer, of
    een ingestelde provider is al 24 uur onbereikbaar."""
    from app.media.providers import secrets

    now = now or dt.datetime.now(dt.UTC)
    out = []
    if secrets.tmdb_key() is not None and secrets.status_memory()["ok"] is False:
        out.append("media_provider_auth")
    for name in PROVIDERS:
        since = unreachable_since(name)
        if since is not None and _configured(name) and now - since >= FAILING_AFTER:
            out.append("media_provider_failing")
            break
    return out


# --- de cli: media-provider-check [--opnemen] --------------------------------------------------


def shape(value: Any, depth: int = 0) -> Any:
    """De vorm van een antwoord: sleutels en soorten, nooit waarden. Een lijst: de vorm van het
    eerste element."""
    if depth > 12:
        return "..."
    if isinstance(value, dict):
        return {str(k): shape(v, depth + 1) for k, v in sorted(value.items())}
    if isinstance(value, list):
        return [shape(value[0], depth + 1)] if value else []
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "str"
    return "null"


async def _config(settings) -> tuple[str | None, bool]:
    """De sleutel van TMDB en of MusicBrainz aan staat (uit de database, zonder app)."""
    from sqlalchemy import inspect
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.config import MediaConfig
    from app.db import get_engine
    from app.media.providers import secrets
    from app.media.state import MEDIA_KEY
    from app.models import Setting

    engine = get_engine(settings)
    async with engine.connect() as conn:
        # Een lege map (nog geen migraties): niets ingesteld, dus alles uit.
        ready = await conn.run_sync(lambda c: inspect(c).has_table(Setting.__tablename__))
    if not ready:
        return None, False
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        key = await secrets.load(session, settings)
        row = await session.get(Setting, MEDIA_KEY)
        config = MediaConfig.model_validate(row.value if row and row.value else {})
    return key, bool(config.musicbrainz)


def _state(error: BaseException | None) -> str:
    from app.media.providers.base import ProviderAuth

    if error is None:
        return "ok"
    return "auth" if isinstance(error, ProviderAuth) else "unavailable"


async def _try(fn) -> BaseException | None:
    from app.media.providers.base import ProviderAuth, ProviderRate, ProviderUnavailable

    try:
        await fn()
    except (ProviderAuth, ProviderUnavailable, ProviderRate) as e:
        return e
    return None


async def _record_tmdb(provider) -> dict[str, Any]:
    """Van elk eindpunt van TMDB één antwoord, alleen de vorm."""
    out: dict[str, Any] = {}
    get = provider._get
    out["configuration"] = shape(await get("/configuration"))
    found = await get("/search/movie", {"query": "the", "language": "en-US", "page": 1})
    out["search/movie"] = shape(found)
    movie = next(iter(found.get("results") or []), {}).get("id")
    if isinstance(movie, int):
        params = {"language": "en-US", "append_to_response": "external_ids"}
        details = await get(f"/movie/{movie}", params)
        out["movie/{id}"] = shape(details)
        imdb = (details.get("external_ids") or {}).get("imdb_id") or details.get("imdb_id")
        if isinstance(imdb, str) and imdb:
            params = {"external_source": "imdb_id", "language": "en-US"}
            out["find/{imdb_id}"] = shape(await get(f"/find/{imdb}", params))
    found = await get("/search/tv", {"query": "the", "language": "en-US", "page": 1})
    out["search/tv"] = shape(found)
    show = next(iter(found.get("results") or []), {}).get("id")
    if isinstance(show, int):
        params = {"language": "en-US", "append_to_response": "external_ids"}
        out["tv/{id}"] = shape(await get(f"/tv/{show}", params))
        out["tv/{id}/season/{n}"] = shape(await get(f"/tv/{show}/season/1", {"language": "en-US"}))
    return out


async def _record_musicbrainz(provider) -> dict[str, Any]:
    out: dict[str, Any] = {}
    found = await provider._get("/release/", {"query": 'release:"test"', "limit": 1})
    out["release (zoeken)"] = shape(found)
    first = next(iter(found.get("releases") or []), {}).get("id")
    if isinstance(first, str):
        inc = {"inc": "release-groups+artist-credits+media"}
        out["release/{mbid}"] = shape(await provider._get(f"/release/{first}", inc))
    return out


async def check(
    settings, *, record: bool = False, path: Path | None = None, outbound: Any = None
) -> dict[str, str]:
    """Voor app.cli media-provider-check: per provider ok, off (geen sleutel of uit), auth of
    unavailable. record (--opnemen, alleen met een echte sleutel): de vorm van elk antwoord
    naar docs/fase-5/provider-shapes.json (§4.4); nooit een waarde, nooit de sleutel.
    outbound: alleen voor tests (de client tegen de nep-providers); anders een eigen client."""
    from app import __version__
    from app.media.providers import caa, http, musicbrainz, tmdb

    key, mb_on = await _config(settings)
    own = outbound is None
    outbound = outbound or http.Outbound(settings)
    previous = http.use(outbound)
    result = dict.fromkeys(PROVIDERS, "off")
    shapes: dict[str, Any] = {}
    try:
        if key is not None:
            provider = tmdb.Tmdb(settings, key)
            result["tmdb"] = _state(await _try(provider.check))
            if record and result["tmdb"] == "ok":
                shapes["tmdb"] = await _record_tmdb(provider)
        if mb_on:
            mb = musicbrainz.MusicBrainz(settings)
            result["musicbrainz"] = _state(await _try(mb.check))
            if record and result["musicbrainz"] == "ok":
                shapes["musicbrainz"] = await _record_musicbrainz(mb)
            result["caa"] = _state(await _try(lambda: _caa(caa.CoverArt(settings), mb)))
    finally:
        http.use(previous)
        if own:
            await outbound.aclose()
    if record and shapes:
        target = path or SHAPES
        document = {"_opgenomen": dt.date.today().isoformat(), "_versie": __version__, **shapes}
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(document, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return result


async def _caa(cover, mb) -> None:
    """De CAA bereikbaar? De voorkant van één release (een 404 telt ook als bereikbaar)."""
    from app.media.providers import http

    found = await mb.search_album("test", None)
    if not found:
        return
    try:
        await cover.front(found[0].id)
    except http.NotFound:
        return
