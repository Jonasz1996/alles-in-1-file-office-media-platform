"""De sleutel van TMDB (providers/secrets.py, Keuze 40, M8).

- AES-GCM met een sleutel via HKDF uit het app-geheim: terug te lezen met hetzelfde geheim, niet
  met een ander, niet onder een andere naam, niet na knoeien;
- een foute sleutel (401 van de nep-TMDB), een sleutel in een verkeerde vorm, of TMDB onbereikbaar:
  niets bewaard;
- de sleutel staat niet in de tabel (alleen versleuteld), niet in een log (caplog, op DEBUG, ook
  httpx), niet in een foutmelding (ook niet als de fout van httpx hem zou noemen), niet in de
  status voor /beheer en niet in een adres: de nep-TMDB zag hem alleen als `Authorization:
  Bearer`;
- na een 401 gaat TMDB dicht tot een nieuwe sleutel (`mark_tmdb`), en "Testen" zet hem weer open.

De access-log van de server (uvicorn) staat in de e2e (test_media_m8.py): die draait de echte
server en zoekt de sleutel in zijn log.
"""

from __future__ import annotations

import json
import logging
import traceback

import httpx
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app import db
from app.config import Settings
from app.media.providers import http, secrets, tmdb
from app.media.providers.base import ProviderAuth, ProviderUnavailable
from app.models import Setting
from tests.media.fake_providers import FakeConfig, create_app, kit, load

pytestmark = pytest.mark.anyio

KEY = kit.KEY


@pytest.fixture(autouse=True)
def _schoon():
    secrets.reset()
    previous = http.use(None)
    yield
    http.use(previous)
    secrets.reset()


@pytest.fixture
def fake():
    return create_app(FakeConfig(tmdb_key=KEY, data=load()))


@pytest.fixture
def out(settings, fake):
    o = kit.outbound(settings, fake)
    http.use(o)
    return o


@pytest.fixture
async def session(settings):
    maker = async_sessionmaker(db.get_engine(settings), expire_on_commit=False)
    async with maker() as s:
        yield s


def everything(exc: BaseException) -> str:
    """Alles wat een fout over zichzelf kan vertellen: tekst, repr en de traceback (met de
    keten)."""
    return "\n".join([str(exc), repr(exc), "".join(traceback.format_exception(exc))])


async def stored(session) -> str:
    row = await session.get(Setting, secrets.SETTING, populate_existing=True)
    return json.dumps(row.value) if row is not None else ""


# --- versleutelen ---


async def test_heen_en_terug(settings):
    entry = secrets.seal(settings, "tmdb", KEY)
    assert KEY not in json.dumps(entry)
    assert secrets.unseal(settings, "tmdb", entry) == KEY
    # elke keer een nieuwe nonce
    assert secrets.seal(settings, "tmdb", KEY)["ct"] != entry["ct"]


async def test_een_ander_app_geheim_of_een_andere_naam_leest_niets(settings, tmp_path):
    entry = secrets.seal(settings, "tmdb", KEY)
    other = Settings(data_dir=tmp_path / "ander", web_dist=tmp_path / "web", jwt_secret="z" * 32)
    assert secrets.unseal(other, "tmdb", entry) is None
    assert secrets.unseal(settings, "iets-anders", entry) is None


@pytest.mark.parametrize(
    "change",
    [
        lambda e: {**e, "v": 2},
        lambda e: {**e, "ct": e["ct"][:-4] + "AAAA"},
        lambda e: {**e, "nonce": "!!"},
        lambda e: {k: v for k, v in e.items() if k != "nonce"},
        lambda e: "geen dict",
        lambda e: None,
    ],
)
async def test_geknoeid_leest_niets(settings, change):
    entry = secrets.seal(settings, "tmdb", KEY)
    assert secrets.unseal(settings, "tmdb", change(entry)) is None


# --- opslaan ---


async def test_een_goede_sleutel_wordt_versleuteld_bewaard(settings, session, out, fake):
    await secrets.save_tmdb(session, settings, KEY)
    text = await stored(session)
    assert text and KEY not in text
    assert secrets.tmdb_key() == KEY and secrets.usable()
    status = await secrets.status(session, settings)
    assert status["set"] is True and status["ok"] is True and status["set_at"]
    assert KEY not in json.dumps(status)
    # De nep-TMDB zag de sleutel alleen als Bearer, en hij klopte; niet in het pad of de query
    log = fake.state.fake.log
    assert [e["inner"] for e in log] == ["/3/configuration"]
    assert log[0]["auth"] == "bearer" and log[0]["auth_ok"] is True
    assert KEY not in json.dumps(log)


async def test_een_foute_sleutel_wordt_niet_bewaard(settings, session, out, fake):
    wrong = "verkeerde-sleutel-" + "y" * 20
    with pytest.raises(ProviderAuth) as caught:
        await secrets.save_tmdb(session, settings, wrong)
    assert wrong not in everything(caught.value)
    assert await stored(session) == ""
    assert secrets.tmdb_key() is None and not secrets.usable()
    assert (await secrets.status(session, settings))["set"] is False
    assert fake.state.fake.log[0]["status"] == 401


async def test_een_foute_sleutel_vervangt_de_goede_niet(settings, session, out):
    await secrets.save_tmdb(session, settings, KEY)
    before = await stored(session)
    with pytest.raises(ProviderAuth):
        await secrets.save_tmdb(session, settings, "verkeerde-sleutel-" + "y" * 20)
    assert await stored(session) == before
    assert await secrets.load(session, settings) == KEY


