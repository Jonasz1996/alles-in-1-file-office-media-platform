"""Mb zonder browser, Mk, Mm en Mj (§4.5): de start van een stroom, de factor f per trede, de
tegendruk en de kosten van het knippen in de app.

Een eigen app (kit.App: de lokale worker onder taskset -c 0-2, PLATFORM_MEDIA_MAX_PROCS=3) en een
virtuele speler zoals die van M4 (e2e/test_media_m4_perf.py): POST play, dan map.json, de init
en de segmenten, via http. De bron: 1080p24 H.264 met ruis (6 Mbit/s, een keyframe per 2 s) en
AAC, zoals de bronnen van de belastingtest; het profiel kan H.264 en AAC tot 1080 met
max.bitrate_kbps 4000, dus transcode (reden bitrate).

- **Mb**, van POST play tot en met segment 0: 1080 naar 1080 alleen (max_transcodes 1, dus
  zonder de reserve voor andere kijkers), 1080 naar 720 alleen (de kwaliteit 720), naast 2
  lopende transcodes, en alle drie tegelijk (die met max_transcodes 3). Grens: alleen ≤ 2,5 s
  (mediaan) en ≤ 3,0 s (slechtste); naast 2 andere ≤ 5 s.
- **Mk**, per trede (de toelating gestuurd met cpu_budget, max_transcodes 1 en de kwaliteit): de
  CPU van ffmpeg en de snelheid terwijl de producent vrij loopt (de speler vraagt niets meer na
  segment 0, de voorsprong groeit tot 60 s). Gemeten kosten = cores / snelheid (cores voor
  1,0×); voorspeld = decide.cost met f en f_dec van de meting. Grens: binnen 15 %.
- **Mm**: daarna, als de voorsprong niet meer groeit (30 segmenten, de tegendruk), 4 s de CPU
  van de worker en ffmpeg. Grens: ≈ 0.
- **Mj**: 3 spelers die op 1× afspelen (30 s buffer, zoals de speler) en 60 s de CPU van het
  proces van de app (het knippen, de cache en de http): per stroom ≤ 0,05 core.

    cd api && uv run python -m tests.media.metingen.start --herhaal 5 --uit /tmp/start.json
"""

from __future__ import annotations

import argparse
import contextlib
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

import httpx

from tests.media.metingen import kit

SOURCE_S = 180
MK_RUNGS = ("1080 veryfast", "1080 superfast", "720 veryfast", "720 superfast", "480 veryfast")
LEAD_S = 60.0
BUFFER_S = 30.0
QUIET = {"cores": 0.8, "wacht_s": 120.0, "gemeten": []}


def rest() -> None:
    """Even wachten tot de machine stil is (hooguit wacht_s); wat er liep, staat in het rapport."""
    QUIET["gemeten"].append(kit.quiet(QUIET["cores"], QUIET["wacht_s"]))


def make_source(work: Path, seconds: int = SOURCE_S) -> Path:
    """De bron (zoals in de e2e van M4, maar langer), met ffmpeg op de cores van de worker."""
    path = work / "bron-1080.mkv"
    if path.exists():
        return path
    lavfi = "testsrc2=size=1920x1080:rate=24,noise=alls=20:allf=t"
    subprocess.run(
        ["taskset", "-c", "0-2", "ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "lavfi",
         "-i", lavfi, "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
         "-t", str(seconds), "-c:v", "libx264", "-preset", "ultrafast", "-b:v", "6M",
         "-maxrate", "6M", "-bufsize", "12M",
         "-x264-params", "keyint=48:min-keyint=48:scenecut=0", "-threads", "3",
         "-c:a", "aac", "-b:a", "128k", "-shortest", str(path)],
        check=True, timeout=900,
    )  # fmt: skip
    return path


