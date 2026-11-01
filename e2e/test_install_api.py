"""install_api.py zelf (het harnas van de installatietests), zonder browser of platform: een kleine
HTTP-server die antwoordt met de vormen van de echte API (de modellen uit app.media.types).

De eerste run van test_install_media.sh --stappen in CI (job media-image) liep hier op stuk:
`post` zonder veld gaf niets terug (2-play.json bleef leeg), het veld worker.sandbox bestaat niet
(de zelftest staat in worker.selftest, Keuze 45), en elke fout was een traceback zonder
HTTP-status. Nu: de hele JSON, en bij een antwoord dat niet klopt één regel met de methode, het
pad, de status en het begin van de body.
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


class Fake(BaseHTTPRequestHandler):
    """GET en POST op een paar vaste paden; /api/v1/admin/media-later geeft eerst één keer een
    worker zonder zelftest (de app sprak de worker nog niet), daarna wel."""

    later = 0

    def answer(self, status: int, body: bytes, kind: str = "application/json") -> None:
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        on = Selftest(ok=True, sandbox="on")
        if self.path == "/api/v1/admin/media":
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
