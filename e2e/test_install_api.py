"""install_api.py zelf (het harnas van de installatietests), zonder browser of platform: een kleine
HTTP-server die antwoordt met de vormen van de echte API (de modellen uit app.media.types).

De eerste run van test_install_media.sh --stappen in CI (job media-image) liep hier op stuk:
`post` zonder veld gaf niets terug (2-play.json bleef leeg), het veld worker.sandbox bestaat niet
(de zelftest staat in worker.selftest, Keuze 45), en elke fout was een traceback zonder
HTTP-status. Nu: de hele JSON, en bij een antwoord dat niet klopt één regel met de methode, het
pad, de status en het begin van de body.

wait, watch en wait-field (ook van test_install_update.sh) proberen het opnieuw na een andere
status of zonder verbinding (een herstart), maar nooit na een 200 waarvan de body niet klopt:
dat was vóór deze fix een fout (een JSONDecodeError) en blijft een fout.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from app.media.types import (
    MediaAdmin,
    PlayOut,
    PlayUrls,
    QualityInfo,
    Selftest,
    WorkerView,
)

SCRIPT = Path(__file__).with_name("install_api.py")
NOW = datetime(2026, 11, 1, 18, 0, tzinfo=UTC)


def admin(selftest: Selftest | None) -> bytes:
    view = MediaAdmin(worker=WorkerView(mode="container", state="ok", since=NOW, selftest=selftest))
    return view.model_dump_json(by_alias=True).encode()


PLAY = PlayOut(
    sid="film.mp4.geheim",
    mode="transcode",
    reasons=["bitrate"],
    urls=PlayUrls(map="/api/v1/stream/film.mp4.geheim/map.json"),
    mime="video/mp4",
    duration_ms=120_000,
    quality=QualityInfo(current="auto"),
    started_at=NOW,
    expires_at=NOW + timedelta(hours=4),
).model_dump_json()


HTML = (200, b"<html><body>even niet</body></html>", "text/html")
OFFICE_OK = (200, b'{"state": "ok", "editing": false}', "application/json")


class Fake(BaseHTTPRequestHandler):
    """GET en POST op een paar vaste paden; /api/v1/admin/media-later geeft eerst één keer een
    worker zonder zelftest (de app sprak de worker nog niet), daarna wel. Een pad in script
    geeft zijn antwoorden op volgorde (het laatste blijft), en hits telt de aanvragen."""

    later = 0
    script: dict[str, list[tuple[int, bytes, str]]] = {}
    hits: dict[str, int] = {}

    def answer(self, status: int, body: bytes, kind: str = "application/json") -> None:
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        on = Selftest(ok=True, sandbox="on")
        Fake.hits[self.path] = Fake.hits.get(self.path, 0) + 1
        if self.path in Fake.script:
            queue = Fake.script[self.path]
            self.answer(*(queue.pop(0) if len(queue) > 1 else queue[0]))
        elif self.path == "/api/v1/admin/media":
            self.answer(200, admin(on))
        elif self.path == "/api/v1/admin/media-later":
            Fake.later += 1
            self.answer(200, admin(on if Fake.later > 1 else None))
        elif self.path == "/api/v1/admin/media-nooit":
            self.answer(200, admin(None))
        elif self.path == "/leeg":
            self.answer(200, b"")
        elif self.path == "/kapot":
            self.answer(502, b"<html><body>502 Bad Gateway</body></html>", "text/html")
        else:
            self.answer(404, b'{"detail": {"code": "not_found"}}')

    def do_POST(self) -> None:
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        if self.path.endswith("/play"):
            self.answer(200, PLAY.encode())
        elif self.path == "/niets":
            self.send_response(204)
            self.end_headers()
        elif self.path == "/leeg":
            self.answer(200, b"")
        else:
            self.answer(404, b'{"detail": {"code": "not_found"}}')

    def log_message(self, *args: object) -> None:  # stil
        pass


@pytest.fixture
def api(tmp_path: Path) -> Iterator:
    server = ThreadingHTTPServer(("127.0.0.1", 0), Fake)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    state = tmp_path / "sessie.json"
    state.write_text("{}")
    env = {
        **os.environ,
        "INSTALL_API_URL": f"http://127.0.0.1:{server.server_address[1]}",
        "INSTALL_API_STATE": str(state),
    }

    def run(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-I", str(SCRIPT), *args],
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )

    Fake.later = 0
    Fake.script = {}
    Fake.hits = {}
    yield run
    server.shutdown()
    server.server_close()


def test_post_zonder_veld_geeft_de_hele_json(api) -> None:
    """Zo leest stap_transcode het antwoord van play (2-play.json): mode, reasons en sid."""
    out = api("post", "/api/v1/media/n/play", '{"profile": {}}')
    assert out.returncode == 0, out.stderr
    body = json.loads(out.stdout)
    assert (body["mode"], body["reasons"], body["sid"]) == (
        "transcode",
        ["bitrate"],
        "film.mp4.geheim",
    )
    one = api("post", "/api/v1/media/n/play", '{"profile": {}}', "mode")
    assert (one.returncode, one.stdout) == (0, "transcode\n")


def test_post_met_204_geeft_niets(api) -> None:
    out = api("post", "/niets", "{}")
    assert (out.returncode, out.stdout, out.stderr) == (0, "", "")


def test_de_zelftest_staat_in_worker_selftest(api) -> None:
    """Het contract van GET /api/v1/admin/media (Keuze 45): worker.selftest {ok, sandbox,
    problems}; een veld worker.sandbox is er niet, en dat zegt de fout met het begin van de
    body erbij."""
    out = api("get", "/api/v1/admin/media", "worker.selftest")
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout) == {"ok": True, "sandbox": "on", "problems": []}
    assert api("get", "/api/v1/admin/media", "worker.selftest.sandbox").stdout == "on\n"
    wrong = api("get", "/api/v1/admin/media", "worker.sandbox")
    assert wrong.returncode == 1
    assert "Traceback" not in wrong.stderr
    assert (
        "GET /api/v1/admin/media gaf 200, maar zonder het veld worker.sandbox (bij 'sandbox')"
        in wrong.stderr
    )
    assert '"selftest":{"ok":true' in wrong.stderr


def test_geen_json_of_een_andere_status_zegt_status_en_body(api) -> None:
    empty = api("get", "/leeg")
    assert empty.returncode == 1
    assert "GET /leeg gaf 200, maar geen JSON: b''" in empty.stderr
    broken = api("get", "/kapot", "x")
    assert broken.returncode == 1
    assert "GET /kapot gaf 502: b'<html><body>502 Bad Gateway" in broken.stderr
    bad = api("post", "/api/v1/media/n/play", "")
    assert bad.returncode == 2
    assert "de body is geen JSON" in bad.stderr
    for out in (empty, broken, bad):
        assert "Traceback" not in out.stderr


def test_wait_field_wacht_tot_de_app_de_worker_sprak(api) -> None:
    """Na een update start de app vóór de worker: de zelftest staat pas op /beheer als de lus
    van health.py de worker sprak. wait-field wacht daarop; blijft hij null, dan exit 1 met wat
    er het laatst kwam."""
    out = api("wait-field", "/api/v1/admin/media-later", "worker.selftest", "30")
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout) == {"ok": True, "sandbox": "on", "problems": []}
    assert Fake.later == 2
    never = api("wait-field", "/api/v1/admin/media-nooit", "worker.selftest", "1")
    assert never.returncode == 1
    assert (
        "na 1 s nog geen worker.selftest: GET /api/v1/admin/media-nooit gaf worker.selftest null"
        in never.stderr
    )


def test_post_met_een_lege_200_is_een_fout(api) -> None:
    """Alleen een 204 heeft geen body. Een lege 200 (zo bleef 2-play.json in CI leeg) zegt dat."""
    out = api("post", "/leeg", "{}")
    assert out.returncode == 1
    assert out.stdout == ""
    assert "POST /leeg gaf 200, maar geen JSON: b''" in out.stderr
    assert "Traceback" not in out.stderr


def test_wait_en_watch_nemen_een_200_zonder_json_niet_aan(api, tmp_path: Path) -> None:
    """Een 200 met een body die geen JSON is, was vóór de fix een JSONDecodeError (de controle
    faalde) en blijft een fout: geen tweede poging, dus het ok daarna telt niet (de controles
    K, V en R1 en de tijdlijn b-status.txt van test_install_update.sh)."""
    path = "/api/v1/office/status"
    Fake.script = {path: [HTML, OFFICE_OK]}
    out = api("wait", "ok", "10")
    assert out.returncode == 1
    assert f"GET {path} gaf 200, maar geen JSON: b'<html>" in out.stderr
    assert "Traceback" not in out.stderr
    assert Fake.hits[path] == 1
    Fake.script = {path: [HTML, OFFICE_OK]}
    Fake.hits = {}
    timeline = tmp_path / "b-status.txt"
    out = api("watch", "10", str(timeline))
    assert out.returncode == 1
    assert f"GET {path} gaf 200, maar geen JSON" in out.stderr
    assert "Traceback" not in out.stderr
    assert Fake.hits[path] == 1
    assert " ok " not in timeline.read_text()
    # JSON zonder het veld state: ook een fout, meteen
    Fake.script = {path: [(200, b'{"editing": false}', "application/json"), OFFICE_OK]}
    Fake.hits = {}
    out = api("wait", "ok", "10")
    assert out.returncode == 1
    assert f"GET {path} gaf 200, maar zonder het veld state" in out.stderr
    assert Fake.hits[path] == 1


def test_wait_en_watch_proberen_een_andere_status_opnieuw(api, tmp_path: Path) -> None:
    """Tijdens een herstart geeft de app even een andere status: dat is geen fout, wait en
    watch vragen opnieuw tot ok (zoals vóór de fix; dit bewaakt dat de fix dat niet brak)."""
    path = "/api/v1/office/status"
    busy = (503, b'{"detail": {"code": "starting"}}', "application/json")
    Fake.script = {path: [busy, OFFICE_OK]}
    out = api("wait", "ok", "20")
    assert out.returncode == 0, out.stderr
    assert Fake.hits[path] == 2
    Fake.script = {path: [busy, OFFICE_OK]}
    timeline = tmp_path / "b-status.txt"
    out = api("watch", "20", str(timeline))
    assert out.returncode == 0, out.stderr
    states = [line.split()[1] for line in timeline.read_text().splitlines()]
    assert len(states) == 2 and states[0].startswith("fout:") and states[1] == "ok", states


def test_wait_field_neemt_een_200_zonder_json_niet_aan(api) -> None:
    """wait-field wacht op null en op een andere status, niet op een body die niet klopt."""
    path = "/api/v1/admin/media-html"
    Fake.script = {path: [HTML, (200, admin(Selftest(ok=True, sandbox="on")), "application/json")]}
    out = api("wait-field", path, "worker.selftest", "20")
    assert out.returncode == 1
    assert out.stdout == ""
    assert f"GET {path} gaf 200, maar geen JSON: b'<html>" in out.stderr
    assert "Traceback" not in out.stderr
    assert Fake.hits[path] == 1
