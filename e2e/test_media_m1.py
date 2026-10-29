"""M1: de worker in isolatie, in de e2e (fase 5, §2 M1 van docs/fase-5/bouwontwerp.md).

Met media aan en de lokale worker (het harnas van M0):
- de preview van een video komt van de worker: het renderproces van de app vraagt een frame,
  en elke ffmpeg of ffprobe die er draait, is een nakomeling van de worker, nooit van de app of
  van het renderproces zelf (regel 4);
- de worker op pid weg (kill -9): de app start hem opnieuw (backoff 1 s), media-status ziet hem
  weer, en een nieuwe video krijgt weer een preview.

Met media uit (de gewone server van de e2e, zoals 0.6.0):
- een video krijgt nog steeds een preview. Het renderproces start voor die ene keer een eigen
  worker (local.oneshot, in zijn procesgroep), en daarna leeft er geen worker meer.

Elke ffmpeg wordt gezien door een eigen draad die elke 20 ms /proc leest; stoppen alleen op pid.
"""

from __future__ import annotations

import signal
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from conftest import Account, Server, create_user
from media_harness import (
    FFMPEG_NAMES,
    alive,
    cmdline,
    comm,
    descendants,
    kill_pid,
    process_table,
    wait_gone,
)

WORKER = "app.media.worker"
RENDER = "from app.render import main"


@dataclass
class Seen:
    """Wat de spion zag: per ffmpeg (pid, starttime) de opdrachtregels van zijn voorouders."""

    ffmpeg: dict[tuple[int, int], list[str]] = field(default_factory=dict)
    workers: dict[tuple[int, int], str] = field(default_factory=dict)  # → cmdline van de ouder
    scans: int = 0

    def outside_worker(self) -> list[str]:
        return [
            f"{pid}: {' <- '.join(chain)}"
            for (pid, _s), chain in self.ffmpeg.items()
            if not any(WORKER in line for line in chain)
        ]


@contextmanager
def spy(root: int, every: float = 0.02) -> Iterator[Seen]:
    """Een draad die elke `every` s kijkt welke ffmpeg en welke worker er onder root draaien."""
    seen = Seen()
    stop = threading.Event()

    def look() -> None:
        table = process_table()
        for pid in descendants([root], table):
            ppid, start, _state = table[pid]
            name = comm(pid)
            if name in FFMPEG_NAMES and (pid, start) not in seen.ffmpeg:
                chain, up = [], ppid
                while up and up != root and up in table:
                    chain.append(cmdline(up)[:160])
                    up = table[up][0]
                seen.ffmpeg[(pid, start)] = chain
            elif (pid, start) not in seen.workers and WORKER in cmdline(pid):
                seen.workers[(pid, start)] = cmdline(ppid)[:200]
        seen.scans += 1

    def run() -> None:
        while not stop.wait(every):
            look()

    thread = threading.Thread(target=run, name="m1-spion", daemon=True)
    look()
    thread.start()
    try:
        yield seen
    finally:
        stop.set()
        thread.join(timeout=5)
        look()


def upload(server: Server, account: Account, fixture: str, parent: str) -> str:
    from tests.media import fixtures

    path = fixtures.make(fixture)
    with open(path, "rb") as body:
        r = server.call(
            account,
            "PUT",
            f"/api/v1/files/{parent}/upload",
            params={"name": Path(fixture).name},
            content=body,
            headers={"Content-Length": str(path.stat().st_size)},
            expect=201,
        )
    return r.json()["id"]


def folder(server: Server, account: Account, name: str) -> str:
    return server.call(
        account,
        "POST",
        f"/api/v1/files/{account.root_id}/folders",
        json={"name": name},
        expect=201,
    ).json()["id"]


def wait_previews(server: Server, account: Account, ids: list[str], seconds: float = 60) -> dict:
    end = time.monotonic() + seconds
    while True:
        got = server.call(account, "POST", "/api/v1/previews", json={"node_ids": ids}, expect=200)
        previews = got.json()
        if all(previews.get(i, {}).get("state") not in (None, "pending") for i in ids):
            return previews
        if time.monotonic() > end:
            raise AssertionError(f"previews niet klaar: {previews}")
        time.sleep(0.25)


