"""De nep-providers van fase 5 (§3.6, §4.4): TMDB, het plaatjeshost van TMDB, MusicBrainz, de
Cover Art Archive en de tweede host waar die naar omleidt. M0 maakt de kern; M8 vult de data
(`data/*.json`) en breidt de eindpunten uit waar zijn clients dat vragen.

In het proces (API-tests):

    app = create_app(FakeConfig(tmdb_key=key, data=Data.from_documents(doc)))
    async with httpx.AsyncClient(transport=transport(app)) as c:
        await c.get("https://api.themoviedb.org/3/configuration", headers=...)

Als proces (e2e): `FakeProvidersProcess` in `process.py`, of `python -m tests.media.fake_providers`.
"""

from .app import BASE_URLS, FAULTS, HOSTS, FakeConfig, FakeProviders, Fault, create_app, transport
from .data import NEP, Data, DataError, load

__all__ = [
    "BASE_URLS",
    "FAULTS",
    "HOSTS",
    "NEP",
    "Data",
    "DataError",
    "FakeConfig",
    "FakeProviders",
    "Fault",
    "create_app",
    "load",
    "transport",
]
