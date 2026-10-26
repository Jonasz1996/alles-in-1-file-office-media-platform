"""De gezondheidslus van office (Keuze 28, §3.8): gezond en weg (na 2 minuten de melding),
version, license en de tokencontrole bij een overgang naar gezond, de nep-DS met jwt-off geeft
misconfigured, en bij weer gezond worden on_healthy en kick aangeroepen."""

import asyncio
import json
import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest

from app.config import Settings
from app.office import convert, engine, health, recovery, runtime
from app.office.oo import client
from app.office.state import office_state
from tests.fake_docserver import FakeConfig, create_app, protocol

T0 = datetime(2026, 10, 26, 9, 0, tzinfo=UTC)


def at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


def office_settings(tmp_path, secret: str, **extra) -> Settings:
    return Settings(
        data_dir=tmp_path,
        jwt_secret="app-" + secrets.token_hex(16),
        office_url="http://ds",
        office_app_url="http://platform:8081",
        office_secret=secret,
        **extra,
    )


@dataclass
class World:
    app: SimpleNamespace
    calls: list[str] = field(default_factory=list)
    fake: object = None
    control: httpx.AsyncClient | None = None

    @property
    def state(self):
        return office_state(self.app)

    async def check(self, seconds: float) -> str:
        await health.check_once(self.app, now=at(seconds))
        return self.state.health

    def commands(self, c: str) -> int:
        return sum(1 for e in self.fake.entries if e["kind"] == "command" and e.get("c") == c)


@pytest.fixture
def hooks(monkeypatch) -> list[str]:
    calls: list[str] = []
    monkeypatch.setattr(convert, "on_healthy", lambda: calls.append("on_healthy"))
    monkeypatch.setattr(recovery, "kick", lambda: calls.append("kick"))
    return calls


async def bind(settings: Settings, transport: httpx.AsyncBaseTransport) -> SimpleNamespace:
    app = SimpleNamespace(state=SimpleNamespace())
    await engine.close()
    client.transport = transport
    runtime.bind(app, settings)
    return app


@pytest.fixture
async def world(tmp_path, hooks):
    secret = secrets.token_urlsafe(32)
    ds = create_app(FakeConfig(secret=secret))
    transport = httpx.ASGITransport(app=ds)
    app = await bind(office_settings(tmp_path, secret), transport)
    control = httpx.AsyncClient(transport=transport, base_url="http://ds")
    try:
        yield World(app=app, calls=hooks, fake=ds.state.fake, control=control)
    finally:
        await control.aclose()
        await engine.close()
        client.transport = None
        runtime.unbind()


async def mock_world(tmp_path, hooks, handler) -> World:
    settings = office_settings(tmp_path, secrets.token_urlsafe(32))
    app = await bind(settings, httpx.MockTransport(handler))
    return World(app=app, calls=hooks)


@pytest.fixture
async def unbind():
    yield
    await engine.close()
    client.transport = None
    runtime.unbind()


@pytest.mark.anyio
async def test_gezond_en_weer_weg(world):
    assert await world.check(0) == "ok"
    state = world.state
    assert world.calls == ["on_healthy", "kick"]
    assert state.version == protocol.BUILD
    assert state.license_connections == protocol.UNLIMITED
    assert state.tokens_enforced is True
    assert state.deep_checked_at == at(0) and state.ever_ok and state.down_since is None
    # De grote controle: version getekend en ongetekend (tokencontrole), license, converter
    assert world.commands("version") == 2 and world.commands("license") == 1
    assert sum(1 for e in world.fake.entries if e["kind"] == "convert") == 1

    # Binnen 10 minuten alleen /healthcheck
    assert await world.check(30) == "ok"
    assert world.commands("version") == 2
    assert world.calls == ["on_healthy", "kick"]

    # Weg: down (hij was al eens gezond, dus geen starting), na 2 minuten de melding
    await world.control.post("/__fake/kill")
    assert await world.check(60) == "down"
    assert state.down_since == at(60) and state.deep_checked_at is None
    assert await world.check(90) == "down"
    assert state.down_since == at(60)  # blijft het begin
    assert not health.down_too_long(state, at(60 + 119))
    assert health.down_too_long(state, at(60 + 120))

    # Terug: opnieuw de grote controle, en de haken
    await world.control.post("/__fake/restart")
    assert await world.check(200) == "ok"
    assert world.commands("version") == 4
    assert world.calls == ["on_healthy", "kick"] * 2
    assert state.down_since is None and not health.down_too_long(state, at(1000))

    # Na 10 minuten weer de grote controle, zonder haken (hij was al gezond)
    assert await world.check(200 + 600) == "ok"
    assert world.commands("version") == 6
    assert state.deep_checked_at == at(800)
    assert world.calls == ["on_healthy", "kick"] * 2


@pytest.mark.anyio
async def test_starting_dan_down(world):
    await world.control.post("/__fake/health", json={"starting": True})
    assert await world.check(0) == "starting"
    state = world.state
    assert state.down_since == at(0)
    assert not health.down_too_long(state, at(250))  # starten duurt even (fonts)
    assert await world.check(250) == "starting"
    assert await world.check(301) == "down"
    assert health.down_too_long(state, at(301))
    assert world.calls == []
    await world.control.post("/__fake/health", json={"ok": True})
    assert await world.check(330) == "ok"
    assert world.calls == ["on_healthy", "kick"]
    # Daarna nooit meer starting, ook niet binnen de eerste 5 minuten van de lus
    await world.control.post("/__fake/kill")
    assert await world.check(340) == "down"