def h264_profile(strings: list[str], bitrate_kbps: int = 4000) -> dict[str, Any]:
    """Een speler met H.264 en AAC via MSE (geen HEVC, geen Hi10P), tot 1080 (zoals M4)."""

    def plays(s: str) -> bool:
        return s.startswith(("avc1.", "mp4a.40.")) and not s.lower().startswith("avc1.6e")

    codecs = {
        s: {"mse": True, "file": True, "smooth": True, "power": True}
        if plays(s)
        else {"mse": False, "file": False}
        for s in dict.fromkeys(strings)
    }
    return {
        "v": 1, "base": "web", "client": {"kind": "web", "name": "meting fase 5"},
        "max": {"height": 1080, "bitrate_kbps": bitrate_kbps}, "codecs": codecs,
    }  # fmt: skip


class Player:
    """De virtuele speler van M4: play, map.json, de init en de segmenten."""

    def __init__(self, app: kit.App, node: str, profile: dict[str, Any], quality: Any = "auto"):
        self.app, self.node, self.profile, self.quality = app, node, profile, quality
        self.http = httpx.Client(base_url=app.url, trust_env=False, timeout=60)
        self.sid = ""
        self.out: dict[str, Any] = {}
        self.map: dict[str, Any] = {}
        self.took: float | None = None
        self.next = 1
        self.error: str | None = None

    def play(self) -> dict[str, Any]:
        body = {"profile": self.profile, "quality": self.quality}
        end = time.monotonic() + 120
        while True:
            r = self.app.call("POST", f"/api/v1/media/{self.node}/play", json=body)
            if r.status_code == 200:
                return r.json()
            detail = r.json().get("detail", {})
            again = (r.status_code == 409 and detail.get("code") == "media_probe_pending") or (
                r.status_code == 503 and (detail.get("params") or {}).get("reason") == "worker"
            )
            if not again or time.monotonic() > end:
                raise RuntimeError(f"play: {r.status_code} {r.text[:300]}")
            time.sleep(float(r.headers.get("retry-after", "1")))

    def start(self) -> float:
        """Van POST play tot en met segment 0; geeft de seconden."""
        began = time.perf_counter()
        self.out = self.play()
        self.sid = self.out["sid"]
        self.map = self.get("map.json").json()
        self.get(self.map["init"])
        self.get("0.m4s")
        self.took = time.perf_counter() - began
        return self.took

    def height(self) -> int:
        from app.media import fmp4

        items = fmp4.Cutter().feed(self.get(self.map["init"]).content)
        init = next(i for i in items if isinstance(i, fmp4.Init))
        return init.video.height if init.video is not None else 0

    def count(self) -> int:
        segments = self.map["segments"]
        return len(segments) if isinstance(segments, list) else segments["count"]

    def get(self, path: str) -> httpx.Response:
        r = self.http.get(f"/api/v1/stream/{self.sid}/{path}")
        if r.status_code != 200:
            raise RuntimeError(f"{path}: {r.status_code} {r.text[:200]}")
        return r

    def play_realtime(self, seconds: float, stop: threading.Event) -> None:
        """Afspelen op 1× met een buffer van 30 s (zoals de speler): segment n zodra het eind
        van de buffer minder dan 30 s voor de afspeeltijd ligt."""
        began = time.monotonic()
        try:
            while not stop.is_set() and time.monotonic() - began < seconds:
                now_s = time.monotonic() - began
                while self.next < self.count() and self.next * 2.0 - now_s < BUFFER_S:
                    self.get(f"{self.next}.m4s")
                    self.next += 1
                stop.wait(0.2)
        except Exception as exc:  # noqa: BLE001 - voor het rapport
            self.error = str(exc)[:200]

    def close(self) -> None:
        if self.sid:
            with contextlib.suppress(Exception):
                self.app.call("DELETE", f"/api/v1/stream/{self.sid}")
            self.sid = ""
        self.http.close()


def settle(app: kit.App, seconds: float = 60) -> None:
    """Tot er geen ffmpeg meer onder de worker loopt."""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        worker = app.worker_pid()
        if worker is None:
            return
        procs = kit.table()
        if not [p for p in kit.descendants(worker, procs) if p.comm in kit.FFMPEG]:
            return
        time.sleep(0.25)
    raise RuntimeError("ffmpeg loopt nog")


