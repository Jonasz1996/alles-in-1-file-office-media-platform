"""M6, "Klaar als": de tijden van de speler in de browser (marker media_perf; §4.5 Ma, Mb, Mc).

Elke meting 10 keer, met de mediaan en de slechtste (§4.5: de tijden in e2e 10 keer). De worker
draait op cores 0–2 (regel 14; de fixture media zet hem vast voor media_perf), en elke pagina
krijgt 40 Mbit/s en 20 ms via CDP (MediaBrowsers met perf, §3.6).

- Ma, direct, het eerste beeld: een bron van 1080p, 20 Mbit/s en 2 minuten (VP9 en Opus in WebM;
  10 s gemaakt en met -stream_loop gekopieerd, dat scheelt minuten coderen). Van de klik (de
  navigatie binnen de app naar /kijken) tot het eerste requestVideoFrameCallback: ≤ 2 s;
- Mb, transcode, het eerste beeld: hevc10_hdr10.mkv naar 720 (VP9 hier, H.264 in CI): ≤ 5 s;
- Mc, zoeken en weer lopen: currentTime naar 70 % (nog niet gemaakt) tot de video voorbij het
  doel loopt, bij direct, remux en transcode: ≤ 3 s ("Klaar als" van M6). De strengere grenzen
  van de tabel (1,5 s / 1,0 s / 3,0 s) staan als informatie in het rapport.

De klik: de test zet de start (mark_start van media_harness) en navigeert dan zoals de router
van de app (pushState en een popstate-event), dus met de app al geladen; de chunk van de speler
telt bij de eerste run mee. Tussen de runs Esc (de speler sluit zijn sessie) en wachten tot er
geen ffmpeg meer loopt.

Een rapport per meting (media-m6-<naam>.json in $E2E_PERF_OUT); met E2E_PERF_REPORT_ONLY=1 (CI)
faalt de test nooit op een grens.
"""

from __future__ import annotations

import json
import os
import shutil
import statistics
import subprocess
from typing import Any

import pytest
from playwright.sync_api import expect

from conftest import report_only, write_result
from media_harness import PERF_CPUS, first_frame_ms, open_app
from test_media_m6 import (
    M2,
    PLAYER,
    VIDEO,
    Plays,
    loop_mkv,
    playing_past,
    ready_state,
    upload_path,
)

RUNS = 10
GATES_MS = {"direct": 2000, "transcode": 5000, "zoeken": 3000}
# Mc in de tabel van §4.5: strenger dan "Klaar als"; alleen ter informatie
INFO_MS = {"direct": 1500, "remux": 1000, "transcode": 3000}
SEEK_TO = 0.7

NAVIGATE_JS = """(href) => {
  window.__e2eMediaMark && window.__e2eMediaMark();
  const idx = (history.state && history.state.idx) || 0;
  history.pushState({ key: "perf-" + Math.random().toString(36).slice(2), idx: idx + 1 }, "", href);
  window.dispatchEvent(new PopStateEvent("popstate", { state: history.state }));
}"""

SEEK_JS = """(fraction) => {
  const v = document.querySelector(".player-video");
  const target = v.duration * fraction;
  window.__m6Seek = { t0: performance.now(), target };
  v.currentTime = target;
  return target;
}"""

SEEK_DONE_JS = """() => {
  const v = document.querySelector(".player-video"), s = window.__m6Seek;
  if (!v || !s || v.paused || v.error || v.currentTime < s.target + 0.25) return null;
  return performance.now() - s.t0;
}"""


