"""De contracttests van de container analyse (§3.11, Keuze 56 en 59): scenario's die tegen de
nep-worker (laag 1) en tegen de echte worker (laag 2) draaien. Zo blijft de nep eerlijk.

Dit bestand heet niet test_*.py en wordt niet zelf verzameld. test_fake_analyse_worker.py draait
elk scenario uit SCENARIOS met een Driver voor de nep; X2a draait dezelfde lijst in
api/tests_analyse/ met een Driver voor de echte worker, zonder dit bestand te wijzigen:

    @pytest.mark.parametrize("scenario", C.SCENARIOS, ids=lambda s: s.__name__)
    async def test_contract_echt(scenario, echte_driver):
        if missing := C.required(scenario) - echte_driver.has:
            pytest.skip(...)
        await scenario(echte_driver)

Alleen de standaardbibliotheek en de app: geen numpy (laag 1 draait in de job api).

Afspraken voor elke Driver:

- `socket` en `key`: waar de worker luistert en de sleutel van de handdruk; `settings()` geeft
  Settings waarmee app.analyse.client hem bereikt (PLATFORM_ANALYSE_ENABLED aan);
- `has`: de opdrachten die deze worker doet naast caps en cancel (caps.built zonder die twee).
  Een scenario met `needs(...)` vraagt opdrachten die er misschien nog niet zijn: zonder slaat
  het over (`required`);
- `models`: de namen die caps hoort te melden (de regels van models.lock, en later die van X3);
- `lang(op)`: vanaf nu blijft elke vraag van `op` lopen tot cancel, tot de verbinding dichtgaat
  of tot zijn deadline. Geeft de Opdracht (args en de fd's) waarmee dat zo is: de nep houdt de
  opdracht vast (`hold`); de echte Driver kiest invoer die lang duurt (X2a: een groepsronde van
  60.000 gezichten; X3: rank over een groot bestand);
- `los()`: zet dat terug (de nep laat zijn poorten open). Na elk scenario.
"""

from __future__ import annotations

import asyncio
import os
import secrets
import socket
import struct
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel

from app.analyse import client, wire
from app.analyse.wire import CHILD, DEADLINE_MS, Child, Code, Op
from app.config import Settings
from app.media import wire as media_wire

GEHEIM = "geheimwoord7"  # een waarde die nooit in een antwoord of een fout mag staan


@dataclass
class Opdracht:
    """Een vraag voor een kind: args, en een maker van verse fd's (de aanroeper sluit ze)."""

    op: Op
    args: BaseModel
    fds: Callable[[], list[int]] = field(default=list)


class AnalyseDriver(Protocol):
    name: str
    socket: Path
    key: str
    has: frozenset[Op]
    models: tuple[str, ...]

    def settings(self) -> Settings: ...

    async def lang(self, op: Op) -> Opdracht: ...

    async def los(self) -> None: ...


Scenario = Callable[[AnalyseDriver], Awaitable[None]]


def needs(*ops: Op) -> Callable[[Scenario], Scenario]:
    """Dit scenario vraagt deze opdrachten van de worker (naast caps en cancel)."""

    def mark(scenario: Scenario) -> Scenario:
        scenario.needs = frozenset(ops)  # type: ignore[attr-defined]
        return scenario

    return mark


def required(scenario: Scenario) -> frozenset[Op]:
    return getattr(scenario, "needs", frozenset())


# --- de standaard-opdrachten (de nep; de echte Driver kiest zijn eigen invoer) ---


def _group_in() -> list[int]:
    head = wire.GROUP_HEAD.pack(wire.GROUP_MAGIC, 0, 0, 0, 0)
    return [wire.sealed_memfd("in", head), wire.out_memfd("uit")]


def _one(name: str) -> Callable[[], list[int]]:
    return lambda: [wire.out_memfd(name)]


def _previews_uit() -> list[int]:
    return [wire.sealed_memfd("preview", b"RIFF"), wire.out_memfd("uit")]