def worker_ticks(app: kit.App) -> int:
    """De CPU van de worker, zijn levende kinderen en de kinderen die al weg zijn (ticks)."""
    worker = app.worker_pid()
    if worker is None:
        return 0
    procs = kit.table()
    me = procs.get(worker)
    if me is None:
        return 0
    return me.ticks + me.child_ticks + sum(p.ticks for p in kit.descendants(worker, procs))


def app_ticks(app: kit.App) -> int:
    found = kit.proc(app.pid) if app.pid else None
    return found.ticks if found is not None else 0


def ahead_of(app: kit.App, sid_session: str | None = None) -> float | None:
    """De voorsprong van de (enige) transcode op /beheer, in s."""
    sessions = app.call("GET", "/api/v1/admin/media").json().get("sessions") or []
    for s in sessions:
        if s.get("mode") == "transcode" and s.get("ahead_s") is not None:
            return float(s["ahead_s"])
    return None


# --- de metingen ---------------------------------------------------------------------------------


def mb(app: kit.App, node: str, profile: dict[str, Any], runs: int) -> dict[str, Any]:
    out: dict[str, Any] = {"alleen_1080": [], "alleen_720": [], "naast_2": [], "drie_tegelijk": []}
    heights: dict[str, list[Any]] = {k: [] for k in out}
    for _ in range(runs):
        for key, quality in (("alleen_1080", "auto"), ("alleen_720", 720)):
            # 1080 naar 1080 alleen: één plaats (max_transcodes 1), dus geen reserve voor
            # andere kijkers; anders kiest de toelating hier 720 (de reserve van Keuze 26)
            app.config(max_transcodes=1 if key == "alleen_1080" else 3)
            rest()
            p = Player(app, node, profile, quality)
            out[key].append(round(p.start(), 3))
            heights[key].append(p.height())
            p.close()
            settle(app)
        rest()
        first, second = Player(app, node, profile), Player(app, node, profile)
        first.start()
        second.start()
        third = Player(app, node, profile)
        out["naast_2"].append(round(third.start(), 3))
        heights["naast_2"].append([x.height() for x in (first, second, third)])
        for x in (first, second, third):
            x.close()
        settle(app)
        rest()
        players = [Player(app, node, profile) for _ in range(3)]
        threads = [threading.Thread(target=p.start) for p in players]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)
        out["drie_tegelijk"].append([round(p.took, 3) if p.took else None for p in players])
        heights["drie_tegelijk"].append([p.height() if p.sid else None for p in players])
        for p in players:
            p.close()
        settle(app)
    flat = [t for run in out["drie_tegelijk"] for t in run]
    return {
        "alleen_1080": kit.spread(out["alleen_1080"]),
        "alleen_720": kit.spread(out["alleen_720"]),
        "naast_2": kit.spread(out["naast_2"]),
        "drie_tegelijk": {**kit.spread(flat), "per_run": out["drie_tegelijk"]},
        "treden": heights,
        "grenzen": {"alleen_mediaan": 2.5, "alleen_slechtste": 3.0, "naast_2": 5.0},
    }


def predicted(rung: str, bench: dict[str, Any]) -> float:
    """decide.cost voor deze trede en de bron (H.264 1080p24, 8 bit), met f en f_dec."""
    from app.media import decide
    from app.media.types import BudgetView, Rung, Stream

    height, preset = rung.split()
    video = Stream(index=0, type="video", codec="h264", width=1920, height=1080, fps=24.0,
                   bit_depth=8)  # fmt: skip
    view = BudgetView(
        budget=16, in_use=0, places_free=1, user_free=1,
        factor=float(bench.get("f") or 1.0), factor_dec=float(bench.get("f_dec") or 1.0),
    )  # fmt: skip
    r = Rung(height=int(height), preset=preset, codec="h264")  # type: ignore[arg-type]
    return decide.cost(r, video, hdr=False, burn=False, budget=view)


