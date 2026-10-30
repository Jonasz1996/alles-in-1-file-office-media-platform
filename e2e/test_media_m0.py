"""M0: het harnas van media in de e2e (fase 5, §3.6, §3.8 en §4.3 van docs/fase-5/bouwontwerp.md).

Een rooktest die laat zien dat het harnas werkt, met media aan en de lokale worker:

- de app start de worker (een kind van de app, met de grenzen van het harnas), en
  `app.cli media-status` ziet zijn caps (de stub van M0 vraagt ze over de socket);
- een geüploade fixture wordt geprobed: de job media.analyse van de app laat de worker proben
  en schrijft media_probe (M2 zet die job straks zelf klaar bij het scannen);
- de viewer van fase 3 speelt nog steeds: VP9 + Opus altijd, H.264 + AAC als de browser het kan
  (anders overgeslagen met de reden, of gefaald met MEDIA_REQUIRE_H264=1);
- een herstart met outage_s, en na elke test 0 ffmpeg (ffmpeg_count);
- de delen van het harnas zelf: de teller ziet een vierde en wat van ouder wisselde, de deadline
  dumpt de draden en stopt.

Met media_perf (job e2e-media-perf, alleen een rapport): S13, de browser en wat hij kan
(isTypeSupported en canPlayType voor avc1.640028 en mp4a.40.2), en het eerste beeld van VP9 in
de viewer onder 40 Mbit/s en 20 ms, met de worker op 3 cores.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time

import pytest
from playwright.sync_api import expect

from conftest import report_only, write_result
from media_harness import (
    MAX_PROCS,
    PERF_CPUS,
    Deadline,
    FfmpegWatch,
    alive,
    codec_support,
    first_frame_ms,
    open_app,
    process_table,
    require_codec,
    wait_playing,
)


def ffmpeg_version() -> str:
    """De versie van de ffmpeg die de worker vindt (dezelfde PATH)."""
    out = subprocess.run(
        ["ffmpeg", "-hide_banner", "-version"], capture_output=True, text=True, check=True
    ).stdout
    return out.split()[2]


def open_viewer(media, media_browsers, fixture: str):
    """Een eigen gebruiker, een map met deze fixture, en de viewer erop."""
    owner = media.user()
    folder = media.folder(owner, "Media M0")
    up = media.upload(owner, fixture, folder)
    page = media_browsers.context_as(owner).new_page()
    page.goto(f"/map/{folder}?bestand={up.id}")
    viewer = page.get_by_role("dialog", name=up.name, exact=True)
    expect(viewer).to_be_visible()
    expect(viewer.locator(".viewer-stage")).to_have_attribute("data-kind", "video")
    return page, viewer, up


@pytest.mark.media
def test_de_app_start_met_de_lokale_worker(media, ffmpeg_count):
    """De worker is een kind van de app, met de grenzen van het harnas, en media-status ziet
    zijn caps. De routes van de pakketten zijn stubs: /beheer → Media is nog 503 media_off."""
    worker = media.worker_pid()
    assert worker is not None, media.server.log_tail()
    line = " ".join(open(f"/proc/{worker}/cmdline").read().split("\0"))
    assert "--local" in line and f"--max-procs {MAX_PROCS}" in line, line
    assert "--max-transcodes 3" in line, line

    status = media.cli("media-status", "--wait", "20")
    assert status.returncode == 0, status.stdout + status.stderr
    first = status.stdout.splitlines()[0]
    assert first == f"Media: aan (lokaal, zonder afscherming, ffmpeg {ffmpeg_version()})", first

    r = media.server.call(media.admin, "GET", "/api/v1/admin/media", expect=503)
    assert r.json()["detail"]["code"] == "media_off"
    assert ffmpeg_count.scans >= 1


@pytest.mark.media
def test_een_upload_wordt_geprobed(media, ffmpeg_count):
    """Via de API geüpload, en dan door de job media.analyse van de app en de worker
    geprobed; een kapot bestand geeft een nette fout."""
    owner = media.user()
    folder = media.folder(owner, "Probe")
    film = media.upload(owner, "vp9_opus.mkv", folder)
    kapot = media.upload(owner, "broken.mp4", folder)
    media.analyse(film.sha)
    media.analyse(kapot.sha)

    row = media.wait_probe(film.sha)
    assert row["state"] == "ready" and row["container"] == "matroska", row
    assert row["duration_ms"] and abs(row["duration_ms"] - 6000) < 100, row
    streams = [(s["type"], s["codec"], s.get("lang")) for s in row["streams"]]
    assert streams == [("video", "vp9", None), ("audio", "opus", "nld"), ("audio", "opus", "eng")]
    assert row["streams"][0]["width"] == 640 and row["streams"][0]["height"] == 360

    row = media.wait_probe(kapot.sha)
    assert row["state"] == "failed" and row["error"] == "unreadable", row
    assert ffmpeg_count.max <= MAX_PROCS


@pytest.mark.media
def test_de_viewer_speelt_vp9(media, media_browsers, ffmpeg_count):
    """De viewer van fase 3 speelt VP9 + Opus met media aan (de speler van M6 is er nog niet:
    de haak toont de <video> van fase 3)."""
    page, viewer, _up = open_viewer(media, media_browsers, "vp9_opus.webm")
    assert first_frame_ms(page) > 0
    assert wait_playing(page, 0.5) >= 0.5
    size = page.evaluate(
        "() => { const v = document.querySelector('dialog video');"
        " return [v.videoWidth, v.videoHeight]; }"
    )
    assert size == [640, 360]
    expect(viewer.get_by_text("Deze browser kan dit formaat niet afspelen")).to_have_count(0)


@pytest.mark.media
def test_de_viewer_speelt_h264(media, media_browsers, ffmpeg_count):
    """H.264 + AAC (720p, moov vooraan) in de viewer, als de browser het kan (Chromium van
    Playwright 141 niet; Chrome for Testing wel, als S13 dat zegt)."""
    owner = media.user()
    page = media_browsers.context_as(owner).new_page()
    open_app(page)
    require_codec(page, 'video/mp4; codecs="avc1.64001f"', "native")
    require_codec(page, 'audio/mp4; codecs="mp4a.40.2"', "native")
    folder = media.folder(owner, "H264")
    up = media.upload(owner, "h264_aac.mp4", folder)
    page.goto(f"/map/{folder}?bestand={up.id}")
    expect(page.get_by_role("dialog", name=up.name, exact=True)).to_be_visible()
    assert first_frame_ms(page) > 0
    assert wait_playing(page, 0.5) >= 0.5


@pytest.mark.media
def test_herstart_met_een_onderbreking(media, ffmpeg_count):
    """outage_s: de app stopt op pid (de worker gaat mee), blijft even weg, en komt terug op
    dezelfde poort met een nieuwe worker; niets blijft over."""
    old = media.worker_pid()
    old_start = process_table()[old][1]
    port = media.server.port
    left = media.restart(outage_s=1)
    assert left == [], left
    assert not alive(old, old_start)
    new = media.worker_pid()
    assert new is not None and new != old
    assert media.server.port == port
    status = media.cli("media-status", "--wait", "20")
    assert status.returncode == 0, status.stdout + status.stderr


@pytest.mark.media
def test_alles_op_pid_weg_en_weer_starten(media, ffmpeg_count):
    """Wat de deadline doet (kill): de app met SIGKILL weg, de worker die dan als wees
    overblijft ook (op pid); daarna start de volgende test hem weer (ensure_running)."""
    worker = media.worker_pid()
    start = process_table()[worker][1]
    left = media.kill()
    assert any(line.startswith(f"{worker} ") for line in left), left
    assert not alive(worker, start)
    media.ensure_running()
    assert media.worker_pid() not in (None, worker)
    assert media.cli("media-status", "--wait", "20").returncode == 0


@pytest.mark.media
def test_ffmpeg_count_ziet_een_vierde_en_een_wees(tmp_path):
    """De teller zelf, zonder echte ffmpeg (regel 13): vier slapers die ffmpeg heten. Hij ziet
    er vier tegelijk, volgt ze ook als hun ouder stopt, en haalt ze op pid weg."""
    fake = tmp_path / "ffmpeg"
    shutil.copy(shutil.which("sleep") or "/bin/sleep", fake)
    spawn = (
        "import subprocess, sys\n"
        "kids = [subprocess.Popen([sys.argv[1], '30']) for _ in range(4)]\n"
        "print(' '.join(str(k.pid) for k in kids), flush=True)\n"
        "sys.stdin.read()\n"
    )
    root = subprocess.Popen(
        [sys.executable, "-c", spawn, str(fake)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    watch = FfmpegWatch(lambda: [root.pid] if root.poll() is None else [])
    try:
        kids = [int(p) for p in root.stdout.readline().split()]
        assert len(kids) == 4
        assert sorted(watch.scan()) == sorted(kids)
        assert watch.max == 4 and len(watch.over) == 1 and "4 tegelijk" in watch.over[0]
        root.stdin.close()  # de ouder stopt; de slapers worden wezen
        root.wait(timeout=10)
        assert sorted(watch.scan()) == sorted(kids)
        assert len(watch.kill_left()) == 4
        assert watch.wait_zero(5) == []
    finally:
        watch.kill_left()
        if root.poll() is None:
            root.kill()
            root.wait(timeout=10)


@pytest.mark.media
def test_de_deadline_dumpt_de_draden():
    """De deadline (hier 0,2 s, met een eigen stop): een dump van alle draden, en dan stoppen."""
    stopped: list[str] = []
    deadline = Deadline(0.2, on_expire=lambda: stopped.append("gestopt") or ["123 python"])
    deadline.start()
    for _ in range(100):
        if deadline.expired and deadline.stopped:
            break
        time.sleep(0.05)
    assert deadline.expired and stopped == ["gestopt"] and deadline.stopped == ["123 python"]
    assert "--- draad MainThread ---" in deadline.dump
    assert "test_de_deadline_dumpt_de_draden" in deadline.dump


S13_CODECS = (
    "avc1.640028",
    "mp4a.40.2",
    'video/mp4; codecs="avc1.640028,mp4a.40.2"',
    "avc1.64001f",
    "vp09.00.10.08",
    "opus",
    "flac",
    "av01.0.04M.08",
    "hvc1.1.6.L93.B0",
    "ac-3",
)


@pytest.mark.media_perf
def test_s13_de_browser_en_het_eerste_beeld(media, media_browser, media_browsers, ffmpeg_count):
    """S13 (§3.7): de versie van de browser en wat hij zegt voor H.264 en AAC, als rapport.
    En het eerste beeld van VP9 in de viewer van fase 3 (navigeren tot het eerste beeld, 3 keer)
    onder 40 Mbit/s en 20 ms, met de worker op 3 cores."""
    owner = media.user()
    folder = media.folder(owner, "S13")
    up = media.upload(owner, "vp9_opus.webm", folder)
    page = media_browsers.context_as(owner).new_page()
    frames = []
    for _ in range(3):
        # Een nieuwe navigatie pas als de vorige pagina speelt (dan is het token ververst)
        page.goto(f"/map/{folder}?bestand={up.id}")
        frames.append(round(first_frame_ms(page), 1))
        wait_playing(page, 0.3)
    codecs = codec_support(page, S13_CODECS)
    agent = page.evaluate("() => navigator.userAgent")
    result = {
        "browser": media_browser.version,
        "user_agent": agent,
        "s13": {
            "avc1.640028": codecs["avc1.640028"],
            "mp4a.40.2": codecs["mp4a.40.2"],
            "h264_en_aac": bool(codecs["avc1.640028"]["mse"] and codecs["mp4a.40.2"]["mse"]),
        },
        "codecs": codecs,
        "eerste_beeld_vp9_ms": frames,
        "netwerk": media_browsers.network,
        "worker_cores": sorted(media.cpus or []),
        "ffmpeg_max": ffmpeg_count.max,
        "report_only": report_only(),
    }
    path = write_result("media-m0-s13", result)
    print(json.dumps(result, ensure_ascii=False, indent=1))
    if path is not None:
        print(f"rapport: {path}")
    assert media_browsers.network and all(n["network"] is True for n in media_browsers.network)
    if PERF_CPUS <= os.sched_getaffinity(0):
        assert os.sched_getaffinity(media.worker_pid()) == PERF_CPUS
    assert all(f > 0 for f in frames)
    assert re.match(r"\d+\.", media_browser.version)