STANDAARD: dict[Op, Opdracht] = {
    Op.FACES: Opdracht(Op.FACES, wire.FacesArgs(), _previews_uit),
    Op.GROUP: Opdracht(Op.GROUP, wire.GroupArgs(mode="full"), _group_in),
    Op.EMBED_IMAGE: Opdracht(Op.EMBED_IMAGE, wire.EmbedImageArgs(), _previews_uit),
    Op.EMBED_TEXT: Opdracht(Op.EMBED_TEXT, wire.EmbedTextArgs(text="een fiets"), _one("uit")),
    Op.RANK: Opdracht(Op.RANK, wire.RankArgs(owner="0" * 32, gen=0, k=0), _one("vectoren")),
    Op.FORGET: Opdracht(Op.FORGET, wire.ForgetArgs(owner="0" * 32)),
}


# --- hulpjes ---


async def until(cond: Callable[[], Awaitable[bool] | bool], limit: float = 10.0) -> None:
    deadline = time.monotonic() + limit
    while True:
        found = cond()
        if asyncio.iscoroutine(found):
            found = await found
        if found:
            return
        assert time.monotonic() < deadline, "niet op tijd"
        await asyncio.sleep(0.02)


async def raw(d: AnalyseDriver) -> socket.socket:
    """Een kale verbinding, zonder handdruk."""
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM | socket.SOCK_CLOEXEC)
    sock.setblocking(False)
    with wire.socket_address(d.socket) as address:
        async with asyncio.timeout(wire.ANSWER_S):
            await asyncio.get_running_loop().sock_connect(sock, address)
    return sock


async def answer(sock: socket.socket) -> dict:
    async with asyncio.timeout(wire.ANSWER_S):
        message, fds = await wire.receive(sock, max_fds=0)
    wire.close_all(fds)
    return message


async def is_closed(sock: socket.socket) -> bool:
    """Na het laatste antwoord gaat de verbinding dicht (EOF), binnen ANSWER_S."""
    try:
        async with asyncio.timeout(wire.ANSWER_S):
            await wire.receive(sock, max_fds=0)
    except wire.Closed:
        return True
    except (wire.WireError, OSError, TimeoutError):
        return False
    return False


async def ask(d: AnalyseDriver, message: dict, fds: list[int] | tuple = ()) -> dict:
    """Een vraag na een goede handdruk (via client.connect); het eerste antwoord."""
    sock = await client.connect(d.settings())
    try:
        await wire.send(sock, message, fds)
        return await answer(sock)
    finally:
        sock.close()


async def begin(d: AnalyseDriver, opdracht: Opdracht, deadline_ms: int | None = None):
    """client.start met verse fd's; de eigen kopieën gaan meteen weer dicht."""
    fds = opdracht.fds()
    try:
        return await client.start(
            d.settings(), opdracht.op, opdracht.args, fds=fds, deadline_ms=deadline_ms
        )
    finally:
        wire.close_all(fds)


def at_eof(fd: int) -> Callable[[], bool]:
    """Waar zodra de andere kant van deze pijp helemaal dicht is."""
    os.set_blocking(fd, False)

    def check() -> bool:
        try:
            return os.read(fd, 1) == b""
        except BlockingIOError:
            return False

    return check


def refused(message: dict, code: Code) -> None:
    assert message.get("ev") == "done", message
    assert message.get("code") == code.value, message
    assert "data" not in message
    assert len(str(message.get("detail") or "")) <= wire.DETAIL_CHARS


# --- de scenario's ---


async def caps_vorm(d: AnalyseDriver) -> None:
    """caps van pid 1: de modellen met een stand, en precies de opdrachten die er zijn."""
    caps = await client.caps(d.settings())
    assert set(caps.built) == {Op.CAPS.value, Op.CANCEL.value} | {op.value for op in d.has}
    names = [m.name for m in caps.models]
    assert len(names) == len(set(names))
    assert set(d.models) <= set(names)
    assert all(m.state in ("ok", "ontbreekt", "anders") for m in caps.models)


async def handdruk_eigen_label(d: AnalyseDriver) -> None:
    """Het bewijs is dat van analyse voor de euid van de app, nooit dat van media."""
    me = os.geteuid()
    sock = await raw(d)
    try:
        nonce = secrets.token_hex(16)
        await wire.send(sock, wire.hello(nonce))
        reply = await answer(sock)
    finally:
        sock.close()
    assert reply["ev"] == "hello" and reply["v"] == wire.PROTOCOL
    assert reply["uid"] == me
    assert reply["proof"] == wire.proof(d.key, nonce, me)
    assert reply["proof"] != media_wire.proof(d.key, nonce, me)


