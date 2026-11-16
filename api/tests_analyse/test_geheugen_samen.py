"""Het geheugen van de container analyse met alles tegelijk (fase 6, BZ5 zonder de echte modellen;
K2-06, Keuze 56, Keuze 75; de samenvoeging van golf 6).

De echte worker (pid 1 en de kinderen werk en zoeken, met RLIMIT_DATA en de wachter per kind) met
de nepmodellen van fake_models.py en fake_beeld.py, en tegelijk:

- in werk een groepsronde (mode full) van 60.000 open gezichten plus 6.000 bevestigde (de grens
  van een ronde, PLATFORM_FACES_GROUP_MAX, en de set van test_group_perf.py);
- in zoeken de cache vol: Eva met 100.000 foto's en Bram met 36.000 (samen 418,9 MB van de
  419,4 MB van ANALYSE_BEELD_CACHE_MB), en dan, midden in de groepsronde, een nieuw bestand van
  Eva met 100.000 foto's dat zoeken herlaadt (f16 → f32) en doorzoekt.

Gemeten: VmHWM (maxrss) van pid 1, werk en zoeken, en elke 20 ms de som van hun VmRSS (de piek
tegelijk). De poort van BZ5 is maxrss ≤ 2,5 GB voor de container onder mem_limit: 3g. Wat de
echte modellen meer kosten dan de nep, telt er hier bij, met de getallen van §3.11 van het
ontwerp: in werk de modellen van gezichten (224 MB, de hele maxrss van werk in B6, dus ruim) en
het beeldmodel (+165 MB, B6), in zoeken het tekstmodel (+340 MB, B6) en de tokenizer (≤ 100 MB,
het doel van BZ7). Zo moet ook elk kind onder zijn RLIMIT_DATA blijven. En geen opdracht faalt,
geen kind wordt vervangen (de wachter doodt een kind boven zijn grens), en zoeken blijft ook
tijdens het herladen bij de grens van de cache plus de import: vóór de samenvoeging hield
Cache.get de oude matrix van Eva vast tot na het laden (zoeken 801 MB in plaats van 507 MB;
test_beeld_cache.py::test_herladen_laat_het_oude_bestand_los_voor_het_laden).

**Met modellen van de echte vorm** (ANALYSE_GEHEUGEN_MODELLEN en ANALYSE_GEHEUGEN_LOCK, samen; de
een zonder de ander faalt): dezelfde test met die map en die lock in plaats van de nep, bijvoorbeeld
SigLIP 2 op ware grootte met willekeurige gewichten (siglip_vol.py, omgezet zoals de job
beeld-model) en de echte YuNet en SFace. Dan laadt werk vóór de groepsronde YuNet, SFace en het
beeldmodel (een faces- en een embed-opdracht, zoals tijdens de backfill), en laadt zoeken de
tokenizer en het tekstmodel bij de vraag midden in de ronde. ECHT_ERBIJ en IMPORT (de schatting
van wat de nep niet kost, en de ruimte van zoeken naast de cache met de nep) gelden dan niet: de
poort is de som ≤ 2,5 GB en elk kind onder zijn RLIMIT_DATA, en de regel noemt per kind het
gemeten getal en zijn RLIMIT_DATA. Zonder die variabelen verandert er niets.

Wat alleen CI of de LXC meet: het echte SigLIP 2 en YuNet en SFace in dezelfde container, en de
piek van de cgroup (memory.peak onder mem_limit: 3g). Zie x3b.md, Open 3.
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path

import anyio
import numpy as np
import pytest
from PIL import Image

from app.analyse import client, wire
from app.analyse.wire import Child, Op
from app.beeld import types as vt
from app.media.worker import guard
from tests.conftest import anyio_backend  # noqa: F401
from tests_analyse import echt, fake_beeld, fake_models, siglip_vol
from tests_analyse.groep_maak import invoer, maak

pytestmark = [pytest.mark.anyio, pytest.mark.analyse_echt]

MB = 1024 * 1024
GRENS = 2_500_000_000  # maxrss van de container (BZ5): 2,5 GB
IMPORT = 200 * MB  # wat zoeken naast de cache mag hebben: de import, de nepmodellen, een stuk
# Wat de echte modellen meer kosten dan de nep (§3.11, de tabel van het geheugen)
ECHT_ERBIJ = {Child.WERK: (224 + 165) * MB, Child.ZOEKEN: (340 + 100) * MB}
OPEN, BEKEND = 60_000, 6_000
EVA, BRAM = "e" * 32, "b" * 32
N_EVA, N_BRAM = 100_000, 36_000
STUK = 10_000
MODELLEN_ENV, LOCK_ENV = "ANALYSE_GEHEUGEN_MODELLEN", "ANALYSE_GEHEUGEN_LOCK"


def vectoren(path: Path, n: int, gen: int, seed: int) -> None:
    """Een bestand per eigenaar zoals app/beeld/vecfile.py het neerzet (kop, num's, f16, het
    begin van elke sha), met n willekeurige vectoren van lengte 1."""
    rng = np.random.default_rng(seed)
    with open(path, "wb") as out:
        out.write(vt.pack_head(vt.Head(fake_beeld.MAP, 1, fake_beeld.DIM, n, gen)))
        out.write(np.arange(1, n + 1, dtype="<i8").tobytes())
        for begin in range(0, n, STUK):
            m = rng.standard_normal((min(STUK, n - begin), fake_beeld.DIM)).astype(np.float32)
            m /= np.linalg.norm(m, axis=1, keepdims=True)
            out.write(m.astype("<f2").tobytes())
        out.write(np.arange(1, n + 1, dtype="<i8").tobytes())


def groep() -> bytes:
    """60.000 open en 6.000 bevestigde gezichten (de bevestigde eerst, twee per identiteit)."""
    vecs, wie = maak(OPEN + BEKEND, seed=6)
    tel: dict[int, int] = {}
    bekend, person, persoon_van = [], [], {}
    for i, ident in enumerate(wie.tolist()):
        if ident < 0 or len(bekend) >= BEKEND:
            continue
        tel[ident] = tel.get(ident, 0) + 1
        if tel[ident] <= 2:
            persoon_van.setdefault(ident, len(persoon_van))
            bekend.append(i)
            person.append(persoon_van[ident])
    rest = np.setdiff1d(np.arange(len(vecs)), bekend)
    assert len(rest) == OPEN and len(bekend) == BEKEND
    return invoer(np.concatenate([vecs[bekend], vecs[rest]]), person, len(persoon_van))


class Meter:
    """Elke 20 ms de som van VmRSS van pid 1 en zijn kinderen; de hoogste som."""

    def __init__(self, pid: int) -> None:
        self.pid = pid
        self.piek = 0
        self._stop = threading.Event()
        self._draad = threading.Thread(target=self._loop, daemon=True)

    def _loop(self) -> None:
        while not self._stop.is_set():
            pids = [self.pid, *echt.children_of(self.pid)]
            som = sum(guard._status(p).get("VmRSS", 0) * 1024 for p in pids)
            self.piek = max(self.piek, som)
            self._stop.wait(0.02)

    def __enter__(self) -> Meter:
        self._draad.start()
        return self

    def __exit__(self, *exc) -> None:
        self._stop.set()
        self._draad.join()


async def rank(settings, owner: str, path: Path, gen: int, **args) -> list:
    fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC)
    try:
        data = await client.call(
            settings, Op.RANK, wire.RankArgs(owner=owner, gen=gen, **args), fds=[fd]
        )
    finally:
        os.close(fd)
    return wire.RankData.model_validate(data).hits


def kinderen(pid: int) -> dict[Child, int]:
    """De kinderen van pid 1 per soort (de naam in /proc/<pid>/cmdline)."""
    uit: dict[Child, int] = {}
    for kind in echt.children_of(pid):
        delen = Path(f"/proc/{kind}/cmdline").read_bytes().split(b"\0")
        for soort in Child:
            if soort.value.encode() in delen:
                uit[soort] = kind
    return uit


@pytest.fixture
def worker(tmp_path):
    modellen = siglip_vol.uit_omgeving(MODELLEN_ENV, LOCK_ENV)
    if modellen is None:
        modellen = fake_models.install(tmp_path / "modellen")
        fake_beeld.install(modellen)
    with echt.EchteWorker(tmp_path / "worker", modellen) as w:
        yield w


async def werk_laadt_alles(settings) -> None:
    """Een faces-opdracht (YuNet en SFace) en een embed-opdracht (het beeldmodel) in werk: dan
    houdt werk alle drie vast tijdens de groepsronde, zoals na de backfill."""
    foto = echt.webp(Image.new("RGB", (256, 256), (200, 120, 90)))
    gevonden, _ = await echt.faces(settings, [foto])
    assert [p.code for p in gevonden.photos] == ["ok"]
    fds = [echt.preview(foto)]
    uit = wire.out_memfd("uit")
    try:
        data = await client.call(settings, Op.EMBED_IMAGE, wire.EmbedImageArgs(), fds=[*fds, uit])
    finally:
        wire.close_all([*fds, uit])
    assert [p.code for p in wire.EmbedImageData.model_validate(data).photos] == ["ok"]


async def test_groeperen_een_volle_cache_en_herladen_tegelijk_onder_2_5_gb(worker, tmp_path):
    s = worker.settings()
    eva1, eva2, bram = tmp_path / "eva1.vec", tmp_path / "eva2.vec", tmp_path / "bram.vec"
    vectoren(eva1, N_EVA, 1, seed=1)
    vectoren(eva2, N_EVA, 2, seed=2)
    vectoren(bram, N_BRAM, 1, seed=3)
    data = groep()
    echte = siglip_vol.uit_omgeving(MODELLEN_ENV, LOCK_ENV) is not None
    with Meter(worker.pid) as meter:
        if echte:
            await werk_laadt_alles(s)
        # De cache vol: Eva en Bram opgewarmd (k = 0), zoals bjobs.warm na een build
        assert await rank(s, EVA, eva1, 1, k=0) == []
        assert await rank(s, BRAM, bram, 1, k=0) == []
        uit = wire.out_memfd("uit")
        src = wire.sealed_memfd("in", data)
        antwoord: dict = {}

        async def groeperen() -> None:
            antwoord.update(
                await client.call(s, Op.GROUP, wire.GroupArgs(mode="full"), fds=[src, uit])
            )

        try:
            async with anyio.create_task_group() as tg:
                tg.start_soon(groeperen)
                # Wacht tot werk groepeert (het kind bestaat en rekent), dan het herladen
                eind = time.monotonic() + 30
                while Child.WERK not in kinderen(worker.pid):
                    assert time.monotonic() < eind, "werk start niet"
                    await anyio.sleep(0.05)
                await anyio.sleep(1.0)
                voor = kinderen(worker.pid)
                hits = await rank(s, EVA, eva2, 2, text="rood", k=10)
                bezig = not antwoord  # de groepsronde liep nog tijdens het herladen
        finally:
            wire.close_all([src])
        groot = os.fstat(uit).st_size
        wire.close_all([uit])
        na = kinderen(worker.pid)
    assert bezig, "de groepsronde was klaar vóór het herladen: niet tegelijk gemeten"
    assert len(hits) == 10
    assert wire.GroupData.model_validate(antwoord).n == OPEN + BEKEND
    assert groot == (OPEN + BEKEND) * wire.GROUP_OUT.size
    # Geen kind vervangen: de wachter doodde niemand boven zijn RLIMIT_DATA
    assert na == voor and set(na) == {Child.WERK, Child.ZOEKEN}
    hwm = {soort: guard._status(pid)["VmHWM"] * 1024 for soort, pid in na.items()}
    pid1 = guard._status(worker.pid)["VmHWM"] * 1024
    som = pid1 + sum(hwm.values())
    if echte:
        # De modellen zitten er zelf in: geen schatting erbij; de som is de poort, en elk kind
        # blijft onder zijn RLIMIT_DATA
        print(
            f"\nBZ5 met modellen van de echte vorm: pid 1 {pid1 / MB:.0f} MB, werk"
            f" {hwm[Child.WERK] / MB:.0f} MB (RLIMIT_DATA"
            f" {wire.CHILDREN[Child.WERK].rlimit_data / MB:.0f}), zoeken"
            f" {hwm[Child.ZOEKEN] / MB:.0f} MB (RLIMIT_DATA"
            f" {wire.CHILDREN[Child.ZOEKEN].rlimit_data / MB:.0f}) (maxrss); som {som / MB:.0f} MB"
            f" (poort {GRENS / MB:.0f} MB), piek tegelijk {meter.piek / MB:.0f} MB"
        )
        assert som <= GRENS
        for soort, waarde in hwm.items():
            assert waarde < wire.CHILDREN[soort].rlimit_data, (soort, waarde)
    else:
        erbij = sum(ECHT_ERBIJ.values())
        print(
            f"\nBZ5 met de nepmodellen: pid 1 {pid1 / MB:.0f} MB, werk {hwm[Child.WERK] / MB:.0f}"
            f" MB, zoeken {hwm[Child.ZOEKEN] / MB:.0f} MB (maxrss); som {som / MB:.0f} MB, piek"
            f" tegelijk {meter.piek / MB:.0f} MB; met de echte modellen erbij"
            f" {(som + erbij) / MB:.0f} MB"
        )
        assert hwm[Child.ZOEKEN] <= wire.ANALYSE_BEELD_CACHE_MB * MB + IMPORT
        assert som + erbij <= GRENS
        for soort, waarde in hwm.items():
            assert waarde + ECHT_ERBIJ[soort] < wire.CHILDREN[soort].rlimit_data, (soort, waarde)
    assert meter.piek <= som
    assert b"Traceback" not in worker.stderr()
