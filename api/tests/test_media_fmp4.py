"""De knipper van fMP4 (M4, Keuze 22, 23; regel 5 en 10): boxen in stukken van 1 byte, te
groot, afgekapt, `size` 0 en 1, `tfdt` versie 0 en 1, `trun` versie 0 en 1 met negatieve
offsets, de vergelijking van `stsd`, en fuzz met een vaste seed.

De stroom hier is met de hand gebouwd (zoals ffmpeg hem met de vlaggen van §3.3 schrijft); de
echte uitvoer van stream staat in de tests met marker media onderaan."""

from __future__ import annotations

import random
import struct
import time

import pytest

from app.media import fmp4
from app.media.fmp4 import Cutter, Fmp4Error, Fragment, Init

V_SCALE = 12288  # 24 fps × 512, zoals ffmpeg
A_SCALE = 48000
FRAME = 512  # één beeld in de tijdschaal van de video


# --- een kleine bouwer ------------------------------------------------------------------------


def box(kind: str, *parts: bytes, large: bool = False) -> bytes:
    body = b"".join(parts)
    if large:
        return struct.pack(">I4sQ", 1, kind.encode(), 16 + len(body)) + body
    return struct.pack(">I4s", 8 + len(body), kind.encode()) + body


def full(kind: str, version: int, flags: int, *parts: bytes) -> bytes:
    return box(kind, bytes([version]) + flags.to_bytes(3, "big"), *parts)


def trak(tid: int, handler: str, scale: int, entry: bytes) -> bytes:
    tkhd = full("tkhd", 0, 3, b"\0" * 8, struct.pack(">I", tid), b"\0" * 68)
    mdhd = full("mdhd", 0, 0, b"\0" * 8, struct.pack(">II", scale, 0), b"\0" * 4)
    hdlr = full("hdlr", 0, 0, b"\0" * 4, handler.encode(), b"\0" * 12, b"x\0")
    stsd = full("stsd", 0, 0, struct.pack(">I", 1), entry)
    stbl = box("stbl", stsd)
    return box("trak", tkhd, box("mdia", mdhd, hdlr, box("minf", stbl)))


def avc1(width: int = 640, height: int = 360, config: bytes = b"\x01\x64\x00\x1e") -> bytes:
    fixed = b"\0" * 6 + struct.pack(">H", 1) + b"\0" * 16 + struct.pack(">HH", width, height)
    fixed += b"\0" * (78 - len(fixed))
    return box("avc1", fixed, box("avcC", config, b"\xff\xe1"))


def mp4a() -> bytes:
    return box("mp4a", b"\0" * 28, box("esds", b"\0" * 20))


def init(width: int = 640, *, audio: bool = True, mvhd: bytes = b"\0" * 100) -> bytes:
    traks = [trak(1, "vide", V_SCALE, avc1(width))]
    trex = [full("trex", 0, 0, struct.pack(">IIIII", 1, 1, 0, 0, 0))]
    if audio:
        traks.append(trak(2, "soun", A_SCALE, mp4a()))
        trex.append(full("trex", 0, 0, struct.pack(">IIIII", 2, 1, 1024, 0, 0)))
    moov = box("moov", box("mvhd", mvhd), *traks, box("mvex", *trex))
    return box("ftyp", b"iso5\0\0\0\0iso6mp41") + moov


def traf(
    tid: int, tfdt: int, samples: list[tuple[int, int, int]], *, tfdt_v: int = 1,
    trun_v: int = 1, first_flags: int | None = None,
) -> bytes:  # fmt: skip
    """samples: (duur, vlaggen, cto). first_flags: via first_sample_flags in plaats van per
    sample."""
    tfhd = full("tfhd", 0, 0x020000, struct.pack(">I", tid))
    if tfdt_v == 1:
        t = full("tfdt", 1, 0, struct.pack(">Q", tfdt))
    else:
        t = full("tfdt", 0, 0, struct.pack(">I", tfdt))
    flags = 0x1 | 0x100 | 0x200 | 0x800
    rows = b""
    if first_flags is not None:
        flags |= 0x4
    else:
        flags |= 0x400
    for duration, sflags, cto in samples:
        rows += struct.pack(">II", duration, 100)
        if first_flags is None:
            rows += struct.pack(">I", sflags)
        rows += struct.pack(">i" if trun_v == 1 else ">I", cto)
    head = struct.pack(">Ii", len(samples), 0)
    if first_flags is not None:
        head += struct.pack(">I", first_flags)
    trun = full("trun", trun_v, flags, head, rows)
    return box("traf", tfhd, t, trun)


