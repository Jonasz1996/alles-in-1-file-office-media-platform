"""De twee kinderen van de worker van de analyse (fase 6, Keuze 56, K2-02, K1-04; X2a, laag 2).

pid 1 draait in dit proces (echt.binnen) met de kinderen van proef.py: echte kinderen (dezelfde
BOOT, grenzen en fd's) die ook WACHT, NEGEER en embed_text kennen. Zo is te zien wat pid 1 doet
terwijl een opdracht loopt: busy per kind, cancel, de deadline, en een beeldvraag (zoeken) naast
een lange opdracht van werk. group (X2b) en rank (X3) zijn er nog niet; embed_text van de proef
staat in voor de beeldvraag.
"""

from __future__ import annotations

import asyncio
import os
import signal
import sys
import time

import pytest

from app.analyse import client, wire
from app.analyse.wire import Child, Code, Op
from app.analyse.worker import child as kids
from app.analyse.worker import server
from tests_analyse import echt, fake_models, proef

pytestmark = [pytest.mark.anyio, pytest.mark.analyse_echt]

BUILT = server.BUILT | {Op.EMBED_TEXT}


@pytest.fixture(scope="module")
def modellen(tmp_path_factory):
    return fake_models.install(tmp_path_factory.mktemp("modellen") / "modellen")


class Klok:
    def __init__(self) -> None:
        self.extra = 0.0

    def __call__(self) -> float:
        return time.monotonic() + self.extra


@pytest.fixture
def klok():
    return Klok()


@pytest.fixture
async def paar(tmp_path, modellen, klok):
    async with echt.binnen(
        tmp_path, modellen, child_code=proef.BOOT, built=BUILT, clock=klok
    ) as pair:
        yield pair


async def _begin_faces(settings, preview: bytes, deadline_ms: int | None = None) -> client.Job:
    fds = [wire.sealed_memfd("preview", preview), wire.out_memfd("uit")]
    try:
        return await client.start(
            settings, Op.FACES, wire.FacesArgs(), fds=fds, deadline_ms=deadline_ms
        )
    finally:
        wire.close_all(fds)


async def _embed(settings, text: str) -> dict:
    out = wire.out_memfd("uit")
    try:
        return await client.call(settings, Op.EMBED_TEXT, wire.EmbedTextArgs(text=text), fds=[out])
    finally:
        os.close(out)


async def _klein(settings) -> None:
    data, _ = await echt.faces(settings, [_preview()])
    assert data.photos[0].code == "ok"


def _preview() -> bytes:
    from PIL import Image

    return echt.webp(Image.new("RGB", (64, 64), (10, 20, 0)))


def _pid(worker: server.Worker, child: Child) -> int:
    proc = worker.kids[child].proc
    assert proc is not None and proc.poll() is None
    return proc.pid


async def test_een_beeldvraag_tijdens_een_lange_opdracht_van_werk_krijgt_geen_busy(paar):
    worker, settings = paar
    werk = await _begin_faces(settings, b"WACHT ")
    try:
        assert werk.child is Child.WERK
        data = await _embed(settings, "een fiets")  # zoeken, terwijl werk bezig is
        assert data["pid"] == _pid(worker, Child.ZOEKEN) != _pid(worker, Child.WERK)
        zoek = await client.start(
            settings, Op.EMBED_TEXT, wire.EmbedTextArgs(text="WACHT"), fds=[wire.out_memfd("u")]
        )
        try:
            assert zoek.child is Child.ZOEKEN and zoek.n != werk.n
            with pytest.raises(client.AnalyseBusy) as exc:
                await _embed(settings, "nog een")
            assert exc.value.child == Child.ZOEKEN.value
        finally:
            zoek.close()
    finally:
        werk.close()


async def test_een_tweede_opdracht_voor_werk_is_busy_en_pid1_antwoordt_gewoon(paar):
    worker, settings = paar
    werk = await _begin_faces(settings, b"WACHT ")
    try:
        with pytest.raises(client.AnalyseBusy) as exc:
            await _begin_faces(settings, _preview())
        assert exc.value.child == Child.WERK.value
        caps = await client.caps(settings)
        assert caps.built == ["cancel", "caps", "embed_text", "faces"]
    finally:
        werk.close()


async def test_cancel_breekt_een_opdracht_af_en_het_kind_blijft(paar):
    worker, settings = paar
    await _klein(settings)
    eerste = _pid(worker, Child.WERK)
    werk = await _begin_faces(settings, b"WACHT ")
    started = time.monotonic()
    try:
        assert werk.n is not None
        assert await client.cancel(settings, werk.n) is True
        with pytest.raises(client.AnalyseFailed) as exc:
            await werk.result()
        assert exc.value.code == Code.CANCELLED.value
        assert time.monotonic() - started < 2
        assert await client.cancel(settings, werk.n) is False
    finally:
        werk.close()
    await _klein(settings)  # werk is vrij, en het is hetzelfde kind
    assert _pid(worker, Child.WERK) == eerste


async def test_een_kind_dat_niet_stopt_gaat_na_kill_grace_weg(paar):
    worker, settings = paar
    await _klein(settings)
    eerste = _pid(worker, Child.WERK)
    werk = await _begin_faces(settings, b"NEGEER ")
    started = time.monotonic()
    try:
        assert await client.cancel(settings, werk.n) is True
        with pytest.raises(client.AnalyseFailed) as exc:
            await werk.result()
        assert exc.value.code == Code.CANCELLED.value
    finally:
        werk.close()
    took = time.monotonic() - started
    assert wire.KILL_GRACE_S - 0.5 < took < wire.KILL_GRACE_S + 3, took
    await _klein(settings)
    assert _pid(worker, Child.WERK) != eerste


