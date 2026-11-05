"""M8 en M10 samen (bij het samenvoegen van golf 5): /beheer → Media met de echte sleutelkluis van
M8 (providers/secrets.py) en de echte staat van de uitgaande client (providers/http.py), tegen de
nep-providers in het proces. In test_admin_media.py staat M8 als nep (FakeSecrets, voor elke
fout op bestelling); hier niet.

- PUT …/tmdb met een foute sleutel: 422 media_provider_auth, niets in de rij `media_secrets`;
  met de goede: bewaard (versleuteld), het overzicht zegt "gezet", en de sleutel staat in geen
  enkel antwoord. Meteen daarna een taak media.lookup voor een online bibliotheek met werk (niet
  pas na de housekeeping van 5 minuten).
- Een 401 bij het opzoeken (de sleutel ingetrokken bij TMDB): de melding media_provider_auth en
  "auth" als laatste fout op /beheer; Testen met een werkende sleutel zet hem weer open.
- 24 uur onbereikbaar (de wandklok van http.py): media_provider_failing op /beheer; weer
  bereikbaar: weg.
- MusicBrainz aan via PUT …/config: providers.status() en lookup zien het meteen.
"""

from __future__ import annotations

import datetime as dt
import json
import uuid

import pytest
from sqlalchemy import select

from app import jobs
from app.media import lookup, providers
from app.media.providers import http, musicbrainz, secrets
from app.models import Job, MediaAlbum, Setting
from tests.media.fake_providers import kit
from tests.test_media_contract import media_app  # noqa: F401
from tests.test_media_lookup import NEPHAVEN, films, make_library, run
from tests.test_media_lookup import net as net  # noqa: F401
from tests.test_media_scanner import items
from tests.test_media_scanner import world as world  # noqa: F401
from tests.test_media_sessions import maker

pytestmark = pytest.mark.anyio

BASE = "/api/v1/admin/media"


def code(r) -> str | None:
    detail = r.json().get("detail")
    return detail.get("code") if isinstance(detail, dict) else None


async def overview(client, headers) -> dict:
    r = await client.get(BASE, headers=headers)
    assert r.status_code == 200, r.text
    assert kit.KEY not in r.text
    return r.json()


def tmdb_row(body: dict) -> dict:
    [tmdb] = [p for p in body["providers"] if p["name"] == "tmdb"]
    return tmdb


def provider_notices(body: dict) -> list[str]:
    return [n["code"] for n in body["notices"] if n["code"].startswith("media_provider")]


async def stored(settings) -> str:
    async with maker(settings)() as session:
        row = await session.get(Setting, secrets.SETTING)
        return json.dumps(row.value) if row is not None else ""


async def lookup_jobs(settings) -> list[Job]:
    async with maker(settings)() as session:
        return list((await session.scalars(select(Job).where(Job.type == lookup.JOB))).all())


async def test_de_sleutel_via_beheer_met_de_echte_kluis(net, client):  # noqa: F811
    headers = net.headers
    library_id = await films(client, net, [NEPHAVEN])  # online, nog lokaal
    assert await lookup_jobs(net.settings) == []

    # Een foute sleutel: TMDB zegt 401, er is niets bewaard
    wrong = "een-foute-sleutel-" + "w" * 24
    r = await client.put(f"{BASE}/tmdb", json={"key": wrong}, headers=headers)
    assert r.status_code == 422 and code(r) == "media_provider_auth", r.text
    assert wrong not in r.text
    assert [e["status"] for e in net.fake.log if e["service"] == "tmdb"] == [401]
    assert "tmdb" not in await stored(net.settings)
    body = await overview(client, headers)
    assert tmdb_row(body)["key_set"] is False and provider_notices(body) == []
    net.fake.log.clear()

    # De goede: bewaard en versleuteld, het overzicht zegt "gezet", en meteen een taak
    r = await client.put(f"{BASE}/tmdb", json={"key": kit.KEY}, headers=headers)
    assert r.status_code == 204 and r.content == b""
    raw = await stored(net.settings)
    assert "tmdb" in raw and kit.KEY not in raw
    body = await overview(client, headers)
    assert tmdb_row(body)["key_set"] is True and tmdb_row(body)["last_error"] is None
    assert provider_notices(body) == []
    [job] = await lookup_jobs(net.settings)
    assert job.payload == {"library_id": library_id} and job.status == "queued"
    # alleen met de kop Authorization, nooit in een adres
    to_tmdb = [e for e in net.fake.log if e["service"] == "tmdb"]
    assert to_tmdb and all(e["auth"] == "bearer" and e["auth_ok"] for e in to_tmdb)
    assert all("api_key" not in e["query"] for e in net.fake.log)

    r = await client.post(f"{BASE}/tmdb/test", headers=headers)
    assert r.status_code == 204
    r = await client.delete(f"{BASE}/tmdb", headers=headers)
    assert r.status_code == 204
    assert "tmdb" not in await stored(net.settings)
    assert tmdb_row(await overview(client, headers))["key_set"] is False