@pytest.mark.parametrize(
    "key",
    [
        "kort",
        KEY + " met spatie",
        KEY + "\nX-Kop: ja",
        "é" * 20,
        # de korte API Key (v3) van TMDB: als Bearer geeft TMDB er 401 op (review golf 5, K2)
        "0123456789abcdef0123456789ABCDEF",
    ],
)
async def test_een_sleutel_in_een_verkeerde_vorm_gaat_niet_naar_buiten(
    settings, session, out, fake, key
):
    with pytest.raises(ProviderAuth) as caught:
        await secrets.save_tmdb(session, settings, key)
    assert key.strip() not in everything(caught.value)
    assert fake.state.fake.log == []
    assert await stored(session) == ""


async def test_onbereikbaar_bewaart_niets(settings, session, out, fake):
    fake.state.fake.fault("drop", "/3/configuration", times=10)
    with pytest.raises(ProviderUnavailable) as caught:
        await secrets.save_tmdb(session, settings, KEY)
    assert KEY not in everything(caught.value)
    assert await stored(session) == ""


async def test_een_fout_van_httpx_met_de_sleutel_lekt_niet(settings, session):
    """Ook als een fout van httpx de kop zou noemen: wat naar buiten komt is een vaste tekst."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"stuk: {request.headers['authorization']}", request=request)

    clock = kit.Clock()
    http.use(
        http.Outbound(
            settings,
            resolver=kit.Resolver(),
            transport=httpx.MockTransport(handler),
            clock=clock,
            sleep=clock.sleep,
        )
    )
    with pytest.raises(ProviderUnavailable) as caught:
        await secrets.save_tmdb(session, settings, KEY)
    assert str(caught.value) == "tmdb: geen verbinding"
    assert KEY not in everything(caught.value)


async def test_de_sleutel_staat_in_geen_enkele_logregel(settings, session, out, fake, caplog):
    caplog.set_level(logging.DEBUG)
    await secrets.save_tmdb(session, settings, KEY)
    provider = tmdb.Tmdb(settings, KEY)
    await provider.search("movie", "De Nephaven", None, "nl-NL")
    fake.state.fake.fault("500", "/3/search/movie", times=10)
    with pytest.raises(ProviderUnavailable):
        await provider.search("movie", "De Nephaven", None, "nl-NL")
    with pytest.raises(ProviderAuth):
        await secrets.save_tmdb(session, settings, "verkeerde-sleutel-" + "y" * 20)
    assert caplog.records  # er is wel gelogd (de regels van http.py en httpx)
    for record in caplog.records:
        assert KEY not in record.getMessage()
        assert KEY not in repr(record.args)
    assert KEY not in repr(provider)


# --- dicht na een 401, open na een test ---


async def test_na_een_401_dicht_tot_testen(settings, session, out, fake):
    await secrets.save_tmdb(session, settings, KEY)
    await secrets.mark_tmdb(session, settings, False)
    assert not secrets.usable()
    assert (await secrets.status(session, settings))["ok"] is False
    # de sleutel blijft wel staan
    assert secrets.tmdb_key() == KEY
    await secrets.verify_tmdb(session, settings)
    assert secrets.usable()
    assert (await secrets.status(session, settings))["ok"] is True


async def test_een_late_401_van_de_oude_sleutel_laat_de_nieuwe_open(settings, session):
    """Review golf 5, C7: een verzoek met de oude sleutel liep nog toen Jonas een nieuwe bewaarde;
    de 401 daarvan zette de nieuwe dicht. Nu telt een 401 alleen voor de sleutel waarmee het
    verzoek ging."""

    async def works(key: str) -> None:
        return None

    old, new = "a" * 40, "b" * 40
    await secrets.save_tmdb(session, settings, old, check=works)
    await secrets.save_tmdb(session, settings, new, check=works)
    await secrets.mark_tmdb(session, settings, False, used=old)
    assert secrets.usable()
    assert (await secrets.status(session, settings))["ok"] is True
    await secrets.mark_tmdb(session, settings, False, used=new)
    assert not secrets.usable()
    assert (await secrets.status(session, settings))["ok"] is False


async def test_testen_met_een_sleutel_die_niet_meer_werkt(settings, session, out, fake):
    await secrets.save_tmdb(session, settings, KEY)
    fake.state.fake.config.tmdb_key = "een-andere-sleutel-" + "z" * 20
    with pytest.raises(ProviderAuth):
        await secrets.verify_tmdb(session, settings)
    assert (await secrets.status(session, settings))["ok"] is False
    assert not secrets.usable()


async def test_testen_zonder_sleutel(settings, session, out):
    with pytest.raises(ProviderAuth):
        await secrets.verify_tmdb(session, settings)


async def test_vergeten(settings, session, out):
    await secrets.save_tmdb(session, settings, KEY)
    await secrets.forget_tmdb(session)
    assert (await secrets.status(session, settings)) == {"set": False, "set_at": None, "ok": None}
    assert secrets.tmdb_key() is None


async def test_een_sleutel_van_een_ander_proces_wordt_gelezen(settings, session, monkeypatch):
    """De cli of de e2e zet de rij; de app leest hem bij de volgende load, en ontsleutelt één keer
    per versie."""
    entry = {**secrets.seal(settings, "tmdb", KEY), "set_at": "2026-01-01T00:00:00Z", "ok": True}
    session.add(Setting(key=secrets.SETTING, value={"tmdb": entry}))
    await session.commit()
    calls = []
    real = secrets.unseal
    monkeypatch.setattr(secrets, "unseal", lambda *a: calls.append(1) or real(*a))
    assert await secrets.load(session, settings) == KEY
    assert await secrets.load(session, settings) == KEY
    assert len(calls) == 1