def steer(rung: str, bench: dict[str, Any]) -> tuple[float, Any]:
    """(cpu_budget, kwaliteit) waarbij de toelating met max_transcodes 1 deze trede kiest: de
    trede past (kosten ≤ 0,9 B), de betere erboven niet."""
    order = {
        "1080 veryfast": None, "1080 superfast": "1080 veryfast",
        "720 veryfast": None, "720 superfast": "720 veryfast", "480 veryfast": None,
    }  # fmt: skip
    quality: Any = {"1080": "auto", "720": 720, "480": 480}[rung.split()[0]]
    better = order[rung]
    own = predicted(rung, bench)
    if better is None:
        return 16.0, quality
    top = predicted(better, bench)
    return round(max(0.5, (own + top) / 2 / 0.9), 3), quality


def mk_mm(app: kit.App, node: str, profile: dict[str, Any], bench: dict, runs: int) -> dict:
    """Per trede: de kosten (Mk) terwijl de producent vrij loopt, en daarna de tegendruk (Mm)."""
    app.config(max_transcodes=1)
    rows: dict[str, list[dict[str, Any]]] = {r: [] for r in MK_RUNGS}
    mm: list[float] = []
    try:
        for _ in range(runs):
            for rung in MK_RUNGS:
                budget, quality = steer(rung, bench)
                app.config(cpu_budget=budget)
                rest()
                p = Player(app, node, profile, quality)
                p.start()
                height = p.height()
                points: list[tuple[float, float, int]] = []
                end = time.monotonic() + 120
                while time.monotonic() < end:
                    ahead = ahead_of(app)
                    points.append((time.monotonic(), ahead or 0.0, worker_ticks(app)))
                    if len(points) >= 4 and points[-1][1] <= points[-4][1] + 0.05:
                        break  # gepauzeerd: de voorsprong groeit niet meer
                    time.sleep(1.0)
                # het venster waarin hij vrij liep: vanaf 4 s voorsprong tot 4 s voor de rem
                free = [x for x in points if 4.0 <= x[1] <= LEAD_S - 4.0]
                row: dict[str, Any] = {"cpu_budget": budget, "hoogte": height}
                if len(free) >= 3:
                    (t0, a0, k0), (t1, a1, k1) = free[0], free[-1]
                    speed = (a1 - a0) / (t1 - t0)
                    cores = (k1 - k0) / kit.TICK / (t1 - t0)
                    row |= {
                        "snelheid": round(speed, 3),
                        "cores": round(cores, 3),
                        "kosten_gemeten": round(cores / speed, 3) if speed > 0 else None,
                    }
                row["kosten_voorspeld"] = round(predicted(rung, bench), 3)
                if row.get("kosten_gemeten"):
                    row["verschil"] = round(row["kosten_gemeten"] / row["kosten_voorspeld"] - 1, 3)
                # Mm: de tegendruk, 4 s de CPU van de worker en ffmpeg
                if points and points[-1][1] >= LEAD_S - 6:
                    k0, t0 = worker_ticks(app), time.monotonic()
                    time.sleep(4.0)
                    k1, t1 = worker_ticks(app), time.monotonic()
                    mm.append((k1 - k0) / kit.TICK)
                    row["tegendruk_cpu_s_in_4s"] = round(mm[-1], 3)
                rows[rung].append(row)
                kit.log("Mk", rung, row)
                p.close()
                settle(app)
    finally:
        app.config(max_transcodes=3, cpu_budget=3.0)
    summary = {}
    for rung, part in rows.items():
        diffs = [r["verschil"] for r in part if r.get("verschil") is not None]
        summary[rung] = {
            "kosten_gemeten": kit.spread(r.get("kosten_gemeten") for r in part),
            "kosten_voorspeld": part[0]["kosten_voorspeld"] if part else None,
            "verschil": kit.spread(diffs, worst="max"),
            "verschil_abs_max": round(max(abs(d) for d in diffs), 3) if diffs else None,
            "snelheid": kit.spread((r.get("snelheid") for r in part), worst="min"),
            "cores": kit.spread(r.get("cores") for r in part),
            "hoogte": sorted({r["hoogte"] for r in part}),
            "runs": part,
        }
    return {
        "mk": summary,
        "mk_grens": 0.15,
        "mm": {**kit.spread(mm), "eenheid": "CPU-seconden in 4 s"},
    }