KEY = 0x02000000
NONKEY = 0x01010000


def gop(start_frame: int, n: int, *, b_frames: bool = True) -> list[tuple[int, int, int]]:
    """Eén GOP in decodeervolgorde: I P B B P B B … met negatieve offsets voor de B's (zoals
    +negative_cts_offsets: het keyframe valt op tfdt)."""
    out = [(FRAME, KEY, 0)]
    for i in range(1, n):
        if b_frames and i % 3 == 1:
            out.append((FRAME, NONKEY, 2 * FRAME))
        elif b_frames:
            out.append((FRAME, NONKEY, -FRAME))
        else:
            out.append((FRAME, NONKEY, 0))
    return out


def fragment(seq: int, v_tfdt: int, frames: int, *, audio: bool = True, **kw) -> bytes:
    trafs = [traf(1, v_tfdt, gop(0, frames), **kw)]
    if audio:
        a_tfdt = v_tfdt * A_SCALE // V_SCALE
        trafs.append(traf(2, a_tfdt, [(1024, 0, 0)] * (frames * 2), trun_v=0))
    moof = box("moof", full("mfhd", 0, 0, struct.pack(">I", seq)), *trafs)
    return moof + box("mdat", b"\0" * 64)


def stream(n: int = 5, frames: int = 48, *, origin_ms: int = 21) -> bytes:
    """Een uitvoer zoals stream hem geeft: tfdt = mediatijd + 1 s, keyframes op origin + 2k."""
    first = round((1000 + origin_ms) * V_SCALE / 1000)
    out = init()
    for k in range(n):
        out += fragment(k + 1, first + k * frames * FRAME, frames)
    return out


def cut(data: bytes, step: int | None = None) -> list[Init | Fragment]:
    c = Cutter()
    items: list[Init | Fragment] = []
    step = step or len(data) or 1
    for i in range(0, len(data), step):
        items += c.feed(data[i : i + step])
    c.close()
    return items


# --- de tests ---------------------------------------------------------------------------------


def test_een_hele_stroom():
    items = cut(stream())
    head, frags = items[0], items[1:]
    assert isinstance(head, Init) and all(isinstance(f, Fragment) for f in frags)
    assert {t.handler for t in head.tracks.values()} == {"vide", "soun"}
    assert head.video is not None and head.video.timescale == V_SCALE
    assert (head.video.width, head.video.height) == (640, 360)
    assert [f.video_start_ms for f in frags] == [21, 2021, 4021, 6021, 8021]
    assert all(f.sync for f in frags)
    assert frags[0].video_end_ms == 2021
    assert frags[0].trun_version == 1
    assert frags[0].audio_start_ms == 21


def test_in_stukken_van_een_byte():
    data = stream(3)
    assert [getattr(x, "video_start_ms", None) for x in cut(data, 1)] == [
        getattr(x, "video_start_ms", None) for x in cut(data)
    ]


def test_de_bytes_blijven_dezelfde():
    data = stream(3)
    items = cut(data, 333)
    assert items[0].data + b"".join(f.data for f in items[1:]) == data


@pytest.mark.parametrize("v", [0, 1])
def test_tfdt_versie_0_en_1(v):
    data = init() + fragment(1, 12288 + 258, 48, tfdt_v=v)
    frag = cut(data)[1]
    assert frag.video_start_ms == 21


