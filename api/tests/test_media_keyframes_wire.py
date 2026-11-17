"""De keyframe-index in het antwoord van de worker en in media_probe (M4, Keuze 12, 17).

Een index van 50.000 keyframes (een film van bijna 3 uur met een GOP van 0,2 s) past in één
antwoord, uitgedund waar nodig; de vorm is zlib(varint-delta's) en heen en terug gelijk; een
blob die niet klopt geeft ValueError, nooit iets anders (fuzz met vaste seeds).
"""

from __future__ import annotations

import base64
import random
import zlib

import pytest

from app.media import wire
from app.media.worker import containers as C


def index(times: list[int], sizes: list[int] | None = None, **kw) -> C.Index:
    return C.Index(times, sizes or [64 * 1024] * len(times), "cues", **kw)


def test_heen_en_terug():
    times = [-42, 0, 2000, 2001, 4000, 7_200_000]
    sizes = [1, 1024, 1025, 0, 5_000_000, 3]
    got_t, got_s = C.decode(C.encode(times, sizes))
    assert got_t == times
    assert got_s == [-(-s // 1024) * 1024 for s in sizes]  # op KiB naar boven


def test_een_film_van_2_uur_is_klein():
    """Keuze 17: een keyframe per 2 s, 2 uur: ±4 KB."""
    times = [2000 * i for i in range(3600)]
    blob = C.encode(times, [500 * 1024] * len(times))
    assert len(blob) < 5000


def test_50000_keyframes_passen_in_een_antwoord():
    rng = random.Random(7)
    times, now = [], 0
    for _ in range(50_000):
        times.append(now)
        now += 200 + rng.randint(-40, 40)  # een GOP van 0,2 s, niet helemaal regelmatig
    sizes = [rng.randint(5_000, 400_000) for _ in times]
    data = C.for_wire(index(times, sizes, end_ms=now))
    assert data["state"] == "ready"  # uitgedund blijft ready
    assert data["total"] == 50_000 and data["count"] < 50_000
    assert data["thinned_ms"] >= 1000 and len(data["keyframes"]) <= C.WIRE_MAX
    # Het hele antwoord past in één boodschap van 64 KB
    message = {"ev": "result", "data": data}
    assert len(wire.encode(message)) <= wire.MAX_MESSAGE
    got_t, got_s = C.decode_b64(data["keyframes"])
    step = data["thinned_ms"]
    assert got_t[0] == 0 and all(b - a >= step for a, b in zip(got_t, got_t[1:], strict=False))
    assert set(got_t) <= set(times)
    # De groottes van wat wegviel, tellen bij de keyframe ervoor (op KiB naar boven)
    assert sum(got_s) >= sum(sizes)
    # max_gop_ms komt uit de volle index, niet uit de uitgedunde
    assert data["max_gop_ms"] == max(b - a for a, b in zip(times, [*times[1:], now], strict=True))


def test_een_kleine_index_wordt_niet_uitgedund():
    data = C.for_wire(index([0, 2000, 4000], end_ms=6000))
    assert data["thinned_ms"] == 0 and data["count"] == data["total"] == 3
    assert C.decode_b64(data["keyframes"])[0] == [0, 2000, 4000]


def test_sparse_blijft_sparse():
    data = C.for_wire(index([0, 30_000], sparse=True))
    assert data["state"] == "sparse"


def test_thin_houdt_de_eerste_en_telt_de_groottes_op():
    t, s = C.thin([0, 300, 900, 1000, 2500, 2600], [1, 2, 3, 4, 5, 6], 1000)
    assert t == [0, 1000, 2500] and s == [6, 4, 11]
    assert C.thin([0, 1], [1, 1], 0) == ([0, 1], [1, 1])


def test_encode_weigert_wat_niet_oploopt():
    with pytest.raises(ValueError):
        C.encode([0, 2000, 1000], [1, 1, 1])
    with pytest.raises(ValueError):
        C.encode([0], [])


def test_een_zip_bom_is_een_fout():
    raw = b"\x80" * (C.BLOB_MAX + 10)
    with pytest.raises(ValueError):
        C.decode(zlib.compress(raw))


@pytest.mark.parametrize("seed", [1, 99])
def test_fuzz_decode(seed):
    rng = random.Random(seed)
    good = C.encode([0, 2000, 4000, 6000], [1024, 2048, 4096, 1])
    raw = zlib.decompress(good)
    for _ in range(500):
        kind = rng.random()
        if kind < 0.3:
            blob = rng.randbytes(rng.randint(0, 64))
        elif kind < 0.7:
            mutated = bytearray(raw)
            for _ in range(rng.randint(1, 4)):
                if mutated:
                    mutated[rng.randrange(len(mutated))] = rng.randrange(256)
            blob = zlib.compress(bytes(mutated[: rng.randint(0, len(mutated))]))
        else:
            mutated = bytearray(good)
            mutated[rng.randrange(len(mutated))] ^= 1 << rng.randrange(8)
            blob = bytes(mutated)
        try:
            times, sizes = C.decode(blob)
        except ValueError:
            continue
        assert len(times) == len(sizes)
    with pytest.raises(ValueError):
        C.decode_b64("niet base64!")
    with pytest.raises(ValueError):
        C.decode_b64(base64.b64encode(b"geen zlib").decode())