async def eerst_hello(d: AnalyseDriver) -> None:
    """Een vraag vóór hello: bad_request, en de verbinding gaat dicht."""
    sock = await raw(d)
    try:
        await wire.send(sock, wire.request(Op.CAPS, wire.CapsArgs()))
        refused(await answer(sock), Code.BAD_REQUEST)
        assert await is_closed(sock)
    finally:
        sock.close()


async def hello_zonder_fds(d: AnalyseDriver) -> None:
    """Een fd bij hello: bad_request; de worker houdt de fd niet."""
    r, w = os.pipe2(os.O_CLOEXEC)
    sock = await raw(d)
    try:
        await wire.send(sock, wire.hello(secrets.token_hex(16)), [w])
        os.close(w)
        w = -1
        refused(await answer(sock), Code.BAD_REQUEST)
        await until(at_eof(r), 5)
    finally:
        sock.close()
        os.close(r)
        if w >= 0:
            os.close(w)


async def andere_protocol(d: AnalyseDriver) -> None:
    sock = await raw(d)
    try:
        await wire.send(sock, {"v": wire.PROTOCOL + 1, "hello": secrets.token_hex(16)})
        reply = await answer(sock)
    finally:
        sock.close()
    refused(reply, Code.BAD_REQUEST)
    assert f"PROTOCOL {wire.PROTOCOL}" in reply["detail"]


async def te_grote_boodschap(d: AnalyseDriver) -> None:
    """Na de handdruk een kop boven MAX_MESSAGE: bad_request, zonder de rest te lezen."""
    sock = await client.connect(d.settings())
    try:
        await asyncio.get_running_loop().sock_sendall(
            sock, struct.pack(">I", wire.MAX_MESSAGE + 1) + b"{"
        )
        refused(await answer(sock), Code.BAD_REQUEST)
    finally:
        sock.close()


async def onbekende_opdracht(d: AnalyseDriver) -> None:
    for op in ("verwijder_alles", "", 7, None, ["caps"]):
        message = {"v": wire.PROTOCOL, "op": op, "deadline_ms": 1000, "args": {}}
        refused(await ask(d, message), Code.BAD_REQUEST)


async def niet_gebouwd(d: AnalyseDriver) -> None:
    """Elke opdracht die de worker (nog) niet heeft: not_built, met geldige of lege args."""
    missing = [op for op in Op if CHILD[op] is not None and op not in d.has]
    for op in missing:
        refused(await ask(d, wire.request(op, {})), Code.NOT_BUILT)
        args = STANDAARD[op].args
        refused(await ask(d, wire.request(op, args)), Code.NOT_BUILT)


async def verkeerde_argumenten(d: AnalyseDriver) -> None:
    """bad_request, en de reden noemt nooit een waarde."""
    for message in (
        wire.request(Op.CANCEL, {"n": GEHEIM}),
        wire.request(Op.CANCEL, {"n": 0}),
        wire.request(Op.CANCEL, {"n": 1, "tekst": GEHEIM}),
        wire.request(Op.CAPS, {"x": GEHEIM}),
        {**wire.request(Op.CAPS, wire.CapsArgs()), "extra": GEHEIM},
        {**wire.request(Op.CAPS, wire.CapsArgs()), "args": [GEHEIM]},
    ):
        reply = await ask(d, message)
        refused(reply, Code.BAD_REQUEST)
        assert GEHEIM not in str(reply)


async def verkeerde_deadline(d: AnalyseDriver) -> None:
    base = wire.request(Op.CAPS, wire.CapsArgs())
    for ms in (0, -1, DEADLINE_MS[Op.CAPS] + 1, "1000", 1000.0, True, None):
        refused(await ask(d, {**base, "deadline_ms": ms}), Code.BAD_REQUEST)
    without = {k: v for k, v in base.items() if k != "deadline_ms"}
    refused(await ask(d, without), Code.BAD_REQUEST)


