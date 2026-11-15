"""De nep-worker van de container analyse (fase 6, A0; §3.11, Keuze 56).

- test_contract_nep: elk scenario uit analyse_contract.py tegen de nep, als worker zonder
  opdrachten (zoals vóór X2 en X3: alles not_built) en als worker met alle opdrachten. Laag 2
  (X2a) draait dezelfde scenario's tegen de echte worker.
- de rest: wat alleen de nep kan (de klok, generation, de scripts) en het werkmodel van beide
  kinderen, met nummers en vrije plaatsen.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path

import pytest

from app.analyse import client, lock, wire
from app.analyse.wire import CHILD, Child, Code, Op
from tests.analyse import analyse_contract as C
from tests.analyse.fake_worker import FakeAnalyseWorker, FakeFail, FakeRequest

pytestmark = pytest.mark.anyio

API = Path(__file__).resolve().parents[2]
KINDEREN = frozenset(op for op in Op if CHILD[op] is not None)


async def _leeg(req: FakeRequest):
    """Een geldig, leeg antwoord per opdracht."""
    n = len(req.previews)
    return {
        Op.FACES: wire.FacesData(photos=[wire.FacesPhoto(code="ok") for _ in range(n)]),
        Op.GROUP: wire.GroupData(n=0),
        Op.EMBED_IMAGE: wire.EmbedImageData(photos=[wire.EmbedPhoto(code="ok")] * n),
        Op.EMBED_TEXT: wire.EmbedTextData(),
        Op.RANK: wire.RankData(),
        Op.FORGET: wire.ForgetData(found=False),
    }[req.op]


class FakeDriver:
    """De Driver van analyse_contract.py voor de nep. alles: elke opdracht heeft een handler."""

    def __init__(self, fake: FakeAnalyseWorker, data_dir: Path, *, alles: bool) -> None:
        self.name = "nep-alles" if alles else "nep-leeg"
        self.fake = fake
        self.socket = fake.socket
        self.key = fake.key
        self.models = tuple(m.name for m in lock.load())
        self._data_dir = data_dir
        self._gates: list = []
        if alles:
            for op in KINDEREN:
                fake.on(op, _leeg)
        self.has = frozenset(fake.built - {Op.CAPS, Op.CANCEL})

    def settings(self):
        return self.fake.settings(self._data_dir)

    async def lang(self, op: Op) -> C.Opdracht:
        assert op in self.has
        self._gates.append(self.fake.hold(op))
        return C.STANDAARD[op]

    async def los(self) -> None:
        for gate in self._gates:
            gate.release()
        self._gates.clear()
        for op in self.has:
            self.fake.on(op, _leeg)


SOORTEN = {"leeg": frozenset(), "alles": KINDEREN}
PAREN = [
    (scenario, soort)
    for soort, has in SOORTEN.items()
    for scenario in C.SCENARIOS
    if C.required(scenario) <= has
]


def test_elk_scenario_draait_tegen_de_nep():
    assert {s for s, _ in PAREN} == set(C.SCENARIOS)
    assert len(C.SCENARIOS) == len({s.__name__ for s in C.SCENARIOS})
    # Wat een scenario vraagt, zijn opdrachten van een kind (caps en cancel zijn er altijd)
    assert all(C.required(s) <= KINDEREN for s in C.SCENARIOS)
    assert set(C.STANDAARD) == KINDEREN


@pytest.mark.parametrize(("scenario", "soort"), PAREN, ids=lambda v: getattr(v, "__name__", str(v)))
async def test_contract_nep(scenario, soort, fake, tmp_path):
    driver = FakeDriver(fake, tmp_path, alles=soort == "alles")
    try:
        await scenario(driver)
    finally:
        await driver.los()


def test_de_nep_importeert_geen_numpy():
    code = (
        "import sys\n"
        "import tests.analyse.fake_worker, tests.analyse.analyse_contract\n"
        "print(sorted(m for m in ('numpy', 'onnxruntime') if m in sys.modules))\n"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], cwd=API, capture_output=True, text=True, timeout=120
    )
    assert out.returncode == 0, out.stderr[-2000:]
    assert out.stdout.strip() == "[]"


async def test_beide_kinderen_tegelijk_met_eigen_nummers(fake, settings_fake):
    werk = fake.hold(Op.GROUP)
    zoeken = fake.hold(Op.RANK)
    a = await C.begin(_d(fake, settings_fake), C.STANDAARD[Op.GROUP])
    b = await C.begin(_d(fake, settings_fake), C.STANDAARD[Op.RANK])
    try:
        assert (a.child, b.child) == (Child.WERK, Child.ZOEKEN)
        assert b.n == a.n + 1
        assert set(fake.running) == {Child.WERK, Child.ZOEKEN}
        werk.release()
        assert await a.result() == {}
        assert set(fake.running) == {Child.ZOEKEN}
        zoeken.release()
        assert await b.result() == {}
        assert fake.running == {}
    finally:
        a.close()
        b.close()
    seen = [(s.op, s.code, s.child) for s in fake.requests]
    assert seen == [(Op.GROUP, Code.OK, Child.WERK), (Op.RANK, Code.OK, Child.ZOEKEN)]


async def test_werk_krijgt_een_nieuw_kind_na_acht_opdrachten(fake, settings_fake):
    fake.on(Op.FACES, _leeg)
    fake.on(Op.RANK, _leeg)
    d = _d(fake, settings_fake)
    for i in range(1, 17):
        await _call(d, Op.FACES)
        assert fake.generation[Child.WERK] == i // wire.ANALYSE_CHILD_BATCHES
    for _ in range(20):
        await _call(d, Op.RANK)
    assert fake.generation[Child.ZOEKEN] == 0  # zoeken leeft lang (de cache)


async def test_werk_krijgt_een_nieuw_kind_na_tien_minuten(tmp_path):
    now = [1000.0]
    async with FakeAnalyseWorker(tmp_path / "w.sock", clock=lambda: now[0]) as fake:
        fake.on(Op.FACES, _leeg)
        fake.on(Op.RANK, _leeg)
        d = _d(fake, fake.settings(tmp_path))
        await _call(d, Op.FACES)
        assert fake.generation[Child.WERK] == 0
        now[0] += wire.CHILD_MAX_AGE_S
        await _call(d, Op.FACES)
        await _call(d, Op.RANK)
        assert fake.generation == {Child.WERK: 1, Child.ZOEKEN: 0}


async def test_timeout_geeft_een_nieuw_kind_en_cancel_niet(fake, settings_fake):
    d = _d(fake, settings_fake)
    fake.hold(Op.RANK)
    job = await C.begin(d, C.STANDAARD[Op.RANK], deadline_ms=50)
    with pytest.raises(client.AnalyseFailed) as exc:
        await job.result()
    assert exc.value.code == Code.TIMEOUT
    assert fake.generation[Child.ZOEKEN] == 1

    fake.hold(Op.GROUP)
    job = await C.begin(d, C.STANDAARD[Op.GROUP])
    assert await client.cancel(settings_fake, job.n) is True
    with pytest.raises(client.AnalyseFailed) as exc:
        await job.result()
    assert exc.value.code == Code.CANCELLED
    assert fake.generation[Child.WERK] == 0
    assert [s.code for s in fake.requests if s.op is not Op.CANCEL] == [
        Code.TIMEOUT,
        Code.CANCELLED,
    ]


async def test_faces_met_previews_en_vectoren_in_uit(fake, settings_fake):
    """De handler ziet de previews en uit; de vectoren staan achter elkaar in uit."""

    async def faces(req: FakeRequest):
        assert sorted(req.extra) == ["uit"]
        photos = []
        for fd in req.previews:
            data = os.pread(fd, 16, 0)
            n = 2 if data == b"twee" else 0
            box = wire.FaceBox(x=0.1, y=0.1, w=0.2, h=0.2, score=0.95, px=80)
            photos.append(wire.FacesPhoto(code="ok", w=1024, h=768, faces=[box] * n, n=n))
            os.write(req.extra["uit"], bytes(wire.FACE_VEC_BYTES * n))
        return wire.FacesData(photos=photos)

    fake.on(Op.FACES, faces)
    previews = [wire.sealed_memfd("p", b"twee"), wire.sealed_memfd("p", b"geen")]
    uit = wire.out_memfd("uit")
    try:
        data = await client.call(settings_fake, Op.FACES, wire.FacesArgs(), fds=[*previews, uit])
        found = wire.FacesData.model_validate(data)
        assert [p.n for p in found.photos] == [2, 0]
        assert len(wire.read_all(uit, 1 << 20)) == found.vector_bytes() == 2 * 512
    finally:
        wire.close_all([*previews, uit])
    assert fake.requests[-1].previews == 2


async def test_group_eist_een_verzegelde_invoer(fake, settings_fake):
    fake.on(Op.GROUP, _leeg)
    los, uit = wire.out_memfd("in"), wire.out_memfd("uit")
    try:
        with pytest.raises(client.AnalyseFailed) as exc:
            await client.call(
                settings_fake, Op.GROUP, wire.GroupArgs(mode="suggest"), fds=[los, uit]
            )
    finally:
        wire.close_all([los, uit])
    assert exc.value.code == Code.BAD_REQUEST


async def test_de_nep_houdt_geen_fd_vast(fake, settings_fake):
    fake.on(Op.FACES, _leeg)
    fake.on(Op.GROUP, _leeg)
    d = _d(fake, settings_fake)
    before = len(os.listdir("/proc/self/fd"))
    for _ in range(5):
        await _call(d, Op.FACES)
        await _call(d, Op.GROUP)
        await client.caps(settings_fake)
        with pytest.raises(client.AnalyseFailed):
            await _call(d, Op.RANK)  # not_built, met een fd
    await asyncio.sleep(0.05)
    assert len(os.listdir("/proc/self/fd")) == before


async def test_scripts_busy_fail_en_internal(fake, settings_fake):
    d = _d(fake, settings_fake)
    fake.on(Op.FACES, _leeg)
    fake.on(Op.EMBED_TEXT, _leeg)
    fake.busy.add(Child.WERK)
    with pytest.raises(client.AnalyseBusy) as busy:
        await _call(d, Op.FACES)
    assert busy.value.child == "werk"
    await _call(d, Op.EMBED_TEXT)  # zoeken is vrij

    fake.fail(Op.RANK, Code.TOO_BIG, "het bestand is te groot")
    with pytest.raises(client.AnalyseFailed) as failed:
        await _call(d, Op.RANK)
    assert (failed.value.code, failed.value.detail) == ("too_big", "het bestand is te groot")

    async def stuk(req: FakeRequest):
        raise RuntimeError(C.GEHEIM)

    fake.on(Op.FORGET, stuk)
    with pytest.raises(client.AnalyseFailed) as failed:
        await _call(d, Op.FORGET)
    assert failed.value.code == "internal"
    assert C.GEHEIM not in str(failed.value)


async def test_caps_volgt_de_nep(fake, settings_fake):
    caps = await client.caps(settings_fake)
    assert caps.built == ["cancel", "caps"]
    assert [m.name for m in caps.models] == [m.name for m in lock.load()]
    assert caps.missing() == [] and caps.beeld is None and caps.limits is None
    fake.models["face_recognition_sface_2021dec.onnx"] = "anders"
    fake.beeld = wire.BeeldCaps(model="siglip2", dim=768, scale=4.7, bias=-16.5)
    fake.limits = wire.LimitsCaps(cpus=2.0, mem_mb=2048)
    fake.on(Op.FACES, _leeg)
    caps = await client.caps(settings_fake)
    assert caps.missing() == ["face_recognition_sface_2021dec.onnx"]
    assert caps.built == ["cancel", "caps", "faces"]
    assert caps.beeld is not None and caps.beeld.dim == 768
    assert caps.limits == wire.LimitsCaps(cpus=2.0, mem_mb=2048)


async def test_pid1_doet_caps_en_cancel_zelf(fake):
    for op in (Op.CAPS, Op.CANCEL):
        with pytest.raises(ValueError):
            fake.on(op, _leeg)


async def test_wat_de_nep_onthoudt_heeft_geen_inhoud(fake, settings_fake):
    """requests heeft de op, de code, het nummer en tijden: geen zoektekst (K1-08)."""
    fake.on(Op.RANK, _leeg)
    args = wire.RankArgs(owner="a" * 32, gen=1, text=C.GEHEIM, k=10)
    vectoren = wire.out_memfd("vectoren")
    try:
        await client.call(settings_fake, Op.RANK, args, fds=[vectoren])
    finally:
        os.close(vectoren)
    assert C.GEHEIM not in repr(fake.requests)
    assert fake.requests[-1].op is Op.RANK and fake.requests[-1].code is Code.OK


async def test_een_handler_ziet_args_en_deadline(fake, settings_fake):
    seen: list[FakeRequest] = []

    async def kijk(req: FakeRequest):
        seen.append(req)
        raise FakeFail(Code.IMAGE_BAD)

    fake.on(Op.EMBED_IMAGE, kijk)
    previews = [wire.sealed_memfd("p", b"x")] * 3
    uit = wire.out_memfd("uit")
    try:
        with pytest.raises(client.AnalyseFailed):
            await client.call(
                settings_fake,
                Op.EMBED_IMAGE,
                wire.EmbedImageArgs(),
                fds=[*previews, uit],
                deadline_ms=2500,
            )
    finally:
        wire.close_all([previews[0], uit])
    [req] = seen
    assert req.deadline_ms == 2500 and len(req.previews) == 3 and req.child is Child.WERK
    assert isinstance(req.args, wire.EmbedImageArgs)


# --- hulpjes ---


def _d(fake: FakeAnalyseWorker, settings) -> C.AnalyseDriver:
    class D:
        name = "nep"
        socket = fake.socket
        key = fake.key
        has = frozenset(fake.built - {Op.CAPS, Op.CANCEL})
        models = ()

        def settings(self):
            return settings

        async def lang(self, op):
            raise NotImplementedError

        async def los(self):
            pass

    return D()


async def _call(d: C.AnalyseDriver, op: Op) -> dict:
    job = await C.begin(d, C.STANDAARD[op])
    try:
        return await job.result()
    finally:
        job.close()
