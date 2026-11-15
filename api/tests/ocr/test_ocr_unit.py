"""De losse delen van OCR (bouwontwerp fase 6, X1; Keuze 22 en 24), zonder tesseract.

- de rechtzetting (app/ocr/deskew.py) op een gemaakte A4 van 300 dpi (tests/ocr/scans.py): bij
  0°, 3°, 7° en −5° binnen 0,15° (gemeten: 0,0°; de fijnste stap is 0,1°), en een blad zonder
  tekstregels blijft staan;
- het terugrekenen (app/ocr/geometry.py) tegen een echt gedraaid beeld van Pillow;
- de PGM (app/ocr/pgm.py): de kop precies zoals header() hem schrijft, alles anders BadImage; een
  kop die liegt (meer of minder bytes dan hij zegt) of te groot is, over een echte pijp
  (ops/ocr.read_pgm), en draaien in stappen van 90°;
- de TSV van tesseract (ops/ocr.parse_tsv), de regel voor de zekerheid (≥ 60 gaat mee), de
  tekst per regel, de stukken van hooguit 60 000 bytes en de OSD.
"""

from __future__ import annotations

import asyncio
import json
import os

import pytest
from PIL import Image

from app.media import wire
from app.media.worker.ops import ocr
from app.ocr import deskew, geometry, pgm
from tests.ocr import scans

TOLERANCE = 0.15  # graden; gemeten 0,0° bij 0, 3, 7, −5 en 11,5°


# --- de rechtzetting -------------------------------------------------------------------------


@pytest.mark.parametrize("angle", [0, 3, 7, -5])
def test_de_rechtzetting_vindt_de_hoek(angle):
    """page(angle) staat angle graden tegen de klok in; measure geeft de draaiing terug."""
    assert abs(deskew.measure(scans.page(angle)) + angle) <= TOLERANCE


def test_de_rechtzetting_tot_aan_de_grens():
    assert abs(deskew.measure(scans.page(11.5)) + 11.5) <= TOLERANCE


def test_een_kleine_hoek_en_een_blad_zonder_regels_blijven_staan():
    # onder MIN_ANGLE draait kind 1 niet: dat maakt het beeld alleen zachter
    assert deskew.measure(scans.page(0.2)) == 0.0
    blank = scans._paper((1240, 1754), "leeg")
    assert deskew.measure(blank) == 0.0
    assert deskew.measure(Image.new("L", (600, 400), 255)) == 0.0


def test_draaien_maakt_groter_en_vult_met_papier():
    img = scans.page(0)
    turned = deskew.turn(img, 5)
    assert turned.size[0] <= deskew.expanded(img.size, 5)[0]
    assert turned.size[1] <= deskew.expanded(img.size, 5)[1]
    assert turned.size[0] > img.size[0] and turned.size[1] > img.size[1]
    # de hoek die erbij kwam is papier (de mediaan), geen zwart
    assert abs(turned.getpixel((2, 2)) - scans.PAPER) <= 8


# --- terugrekenen ----------------------------------------------------------------------------


def _dot(img: Image.Image) -> tuple[float, float]:
    """Het zwaartepunt van de donkere pixels."""
    w, h = img.size
    data = img.tobytes()
    xs = ys = n = 0
    for i, v in enumerate(data):
        if v < 128:
            xs += i % w
            ys += i // w
            n += 1
    assert n
    return xs / n + 0.5, ys / n + 0.5


@pytest.mark.parametrize("skew", [0.0, 3.0, -5.0, 7.5, -12.0])
def test_terug_in_de_bron_tegen_pillow(skew):
    """Een stip in de bron; kind 1 zet de bron recht (turn over −skew); het midden van de stip
    in het rechte beeld gaat met back() terug naar waar hij in de bron stond."""
    source = (900, 600)
    for x, y in ((120, 80), (450, 300), (800, 550)):
        img = Image.new("L", source, 255)
        img.paste(0, (x - 4, y - 4, x + 4, y + 4))
        straight = deskew.turn(img, -skew)
        sx, sy = _dot(straight)
        bx, by = geometry.back(sx, sy, skew, straight.size, source)
        assert abs(bx - x) <= 1.0 and abs(by - y) <= 1.0, (skew, (x, y), (bx, by))


def test_het_midden_van_een_vak():
    assert geometry.centre((10, 20, 30, 40), 0.0, (100, 100), (100, 100)) == (25.0, 40.0)
    # een ander formaat (de bron is kleiner gemaakt): dan ligt het midden even ver van het midden
    x, y = geometry.centre((60, 60, 0, 0), 0.0, (120, 120), (100, 100))
    assert (x, y) == (50.0, 50.0)


# --- de PGM ----------------------------------------------------------------------------------