@pytest.fixture(scope="module")
def big_webm(tmp_path_factory: pytest.TempPathFactory) -> Any:
    """1080p24, 20 Mbit/s, 120 s, VP9 en Opus in WebM: 10 s gecodeerd (ruis, zodat de bitrate
    echt 20 Mbit/s is), dan 12 keer gekopieerd. Eenmalig, met één ffmpeg (regel 13)."""
    work = tmp_path_factory.mktemp("m6-perf")
    piece, path = work / "stuk.webm", work / "direct-1080-20M.webm"
    lavfi = "testsrc2=size=1920x1080:rate=24,noise=alls=12:allf=t"
    subprocess.run(
        ["taskset", "-c", "0-2", "ffmpeg", "-nostdin", "-v", "error", "-y",
         "-f", "lavfi", "-i", lavfi, "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
         "-t", "10", "-c:v", "libvpx-vp9", "-deadline", "realtime", "-cpu-used", "8",
         "-b:v", "20M", "-minrate", "20M", "-maxrate", "20M", "-g", "48", "-threads", "3",
         "-row-mt", "1", "-c:a", "libopus", "-b:a", "128k", "-shortest", str(piece)],
        check=True, timeout=300,
    )  # fmt: skip
    subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-y", "-stream_loop", "11", "-i", str(piece),
         "-map", "0", "-c", "copy", str(path)],
        check=True, timeout=120,
    )  # fmt: skip
    piece.unlink()
    yield path
    shutil.rmtree(work, ignore_errors=True)


def settle(ffmpeg_count: Any) -> None:
    """Tot er geen ffmpeg meer loopt (de vorige run, de probe of de sprites)."""
    assert not ffmpeg_count.wait_zero(30), "ffmpeg loopt nog"


def start(page: Any, node: str) -> None:
    """De klik: binnen de app naar /kijken/:node, met de start van de meting."""
    page.evaluate(NAVIGATE_JS, f"/kijken/{node}")
    expect(page.locator(PLAYER)).to_be_visible()


def stop(page: Any, ffmpeg_count: Any) -> None:
    """Esc: terug, de speler sluit zijn sessie; dan geen ffmpeg meer."""
    page.keyboard.press("Escape")
    expect(page.locator(PLAYER)).to_have_count(0)
    settle(ffmpeg_count)


def summary(values: list[float]) -> dict[str, Any]:
    return {
        "ms": [round(v) for v in values],
        "mediaan": round(statistics.median(values)),
        "slechtste": round(max(values)),
    }


def report(name: str, result: dict[str, Any], media: Any, ffmpeg_count: Any) -> None:
    result |= {
        "runs": RUNS,
        "worker_cores": sorted(media.cpus or []),
        "ffmpeg_max": ffmpeg_count.max,
        "report_only": report_only(),
    }
    path = write_result(f"media-m6-{name}", result)
    print(json.dumps(result, ensure_ascii=False, indent=1))
    if path is not None:
        print(f"rapport: {path}")


def pinned(media: Any) -> None:
    if PERF_CPUS <= os.sched_getaffinity(0):
        assert os.sched_getaffinity(media.worker_pid()) == PERF_CPUS


def prepare(media: Any, media_browsers: Any, name: str) -> tuple[Any, str, Any, Any]:
    """Een gebruiker, een map, de stand-in van M2, en de app open op de start (de klik)."""
    pinned(media)
    owner = media.user()
    folder = media.folder(owner, name)
    context = media_browsers.context_as(owner)
    M2(context)
    page = context.new_page()
    open_app(page)
    return owner, folder, context, page


def first_frames(page: Any, node: str, mode: str, ffmpeg_count: Any, plays: Plays) -> list[float]:
    """RUNS keer: de klik, het eerste beeld, Esc. Eén run vooraf (de probe, de keyframes en de
    sprites zijn dan klaar; die horen bij het eerste scannen, niet bij afspelen)."""
    start(page, node)
    ready_state(page, mode)
    playing_past(page, 0.5)
    stop(page, ffmpeg_count)
    out: list[float] = []
    for _ in range(RUNS):
        start(page, node)
        out.append(first_frame_ms(page, VIDEO, timeout_ms=30_000))
        ready_state(page, mode)
        stop(page, ffmpeg_count)
    assert all(c["status"] == 200 for c in plays.calls), plays.calls
    return out


