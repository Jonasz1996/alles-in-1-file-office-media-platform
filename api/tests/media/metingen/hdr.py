"""Mf en Mh (§4.5): HDR live. 1 × 1080 HDR alleen; 1 × 720 HDR naast 2 andere transcodes.
Grens: 0 keer haperen; de worker met zijn ffmpeg ≤ 3 GB.

Een eigen app (kit.App, worker onder taskset -c 0-2) met hdr_1080 aan (Keuze 27: anders gaat HDR
live alleen naar 720). De bronnen: HEVC 10 bit HDR10 (PQ, BT.2020) 1080p24 met ruis, zoals
hevc10_hdr10.mkv van de fixtures maar groter en langer, en de SDR-bron van start.py (H.264 1080p24
met ruis). Spelers zoals start.Player, die op 1× afspelen met een buffer van 30 s; haperen = een
segment dat er nog niet is als de afspeeltijd het nodig heeft (de wachttijd telt als haperen).

- alleen: 1 HDR-kijker, kwaliteit 1080 (bij auto gaat HDR live hooguit naar 720, ook met
  hdr_1080: decide.rungs_for), met max_transcodes 1 (zonder de reserve voor andere kijkers, net
  als Mb alleen in start.py); de trede onder 1080 kiest de toelating;
- naast_2: eerst de HDR-kijker (kwaliteit 720), dan 2 SDR-kijkers (kwaliteit 720), met
  max_transcodes 3; de treden kiest de toelating (§4.5 verwacht 720 superfast voor de twee);
- naast_1: hetzelfde met 1 SDR-kijker en max_transcodes 2: wat in de sandbox wel past als de
  toelating naast_2 weigert.

Weigert de toelating een kijker (503 media_busy), dan staat dat in de run (geweigerd, met de
reden en de treden tot dan) en speelt die run niet.

    cd api && uv run python -m tests.media.metingen.hdr --herhaal 5 --uit /tmp/mf.json
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from tests.media.metingen import kit
from tests.media.metingen.start import Player, h264_profile, make_source, settle

SOURCE_S = 150
PLAY_S = 120.0
BUFFER_S = 30.0
VARIANTS = {"alleen": 1, "naast_2": 3, "naast_1": 2}  # de variant en max_transcodes
X265_HDR = (
    "-c:v", "libx265", "-preset", "ultrafast", "-x265-params",
    "log-level=error:hdr10=1:repeat-headers=1:keyint=48:min-keyint=48:scenecut=0:"
    "colorprim=bt2020:transfer=smpte2084:colormatrix=bt2020nc:"
    "master-display=G(13250,34500)B(7500,3000)R(34000,16000)WP(15635,16450)L(10000000,1):"
    "max-cll=1000,400:vbv-maxrate=8000:vbv-bufsize=16000:bitrate=8000",
    "-pix_fmt", "yuv420p10le", "-color_primaries", "bt2020", "-color_trc", "smpte2084",
    "-colorspace", "bt2020nc",
)  # fmt: skip


def make_hdr(work: Path, seconds: int = SOURCE_S) -> Path:
    path = work / "bron-hdr-1080.mkv"
    if path.exists():
        return path
    lavfi = "testsrc2=size=1920x1080:rate=24,noise=alls=12:allf=t"
    subprocess.run(
        ["taskset", "-c", "0-2", "ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "lavfi",
         "-i", lavfi, "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
         "-t", str(seconds), *X265_HDR, "-c:a", "aac", "-b:a", "128k", "-shortest", str(path)],
        check=True, timeout=1800,
    )  # fmt: skip
    return path


def realtime(p: Player, seconds: float, out: dict[str, Any]) -> None:
    """Afspelen op 1× vanaf segment 0 (al binnen): segment n zodra de buffer minder dan 30 s
    vooruit is; komt het later binnen dan de afspeeltijd het nodig heeft, dan hapert hij."""
    began = time.monotonic()
    stall, stalls, n = 0.0, 0, 1
    try:
        total = p.count()
        while n < total and time.monotonic() - began - stall < seconds:
            position = time.monotonic() - began - stall
            if n * 2.0 - position > BUFFER_S:
                time.sleep(0.1)
                continue
            p.get(f"{n}.m4s")
            late = time.monotonic() - (began + stall + n * 2.0)
            if late > 0.01:
                stalls += 1
                stall += late
            n += 1
    except Exception as exc:  # noqa: BLE001 - voor het rapport
        out["fout"] = str(exc)[:200]
    out |= {"haperen": stalls, "haperen_s": round(stall, 2), "segmenten": n}


def watch(players: list[Player], seconds: float) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = [{} for _ in players]
    threads = [
        threading.Thread(target=realtime, args=(p, seconds, r))
        for p, r in zip(players, results, strict=True)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=seconds * 3 + 120)
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--herhaal", type=int, default=5)
    parser.add_argument("--duur", type=float, default=PLAY_S)
    parser.add_argument("--werkmap", type=Path, default=None)
    parser.add_argument("--uit", type=Path, default=None)
    parser.add_argument("--rust", type=float, default=0.8)
    args = parser.parse_args(argv)
    keep = args.werkmap is not None
    work = args.werkmap or Path(tempfile.mkdtemp(prefix="meet5-hdr-"))
    work.mkdir(parents=True, exist_ok=True)
    dog = kit.Watchdog()
    dog.start()
    app = None
    result: dict[str, Any] = {"meting": "hdr", "omstandigheden": kit.conditions(), "runs": []}
    try:
        hdr_source = make_hdr(work)
        sdr_source = make_source(work)
        app = kit.App(work / "app")
        dog.watch(lambda: app.pid if app is not None else None)
        app.start()
        app.config(max_user_transcodes=3, max_transcodes=1, hdr_1080=True)
        result["rust_voor_bench"] = kit.quiet(args.rust, 900)
        result["bench"] = app.bench()
        kit.log("bench:", result["bench"])
        folder = app.mkdir(app.root_folder(), "HDR")
        nodes = {"hdr": app.upload(folder, hdr_source), "sdr": app.upload(folder, sdr_source)}
        strings: list[str] = []
        for node in nodes.values():
            found = app.call("GET", f"/api/v1/media/{node}/codecs").json()
            strings += found.get("candidates") or []
        for node in nodes.values():  # de probe en de keyframes vooraf
            warm = Player(app, node, h264_profile(strings), 480)
            warm.start()
            warm.close()
            strings += app.call("GET", f"/api/v1/media/{node}/codecs").json().get("item") or []
        profile = h264_profile(strings)
        settle(app)
        sampler = kit.Sampler(app)
        sampler.start()
        try:
            for i in range(args.herhaal):
                for variant, places in VARIANTS.items():
                    app.config(max_transcodes=places)
                    rest = kit.quiet(args.rust, 120)
                    began = time.monotonic()
                    if variant == "alleen":
                        players = [Player(app, nodes["hdr"], profile, 1080)]
                    else:
                        players = [Player(app, nodes["hdr"], profile, 720)]
                        players += [
                            Player(app, nodes["sdr"], profile, 720) for _ in range(places - 1)
                        ]
                    refused = None
                    started: list[Player] = []
                    for p in players:
                        try:
                            p.start()
                        except RuntimeError as exc:
                            refused = str(exc)[:300]
                            break
                        started.append(p)
                    heights = [p.height() for p in started]
                    rungs = [
                        {"bron": v.get("title"), **(v.get("rung") or {})}
                        for v in app.call("GET", "/api/v1/admin/media").json().get("sessions") or []
                        if v.get("mode") == "transcode"
                    ]
                    plays = watch(started, args.duur) if refused is None else []
                    for p in players:
                        p.close()
                    settle(app)
                    run = {
                        "run": i + 1,
                        "variant": variant,
                        "max_transcodes": places,
                        "treden": heights,
                        "treden_beheer": rungs,
                        "spelers": plays,
                        "haperen": sum(x.get("haperen", 0) for x in plays),
                        "processen": sampler.window(began, time.monotonic()),
                        "rust_voor_cores": rest,
                    }
                    if refused is not None:
                        run["geweigerd"] = refused
                    result["runs"].append(run)
                    kit.log(variant, heights, run["haperen"], plays)
                    kit.write_json(args.uit, result)
        finally:
            sampler.stop()
            result["ffmpeg_max"] = sampler.max_ffmpeg
        for variant in VARIANTS:
            mine = [r for r in result["runs"] if r["variant"] == variant]
            part = [r for r in mine if "geweigerd" not in r]
            result[variant] = {
                "geweigerd": len(mine) - len(part),
                "haperen": kit.spread(r["haperen"] for r in part),
                "worker_totaal_rss_mb": kit.spread(
                    r["processen"].get("worker_totaal_rss_mb_max") for r in part
                ),
                "treden": [r["treden"] for r in part],
            }
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