async def test_de_verbinding_sluiten_breekt_de_opdracht_af(paar):
    worker, settings = paar
    werk = await _begin_faces(settings, b"WACHT ")
    werk.close()
    end = time.monotonic() + 5
    while Child.WERK in worker.running:
        assert time.monotonic() < end
        await asyncio.sleep(0.02)
    await _klein(settings)


async def test_de_deadline_is_timeout_en_daarna_een_nieuw_kind(paar):
    worker, settings = paar
    await _klein(settings)
    eerste = _pid(worker, Child.WERK)
    werk = await _begin_faces(settings, b"WACHT ", deadline_ms=300)
    started = time.monotonic()
    try:
        with pytest.raises(client.AnalyseFailed) as exc:
            await werk.result()
        assert exc.value.code == Code.TIMEOUT.value
    finally:
        werk.close()
    assert time.monotonic() - started < 0.3 + 2
    await _klein(settings)
    assert _pid(worker, Child.WERK) != eerste


async def test_werk_start_opnieuw_na_8_opdrachten(paar):
    worker, settings = paar
    pids = []
    for _ in range(wire.ANALYSE_CHILD_BATCHES + 1):
        await _klein(settings)
        pids.append(worker.kids[Child.WERK].pids[-1])
    assert wire.ANALYSE_CHILD_BATCHES == 8
    assert len(set(pids[:8])) == 1 and pids[8] != pids[0]
    assert worker.kids[Child.WERK].generation == 2


async def test_werk_start_opnieuw_na_10_minuten(paar, klok):
    worker, settings = paar
    await _klein(settings)
    eerste = _pid(worker, Child.WERK)
    klok.extra = wire.CHILD_MAX_AGE_S - 1
    await _klein(settings)
    assert _pid(worker, Child.WERK) == eerste
    klok.extra = wire.CHILD_MAX_AGE_S + 1
    await _klein(settings)  # na deze opdracht is werk te oud: weg
    await _klein(settings)
    assert _pid(worker, Child.WERK) != eerste


async def test_zoeken_leeft_lang_en_decodeert_nooit_een_beeld(paar):
    worker, settings = paar
    assert kids.handlers(Child.ZOEKEN) == {}
    assert set(kids.handlers(Child.WERK)) == {Op.FACES.value}
    seen = {(await _embed(settings, f"vraag {i}"))["pid"] for i in range(12)}
    assert len(seen) == 1
    data = await _embed(settings, "pil")
    assert data["pil"] is False
    assert wire.CHILDREN[Child.ZOEKEN].max_ops is None
    assert wire.CHILDREN[Child.ZOEKEN].decodes_images is False


def _limits(pid: int) -> dict[str, str]:
    out = {}
    with open(f"/proc/{pid}/limits") as f:
        for line in f.readlines()[1:]:
            name, soft = line[:26].strip(), line[26:47].strip()
            out[name] = soft
    return out


def _nice(pid: int) -> int:
    with open(f"/proc/{pid}/stat") as f:
        return int(f.read().rpartition(")")[2].split()[16])


async def test_de_grenzen_en_nice_per_kind(paar):
    worker, settings = paar
    await _klein(settings)
    await _embed(settings, "grenzen")
    for child, spec in wire.CHILDREN.items():
        pid = _pid(worker, child)
        limits = _limits(pid)
        assert limits["Max data size"] == str(spec.rlimit_data)
        assert limits["Max core file size"] == "0"
        assert limits["Max open files"] == str(kids.NOFILE)
        assert limits["Max file size"] == str(kids.FSIZE)
        assert _nice(pid) == spec.nice
        assert worker.kids[child].ready["dumpable"] == 0
    assert wire.CHILDREN[Child.WERK].rlimit_data == 1024**3
    assert wire.CHILDREN[Child.ZOEKEN].rlimit_data == 3 * 1024**3 // 2
    assert (wire.CHILDREN[Child.WERK].nice, wire.CHILDREN[Child.ZOEKEN].nice) == (19, 0)


async def test_opt_analyse_alleen_in_het_pad_van_een_kind(tmp_path, modellen):
    """Keuze 57: de map met de vijf pakketten komt achteraan in sys.path van een kind, nooit in
    dat van pid 1."""
    extra = tmp_path / "opt-analyse"
    extra.mkdir()
    sock = tmp_path / "run" / "w.sock"
    config = server.Config(
        socket=sock, models=modellen.root, key="k" * 64, built=BUILT,
        child_code=proef.BOOT, analyse_path=str(extra), lock=modellen.lock,
    )  # fmt: skip
    worker = server.Worker(config)
    await worker.start()
    try:
        data = await _embed(echt.settings_for(tmp_path, sock, config.key), "pad")
        assert data["path"][-1] == str(extra)
        assert str(extra) not in sys.path
    finally:
        await worker.stop()


async def test_een_kind_gaat_weg_als_pid1_wegvalt(tmp_path, modellen):
    """PR_SET_PDEATHSIG: SIGKILL op pid 1, en het kind volgt."""
    with echt.EchteWorker(tmp_path, modellen) as w:
        await echt.faces(w.settings(), [_preview()])
        (kind,) = w.children()
        os.kill(w.pid, signal.SIGKILL)
        w.proc.wait()
        end = time.monotonic() + 5
        while _leeft(kind):
            assert time.monotonic() < end, "het kind leeft nog"
            await asyncio.sleep(0.05)


def _leeft(pid: int) -> bool:
    try:
        with open(f"/proc/{pid}/stat") as f:
            return f.read().rpartition(")")[2].split()[0] not in ("Z", "X")
    except FileNotFoundError:
        return False