def test_trun_versie_1_met_negatieve_offsets():
    """De pts van het eerste sample is tfdt + cto, en de grootste pts komt van een P-beeld
    met een positieve offset: de B-beelden ervoor hebben een negatieve offset (V4)."""
    data = init() + fragment(1, 12288, 7)
    frag = cut(data)[1]
    assert frag.video_start_ms == 0 and frag.video_cto_ms == 0
    # decodeervolgorde I P B B P B B: dts 0..6 beelden; de P op dts 4 heeft cto +2 → pts 6
    assert frag.video_last_ms == round(6 * FRAME * 1000 / V_SCALE)
    assert frag.video_end_ms == round(7 * FRAME * 1000 / V_SCALE)


def test_trun_versie_0_leest_de_offset_zonder_teken():
    samples = [(FRAME, KEY, 2 * FRAME)]
    moof = box("moof", traf(1, 12288, samples, trun_v=0))
    frag = cut(init(audio=False) + moof + box("mdat", b"x"))[1]
    assert frag.video_start_ms == round(2 * FRAME * 1000 / V_SCALE)
    assert frag.trun_version == 0


def test_een_eerste_offset_onder_nul_bij_versie_1():
    samples = [(FRAME, KEY, -FRAME), (FRAME, NONKEY, 0)]
    moof = box("moof", traf(1, 12288 + 2 * FRAME, samples))
    frag = cut(init(audio=False) + moof + box("mdat", b"x"))[1]
    assert frag.video_start_ms == round(FRAME * 1000 / V_SCALE)
    assert frag.video_cto_ms == -round(FRAME * 1000 / V_SCALE)


def test_first_sample_flags():
    moof = box("moof", traf(1, 12288, [(FRAME, 0, 0), (FRAME, 0, 0)], first_flags=NONKEY))
    frag = cut(init(audio=False) + moof + box("mdat", b"x"))[1]
    assert frag.sync is False


def test_een_fragment_zonder_beeld():
    moof = box("moof", traf(2, 48000, [(1024, 0, 0)] * 4, trun_v=0))
    frag = cut(init() + moof + box("mdat", b"x"))[1]
    assert frag.video_start_ms is None and frag.audio_start_ms == 0


def test_size_1_is_een_64_bits_grootte():
    data = (
        init()
        + box("moof", traf(1, 12288, gop(0, 3)), large=True)
        + box("mdat", b"\0" * 10, large=True)
    )
    assert cut(data, 5)[1].video_start_ms == 0


def test_size_0_wordt_geweigerd():
    data = init() + struct.pack(">I4s", 0, b"mdat") + b"\0" * 32
    with pytest.raises(Fmp4Error, match="zonder grootte"):
        cut(data)


@pytest.mark.parametrize(
    ("kind", "size"),
    [("moof", fmp4.MOOF_MAX + 1), ("mdat", fmp4.MDAT_MAX + 1), ("moov", fmp4.INIT_MAX + 1),
     ("mdat", 4 * 1024**3), ("mdat", 2**63)],
)  # fmt: skip
def test_te_groot_al_bij_de_kop(kind, size):
    """De grens geldt bij de kop: de knipper wacht nooit op 4 GB."""
    c = Cutter()
    head = init() if kind != "moov" else box("ftyp", b"iso5")
    c.feed(head)
    if kind == "mdat":
        c.feed(box("moof", traf(1, 12288, gop(0, 2))))
    big = (
        struct.pack(">I4sQ", 1, kind.encode(), size)
        if size > 2**32 - 1
        else struct.pack(">I4s", size, kind.encode())
    )
    with pytest.raises(Fmp4Error, match="groter dan"):
        c.feed(big)
    assert c.pending < 64


def test_de_init_samen_hooguit_1_mb():
    c = Cutter()
    c.feed(box("ftyp", b"\0" * (60 * 1024)))
    with pytest.raises(Fmp4Error):
        c.feed(box("moov", b"\0" * (fmp4.INIT_MAX - 30 * 1024)))


