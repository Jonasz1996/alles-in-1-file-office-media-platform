"""De meldingen van media op /beheer (M10, media/monitor.py): elke melding gaat aan zolang het zo
is, en vanzelf weer uit. Zonder media (env uit, of op /beheer uitgezet) zijn er geen.

De staat komt uit wat de andere pakketten bijhouden: MediaState (M1), cache.disk_low (M4),
providers.status() en secrets.status() (M8). Hier gezet met de hand, met een nepklok voor de 24
uur van een provider die faalt.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.config import MediaConfig
from app.main import app
from app.media import cache, monitor, providers
from app.media.state import MediaPolicy, MediaState
from app.media.types import WorkerCaps
from tests.media import fake_worker as FW

pytestmark = pytest.mark.anyio

NOW = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)


def caps(**changes) -> WorkerCaps:
    found = WorkerCaps.model_validate(FW.FakeWorker(Path("/niet/gebruikt.sock")).caps())
    return found.model_copy(update=changes)


@pytest.fixture
def state(monkeypatch) -> MediaState:
    """Media aan, de worker gezond in een container (afgeschermd), niets aan de hand."""
    s = MediaState(enabled=True, worker="ok", mode="container")
    s.caps = caps(mode="container", sandbox="on")
    monkeypatch.setattr(app.state, "media", s, raising=False)
    monkeypatch.setattr(app.state, "media_policy", MediaPolicy(MediaConfig()), raising=False)
    monkeypatch.setattr(cache, "disk_low", False)
    monkeypatch.setattr(providers, "status", lambda: {n: {} for n in providers.PROVIDERS})
    monkeypatch.setattr(monitor, "now", lambda: NOW)
    return s


async def codes() -> list[str]:
    return [n.code for n in await monitor.notices(app, None)]


async def test_niets_aan_de_hand_geen_meldingen(state):
    assert await codes() == []


async def test_zonder_media_geen_meldingen(state):
    state.set_worker("down", "weg")
    assert await codes() == ["media_worker_down"]
    app.state.media_policy.config = MediaConfig(enabled=False)  # op /beheer uitgezet
    assert await codes() == []
    app.state.media_policy.config = MediaConfig()
    state.enabled = False  # PLATFORM_MEDIA_ENABLED uit: precies 0.6.0
    assert await codes() == []


async def test_de_worker_is_weg(state):
    state.set_worker("down", "zelftest: seccomp ontbreekt")
    assert await codes() == ["media_worker_down"]  # alleen die: de rest zegt dan niets
    state.set_worker("ok")
    assert await codes() == []


async def test_ffmpeg_ontbreekt(state):
    state.set_worker("ffmpeg_missing", "ffmpeg ontbreekt")
    assert await codes() == ["media_ffmpeg_missing"]
    state.set_worker("ok")
    assert await codes() == []
    state.caps = caps(mode="container", sandbox="on", missing=["libx264"])
    assert await codes() == ["media_ffmpeg_missing"]
    state.caps = caps(mode="container", sandbox="on", ffmpeg=None)
    assert await codes() == ["media_ffmpeg_missing"]
    state.caps = caps(mode="container", sandbox="on")
    assert await codes() == []


async def test_zonder_afscherming(state):
    state.caps = caps(mode="local", sandbox="off")
    [notice] = await monitor.notices(app, None)
    assert notice.code == "media_sandbox_off" and notice.text == monitor.NOTICES[notice.code]
    # de lokale worker voordat de caps er zijn, maar niet tijdens het starten
    state.caps, state.mode = None, "local"
    assert await codes() == ["media_sandbox_off"]
    state.set_worker("starting")
    assert await codes() == []
    state.set_worker("ok")
    state.caps, state.mode = caps(mode="container", sandbox="on"), "container"
    assert await codes() == []
    state.caps = caps(mode="container", sandbox="unchecked")  # geen bewijs, wel geen melding
    assert await codes() == []


async def test_de_cache_is_vol(state, monkeypatch):
    monkeypatch.setattr(cache, "disk_low", True)
    assert await codes() == ["media_cache_full"]
    monkeypatch.setattr(cache, "disk_low", False)
    assert await codes() == []


async def test_een_provider_met_een_sleutel_die_niet_werkt(state, monkeypatch):
    monkeypatch.setattr(providers, "status", lambda: {"tmdb": {"auth": True, "failing": True}})
    [notice] = await monitor.notices(app, None)
    assert notice.code == "media_provider_auth" and notice.params == {"provider": "tmdb"}
    monkeypatch.setattr(providers, "status", lambda: {"tmdb": {"auth": False}})
    assert await codes() == []


async def test_een_provider_die_24_uur_faalt(state, monkeypatch):
    found: dict = {"musicbrainz": {"failing_since": (NOW - timedelta(hours=23)).isoformat()}}
    monkeypatch.setattr(providers, "status", lambda: found)
    assert await codes() == []  # korter offline: de jobs proberen het later opnieuw
    found["musicbrainz"]["failing_since"] = (NOW - timedelta(hours=24)).isoformat()
    [notice] = await monitor.notices(app, None)
    assert notice.code == "media_provider_failing" and notice.params == {"provider": "musicbrainz"}
    # met de klok: 23 uur, en een uur later wel
    found["musicbrainz"]["failing_since"] = NOW - timedelta(hours=23)
    assert await codes() == []
    monkeypatch.setattr(monitor, "now", lambda: NOW + timedelta(hours=1))
    assert await codes() == ["media_provider_failing"]
    # zonder tijdzone telt als UTC; onzin telt niet
    found["musicbrainz"]["failing_since"] = "2026-10-30T12:00:00"
    assert await codes() == ["media_provider_failing"]
    found["musicbrainz"]["failing_since"] = "gisteren"
    assert await codes() == []
    # M8 zegt het zelf
    found["musicbrainz"] = {"failing": True}
    assert await codes() == ["media_provider_failing"]
    found["musicbrainz"] = {}
    assert await codes() == []


async def test_een_fout_van_de_providers_breekt_beheer_niet(state, monkeypatch, caplog):
    def broken():
        raise RuntimeError("M8 stuk")

    monkeypatch.setattr(providers, "status", broken)
    assert await codes() == []
    assert "de staat van de providers" in caplog.text


async def test_de_volgorde_van_notices(state, monkeypatch):
    monkeypatch.setattr(cache, "disk_low", True)
    state.caps = caps(mode="local", sandbox="off", missing=["libvpx-vp9"])
    found = {"caa": {"failing": True}, "tmdb": {"auth": True}}
    monkeypatch.setattr(providers, "status", lambda: found)
    assert await codes() == [
        "media_ffmpeg_missing", "media_cache_full", "media_sandbox_off", "media_provider_auth",
        "media_provider_failing",
    ]  # fmt: skip


async def test_de_meldingen_op_admin_status(state, client, admin, monkeypatch):
    headers, _ = admin
    monkeypatch.setattr(cache, "disk_low", True)
    r = await client.get("/api/v1/admin/status", headers=headers)
    assert r.status_code == 200, r.text
    mine = [n for n in r.json()["notices"] if n["code"].startswith("media_")]
    assert [n["code"] for n in mine] == ["media_cache_full"]
    assert mine[0]["text"] == monitor.NOTICES["media_cache_full"]
    monkeypatch.setattr(cache, "disk_low", False)
    r = await client.get("/api/v1/admin/status", headers=headers)
    assert not [n for n in r.json()["notices"] if n["code"].startswith("media_")]
