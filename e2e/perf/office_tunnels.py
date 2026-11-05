"""Het geheugen van de app met 50 open tunnels (fase 4, §4.7).

Doel (docs/fase-4/bouwontwerp.md §4.7): 50 tunnels kosten de app ≤ 20 MB extra geheugen.

Methode
- Een eigen server (office_bench.start_office, timers van productie) tegen de echte DS uit
  E2E_REAL_DS. Tien gebruikers (een eigenaar en negen leden met schrijfrecht) en vijf docx in
  een gedeelde map: elke gebruiker opent elk document, dus 50 tunnels, 5 per gebruiker (de grens
  is 8) en 10 per document (max_editors is 20).
- Eerst één echte editor in Chromium (via /bewerken/<id>, door de proxy en de tunnel), om het
  CONNECT-pakket en het eerste auth-bericht van de editor te vangen. Daarna geen browser meer:
  een kleine WebSocket-client in dit script (asyncio, zonder extra pakketten) speelt per tunnel
  de editor na. Elke gebruiker doet POST …/session per document (de config met zijn eigen token
  en het poort-cookie), en elke tunnel gaat zoals in de browser: de upgrade op
  /office/<versie>/doc/<key>/c/ met het cookie en dezelfde Origin, het CONNECT-pakket en het
  auth-bericht van de editor met de key en het token van die sessie, en daarna pong op elke
  ping van engine.io en van WebSocket. Alles gaat dus door de echte poort, de StreamGuard en
  de pomp van de tunnel; DS zelf antwoordt.
- Wat de echte editor ook doet: komt er een tweede bewerker bij, dan legt DS een slot op de
  eerste (de tweede krijgt waitAuth) tot die naar samen bewerken schakelt en unLockDocument
  stuurt. Zonder dat stuurt DS de eerste na expire.lockDoc weg (disconnectReason "drop"). De
  client stuurt het daarom na elke connectState (bij wie het slot niet heeft, doet het niets).
- Het geheugen is VmRSS van het serverproces: de mediaan van 3 metingen (1 s ertussen). Eerst de
  basis, na het maken van de sessies en één tunnel open en weer dicht (zodat de code van de
  tunnel geladen is). Dan 50 tunnels open; 10 s nadat de laatste zijn antwoord van DS had, en
  nog eens na 30 s open houden (een ronde van ping en pong, en de controle elke 15 s). Daarna
  alles dicht en na 5 s nog eens.
- De asyncio-lus van de client draait in een eigen draad (run_async van het harnas): de
  synchrone Playwright heeft de hoofddraad.
- Herhaling: per ronde een nieuwe server (de eerste keer is wat telt; Python geeft geheugen
  niet altijd terug), en binnen een ronde een tweede keer 50 tunnels op dezelfde server.

Los draaien (met een echte DS; JSON op stdout):
    cd api && E2E_REAL_DS=http://127.0.0.1:18150 E2E_REAL_DS_SECRET_FILE=<map>/jwt.secret \\
      uv run --with playwright python ../e2e/perf/office_tunnels.py --rounds 3
Via pytest (marker perf; zonder E2E_REAL_DS overgeslagen):
    cd api && E2E_REAL_DS=… uv run --with playwright pytest ../e2e/perf/office_tunnels.py -s
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import contextlib
import json
import os
import shutil
import statistics
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

import office_bench as ob  # noqa: E402 - pas na het pad naar e2e/perf

from conftest import Browsers, launch_browser, report_only, write_result  # noqa: E402
from office_harness import fixture, run_async  # noqa: E402

pytestmark = pytest.mark.perf

GATE = {"extra_mb": 20.0}
USERS = 10
DOCS = 5
HOLD_S = 30.0
MB = 1000**2


# --- een kleine WebSocket-client (RFC 6455, alleen tekst, gemaskeerd) ---


def _mask(data: bytes, mask: bytes) -> bytes:
    n = len(data)
    if not n:
        return b""
    pad = (mask * (n // 4 + 1))[:n]
    return (int.from_bytes(data, "big") ^ int.from_bytes(pad, "big")).to_bytes(n, "big")


def _frame(opcode: int, payload: bytes) -> bytes:
    mask = os.urandom(4)
    n = len(payload)
    if n < 126:
        head = bytes([0x80 | opcode, 0x80 | n])
    elif n < 65536:
        head = bytes([0x80 | opcode, 0x80 | 126]) + n.to_bytes(2, "big")
    else:
        head = bytes([0x80 | opcode, 0x80 | 127]) + n.to_bytes(8, "big")
    return head + mask + _mask(payload, mask)


# Berichten van DS die zeggen waarom een verbinding niet mag of weg moet: die bewaren we
# (zonder tokens) in de uitslag
UNLOCK = b'42["message",{"type":"unLockDocument","isSave":false,"unlock":true}]'
NOTE_TYPES = {"drop", "disconnectReason", "license", "waitAuth", "error", "authChanges"}


def _redact(value: Any, depth: int = 0) -> Any:
    """Een bericht zonder tokens (alles wat op een JWT lijkt) en zonder grote stukken."""
    if depth > 4:
        return "…"
    if isinstance(value, dict):
        return {k: _redact(v, depth + 1) for k, v in list(value.items())[:20]}
    if isinstance(value, list):
        return [_redact(v, depth + 1) for v in value[:10]]
    if isinstance(value, str):
        if value.count(".") == 2 and len(value) > 40:
            return "<jwt>"
        return value if len(value) <= 80 else value[:80] + "…"
    return value


@dataclass
class Tunnel:
    """Eén tunnel zoals de editor hem gebruikt."""

    name: str
    key: str
    connect: str
    auth: str
    status: int | None = None
    opened: float | None = None
    answered: float | None = None  # het eerste antwoord van DS op het auth-bericht
    closed_by_peer: bool = False
    received: int = 0
    pings: int = 0
    unlocks: int = 0
    types: list[str] = field(default_factory=list)
    error: str | None = None
    notes: list[Any] = field(default_factory=list)  # berichten over weigeren of wegsturen
    _writer: Any = None
    _task: Any = None

    async def run(self, host: str, port: int, path: str, cookie: str, origin: str) -> None:
        try:
            reader, writer = await asyncio.open_connection(host, port)
            self._writer = writer
            key = base64.b64encode(os.urandom(16)).decode()
            head = (
                f"GET {path} HTTP/1.1\r\nHost: {host}:{port}\r\nUpgrade: websocket\r\n"
                f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n"
                f"Sec-WebSocket-Version: 13\r\nOrigin: {origin}\r\nCookie: office={cookie}\r\n"
                "User-Agent: Mozilla/5.0 (meting office_tunnels)\r\n\r\n"
            )
            writer.write(head.encode())
            await writer.drain()
            answer = await reader.readuntil(b"\r\n\r\n")
            self.status = int(answer.split(b" ", 2)[1])
            if self.status != 101:
                return
            self.opened = time.monotonic()
            await self._loop(reader, writer)
        except (OSError, asyncio.IncompleteReadError, ValueError) as exc:
            self.error = type(exc).__name__

    async def _loop(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        message = bytearray()
        sent_connect = sent_auth = False
        while True:
            b0, b1 = await reader.readexactly(2)
            n = b1 & 0x7F
            if n == 126:
                n = int.from_bytes(await reader.readexactly(2), "big")
            elif n == 127:
                n = int.from_bytes(await reader.readexactly(8), "big")
            payload = await reader.readexactly(n)
            opcode = b0 & 0x0F
            if opcode == 8:
                self.closed_by_peer = True
                return
            if opcode == 9:
                writer.write(_frame(10, payload))
                continue
            if opcode in (1, 2, 0):
                message += payload
                if not b0 & 0x80:
                    continue
                text = bytes(message).decode("utf-8", "replace")
                message.clear()
                self.received += 1
                if text == "2":  # ping van engine.io
                    self.pings += 1
                    writer.write(_frame(1, b"3"))
                elif text.startswith("0") and not sent_connect:
                    writer.write(_frame(1, self.connect.encode()))
                    sent_connect = True
                elif text.startswith("40") and not sent_auth:
                    writer.write(_frame(1, self.auth.encode()))
                    sent_auth = True
                elif text.startswith("42"):
                    if self.answered is None:
                        self.answered = time.monotonic()
                    with contextlib.suppress(ValueError, TypeError, IndexError, KeyError):
                        body = json.loads(text[2:])[1]
                        kind = body.get("type")
                        if isinstance(kind, str) and len(self.types) < 20:
                            self.types.append(kind)
                        if kind in NOTE_TYPES and len(self.notes) < 5:
                            self.notes.append(_redact(body))
                        if kind == "connectState":
                            # Komt er een tweede bewerker bij, dan legt DS een slot op de
                            # eerste en wacht de tweede (waitAuth) tot de editor van de eerste
                            # naar samen bewerken schakelt en unLockDocument stuurt; anders
                            # stuurt DS de eerste na expire.lockDoc weg (drop). De echte editor
                            # doet dat zelf; deze client doet het na elke connectState (wie
                            # het slot niet heeft, verandert er niets mee).
                            writer.write(_frame(1, UNLOCK))
                            self.unlocks += 1
                await writer.drain()

    def start(self, *args: Any) -> None:
        self._task = asyncio.ensure_future(self.run(*args))

    async def close(self) -> None:
        if self._writer is not None:
            with contextlib.suppress(OSError, RuntimeError):
                self._writer.write(_frame(8, b"\x03\xe8"))
                await self._writer.drain()
            with contextlib.suppress(OSError, RuntimeError):
                self._writer.close()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task

    @property
    def alive(self) -> bool:
        return self._task is not None and not self._task.done()


# --- de pakketten van de editor ---


def capture(bench: ob.Bench, browsers: Browsers, account: Any, node: str) -> dict[str, Any]:
    """Opent /bewerken/<node> in Chromium en geeft het CONNECT-pakket, het eerste auth-bericht,
    de key en het pad van de socket (met de versie van DS)."""
    context = browsers.context_as(account)
    page = context.new_page()
    sent: list[str] = []
    urls: list[str] = []

    def on_socket(ws: Any) -> None:
        urls.append(ws.url)
        ws.on("framesent", lambda p: sent.append(p) if isinstance(p, str) else None)

    page.on("websocket", on_socket)
    with page.expect_response(
        lambda r: r.request.method == "POST" and r.url.endswith(f"/files/{node}/session")
    ) as answer:
        page.goto(f"/bewerken/{node}")
    key = answer.value.json()["key"]
    page.locator(".editor-status").filter(has_text="Bewerken").wait_for(timeout=90_000)
    page.goto("about:blank")
    problems = browsers.close(context)
    connect = next((json.loads(s[2:]) for s in sent if s.startswith("40{")), None)
    auth = next(
        (json.loads(s[2:])[1] for s in sent if s.startswith('42["message",{"type":"auth"')), None
    )
    if connect is None or auth is None or not urls:
        raise AssertionError(f"geen CONNECT of auth van de editor gezien ({len(sent)} frames)")
    return {
        "connect": connect,
        "auth": auth,
        "key": key,
        "path": urlsplit(urls[0]).path,
        "problems": problems,
    }


def packets(captured: dict[str, Any], key: str, token: str) -> tuple[str, str]:
    """Het CONNECT-pakket en het auth-bericht van de editor, met deze key en dit token op elke
    plek waar de editor ze zet."""
    old = captured["key"]
    connect = json.loads(json.dumps(captured["connect"]).replace(old, key))
    connect["token"] = token
    connect.pop("session", None)
    if isinstance(connect.get("data"), dict):
        connect["data"]["jwtOpen"] = token
        connect["data"].pop("jwtSession", None)
    auth = json.loads(json.dumps(captured["auth"]).replace(old, key))
    auth["jwtOpen"] = token
    auth.pop("jwtSession", None)
    return (
        "40" + json.dumps(connect, separators=(",", ":")),
        "42" + json.dumps(["message", auth], separators=(",", ":")),
    )


# --- meten ---


def rss(pid: int, samples: int = 3) -> int:
    values = []
    for n in range(samples):
        if n:
            time.sleep(1)
        values.append(ob.rss_bytes(pid))
    return int(statistics.median(values))


def sessions_for(bench: ob.Bench, squad: ob.Team, docs: list[str]) -> list[dict[str, Any]]:
    """POST …/session voor elke gebruiker op elk document: key, token en poort-cookie."""
    out = []
    for account in squad.everyone:
        for name in docs:
            node = squad.nodes[name]
            r = bench.server.call(
                account,
                "POST",
                f"/api/v1/office/files/{node}/session",
                json={"theme": "light", "type": "desktop"},
                expect=200,
            )
            cookie = r.cookies.get("office")
            assert cookie, "geen poort-cookie uit POST …/session"
            body = r.json()
            out.append(
                {
                    "user": account.display_name,
                    "doc": name,
                    "key": body["key"],
                    "token": body["config"]["token"],
                    "cookie": cookie,
                }
            )
    return out


async def _open_all(
    bench: ob.Bench, captured: dict[str, Any], rows: list[dict[str, Any]], version: str
) -> list[Tunnel]:
    parts = urlsplit(bench.server.url)
    assert parts.hostname is not None and parts.port is not None
    origin = f"{parts.scheme}://{parts.netloc}"
    tunnels = []
    for row in rows:
        connect, auth = packets(captured, row["key"], row["token"])
        t = Tunnel(f"{row['user']}/{row['doc']}", row["key"], connect, auth)
        path = (
            f"/office/{version}/doc/{row['key']}/c/?shardkey={row['key']}&EIO=4&transport=websocket"
        )
        t.start(parts.hostname, parts.port, path, row["cookie"], origin)
        tunnels.append(t)
        await asyncio.sleep(0.05)  # zoals mensen die na elkaar openen, niet allemaal tegelijk
    return tunnels


async def _wait_answers(tunnels: list[Tunnel], seconds: float = 60) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if all(t.answered is not None or not t.alive for t in tunnels):
            return
        await asyncio.sleep(0.2)


def _summary(tunnels: list[Tunnel]) -> dict[str, Any]:
    kinds: dict[str, int] = {}
    for t in tunnels:
        for k in t.types:
            kinds[k] = kinds.get(k, 0) + 1
    return {
        "tunnels": len(tunnels),
        "upgraded": sum(1 for t in tunnels if t.status == 101),
        "statuses": sorted({t.status for t in tunnels if t.status is not None}),
        "answered": sum(1 for t in tunnels if t.answered is not None),
        "alive": sum(1 for t in tunnels if t.alive),
        "closed_by_peer": sum(1 for t in tunnels if t.closed_by_peer),
        "errors": sorted({t.error for t in tunnels if t.error}),
        "pings": sum(t.pings for t in tunnels),
        "unlocks": sum(t.unlocks for t in tunnels),
        "message_types": kinds,
        "refused": [t.name for t in tunnels if t.status not in (None, 101)],
        "dropped": [
            {"tunnel": t.name, "notes": [n for n in t.notes if n.get("type") != "license"]}
            for t in tunnels
            if t.closed_by_peer or (t.status == 101 and not t.alive)
        ],
        "license": next((n for t in tunnels for n in t.notes if n.get("type") == "license"), None),
    }


def _connections(bench: ob.Bench) -> Any:
    """Wat /beheer over de verbindingen zegt (O5), als de app het geeft."""
    r = bench.server.call(bench.office.admin, "GET", "/api/v1/admin/office", expect=200).json()
    wanted = ("connections", "people", "documents", "peak", "license_connections")
    return {k: r.get(k) for k in wanted if k in r}


def wave(
    bench: ob.Bench, captured: dict[str, Any], rows: list[dict[str, Any]], version: str
) -> dict[str, Any]:
    """Eén keer 50 tunnels open, meten, open houden, meten, dicht, meten."""
    pid = bench.pid
    quiet = ob.wait_quiet()
    base = rss(pid)
    cpu0 = ob.tree_cpu(pid)

    async def go() -> dict[str, Any]:
        tunnels = await _open_all(bench, captured, rows, version)
        await _wait_answers(tunnels)
        await asyncio.sleep(10)
        opened = await asyncio.to_thread(rss, pid)
        admin = await asyncio.to_thread(_connections, bench)
        state_open = _summary(tunnels)
        hold0 = ob.tree_cpu(pid)
        await asyncio.sleep(HOLD_S)
        hold_cpu = ob.tree_cpu(pid) - hold0
        held = await asyncio.to_thread(rss, pid)
        state_held = _summary(tunnels)
        for t in tunnels:
            await t.close()
        await asyncio.sleep(5)
        closed = await asyncio.to_thread(rss, pid)
        return {
            "open": state_open,
            "held": state_held,
            "admin": admin,
            "rss_open": opened,
            "rss_held": held,
            "rss_closed": closed,
            "cpu_hold_s": round(hold_cpu, 2),
        }

    out = run_async(go)
    cpu = ob.tree_cpu(pid) - cpu0
    return {
        "quiet": quiet,
        "rss_base": base,
        **out,
        "extra_open_mb": round((out["rss_open"] - base) / MB, 2),
        "extra_held_mb": round((out["rss_held"] - base) / MB, 2),
        "extra_closed_mb": round((out["rss_closed"] - base) / MB, 2),
        "cpu_s": round(cpu, 2),
    }


def one_round(
    url: str, secret: str, browser: Any, *, waves: int = 2, users: int = USERS, docs: int = DOCS
) -> dict[str, Any]:
    base = ob.tmpdir("office-tunnels-")
    bench = ob.start_office(base, url, secret)
    try:
        names = [f"stuk-{n + 1}.docx" for n in range(docs)]
        extra = ("vangst.docx", "opwarmen.docx")
        squad = ob.team(
            bench, dict.fromkeys([*names, *extra], fixture("proef.docx")), members=users - 1
        )
        ob.wait_background(bench.server)
        browsers = Browsers(browser, bench.server, office_frames="record")
        try:
            captured = capture(bench, browsers, squad.owner, squad.nodes["vangst.docx"])
        finally:
            browsers.close_all()
        version = captured["path"].split("/")[2]
        # De code van de tunnel laden: één tunnel op een eigen document open tot DS antwoordt,
        # en weer dicht
        warm = wave_one(bench, captured, sessions_for(bench, squad, ["opwarmen.docx"])[0], version)
        results = []
        for _ in range(waves):
            # Na een keer sluit DS elke sessie (status 4): elke keer dus nieuwe sessies
            wait_closed(bench)
            rows = sessions_for(bench, squad, names)
            results.append(wave(bench, captured, rows, version))
        return {
            "users": users,
            "docs": docs,
            "tunnels": users * docs,
            "warmup": warm,
            "waves": results,
            "guard": captured["problems"],
        }
    finally:
        bench.stop()
        shutil.rmtree(base, ignore_errors=True)


def wait_closed(bench: ob.Bench, seconds: float = 120) -> None:
    """Tot geen sessie meer open is (DS stuurt status 4 ±10 s na de laatste die wegging)."""
    import sqlite3

    db = bench.server.data_dir / "platform.db"
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        with sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=10) as conn:
            left = conn.execute(
                "SELECT count(*) FROM office_session WHERE state != 'closed'"
            ).fetchone()[0]
        if not left:
            return
        time.sleep(1)
    raise AssertionError(f"nog {left} sessies open na {seconds:.0f} s")


def wave_one(
    bench: ob.Bench, captured: dict[str, Any], row: dict[str, Any], version: str
) -> dict[str, Any]:
    async def go() -> dict[str, Any]:
        tunnels = await _open_all(bench, captured, [row], version)
        await _wait_answers(tunnels, 30)
        state = _summary(tunnels)
        for t in tunnels:
            await t.close()
        await asyncio.sleep(2)
        return state

    return run_async(go)


def measure(url: str, secret: str, rounds: int = 3) -> dict[str, Any]:
    from playwright.sync_api import sync_playwright

    result: dict[str, Any] = {
        "tool": "office_tunnels",
        "rounds": rounds,
        "gate": GATE,
        "hold_s": HOLD_S,
        "round_results": [],
    }
    with sync_playwright() as pw:
        browser = launch_browser(pw)
        try:
            for n in range(rounds):
                r = one_round(url, secret, browser)
                result["round_results"].append(r)
                first = r["waves"][0]
                print(
                    f"ronde {n + 1}: +{first['extra_open_mb']} MB open,"
                    f" +{first['extra_held_mb']} MB na {HOLD_S:.0f} s,"
                    f" open {first['open']['alive']}/{first['open']['tunnels']}",
                    file=sys.stderr,
                )
            result["machine"] = ob.machine(url, browser.version)
        finally:
            browser.close()
    firsts = [r["waves"][0] for r in result["round_results"]]
    seconds = [w for r in result["round_results"] for w in r["waves"][1:]]
    result["first_wave"] = {
        "extra_open_mb": ob.stats([w["extra_open_mb"] for w in firsts], 2),
        "extra_held_mb": ob.stats([w["extra_held_mb"] for w in firsts], 2),
        "extra_closed_mb": ob.stats([w["extra_closed_mb"] for w in firsts], 2),
    }
    result["later_waves"] = {
        "extra_open_mb": ob.stats([w["extra_open_mb"] for w in seconds], 2),
        "extra_held_mb": ob.stats([w["extra_held_mb"] for w in seconds], 2),
    }
    result["ok"] = not check(result)
    return result


def check(result: dict[str, Any]) -> list[str]:
    errors = []
    for n, r in enumerate(result["round_results"], start=1):
        errors += [f"ronde {n}: bewaking: {p}" for p in r["guard"]]
        for m, w in enumerate(r["waves"], start=1):
            if w["held"]["alive"] != w["held"]["tunnels"]:
                errors.append(
                    f"ronde {n}, keer {m}: {w['held']['alive']} van {w['held']['tunnels']}"
                    f" tunnels nog open na {HOLD_S:.0f} s"
                )
    worst = max((result["first_wave"][k]["max"] or 0) for k in ("extra_open_mb", "extra_held_mb"))
    if not report_only() and worst > GATE["extra_mb"]:
        errors.append(f"50 tunnels: tot +{worst} MB > {GATE['extra_mb']} MB")
    return errors


@pytest.mark.skipif(ob.real_ds() is None, reason="geen echte DS (E2E_REAL_DS)")
def test_office_tunnels() -> None:
    url, secret = ob.need_real_ds()
    result = measure(url, secret, rounds=1)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    write_result("office_tunnels", result)
    errors = check(result)
    assert not errors, "\n".join(errors)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Geheugen van de app met 50 tunnels (§4.7)")
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--out", type=Path, help="JSON ook naar dit bestand")
    args = parser.parse_args(argv)
    url, secret = ob.need_real_ds()
    result = measure(url, secret, args.rounds)
    text = json.dumps(result, indent=2, ensure_ascii=False)
    print(text)
    if args.out:
        args.out.write_text(text + "\n")
    write_result("office_tunnels", result)
    errors = check(result)
    for e in errors:
        print(f"FOUT {e}", file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