async def test_een_401_bij_het_opzoeken_staat_op_beheer(net, client):  # noqa: F811
    headers = net.headers
    r = await client.put(f"{BASE}/tmdb", json={"key": kit.KEY}, headers=headers)
    assert r.status_code == 204
    library_id = await films(client, net, [NEPHAVEN])

    # Ingetrokken bij TMDB: het opzoeken zet de sleutel dicht (M8), /beheer meldt het (M10)
    net.fake.config.tmdb_key = "een-andere-sleutel-" + "x" * 20
    await lookup.run(net.settings, {"library_id": library_id})
    assert (await items(net.settings, library_id))[NEPHAVEN].match_state == "local"
    body = await overview(client, headers)
    assert provider_notices(body) == ["media_provider_auth"]
    assert tmdb_row(body)["key_set"] is True and tmdb_row(body)["last_error"] == "auth"
    r = await client.get("/api/v1/admin/status", headers=headers)
    assert "media_provider_auth" in [n["code"] for n in r.json()["notices"]]
    assert kit.KEY not in r.text

    # Testen met de oude sleutel: nog steeds dicht
    r = await client.post(f"{BASE}/tmdb/test", headers=headers)
    assert r.status_code == 422 and code(r) == "media_provider_auth"

    # TMDB kent hem weer (of Jonas zette een nieuwe): Testen zet hem open, en het opzoeken loopt
    net.fake.config.tmdb_key = kit.KEY
    r = await client.post(f"{BASE}/tmdb/test", headers=headers)
    assert r.status_code == 204
    body = await overview(client, headers)
    assert provider_notices(body) == [] and tmdb_row(body)["last_error"] is None
    await run(net.settings)
    assert (await items(net.settings, library_id))[NEPHAVEN].match_state == "matched"


async def test_24_uur_onbereikbaar_staat_op_beheer(net, client):  # noqa: F811
    headers = net.headers
    r = await client.put(f"{BASE}/tmdb", json={"key": kit.KEY}, headers=headers)
    assert r.status_code == 204
    library_id = await films(client, net, [NEPHAVEN])

    # De eerste fout 25 uur geleden (de wandklok van http.py); de monitor kijkt met de echte klok
    net.wall.now = dt.datetime.now(dt.UTC) - dt.timedelta(hours=25)
    net.fake.fault("500", host="tmdb", times=None)
    with pytest.raises(jobs.Defer):
        await lookup.run(net.settings, {"library_id": library_id})
    body = await overview(client, headers)
    assert provider_notices(body) == ["media_provider_failing"]
    assert tmdb_row(body)["last_error"]  # een vaste reden van http.py
    assert body["notices"][-1]["params"] == {"provider": "tmdb"}

    # Weer bereikbaar (na de stroomonderbreker): de melding is weg
    net.fake.clear_faults()
    net.clock.now += http.BREAKER_OPEN + 1
    net.wall.now = dt.datetime.now(dt.UTC)
    await lookup.run(net.settings, {"library_id": library_id})
    assert (await items(net.settings, library_id))[NEPHAVEN].match_state == "matched"
    body = await overview(client, headers)
    assert provider_notices(body) == [] and tmdb_row(body)["last_ok"] is not None


async def test_musicbrainz_aan_op_beheer(net, client):  # noqa: F811
    headers = net.headers
    library_id, _folder = await make_library(client, headers, "Muziek", "music")
    async with maker(net.settings)() as session:
        session.add(
            MediaAlbum(library_id=uuid.UUID(library_id), key="nep orkest|zeegezichten",
                       title="Zeegezichten", sort_title="zeegezichten",
                       album_artist="Nep Orkest", track_count=10)
        )  # fmt: skip
        await session.commit()
    # Standaard uit (Keuze 41): niets naar buiten, geen taak
    assert musicbrainz.enabled() is False
    assert providers.status()["musicbrainz"]["configured"] is False
    assert await lookup.queue_all(net.settings) == 0

    r = await client.put(f"{BASE}/config", json={"musicbrainz": True}, headers=headers)
    assert r.status_code == 200, r.text
    assert musicbrainz.enabled() is True
    assert providers.status()["musicbrainz"]["configured"] is True
    [job] = await lookup_jobs(net.settings)  # meteen, niet pas bij de housekeeping
    assert job.payload == {"library_id": library_id}
    await run(net.settings)
    async with maker(net.settings)() as session:
        album = (await session.scalars(select(MediaAlbum))).one()
    assert album.match_state == "matched" and "cover" in album.art
    assert {e["service"] for e in net.fake.log} >= {"musicbrainz", "caa"}

    r = await client.put(f"{BASE}/config", json={"musicbrainz": False}, headers=headers)
    assert r.status_code == 200
    assert musicbrainz.enabled() is False