def thumb_is_webp(server: Server, info: dict) -> None:
    assert info["state"] == "ready" and info["thumb"], info
    r = server.request("GET", info["thumb"])
    assert r.status_code == 200, r.text
    data = r.content
    assert r.headers["content-type"] == "image/webp" and data[:4] == b"RIFF", data[:16]
    assert data[8:12] == b"WEBP"


@pytest.mark.media
def test_de_preview_van_een_video_komt_van_de_worker(media, ffmpeg_count):
    owner = media.user()
    parent = folder(media.server, owner, "Previews M1")
    worker = media.worker_pid()
    assert worker is not None
    with spy(media.pid) as seen:
        ids = [
            upload(media.server, owner, "vp9_opus.webm", parent),
            upload(media.server, owner, "h264_aac.mp4", parent),
        ]
        previews = wait_previews(media.server, owner, ids)
    for node in ids:
        thumb_is_webp(media.server, previews[node])
    # Nooit ffmpeg buiten de worker; wat de spion zag (een frame duurt ±0,2 s), liep eronder
    assert seen.outside_worker() == [], seen.ffmpeg
    assert seen.scans > 10
    # de lokale worker van de app deed het werk: geen worker voor één keer (die is voor media uit)
    assert {pid for pid, _s in seen.workers} == {worker}, seen.workers
    assert ffmpeg_count.max <= 3


@pytest.mark.media
def test_de_worker_weg_en_terug(media, ffmpeg_count):
    """kill -9 op de pid van de worker: de bewaker in de app start hem na 1 s opnieuw."""
    old = media.worker_pid()
    assert old is not None
    start = process_table()[old][1]
    assert kill_pid(old, start, signal.SIGKILL)
    assert wait_gone([(old, start)]) == []
    new = None
    end = time.monotonic() + 20
    while time.monotonic() < end:
        new = media.worker_pid()
        if new not in (None, old):
            break
        time.sleep(0.1)
    assert new not in (None, old), media.server.log_tail()
    status = media.cli("media-status", "--wait", "20")
    assert status.returncode == 0, status.stdout + status.stderr
    assert status.stdout.startswith("Media: aan (lokaal, zonder afscherming"), status.stdout
    assert f"lokale mediaworker {old} stopte" in media.server.log_tail(400)

    owner = media.user()
    parent = folder(media.server, owner, "Na de herstart")
    node = upload(media.server, owner, "vp9_opus.mkv", parent)
    thumb_is_webp(media.server, wait_previews(media.server, owner, [node])[node])
    assert media.worker_pid() == new


@pytest.mark.media
def test_met_media_uit_krijgt_een_video_een_preview_zoals_in_0_6_0(server, admin):
    """De gewone server (PLATFORM_MEDIA_ENABLED niet gezet): het renderproces start een worker
    voor één keer, en na de preview is er geen worker meer (ook geen wees)."""
    assert server.proc is not None
    root = server.proc.pid
    owner = create_user(server, admin, name="Media uit M1")
    parent = folder(server, owner, "Zonder media")
    with spy(root) as seen:
        node = upload(server, owner, "vp9_opus.webm", parent)
        previews = wait_previews(server, owner, [node])
        thumb_is_webp(server, previews[node])
        # het renderproces ruimt zijn worker op voor het stopt
        end = time.monotonic() + 10
        while time.monotonic() < end and any(alive(p, s) for p, s in seen.workers):
            time.sleep(0.05)
    assert seen.outside_worker() == [], seen.ffmpeg
    assert seen.workers, "geen worker gezien: wie maakte het frame?"
    for (pid, start), parent_line in seen.workers.items():
        assert RENDER in parent_line, parent_line  # een kind van het renderproces
        assert not alive(pid, start), f"de worker {pid} leeft nog"
    left = [p for p in descendants([root]) if WORKER in cmdline(p)]
    assert left == [], left