def test_afgekapt():
    data = stream(2)
    for end in (len(data) - 1, len(data) - 70, 10):
        c = Cutter()
        c.feed(data[:end])
        with pytest.raises(Fmp4Error):
            c.close()


def test_een_moof_zonder_mdat_aan_het_eind():
    with pytest.raises(Fmp4Error, match="zonder mdat"):
        cut(init() + box("moof", traf(1, 12288, gop(0, 2))))


@pytest.mark.parametrize(
    "data",
    [
        box("moof", b"") + box("mdat", b""),  # vóór de init
        box("ftyp", b"x") + box("mdat", b""),
        box("ftyp", b"x") + box("ftyp", b"x") + box("moov", b""),  # moov zonder sporen
        box("wide", b""),  # onbekend
    ],
)
def test_de_vorm(data):
    with pytest.raises(Fmp4Error):
        cut(data)


def test_twee_moofs_en_een_mdat_zonder_moof():
    with pytest.raises(Fmp4Error, match="twee moof"):
        cut(init() + box("moof", traf(1, 0, gop(0, 1))) * 2)
    with pytest.raises(Fmp4Error, match="mdat zonder moof"):
        cut(init() + box("mdat", b""))


def test_wat_weg_mag():
    data = init() + box("free", b"\0" * 10) + fragment(1, 12288, 3) + box("skip", b"")
    items = cut(data)
    assert len(items) == 2


def test_een_trun_met_meer_samples_dan_bytes():
    tfhd = full("tfhd", 0, 0x020000, struct.pack(">I", 1))
    trun = full("trun", 1, 0x100, struct.pack(">I", 1_000_000), b"\0" * 8)
    moof = box("moof", box("traf", tfhd, trun))
    with pytest.raises(Fmp4Error, match="past niet"):
        cut(init() + moof + box("mdat", b""))


def test_een_trun_zonder_velden_rekent_zonder_lus():
    """Alle samples met de standaardwaarden: een miljoen samples kost geen miljoen stappen."""
    tfhd = full("tfhd", 0, 0x020000 | 0x8 | 0x20, struct.pack(">III", 1, FRAME, 0))
    tfdt = full("tfdt", 1, 0, struct.pack(">Q", 12288))
    trun = full("trun", 1, 0, struct.pack(">I", fmp4.MAX_SAMPLES))
    moof = box("moof", box("traf", tfhd, tfdt, trun))
    started = time.monotonic()
    frag = cut(init(audio=False) + moof + box("mdat", b""))[1]
    assert time.monotonic() - started < 0.5
    assert frag.video_end_ms == round(fmp4.MAX_SAMPLES * FRAME * 1000 / V_SCALE)
    too_many = full("trun", 1, 0, struct.pack(">I", fmp4.MAX_SAMPLES + 1))
    with pytest.raises(Fmp4Error):
        cut(init(audio=False) + box("moof", box("traf", tfhd, tfdt, too_many)) + box("mdat", b""))


def test_een_traf_voor_een_onbekend_spoor():
    moof = box("moof", traf(9, 0, gop(0, 1)))
    with pytest.raises(Fmp4Error, match="onbekend spoor"):
        cut(init() + moof + box("mdat", b""))


def test_tijdschaal_0():
    bad = init().replace(struct.pack(">II", V_SCALE, 0), struct.pack(">II", 0, 0), 1)
    with pytest.raises(Fmp4Error, match="tijdschaal"):
        cut(bad)


# --- de generatie (Keuze 22, spike punt 13) ----------------------------------------------------


def test_dezelfde_stsd_is_dezelfde_generatie():
    a = fmp4.parse_init(init())
    b = fmp4.parse_init(init(mvhd=b"\x01" * 100))  # een andere mvhd telt niet
    assert a.key == b.key


def test_een_andere_maat_is_een_andere_generatie():
    a = fmp4.parse_init(init(640))
    b = fmp4.parse_init(init(480))
    assert a.key != b.key
    assert a.video is not None and b.video is not None
    assert a.video.stsd != b.video.stsd


def test_een_ander_spoor_is_een_andere_generatie():
    assert fmp4.parse_init(init()).key != fmp4.parse_init(init(audio=False)).key


