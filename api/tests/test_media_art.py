"""Plaatjes van providers (providers/art.py, Keuze 44, M8).

- Alleen JPEG, PNG en WebP (op de eerste bytes); groter dan 10 MB geweigerd.
- Omzetten in het renderproces (`python -I`, kale omgeving, eigen procesgroep): WebP in drie maten
  (256, 512, 1.280; nooit groter dan het origineel), alle plaatjes van één aanroep in één proces;
  een kapot plaatje of een decompressiebom komt er niet door; wat er al staat, wordt niet opnieuw
  gemaakt.
- De adressen: een HMAC met een eigen sleutel; met exp (kandidaten) of zonder (art bij een item).
- Opruimen: art zonder verwijzing (ouder dan een uur), de tijdelijke map na 24 uur.
- Een fuzztest met een vaste seed op het renderproces (regel 10).
"""

from __future__ import annotations

import io
import os
import random
import struct
import sys
import time
import zlib

import pytest
from PIL import Image

from app.config import Settings
from app.media.providers import art

pytestmark = pytest.mark.anyio

SEED = 20260510


def picture(fmt: str = "JPEG", size=(1600, 900), color=(200, 30, 30)) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", size, color).save(out, fmt)
    return out.getvalue()


def png_bomb(width: int = 30_000, height: int = 30_000) -> bytes:
    """Een PNG die zegt dat hij 30.000 × 30.000 is, met bijna niets erin."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    header = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(b"\0" * 100))
        + chunk(b"IEND", b"")
    )


@pytest.fixture
def spawned(monkeypatch):
    """Telt de renderprocessen (en kijkt hoe ze starten)."""
    import asyncio

    calls: list[tuple] = []
    real = asyncio.create_subprocess_exec

    async def spy(*args, **kwargs):
        calls.append((args, kwargs))
        return await real(*args, **kwargs)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spy)
    return calls


# --- soort en grootte -----------------------------------------------------------------------------


def test_alleen_jpeg_png_en_webp():
    assert art.check(picture("JPEG")) == "image/jpeg"
    assert art.check(picture("PNG")) == "image/png"
    assert art.check(picture("WEBP")) == "image/webp"
    for body in (picture("GIF"), picture("BMP"), b"<svg xmlns='x'/>", b"", b"\xff\xd8"):
        with pytest.raises(art.Refused):
            art.check(body)


def test_groter_dan_10_mb_geweigerd():
    with pytest.raises(art.Refused, match="te groot"):
        art.check(b"\xff\xd8\xff" + b"\0" * art.MAX_BYTES)
    assert art.check(b"\xff\xd8\xff" + b"\0" * (art.MAX_BYTES - 3)) == "image/jpeg"


async def test_een_te_groot_plaatje_komt_niet_in_het_renderproces(settings, spawned):
    assert await art.store(settings, [b"\xff\xd8\xff" + b"\0" * art.MAX_BYTES]) == [None]
    assert spawned == []


# --- omzetten in het renderproces -----------------------------------------------------------------


async def test_webp_in_drie_maten_via_het_renderproces(settings, spawned):
    bodies = [picture("JPEG", (1600, 900)), picture("PNG", (300, 450)), picture("WEBP", (800, 800))]
    shas = await art.store(settings, bodies)
    assert all(shas) and len(set(shas)) == 3
    # Eén proces voor alle drie, zoals de previews: python -I, kale omgeving, eigen groep
    [(args, kwargs)] = spawned
    assert args[0] == sys.executable and args[1] == "-I"
    assert set(kwargs["env"]) == {"PATH", "LANG", "HOME"}
    assert kwargs["start_new_session"] is True
    expected = {
        shas[0]: {"w1280": (1280, 720), "w512": (512, 288), "w256": (256, 144)},
        shas[1]: {"w1280": (300, 450), "w512": (300, 450), "w256": (171, 256)},
        shas[2]: {"w1280": (800, 800), "w512": (512, 512), "w256": (256, 256)},
    }
    for sha, sizes in expected.items():
        for size, dims in sizes.items():
            path = art.find(settings, sha, size)
            assert path == settings.media_dir / "art" / sha / f"{size}.webp"
            assert art.sniff(path.read_bytes()) == "image/webp"
            with Image.open(path) as img:
                assert img.format == "WEBP" and img.size == dims
    # De werkmap is weer leeg
    assert list((settings.media_dir / "art-work").iterdir()) == []


async def test_wat_er_al_staat_wordt_niet_opnieuw_gemaakt(settings, spawned):
    body = picture()
    [sha] = await art.store(settings, [body])
    assert await art.store(settings, [body, body]) == [sha, sha]
    assert len(spawned) == 1


async def test_kapot_of_een_bom_komt_er_niet_door(settings, spawned):
    good = picture("PNG", (64, 64))
    broken = picture("JPEG")[:400]  # de kop klopt, de rest ontbreekt
    shas = await art.store(settings, [broken, png_bomb(), good])
    assert shas[0] is None and shas[1] is None and shas[2] is not None
    assert len(spawned) == 1
    assert sorted(p.name for p in (settings.media_dir / "art").iterdir()) == [shas[2]]


async def test_kandidaten_in_de_tijdelijke_map(settings):
    body = picture()
    [sha] = await art.store(settings, [body], tmp=True)
    folder = settings.media_dir / "art-tmp" / sha
    assert (folder / "w256.webp").is_file()
    assert not (settings.media_dir / "art" / sha).exists()
    # Nog eens: weer 24 uur (de map krijgt een nieuwe tijd)
    os.utime(folder, (1, 1))
    assert await art.store(settings, [body], tmp=True) == [sha]
    assert folder.stat().st_mtime > time.time() - 60
    # Voor een item komt hij in de vaste map
    await art.store(settings, [body])
    assert (settings.media_dir / "art" / sha / "w512.webp").is_file()


async def test_een_renderproces_dat_faalt_is_refused(settings, monkeypatch):
    monkeypatch.setattr(art, "CHILD", "import sys; sys.exit(3)")
    with pytest.raises(art.Refused):
        await art.store(settings, [picture()])
    monkeypatch.setattr(art, "CHILD", "print('geen json')")
    with pytest.raises(art.Refused):
        await art.store(settings, [picture("PNG")])


async def test_fuzz_het_renderproces_met_een_vaste_seed(settings):
    """Verknoeide JPEG's, PNG's en WebP's: nooit een fout naar buiten, nooit een hang; wat
    lukt, is een echte WebP in drie maten."""
    rng = random.Random(SEED)
    sources = [picture("JPEG", (200, 120)), picture("PNG", (90, 160)), picture("WEBP", (64, 64))]
    bodies = []
    for _ in range(60):
        body = bytearray(rng.choice(sources))
        for _ in range(rng.randint(1, 12)):
            at = rng.randrange(3 if body[:3] == b"\xff\xd8\xff" else 12, len(body))
            body[at] = rng.randrange(256)
        if rng.random() < 0.2:
            body = body[: rng.randint(12, len(body))]
        bodies.append(bytes(body))
    started = time.monotonic()
    shas = await art.store(settings, bodies)
    assert time.monotonic() - started < art.CHILD_SECONDS
    assert len(shas) == len(bodies)
    for sha in filter(None, shas):
        for size in art.SIZES:
            path = art.find(settings, sha, size)
            assert path is not None and art.sniff(path.read_bytes()) == "image/webp"


# --- adressen -------------------------------------------------------------------------------------


async def test_adressen_met_en_zonder_exp(settings):
    key = art.url_key(settings)
    sha = "ab" * 32
    now = 1_800_000_000
    exp = art.expiry(now)
    assert exp % 3600 == 0 and now + 12 * 3600 <= exp < now + 13 * 3600
    url = art.url(key, sha, "w256", exp)
    assert url.startswith(f"/api/v1/media/art/{sha}/w256.webp?exp={exp}&sig=")
    sig = url.rsplit("sig=", 1)[1]
    assert art.valid(key, sha, "w256", exp, sig, now)
    assert not art.valid(key, sha, "w256", exp, sig, exp + 1)  # verlopen
    assert not art.valid(key, sha, "w512", exp, sig, now)  # een andere maat
    assert not art.valid(key, sha, "w256", exp + 3600, sig, now)  # een andere exp
    assert not art.valid(key, "cd" * 32, "w256", exp, sig, now)
    assert not art.valid(key, sha, "w256", -1, sig, now)
    # Zonder exp: blijft geldig (art bij een item; zie m8.md)
    forever = art.url(key, sha, "w512")
    assert "exp=" not in forever
    assert art.valid(key, sha, "w512", 0, forever.rsplit("sig=", 1)[1], now + 10**9)
    # Een ander app-geheim: een andere sleutel
    other = Settings(data_dir=settings.data_dir, web_dist=settings.web_dist, jwt_secret="z" * 32)
    assert art.url_key(other) != key
    assert art.entry(key, sha, "backdrop") == {"art": sha, "url": art.url(key, sha, "w1280")}


async def test_find_alleen_geldige_namen(settings):
    assert art.find(settings, "../" * 10, "w256") is None
    assert art.find(settings, "a" * 64, "w9999") is None
    assert art.find(settings, "A" * 64, "w256") is None


def test_shas_in():
    sha = "ef" * 32
    value = {
        "poster": {"art": sha, "url": "/api/…"},
        "thumb": {"node": "1234"},
        "backdrop": {"art": "geen-sha"},
        "x": "y",
    }
    assert art.shas_in(value) == {sha}
    assert art.shas_in(None) == set() and art.shas_in([1]) == set()


# --- opruimen -------------------------------------------------------------------------------------


async def test_opruimen(settings, monkeypatch):
    now = time.time()
    base = settings.media_dir
    made = {}
    for where, name, age in (
        ("art", "a" * 64, 3 * 3600),  # met verwijzing: blijft
        ("art", "b" * 64, 3 * 3600),  # zonder verwijzing, oud: weg
        ("art", "c" * 64, 60),  # zonder verwijzing, net gemaakt: blijft (de verwijzing komt nog)
        ("art-tmp", "d" * 64, 25 * 3600),  # kandidaat van gisteren: weg
        ("art-tmp", "e" * 64, 3600),  # kandidaat van net: blijft
        ("art-work", "tmpabc", 2 * 3600),  # wat een renderproces liet staan: weg
    ):
        folder = base / where / name
        folder.mkdir(parents=True)
        (folder / "w256.webp").write_bytes(b"RIFF\0\0\0\0WEBP")
        os.utime(folder, (now - age, now - age))
        made[(where, name[0])] = folder

    async def referenced(_settings):
        return {"a" * 64}

    monkeypatch.setattr(art, "referenced", referenced)
    assert await art.cleanup(settings, now) == {"art": 1, "tmp": 1, "work": 1}
    gone = {k for k, folder in made.items() if not folder.exists()}
    assert gone == {("art", "b"), ("art-tmp", "d"), ("art-work", "t")}
    # Niets meer te doen
    assert await art.cleanup(settings, now) == {"art": 0, "tmp": 0, "work": 0}


async def test_opruimen_zonder_mappen(settings):
    assert await art.cleanup(settings) == {"art": 0, "tmp": 0, "work": 0}