def mj(app: kit.App, node: str, profile: dict[str, Any], seconds: float, runs: int) -> dict:
    """3 spelers op 1×, de CPU van de app per stroom."""
    values = []
    for _ in range(runs):
        rest()
        players = [Player(app, node, profile) for _ in range(3)]
        for p in players:
            p.start()
        stop = threading.Event()
        threads = [
            threading.Thread(target=p.play_realtime, args=(seconds + 10, stop)) for p in players
        ]
        for t in threads:
            t.start()
        time.sleep(10)  # de eerste buffer is gevuld
        a0, t0 = app_ticks(app), time.monotonic()
        time.sleep(seconds)
        a1, t1 = app_ticks(app), time.monotonic()
        stop.set()
        for t in threads:
            t.join(timeout=30)
        cores = (a1 - a0) / kit.TICK / (t1 - t0)
        values.append(cores / 3)
        kit.log(f"Mj: app {cores:.3f} cores voor 3 stromen", [p.error for p in players])
        for p in players:
            p.close()
        settle(app)
    return {**kit.spread(values), "eenheid": "core per stroom", "grens": 0.05}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--herhaal", type=int, default=5)
    parser.add_argument("--alleen", choices=("mb", "mk", "mj"), action="append")
    parser.add_argument("--mj-duur", type=float, default=60.0)
    parser.add_argument("--werkmap", type=Path, default=None)
    parser.add_argument("--uit", type=Path, default=None)
    parser.add_argument("--rust", type=float, default=0.8, help="cores: eerst wachten tot stil")
    parser.add_argument("--rust-wacht", type=float, default=120.0)
    args = parser.parse_args(argv)
    QUIET["cores"], QUIET["wacht_s"] = args.rust, args.rust_wacht
    parts = args.alleen or ["mb", "mk", "mj"]
    keep = args.werkmap is not None
    work = args.werkmap or Path(tempfile.mkdtemp(prefix="meet5-start-"))
    work.mkdir(parents=True, exist_ok=True)
    dog = kit.Watchdog()
    dog.start()
    app = None
    result: dict[str, Any] = {"meting": "start", "omstandigheden": kit.conditions()}
    try:
        source = make_source(work)
        app = kit.App(work / "app")
        dog.watch(lambda: app.pid if app is not None else None)
        app.start()
        app.config(max_user_transcodes=3)  # één gebruiker met drie kijkers
        result["rust_voor_bench"] = kit.quiet(args.rust, 900)
        result["bench"] = app.bench()
        kit.log("bench:", result["bench"])
        node = app.upload(app.mkdir(app.root_folder(), "Meten"), source)
        strings = app.call("GET", f"/api/v1/media/{node}/codecs").json().get("candidates") or []
        warm = Player(app, node, h264_profile(strings))
        warm.start()
        warm.close()
        strings += app.call("GET", f"/api/v1/media/{node}/codecs").json().get("item") or []
        profile = h264_profile(strings)
        settle(app)
        sampler = kit.Sampler(app)
        sampler.start()
        try:
            if "mb" in parts:
                result["mb"] = mb(app, node, profile, args.herhaal)
                kit.log("Mb:", result["mb"])
                kit.write_json(args.uit, result)
            if "mk" in parts:
                result |= mk_mm(app, node, profile, result["bench"], args.herhaal)
                kit.write_json(args.uit, result)
            if "mj" in parts:
                result["mj"] = mj(app, node, profile, args.mj_duur, args.herhaal)
                kit.log("Mj:", result["mj"])
        finally:
            sampler.stop()
            result["ffmpeg_max"] = sampler.max_ffmpeg
            result["rust_voor_runs"] = kit.spread(QUIET["gemeten"])
    finally:
        dog.stop()
        result["waakhond"] = dog.summary()
        if app is not None:
            app.close()
        kit.write_json(args.uit, result)
        if not keep:
            shutil.rmtree(work, ignore_errors=True)
    return 0 if not dog.tripped else 99


if __name__ == "__main__":
    raise SystemExit(main())