def gate(name: str, values: list[float], limit: float) -> None:
    if report_only():
        return
    assert max(values) <= limit, f"{name}: de slechtste {max(values):.0f} ms > {limit:.0f} ms"


@pytest.mark.media_perf
def test_m6_ma_eerste_beeld_direct(media, media_browsers, ffmpeg_count, big_webm):
    owner, folder, context, page = prepare(media, media_browsers, "M6 Ma")
    plays = Plays(context)
    node = upload_path(media, owner, folder, big_webm)
    times = first_frames(page, node, "direct", ffmpeg_count, plays)
    result = {"direct": summary(times), "grens_ms": GATES_MS["direct"]}
    result["netwerk"] = media_browsers.network[-1:]
    report("ma-direct", result, media, ffmpeg_count)
    gate("Ma direct", times, GATES_MS["direct"])


@pytest.mark.media_perf
def test_m6_mb_eerste_beeld_transcode(media, media_browsers, ffmpeg_count):
    owner, folder, context, page = prepare(media, media_browsers, "M6 Mb")
    plays = Plays(context)
    node = media.upload(owner, "hevc10_hdr10.mkv", folder).id
    times = first_frames(page, node, "transcode", ffmpeg_count, plays)
    heights = {c["out"]["quality"]["current"] for c in plays.ok}
    result = {"transcode": summary(times), "grens_ms": GATES_MS["transcode"]}
    result["kwaliteit"] = sorted(str(h) for h in heights)
    report("mb-transcode", result, media, ffmpeg_count)
    gate("Mb transcode", times, GATES_MS["transcode"])


def seeks(page: Any, node: str, mode: str, ffmpeg_count: Any) -> list[float]:
    """RUNS keer: de klik, lopen, dan currentTime naar 70 % tot hij voorbij het doel loopt."""
    out: list[float] = []
    for _ in range(RUNS):
        start(page, node)
        ready_state(page, mode)
        playing_past(page, 0.5)
        page.evaluate(SEEK_JS, SEEK_TO)
        took = page.wait_for_function(SEEK_DONE_JS, timeout=30_000, polling=50)
        out.append(float(took.json_value()))
        stop(page, ffmpeg_count)
    return out


@pytest.mark.media_perf
def test_m6_mc_zoeken_en_weer_lopen(media, media_browsers, ffmpeg_count, tmp_path, big_webm):
    owner, folder, context, page = prepare(media, media_browsers, "M6 Mc")
    plays = Plays(context)
    # 70 % ligt ver voorbij wat de producent al maakte: 300 s remux (hij loopt hooguit 30
    # segmenten voor), 60 s transcode (HDR, iets sneller dan afspelen)
    sources = {
        "direct": upload_path(media, owner, folder, big_webm),
        "remux": upload_path(media, owner, folder, loop_mkv(tmp_path, 50)),
        "transcode": upload_path(media, owner, folder, loop_mkv(tmp_path, 5, "hevc10_hdr10.mkv")),
    }
    for path in tmp_path.glob("*.mkv"):
        path.unlink()
    # Vooraf, buiten de meting: elke bron één keer (de probe, de keyframes, de sprites)
    for mode, node in sources.items():
        start(page, node)
        ready_state(page, mode)
        playing_past(page, 0.5)
        stop(page, ffmpeg_count)
    times = {mode: seeks(page, node, mode, ffmpeg_count) for mode, node in sources.items()}
    assert all(c["status"] == 200 for c in plays.calls), plays.calls
    result = {mode: summary(v) for mode, v in times.items()}
    result |= {"grens_ms": GATES_MS["zoeken"], "tabel_ms (informatie)": INFO_MS}
    result["boven_de_tabel"] = {m: max(v) for m, v in times.items() if max(v) > INFO_MS[m]}
    report("mc-zoeken", result, media, ffmpeg_count)
    for mode, values in times.items():
        gate(f"Mc {mode}", values, GATES_MS["zoeken"])