def test_de_kop_van_de_pgm():
    assert pgm.header(300, 200) == b"P5\n300 200\n255\n"
    assert pgm.read_header(b"P5\n300 200\n255\n\x00\x01") == (300, 200, 15)
    assert pgm.read_header(pgm.header(pgm.MAX_SIDE, 2000)) == (pgm.MAX_SIDE, 2000, 18)
    for w, h in ((0, 10), (10, 0), (pgm.MAX_SIDE + 1, 10), (7000, 7000)):
        with pytest.raises(pgm.BadImage):
            pgm.header(w, h)


@pytest.mark.parametrize(
    "head",
    [
        b"P6\n300 200\n255\n",  # kleur
        b"P2\n300 200\n255\n",  # tekst
        b"P5 300 200 255\n",  # andere witruimte
        b"P5\n300  200\n255\n",
        b"P5\n# opmerking\n300 200\n255\n",
        b"P5\n300 200\n65535\n",  # 16 bits
        b"P5\n300 200\n254\n",
        b"P5\n0300 200\n255\n",  # voorloopnul
        b"P5\n-300 200\n255\n",
        b"P5\n300 0\n255\n",
        b"P5\n300 200\n",  # geen einde
        b"P5\n123456 2\n255\n",  # zes cijfers
        b"\x00P5\n300 200\n255\n",
        b"",
    ],
)
def test_een_andere_kop_is_bad_image(head):
    with pytest.raises(pgm.BadImage):
        pgm.read_header(head)


@pytest.mark.parametrize("w,h", [(pgm.MAX_SIDE + 1, 10), (10, 20001), (7000, 6000), (20000, 20000)])
def test_een_te_grote_kop_is_bad_image(w, h):
    with pytest.raises(pgm.BadImage, match="maat"):
        pgm.read_header(b"P5\n%d %d\n255\n" % (w, h))


def test_genoeg_gelezen_voor_de_kop():
    assert not pgm.header_done(b"P5\n300 ")
    assert pgm.header_done(b"P5\n300 200\n255\n")
    assert pgm.header_done(b"x" * pgm.HEADER_MAX)  # dan beslist read_header (en weigert)


def _grid(w: int, h: int) -> Image.Image:
    return Image.frombytes("L", (w, h), bytes((i * 37) % 251 for i in range(w * h)))


@pytest.mark.parametrize(
    "cw,transpose",
    [(90, Image.Transpose.ROTATE_270), (180, Image.Transpose.ROTATE_180),
     (270, Image.Transpose.ROTATE_90)],
)  # fmt: skip
def test_draaien_met_de_klok_mee_zoals_pillow(cw, transpose):
    img = _grid(7, 4)
    pixels, w, h = pgm.rotate(img.tobytes(), 7, 4, cw)
    want = img.transpose(transpose)
    assert (w, h) == want.size and pixels == want.tobytes()
    assert pgm.rotate(img.tobytes(), 7, 4, 0) == (img.tobytes(), 7, 4)
    with pytest.raises(pgm.BadImage):
        pgm.rotate(b"\x00" * 27, 7, 4, 90)


@pytest.mark.parametrize("cw", [0, 90, 180, 270])
def test_een_vak_terug_na_het_draaien(cw):
    """Een zwart vak in het beeld; na pgm.rotate het vak in het gedraaide beeld zoeken en met
    unrotate_box terugzetten: hetzelfde vak."""
    w, h = 40, 25
    box = (5, 3, 9, 6)
    img = Image.new("L", (w, h), 255)
    img.paste(0, (box[0], box[1], box[0] + box[2], box[1] + box[3]))
    pixels, w2, h2 = pgm.rotate(img.tobytes(), w, h, cw)
    turned = Image.frombytes("L", (w2, h2), pixels)
    left, top, right, bottom = Image.eval(turned, lambda v: 255 - v).getbbox()
    assert pgm.unrotate_box((left, top, right - left, bottom - top), w, h, cw) == box


# --- de PGM over een pijp (zoals de worker hem van kind 1 leest) -----------------------------


async def _read_from(data: bytes) -> tuple[int, int, bytes]:
    """read_pgm op de leeskant van een echte pijp; een schrijver zet data erin en sluit dan."""
    r, w = os.pipe2(os.O_CLOEXEC)
    os.set_blocking(r, False)

    def write() -> None:
        try:
            view = memoryview(data)
            while view:
                view = view[os.write(w, view[: 1 << 16]) :]
        except BrokenPipeError:
            pass  # de lezer gaf het al op
        finally:
            os.close(w)

    writer = asyncio.get_running_loop().run_in_executor(None, write)
    try:
        return await asyncio.wait_for(ocr.read_pgm(r), 10)
    finally:
        os.close(r)
        await asyncio.wait_for(writer, 5)