async def verkeerd_aantal_fds(d: AnalyseDriver) -> None:
    """caps met een fd: bad_request, en de worker sluit zijn kopie."""
    r, w = os.pipe2(os.O_CLOEXEC)
    try:
        reply = await ask(d, wire.request(Op.CAPS, wire.CapsArgs()), [w])
        os.close(w)
        w = -1
        refused(reply, Code.BAD_REQUEST)
        await until(at_eof(r), 5)
    finally:
        os.close(r)
        if w >= 0:
            os.close(w)


async def cancel_onbekend(d: AnalyseDriver) -> None:
    assert await client.cancel(d.settings(), 10**9) is False


@needs(Op.GROUP)
async def bezet_per_kind_werk(d: AnalyseDriver) -> None:
    """Een tweede opdracht voor werk krijgt busy; caps en cancel (pid 1) gaan gewoon door."""
    opdracht = await d.lang(Op.GROUP)
    first = await begin(d, opdracht)
    try:
        assert first.child is Child.WERK and first.n is not None and first.n >= 1
        try:
            await begin(d, opdracht)
            raise AssertionError("geen busy")
        except client.AnalyseBusy as exc:
            assert exc.child == Child.WERK.value
        await client.caps(d.settings())
        assert await client.cancel(d.settings(), first.n) is True
        try:
            await first.result()
            raise AssertionError("niet afgebroken")
        except client.AnalyseFailed as exc:
            assert exc.code == Code.CANCELLED.value
        assert await client.cancel(d.settings(), first.n) is False
    finally:
        first.close()
    # Daarna is werk weer vrij
    again = await begin(d, opdracht)
    try:
        assert again.child is Child.WERK and again.n != first.n
    finally:
        again.close()


@needs(Op.GROUP, Op.RANK)
async def zoeken_naast_werk(d: AnalyseDriver) -> None:
    """Een zoekvraag tijdens een groepsronde krijgt geen busy (Keuze 56, K2-02)."""
    werk = await begin(d, await d.lang(Op.GROUP))
    try:
        zoeken = await begin(d, await d.lang(Op.RANK))
        try:
            assert werk.child is Child.WERK and zoeken.child is Child.ZOEKEN
            assert werk.n != zoeken.n
        finally:
            zoeken.close()
    finally:
        werk.close()


@needs(Op.RANK)
async def bezet_per_kind_zoeken(d: AnalyseDriver) -> None:
    opdracht = await d.lang(Op.RANK)
    first = await begin(d, opdracht)
    try:
        try:
            await begin(d, opdracht)
            raise AssertionError("geen busy")
        except client.AnalyseBusy as exc:
            assert exc.child == Child.ZOEKEN.value
    finally:
        first.close()


@needs(Op.GROUP)
async def dicht_is_afbreken(d: AnalyseDriver) -> None:
    """De verbinding van een opdracht sluiten breekt hem af: werk is daarna weer vrij."""
    opdracht = await d.lang(Op.GROUP)
    first = await begin(d, opdracht)
    first.close()

    async def free() -> bool:
        try:
            job = await begin(d, opdracht)
        except client.AnalyseBusy:
            return False
        job.close()
        return True

    await until(free, 10)


@needs(Op.GROUP)
async def deadline_is_timeout(d: AnalyseDriver) -> None:
    """Na deadline_ms: timeout (de worker stopt het kind), en werk is daarna weer vrij."""
    opdracht = await d.lang(Op.GROUP)
    job = await begin(d, opdracht, deadline_ms=300)
    started = time.monotonic()
    try:
        await job.result()
        raise AssertionError("geen timeout")
    except client.AnalyseFailed as exc:
        assert exc.code == Code.TIMEOUT.value
    finally:
        job.close()
    assert time.monotonic() - started < 0.3 + wire.KILL_GRACE_S + 1
    again = await begin(d, opdracht)
    again.close()


SCENARIOS: list[Scenario] = [
    caps_vorm,
    handdruk_eigen_label,
    eerst_hello,
    hello_zonder_fds,
    andere_protocol,
    te_grote_boodschap,
    onbekende_opdracht,
    niet_gebouwd,
    verkeerde_argumenten,
    verkeerde_deadline,
    verkeerd_aantal_fds,
    cancel_onbekend,
    bezet_per_kind_werk,
    zoeken_naast_werk,
    bezet_per_kind_zoeken,
    dicht_is_afbreken,
    deadline_is_timeout,
]