@pytest.mark.anyio
async def test_jwt_off_geeft_misconfigured(world):
    await world.control.post("/__fake/jwt-off", json={"off": True})
    assert await world.check(0) == "misconfigured"
    state = world.state
    assert state.tokens_enforced is False
    assert state.version == protocol.BUILD  # de rest van de controle ging wel
    assert world.calls == []  # niet "weer gezond"
    # Blijft zo tot de volgende grote controle, ook als DS intussen weer tokens eist
    await world.control.post("/__fake/jwt-off", json={"off": False})
    versions = world.commands("version")
    assert await world.check(30) == "misconfigured"
    assert world.commands("version") == versions
    assert await world.check(601) == "ok"
    assert state.tokens_enforced is True
    assert world.calls == ["on_healthy", "kick"]
    # En weer terug naar misconfigured bij de volgende grote controle
    await world.control.post("/__fake/jwt-off", json={"off": True})
    assert await world.check(601 + 600) == "misconfigured"


@pytest.mark.anyio
async def test_misconfigured_dan_weg_dan_terug(world):
    await world.control.post("/__fake/jwt-off", json={"off": True})
    assert await world.check(0) == "misconfigured"
    await world.control.post("/__fake/kill")
    assert await world.check(30) == "down"
    await world.control.post("/__fake/restart")  # restart zet jwt niet terug
    assert await world.check(60) == "misconfigured"
    assert world.calls == []


@pytest.mark.anyio
async def test_conversie_zonder_token_telt_ook(tmp_path, hooks, unbind):
    """version weigert zonder token, maar /converter neemt het aan: ook misconfigured."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/healthcheck":
            return httpx.Response(200, text="true")
        body = json.loads(request.content)
        if request.url.path == "/converter":
            return httpx.Response(200, json={"error": -4})  # hij probeerde op te halen
        if "token" not in body:
            return httpx.Response(200, text='{"error":6}')
        if body["c"] == "version":
            return httpx.Response(200, json=protocol.version_answer())
        return httpx.Response(200, json=protocol.license_answer(20))

    w = await mock_world(tmp_path, hooks, handler)
    assert await w.check(0) == "misconfigured"
    assert w.state.tokens_enforced is False and w.state.license_connections == 20


@pytest.mark.anyio
async def test_mislukte_grote_controle(tmp_path, hooks, unbind):
    """Pas gezond na een geslaagde controle; eenmaal gezond blijft hij dat bij een mislukte
    herhaling (en probeert het de volgende keer weer)."""
    broken = {"license": True}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/healthcheck":
            return httpx.Response(200, text="true")
        if request.url.path == "/converter":
            return httpx.Response(200, json={"error": -8})
        body = json.loads(request.content)
        if "token" not in body:
            return httpx.Response(200, text='{"error":6}')
        if body["c"] == "license" and broken["license"]:
            return httpx.Response(500)
        if body["c"] == "version":
            return httpx.Response(200, json=protocol.version_answer())
        return httpx.Response(200, json=protocol.license_answer(protocol.UNLIMITED))

    w = await mock_world(tmp_path, hooks, handler)
    assert await w.check(0) == "starting"  # nooit "off": office is ingesteld
    assert await w.check(400) == "down"
    broken["license"] = False
    assert await w.check(430) == "ok"
    assert w.calls == ["on_healthy", "kick"]
    broken["license"] = True
    assert await w.check(430 + 600) == "ok"
    assert w.state.deep_checked_at == at(430)  # niet bijgewerkt: de volgende keer opnieuw
    broken["license"] = False
    assert await w.check(430 + 630) == "ok"
    assert w.state.deep_checked_at == at(1060)
    assert w.calls == ["on_healthy", "kick"]


@pytest.mark.anyio
async def test_healthcheck_moet_true_zijn(tmp_path, hooks, unbind):
    answers = iter(["false", "", "TRUE", "true\n"])

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/healthcheck":
            return httpx.Response(200, text=next(answers))
        raise httpx.ConnectError("weg")

    w = await mock_world(tmp_path, hooks, handler)
    for n in range(3):
        assert await w.check(n) == "starting"
    # "true" met een regeleinde is gezond; de grote controle faalt hier: nog niet ok
    assert await w.check(3) == "starting"
    assert w.calls == []


@pytest.mark.anyio
async def test_een_haak_die_faalt_stopt_niets(world, monkeypatch, caplog):
    def broken() -> None:
        raise RuntimeError("kapot")

    monkeypatch.setattr(convert, "on_healthy", broken)
    assert await world.check(0) == "ok"
    assert world.calls == ["kick"]
    assert "haak bij weer gezond mislukt" in caplog.text


@pytest.mark.anyio
async def test_zonder_office(tmp_path, unbind):
    app = SimpleNamespace(state=SimpleNamespace())
    runtime.bind(app, Settings(data_dir=tmp_path, jwt_secret="test" * 8))
    await health.check_once(app, now=at(0))
    assert office_state(app).health == "off" and office_state(app).problem is None
    # Ingesteld maar fout: misconfigured met de reden
    runtime.bind(app, office_settings(tmp_path, "te-kort"))
    await health.check_once(app, now=at(1))
    assert office_state(app).health == "misconfigured"
    assert office_state(app).problem


@pytest.mark.anyio
async def test_de_lus_start_en_stopt(tmp_path, hooks):
    secret = secrets.token_urlsafe(32)
    ds = create_app(FakeConfig(secret=secret))
    settings = office_settings(tmp_path, secret, office_health_seconds=0.01)
    app = await bind(settings, httpx.ASGITransport(app=ds))
    try:
        await health.start(app)
        state = office_state(app)
        assert len(state.tasks) == 1
        for _ in range(200):
            if state.health == "ok":
                break
            await asyncio.sleep(0.01)
        assert state.health == "ok"
        task = state.tasks[0]
        await health.stop(app)
        assert task.done() and state.tasks == []
    finally:
        await engine.close()
        client.transport = None
        runtime.unbind()