@pytest.mark.anyio
async def test_een_goede_pgm_over_de_pijp():
    pixels = bytes(range(256)) * (2480 * 3508 // 256) + bytes(2480 * 3508 % 256)
    assert await _read_from(pgm.header(2480, 3508) + pixels) == (2480, 3508, pixels)
    assert await _read_from(pgm.header(1, 1) + b"\x07") == (1, 1, b"\x07")


@pytest.mark.anyio
async def test_een_kop_die_liegt():
    pixels = b"\x80" * (300 * 200)
    with pytest.raises(ocr.BadImage, match="te kort"):  # zegt meer dan er komt
        await _read_from(b"P5\n300 201\n255\n" + pixels)
    with pytest.raises(ocr.BadImage, match="te lang"):  # zegt minder dan er komt
        await _read_from(b"P5\n300 199\n255\n" + pixels)
    with pytest.raises(ocr.BadImage, match="te lang"):  # alles past al in het eerste stuk
        await _read_from(b"P5\n2 2\n255\n" + b"\x00" * 5)


@pytest.mark.anyio
async def test_een_te_grote_kop_wacht_niet_op_de_pixels():
    """De kop zegt 20000 × 20000: meteen BadImage, ook al blijft de pijp open en komt er niets
    meer (de worker leest geen 400 MB in om dan pas te weigeren)."""
    r, w = os.pipe2(os.O_CLOEXEC)
    os.set_blocking(r, False)
    try:
        os.write(w, b"P5\n20000 20000\n255\n")
        with pytest.raises(ocr.BadImage, match="maat"):
            await asyncio.wait_for(ocr.read_pgm(r), 2)
        os.write(w, b"P6\n")
    finally:
        os.close(r)
        os.close(w)


@pytest.mark.anyio
async def test_geen_pgm_over_de_pijp():
    with pytest.raises(ocr.BadImage, match="kop"):
        await _read_from(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
    with pytest.raises(ocr.BadImage, match="kop"):
        await _read_from(b"P5\n300")  # een halve kop en dan EOF
    with pytest.raises(EOFError):
        await _read_from(b"")


def test_het_antwoord_van_kind_1():
    said = {"skew": 3.456, "width": 300, "height": 200, "source": [280, 180],
            "ms": {"render": 12, "deskew": 30, "anders": 5, "ocr": "x"}}  # fmt: skip
    assert ocr._answer(json.dumps(said).encode(), 300, 200) == {
        "skew": 3.46,
        "source": [280, 180],
        "ms": {"render": 12, "deskew": 30},
    }
    with pytest.raises(ocr.BadImage):
        ocr._answer(json.dumps(said).encode(), 300, 201)
    for bad in ({"skew": 46}, {"source": [0, 10]}, {"source": [10]}, {"source": "10x10"},
                {"source": [pgm.MAX_SIDE + 1, 10]}, {"source": [1.5, 10]}):  # fmt: skip
        with pytest.raises(ValueError):
            ocr._answer(json.dumps({**said, **bad}).encode(), 300, 200)
    with pytest.raises(ValueError):
        ocr._answer(json.dumps({"error": "UnidentifiedImageError"}).encode(), 300, 200)
    with pytest.raises(ValueError):
        ocr._answer(b"[]", 300, 200)


# --- de TSV, de zekerheid, de stukken, de OSD ------------------------------------------------

HEAD = "\t".join(
    ["level", "page_num", "block_num", "par_num", "line_num", "word_num", "left", "top", "width",
     "height", "conf", "text"]
)  # fmt: skip


def tsv(*rows: tuple) -> bytes:
    return ("\n".join([HEAD, *("\t".join(str(c) for c in row) for row in rows)]) + "\n").encode()


def word(block, line, n, left, conf, text, par=1):
    return (5, 1, block, par, line, n, left, 100 * line, 50, 30, conf, text)


SAMPLE = tsv(
    (1, 1, 0, 0, 0, 0, 0, 0, 2480, 3508, -1, ""),
    (2, 1, 1, 0, 0, 0, 240, 360, 1800, 200, -1, ""),
    (3, 1, 1, 1, 0, 0, 240, 360, 1800, 200, -1, ""),
    (4, 1, 1, 1, 1, 0, 240, 360, 1800, 40, -1, ""),
    word(1, 1, 1, 240, 96.5, "Gemeente"),
    word(1, 1, 2, 400, 91.0, "Utrecht"),
    word(1, 1, 3, 560, 59.99, "afdeling"),
    word(1, 2, 1, 240, 60, "Factuur"),
    word(1, 2, 2, 400, 12.25, "nummer"),
    word(1, 2, 3, 560, 88, "   "),  # leeg: geen woord
    word(2, 1, 1, 240, 77, "Kenmerk"),
    (5, 1, 2, 1, 1, 2, "x", 100, 50, 30, 90, "kapot"),  # geen getal
    (5, 1, 2, 1, 1, 3, 400, 100, 50, 30, -1, "zonder"),  # geen zekerheid
    (5, 1, 2, 1, 1, 4),  # te weinig kolommen
)


def test_de_tsv_lezen():
    words, conf = ocr.parse_tsv(SAMPLE)
    assert [w[0] for w in words] == [
        "Gemeente", "Utrecht", "afdeling", "Factuur", "nummer", "Kenmerk",
    ]  # fmt: skip
    assert words[0] == ["Gemeente", 240, 100, 50, 30, 96.5, (1, 1, 1)]
    assert words[-1][6] == (2, 1, 1)
    assert conf == pytest.approx((96.5 + 91 + 59.99 + 60 + 12.25 + 77) / 6)
    assert ocr.parse_tsv(tsv()) == ([], 0.0)
    assert ocr.parse_tsv(b"") == ([], 0.0)
    assert ocr.parse_tsv(b"\xff\xfe onzin\n\t\t\n") == ([], 0.0)
    long, _ = ocr.parse_tsv(tsv(word(1, 1, 1, 0, 90, "x" * 500)))
    assert long[0][0] == "x" * ocr.MAX_WORD_CHARS


def test_de_zekerheidsregel_en_de_regels():
    """Woorden met zekerheid ≥ 60 gaan mee, de rest niet; regel telt de regels van de pagina
    (blok, alinea, regel) in de volgorde van de TSV, ook als een regel er helemaal uit valt."""
    kept = ocr.keep(ocr.parse_tsv(SAMPLE)[0])
    assert kept == [
        ["Gemeente", 240, 100, 50, 30, 0],
        ["Utrecht", 400, 100, 50, 30, 0],
        ["Factuur", 240, 200, 50, 30, 1],
        ["Kenmerk", 240, 100, 50, 30, 2],
    ]
    assert ocr.text_of(kept) == "Gemeente Utrecht\nFactuur\nKenmerk"
    low = ocr.keep([["laag", 0, 0, 1, 1, 30.0, (1, 1, 1)], ["hoog", 0, 0, 1, 1, 70.0, (1, 1, 2)]])
    assert low == [["hoog", 0, 0, 1, 1, 0]]
    assert ocr.text_of([]) == ""


def test_hooguit_max_words():
    many = [[f"w{i}", 0, 0, 1, 1, 99.0, (1, 1, i // 10)] for i in range(ocr.MAX_WORDS + 50)]
    assert len(ocr.keep(many)) == ocr.MAX_WORDS


def test_de_stukken_passen_in_een_boodschap():
    words = [["é" * ocr.MAX_WORD_CHARS, 20000, 20000, 20000, 20000, 9999] for _ in range(1500)]
    words += [[f"w{i}", i, i, 3, 3, i // 7] for i in range(3000)]
    chunks = ocr.parts(words)
    assert len(chunks) > 2 and [w for c in chunks for w in c] == words
    for i, chunk in enumerate(chunks):
        size = len(json.dumps(chunk, separators=(",", ":"), ensure_ascii=False).encode())
        assert size <= ocr.PART_BYTES + 2
        message = {"ev": "result", "data": {"part": i, "words": chunk}}
        assert len(wire.encode(message)) <= wire.MAX_MESSAGE
    assert ocr.parts([]) == []
    assert ocr.parts([["a", 0, 0, 1, 1, 0]]) == [[["a", 0, 0, 1, 1, 0]]]


def test_de_osd():
    out = b"Page number: 0\nOrientation in degrees: 270\nRotate: 90\nOrientation confidence: 4.2\n"
    assert ocr.parse_osd(out) == 90
    assert ocr.parse_osd(out.replace(b"\n", b"\r\n")) == 90
    assert ocr.parse_osd(b"Rotate: 180\n") == 180
    assert ocr.parse_osd(b"Rotate: 270") == 270
    for bad in (b"", b"Rotate: 45\n", b"Rotate: 90 graden\n", b"rotate: 90\n", b"xRotate: 90\n"):
        assert ocr.parse_osd(bad) == 0, bad


def test_de_samenvatting_heeft_vaste_sleutels():
    assert ocr._summary("none", "ocr_missing") == {
        "state": "none", "code": "ocr_missing", "error": None, "parts": 0, "words": 0,
        "chars": 0, "conf": None, "skew": 0.0, "width": 0, "height": 0, "source": None,
        "rotate": 0, "osd": False, "ms": {},
    }  # fmt: skip
