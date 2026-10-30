"""M4, "Klaar als": de start van een stroom zonder browser (marker media_perf).

Een virtuele speler zoals die van §4.6: hij praat via http met de app (dus de beslissing, de
toelating, de controle, de producent, de knipper en de cache doen mee) en begint zodra segment 0
helemaal binnen is. Gemeten: van POST play tot en met segment 0 (map.json, de init en het
segment), met de worker op cores 0–2 (regel 14; de fixture media zet hem vast voor media_perf).

- transcode 1080 naar 1080, alleen: ≤ 2,5 s;
- hetzelfde terwijl er al 2 transcodes lopen (1080 veryfast en 720 superfast; de derde krijgt
  van de toelating ook 720 superfast): ≤ 5 s;
- na een sprong naar 70 %: het segment daar ≤ 2 s (een nieuwe run met -ss);
- remux: ≤ 0,5 s.

De bron: 60 s 1080p24 H.264 met ruis (6 Mbit/s, een keyframe per 2 s) en AAC in Matroska, zoals
de bronnen van de belastingtest maar korter. Het profiel kan H.264 en AAC (de virtuele speler
decodeert niet) met max.bitrate_kbps 4000: de bron van 6 Mbit/s gaat dan naar transcode (reden
bitrate), zoals in §4.6; zonder die grens is het remux (reden container).

Elke meting drie keer. Een rapport (media-m4-start.json in $E2E_PERF_OUT); met
E2E_PERF_REPORT_ONLY=1 (CI) faalt de test nooit op een grens.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

import pytest

from conftest import report_only, write_result
from media_harness import PERF_CPUS
from test_media_m3 import codecs_of, login, play

RUNS = 3
GATES_S = {"transcode_1080": 2.5, "naast_twee": 5.0, "sprong_70": 2.0, "remux": 0.5}
SOURCE_S = 60


@pytest.fixture(scope="module")
def source(tmp_path_factory: pytest.TempPathFactory) -> Any:
    """De bron (eenmalig gemaakt, buiten de app; daarna weer weg)."""
    work = tmp_path_factory.mktemp("m4-perf")
    path = work / "bron-1080.mkv"
    lavfi = "testsrc2=size=1920x1080:rate=24,noise=alls=20:allf=t"
    subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i", lavfi,
         "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000", "-t", str(SOURCE_S),
         "-c:v", "libx264", "-preset", "ultrafast", "-b:v", "6M", "-maxrate", "6M",
         "-bufsize", "12M", "-x264-params", "keyint=48:min-keyint=48:scenecut=0",
         "-threads", "3", "-c:a", "aac", "-b:a", "128k", "-shortest", str(path)],
        check=True, timeout=240,
    )  # fmt: skip
    yield path
    shutil.rmtree(work, ignore_errors=True)


def upload(media: Any, account: Any, parent: str, path: Path) -> str:
    size = path.stat().st_size
    with open(path, "rb") as body:
        r = media.server.call(
            account, "PUT", f"/api/v1/files/{parent}/upload", params={"name": path.name},
            content=body, headers={"Content-Length": str(size)}, expect=201,
        )  # fmt: skip
    return r.json()["id"]


def h264_profile(strings: list[str], bitrate_kbps: int = 0) -> dict[str, Any]:
    """Een speler met H.264 en AAC via MSE (geen HEVC, geen Hi10P), tot 1080."""

    def plays(s: str) -> bool:
        return s.startswith(("avc1.", "mp4a.40.")) and not s.lower().startswith("avc1.6e")

    codecs = {
        s: {"mse": True, "file": True, "smooth": True, "power": True}
        if plays(s)
        else {"mse": False, "file": False}
        for s in dict.fromkeys(strings)
    }
    return {
        "v": 1, "base": "web", "client": {"kind": "web", "name": "virtuele speler M4"},
        "max": {"height": 1080, "bitrate_kbps": bitrate_kbps}, "codecs": codecs,
    }  # fmt: skip


class Player:
    """De virtuele speler: play, en dan map.json, de init en de segmenten die hij vraagt."""

    def __init__(self, media: Any, token: str, node: str, profile: dict[str, Any]) -> None:
        self.media, self.token, self.node, self.profile = media, token, node, profile
        self.sid = ""
        self.out: dict[str, Any] = {}
        self.map: dict[str, Any] = {}

    def start(self) -> float:
        """Van POST play tot en met segment 0; geeft de seconden."""
        began = time.perf_counter()
        self.out = play(self.media, self.token, self.node, {"profile": self.profile})
        self.sid = self.out["sid"]
        self.map = self.get("map.json").json()
        self.get(self.map["init"])
        self.get("0.m4s")
        return time.perf_counter() - began

    def height(self) -> int:
        """De hoogte uit de init: de trede die de toelating koos."""
        from app.media import fmp4

        cutter = fmp4.Cutter()
        items = cutter.feed(self.get(self.map["init"]).content)
        init = next(i for i in items if isinstance(i, fmp4.Init))
        return init.video.height if init.video is not None else 0

    def count(self) -> int:
        segments = self.map["segments"]
        return len(segments) if isinstance(segments, list) else segments["count"]

    def seek(self, fraction: float) -> tuple[int, float]:
        """Een sprong: het segment op fraction van de duur; geeft (n, seconden)."""
        n = int(self.count() * fraction)
        began = time.perf_counter()
        self.get(f"{n}.m4s")
        return n, time.perf_counter() - began

    def get(self, path: str) -> Any:
        r = self.media.server.request("GET", f"/api/v1/stream/{self.sid}/{path}", timeout=60)
        assert r.status_code == 200, (path, r.status_code, r.text[:300])
        return r

    def close(self) -> None:
        if self.sid:
            self.media.server.request(
                "DELETE", f"/api/v1/stream/{self.sid}", token=self.token, expect=204
            )
            self.sid = ""


def settle(ffmpeg_count: Any) -> None:
    """Tot er geen ffmpeg meer loopt (de vorige run, een preview of de probe)."""
    assert not ffmpeg_count.wait_zero(30), "ffmpeg loopt nog"


@pytest.mark.media_perf
def test_m4_de_start_van_een_stroom(media, ffmpeg_count, source):
    if PERF_CPUS <= os.sched_getaffinity(0):
        assert os.sched_getaffinity(media.worker_pid()) == PERF_CPUS
    anna, bob = media.user(), media.user()
    folders = {a.id: media.folder(a, "M4 start") for a in (anna, bob)}
    tokens = {a.id: login(media, a)["access_token"] for a in (anna, bob)}
    nodes = {a.id: upload(media, a, folders[a.id], source) for a in (anna, bob)}
    found = codecs_of(media, tokens[anna.id], nodes[anna.id])
    strings = found["candidates"]
    # Eén keer vooraf: de probe en de keyframe-index bij play, dan de strings van de bron
    warm = Player(media, tokens[anna.id], nodes[anna.id], h264_profile(strings))
    warm.start()
    warm.close()
    strings += codecs_of(media, tokens[anna.id], nodes[anna.id])["item"]
    capped, free = h264_profile(strings, 4000), h264_profile(strings)
    settle(ffmpeg_count)

    def player(account: Any, profile: dict[str, Any]) -> Player:
        return Player(media, tokens[account.id], nodes[account.id], profile)

    result: dict[str, Any] = {k: [] for k in GATES_S}
    result["treden"] = []
    # remux
    for _ in range(RUNS):
        p = player(anna, free)
        result["remux"].append(round(p.start(), 3))
        assert p.out["mode"] == "remux" and p.out["reasons"] == ["container"], p.out
        p.close()
        settle(ffmpeg_count)
    # transcode 1080 naar 1080, alleen; daarna de sprong naar 70 %
    for _ in range(RUNS):
        p = player(anna, capped)
        result["transcode_1080"].append(round(p.start(), 3))
        assert p.out["mode"] == "transcode" and "bitrate" in p.out["reasons"], p.out
        assert p.map["mime"].startswith('video/mp4; codecs="avc1.64'), p.map
        n, took = p.seek(0.7)
        result["sprong_70"].append(round(took, 3))
        result["treden"].append({"alleen": p.height(), "sprong_naar": n, "van": p.count()})
        p.close()
        settle(ffmpeg_count)
    # naast twee lopende transcodes (twee kijkers: hooguit 2 per gebruiker)
    for _ in range(RUNS):
        first, second = player(anna, capped), player(anna, capped)
        first.start()
        second.start()
        third = player(bob, capped)
        result["naast_twee"].append(round(third.start(), 3))
        assert third.out["mode"] == "transcode", third.out
        result["treden"].append({"drie": [x.height() for x in (first, second, third)]})
        for x in (first, second, third):
            x.close()
        settle(ffmpeg_count)
    report = {
        "seconden": {k: result[k] for k in GATES_S},
        "max": {k: max(result[k]) for k in GATES_S},
        "grenzen": GATES_S,
        "treden": result["treden"],
        "worker_cores": sorted(media.cpus or []),
        "ffmpeg_max": ffmpeg_count.max,
        "report_only": report_only(),
    }
    path = write_result("media-m4-start", report)
    print(json.dumps(report, ensure_ascii=False, indent=1))
    if path is not None:
        print(f"rapport: {path}")
    assert ffmpeg_count.max <= 3
    if report_only():
        return
    over = {k: v for k, v in report["max"].items() if v > GATES_S[k]}
    assert not over, f"boven de grens: {over} (grenzen {GATES_S})"
