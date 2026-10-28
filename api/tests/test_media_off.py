"""Media uit is 0.6.0 (fase 5, §3.5). Zonder PLATFORM_MEDIA_ENABLED draait er niets van media:
geen worker, geen health, geen rijen buiten default, geen opruimwerk, geen map media/, geen
meldingen op /beheer, en elk media-eindpunt zegt 503 media_off. Dat het gedrag van fase 3 en 4
verder gelijk bleef, laat de hele testset zien (die draait met media uit); deze test kijkt na
wat M0 erbij zette, op de echte server (serve.run met de lifespan).

Met media aan (marker media: start een echte lokale worker) gaat het omgekeerd, en na het
stoppen is alles weer weg.
"""

from __future__ import annotations

import asyncio

import pytest

from app import jobs
from app.main import app
from app.media import runtime as media_runtime
from tests.live import live


def lanes_running() -> set[str]:
    """De rijen waarvan nu een worker draait (taken "achtergrondtaken <rij> <n>")."""
    names = [task.get_name() for task in asyncio.all_tasks()]
    return {name.split()[1] for name in names if name.startswith("achtergrondtaken ")}


@pytest.mark.anyio
async def test_media_uit_draait_niets_van_media(tmp_path, monkeypatch):
    async with live(tmp_path, monkeypatch) as server:
        state = app.state.media
        assert state.enabled is False and not media_runtime.enabled(app)
        assert getattr(app.state, "media_local", None) is None
        assert getattr(app.state, "media_health", None) is None
        assert lanes_running() == {"default"}
        assert media_runtime.SWEEP not in jobs._housekeeping
        assert not (tmp_path / "media").exists()

        headers = await server.admin()
        for method, path in (
            ("GET", "/api/v1/media/libraries"),
            ("POST", "/api/v1/media/progress/batch"),
            ("GET", "/api/v1/admin/media"),
            ("GET", "/api/v1/stream/node-1.geheim/map.json"),
        ):
            body = {"node_ids": [], "watched": True} if method == "POST" else None
            r = await server.client.request(method, path, headers=headers, json=body)
            assert r.status_code == 503, (path, r.text)
            assert r.json()["detail"]["code"] == "media_off"
        r = await server.client.get("/api/v1/admin/status", headers=headers)
        assert r.status_code == 200, r.text
        assert not [n for n in r.json()["notices"] if n["code"].startswith("media_")]
        # De rest werkt zoals in 0.6.0
        assert (await server.client.get("/api/v1/files/root", headers=headers)).status_code == 200
    # Het stoppen deed niets met media (begin_stop zonder media is niets)
    assert state.stopping is False
    assert not (tmp_path / "media").exists()


@pytest.mark.media
@pytest.mark.anyio
async def test_media_aan_start_en_stopt_met_de_app(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs, "_housekeeping", dict(jobs._housekeeping))
    old = tmp_path / "media" / "sessions" / "s1"
    old.mkdir(parents=True)
    (old / "0.m4s").write_bytes(b"van een vorige keer")
    async with live(
        tmp_path, monkeypatch, PLATFORM_MEDIA_ENABLED="true", PLATFORM_MEDIA_MAX_PROCS="3"
    ):
        state = app.state.media
        assert state.enabled and media_runtime.enabled(app)
        assert state.mode == "local"
        worker = app.state.media_local
        assert worker is not None and worker.process.returncode is None
        assert app.state.media_health is not None
        assert lanes_running() == set(jobs.LANES)
        assert media_runtime.SWEEP in jobs._housekeeping
        assert not old.exists()  # na een herstart is elke sessie weg
    # Gestopt: de worker (op zijn pid), zijn socket en het opruimwerk zijn weg
    assert worker.process.returncode is not None
    assert not worker.socket.exists()
    assert media_runtime.SWEEP not in jobs._housekeeping
    assert state.stopping is True