def test_media_ms():
    assert fmp4.media_ms(12288, V_SCALE) == 0
    assert fmp4.media_ms(round(3.021 * V_SCALE), V_SCALE) == 2021  # 37122 / 12,288 = 3020,996
    assert fmp4.media_ms(0, 1000) == -1000


# --- fuzz (regel 10) ---------------------------------------------------------------------------


def _mutate(data: bytearray, rng: random.Random) -> bytes:
    for _ in range(rng.randint(1, 8)):
        kind = rng.random()
        pos = rng.randrange(len(data))
        if kind < 0.5:
            data[pos] = rng.randrange(256)
        elif kind < 0.7:
            del data[pos : pos + rng.randint(1, 16)]
        elif kind < 0.85:
            data[pos:pos] = bytes(rng.randrange(256) for _ in range(rng.randint(1, 16)))
        else:  # een grootte of een telling verbouwen
            data[pos : pos + 4] = struct.pack(">I", rng.choice([0, 1, 7, 2**31, 2**32 - 1]))
        if not data:
            data.append(0)
    return bytes(data)


def test_fuzz_nooit_een_andere_fout_of_een_hang():
    rng = random.Random(4)
    base = stream(4, 12)
    started = time.monotonic()
    for _ in range(1500):
        data = _mutate(bytearray(base), rng)
        c = Cutter()
        try:
            step = rng.choice([1, 7, 64, 4096, len(data)])
            for i in range(0, len(data), step):
                c.feed(data[i : i + step])
            c.close()
        except Fmp4Error:
            pass
        assert c.pending <= len(data)
    assert time.monotonic() - started < 30


def test_fuzz_willekeur():
    rng = random.Random(11)
    for _ in range(300):
        data = bytes(rng.randrange(256) for _ in range(rng.randint(0, 600)))
        c = Cutter()
        try:
            c.feed(data)
            c.close()
        except Fmp4Error:
            pass


# --- de echte uitvoer (marker media: de fixtures maken ffmpeg) ---------------------------------


@pytest.mark.media
@pytest.mark.parametrize("name", ["stream_h264.mp4", "stream_vp9.mp4", "stream_h264_alt.mp4"])
def test_de_echte_uitvoer_tegen_de_lezer_van_de_tests(name):
    from tests.media import boxes
    from tests.media import fixtures as F

    data = F.make(name).read_bytes()
    ref = boxes.parse(data)
    items = cut(data, 65536)
    head, frags = items[0], items[1:]
    assert isinstance(head, Init) and head.data == ref.init
    assert len(frags) == len(ref.fragments)
    for a, b in zip(frags, ref.fragments, strict=True):
        assert (a.video_start_ms, a.video_last_ms, a.video_end_ms, a.sync) == (
            b.video_start_ms, b.video_last_ms, b.end_ms, b.sync,
        )  # fmt: skip
        assert a.data == b.data and a.trun_version == 1
    assert [f.video_start_ms for f in frags] == [21 + 2000 * k for k in range(10)]


@pytest.mark.media
def test_de_echte_alt_is_een_andere_generatie():
    from tests.media import fixtures as F

    keys = set()
    for name in ("stream_h264.mp4", "stream_h264_alt.mp4", "stream_vp9.mp4"):
        head = Cutter().feed(F.make(name).read_bytes()[:200_000])[0]
        assert isinstance(head, Init)
        keys.add(head.key)
    assert len(keys) == 3


@pytest.mark.media
def test_fuzz_op_de_echte_uitvoer():
    from tests.media import fixtures as F

    rng = random.Random(23)
    base = F.make("stream_h264.mp4").read_bytes()[:400_000]
    started = time.monotonic()
    for _ in range(200):
        data = _mutate(bytearray(base), rng)
        c = Cutter()
        try:
            for i in range(0, len(data), 65536):
                c.feed(data[i : i + 65536])
        except Fmp4Error:
            pass
    assert time.monotonic() - started < 60
