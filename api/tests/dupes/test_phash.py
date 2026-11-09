"""pHash, de tweede controle en de poort van bijna-duplicaten (bouwontwerp fase 6, Keuzes 35 en
36, V5; Z3).

Alles gaat door de echte code: het voorbeeld van 256 zoals de preview-taak het schrijft
(render.thumbnails), de pHash en de tweede controle zoals het renderproces ze rekent
(render_meta.run_meta, hier in dit proces), de kandidaten uit blob_hash (photo_hash.candidates),
het niveau (photo_hash.levels) en de klieken (dupes.groups.cliques).

- De grenzen liggen hier vast, ook de gemeten MAD64 en die per blok van 8 × 8 (docs/fase-6/z3.md).
- De varianten van V5: half zo groot en JPEG q35 zijn "zeker"; 3–8% bijgesneden en ±4° gedraaid
  zijn nooit "zeker" (hooguit "lijkt").
- Vlak is geen hash: de zwarte en de muur-foto's van §3.6 krijgen state flat.
- De poort: op alle fixtures van §3.6 samen (varianten, brieven, formulieren, schermen, egaal,
  anders) 0 groepen "zeker" met twee verschillende originelen; en elk paar van verschillende
  originelen met pHash ≤ 6 zakt voor de tweede controle.
- Tekst telt mee: andere tekst maakt een paar nooit "zeker".
"""

from __future__ import annotations

import io
import itertools
from pathlib import Path

import pytest
from PIL import Image, ImageOps

from app import photo_hash, render, render_meta
from app.dupes import groups, phash
from app.models import BlobHash
from tests.factories import maker
from tests.search import fixtures as fx

pytestmark = [pytest.mark.anyio, pytest.mark.zoek]

# Keuze 35, met de grenzen die Z3 gemeten heeft (MAD64 2,0 en per blok 5,0; de voorlopige 3,0
# van het ontwerp liet 520 paren van verschillende brieven door)
GRENZEN = {
    "FLAT": 3.0, "NEAR": 10, "SURE": 6, "ASPECT": 0.02, "DHASH": 10, "MAD64": 2.0,
    "BLOCK64": 5.0,
}  # fmt: skip
# Wat de varianten van V5 hooguit halen en verschillende bestanden minstens (gemeten, z3.md)
VARIANT_MAD, VARIANT_BLOK = 1.5, 4.0
ANDERS_BLOK = 6.0
CATEGORIEEN = ("varianten", "brieven", "formulieren", "schermen", "egaal", "anders")


def test_de_grenzen_liggen_vast():
    assert {naam: getattr(phash, naam) for naam in GRENZEN} == GRENZEN
    # De gemeten marges: tussen de varianten en de grens, en tussen de grens en wat anders is
    assert VARIANT_MAD < phash.MAD64 and VARIANT_BLOK < phash.BLOCK64 < ANDERS_BLOK


class Beelden:
    """Beelden als bestand met hun voorbeeld van 256, en wat render_meta ervan maakt."""

    def __init__(self, map_: Path) -> None:
        self.map = map_
        self.items: dict[str, dict] = {}

    def bestand(self, naam: str, pad: Path) -> str:
        """Een bestand (zoals een upload): het voorbeeld zoals de preview-taak het maakt."""
        uit = self.map / naam
        uit.mkdir(parents=True)
        with Image.open(pad) as img:
            render.thumbnails(ImageOps.exif_transpose(img), uit, (render.THUMB,))
        mime = "image/png" if pad.suffix == ".png" else "image/jpeg"
        self.items[naam] = {"sha": naam, "source": str(pad), "mime": mime,
                            "thumb": str(uit / "256.webp")}  # fmt: skip
        return naam

    def beeld(self, naam: str, img: Image.Image, **jpeg) -> str:
        pad = self.map / f"{naam}.jpg"
        pad.parent.mkdir(parents=True, exist_ok=True)
        img.convert("RGB").save(pad, "JPEG", quality=jpeg.get("quality", 90))
        return self.bestand(naam, pad)

    def hashes(self) -> dict[str, photo_hash.Hashed]:
        items = [{**item, "hash": True} for item in self.items.values()]
        answer = render_meta.run_meta({"items": items, "max_pixels": 100_000_000})
        return {
            sha: photo_hash.parse(sha, answer["results"][sha].get("hash")) for sha in self.items
        }

    def meet(self, paren: list[tuple[str, str]]) -> dict[tuple[str, str], dict]:
        """De tweede controle in render_meta, zoals photometa hem vraagt."""
        looks = {
            sha: {"thumb": self.items[sha]["thumb"], "source": self.items[sha]["source"]}
            for paar in paren
            for sha in paar
        }
        answer = render_meta.run_meta({"items": [], "pairs": [list(p) for p in paren],
                                       "looks": looks})  # fmt: skip
        return photo_hash.parse_pairs(answer["pairs"])


def _niveau(b: Beelden, hashes, a: str, x: str) -> str | None:
    ha, hx = hashes[a], hashes[x]
    assert ha.state == hx.state == "ok", (a, ha, x, hx)
    paar = (min(a, x), max(a, x))
    dist = phash.hamming(ha.value, hx.value)
    gemeten = b.meet([paar])
    rijen = photo_hash.levels({paar: dist}, gemeten, {})
    return rijen[0]["p_level"] if rijen else None


def _varianten(img: Image.Image) -> dict[str, Image.Image]:
    """De varianten van V5: half zo groot, JPEG q35, 3–8% bijgesneden en ±4° gedraaid."""
    w, h = img.size
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=35)
    uit = {"half": img.resize((w // 2, h // 2), Image.Resampling.LANCZOS),
           "q35": Image.open(io.BytesIO(buf.getvalue()))}  # fmt: skip
    for c in (3, 5, 8):
        dx, dy = round(w * c / 200), round(h * c / 200)
        uit[f"bijgesneden-{c}"] = img.crop((dx, dy, w - dx, h - dy))
    for hoek in (-4, 4):
        wit = (255, 255, 255)
        uit[f"gedraaid{hoek:+d}"] = img.rotate(hoek, Image.Resampling.BICUBIC, fillcolor=wit)
    return uit


def test_de_varianten_van_v5(tmp_path):
    b = Beelden(tmp_path)
    bronnen = {f"foto-{i}": fx.beeld(("v5", i), 1600, 1200) for i in range(8)}
    for i, pad in enumerate(sorted(fx.make("bijna/brieven").iterdir())[:6]):
        bronnen[f"brief-{i}"] = Image.open(pad).convert("RGB")
    for i, pad in enumerate(sorted(fx.make("bijna/formulieren").iterdir())[:4]):
        bronnen[f"formulier-{i}"] = Image.open(pad).convert("RGB")
    for naam, img in bronnen.items():
        b.beeld(naam, img, quality=95)
        for soort, variant in _varianten(img).items():
            b.beeld(f"{naam}~{soort}", variant)
    hashes = b.hashes()
    uitkomst: dict[str, list[str | None]] = {}
    for naam in bronnen:
        for soort in _varianten(Image.new("RGB", (40, 30))):
            uitkomst.setdefault(soort, []).append(_niveau(b, hashes, naam, f"{naam}~{soort}"))
    n = len(bronnen)
    assert uitkomst["half"] == ["zeker"] * n, uitkomst["half"]
    assert uitkomst["q35"] == ["zeker"] * n, uitkomst["q35"]
    for soort, niveaus in uitkomst.items():
        if soort.startswith(("bijgesneden", "gedraaid")):
            assert "zeker" not in niveaus, (soort, niveaus)


def test_de_varianten_van_de_fixtures(tmp_path):
    """bijna/varianten van §3.6: kleiner en q40 zijn "zeker", bijgesneden en lichter "lijkt"."""
    b = Beelden(tmp_path)
    map_ = fx.make("bijna/varianten")
    for pad in sorted(map_.iterdir()):
        b.bestand(pad.stem, pad)
    hashes = b.hashes()
    for i in range(10):
        gevonden = {
            soort: _niveau(b, hashes, f"orig-{i}", f"orig-{i}-{soort}")
            for soort in ("klein", "q40", "bijgesneden", "lichter")
        }
        assert gevonden == {"klein": "zeker", "q40": "zeker", "bijgesneden": "lijkt",
                            "lichter": "lijkt"}, (i, gevonden)  # fmt: skip


def test_vlak_is_geen_hash(tmp_path):
    b = Beelden(tmp_path)
    for pad in sorted(fx.make("bijna/egaal").iterdir()):
        b.bestand(pad.stem, pad)
    b.beeld("helemaal-zwart", Image.new("RGB", (800, 600), (0, 0, 0)))
    hashes = b.hashes()
    staten = {naam: h.state for naam, h in hashes.items()}
    vlak = {naam for naam, state in staten.items() if state == "flat"}
    assert {n for n in staten if n.startswith(("zwart", "muur", "helemaal"))} <= vlak, staten
    # De lucht heeft een verloop: wel een hash, maar die geeft hier geen enkel paar "zeker"
    lucht = sorted(n for n in staten if n.startswith("lucht"))
    assert all(staten[n] == "ok" for n in lucht), staten
    assert all(hashes[n].value is None for n in vlak)


async def _kandidaten(settings, hashed: list[photo_hash.Hashed]) -> dict[tuple[str, str], int]:
    """photo_hash.candidates zoals photometa hem vraagt: alles in één stuk, blob_hash leeg (dan
    vergelijkt hij binnen het stuk)."""
    async with maker(settings)() as db:
        assert await db.get(BlobHash, "geen") is None
        return await photo_hash.candidates(db, hashed)


def _origineel(naam: str) -> str:
    """Van welk origineel een bestand is: orig-3-klein hoort bij orig-3; de rest is zijn eigen
    origineel."""
    if naam.startswith("orig-"):
        return "-".join(naam.split("-")[:2])
    return naam


async def test_de_poort_geen_groep_zeker_met_twee_originelen(settings, tmp_path):
    b = Beelden(tmp_path)
    for soort in CATEGORIEEN:
        for pad in sorted(fx.make(f"bijna/{soort}").iterdir()):
            b.bestand(pad.stem, pad)
    hashes = b.hashes()
    hashed = list(hashes.values())
    paren = await _kandidaten(settings, hashed)
    gemeten = b.meet(sorted(paren))
    rijen = photo_hash.levels(paren, gemeten, {})
    gevonden = groups.cliques((r["p_a"], r["p_b"], r["p_dist"], r["p_level"]) for r in rijen)
    zeker = [g for g in gevonden if g.level == "zeker"]
    fout = [g.shas for g in zeker if len({_origineel(s) for s in g.shas}) > 1]
    assert fout == [], fout
    # De paren "zeker" zijn precies die van een origineel met zijn kleinere en zijn q40
    zekere_paren = {(r["p_a"], r["p_b"]) for r in rijen if r["p_level"] == "zeker"}
    assert zekere_paren == {
        paar
        for i in range(10)
        for paar in itertools.combinations((f"orig-{i}", f"orig-{i}-klein", f"orig-{i}-q40"), 2)
    }, sorted(zekere_paren)
    # Elk origineel vindt zijn varianten in één groep, zonder iets van een ander. Die groep is
    # "lijkt": de lichtere (afstand 0, MAD64 te hoog) is er ook een kliek mee, en een groep is zo
    # zeker als zijn zwakste paar (Keuze 36; zie Open in z3.md)
    for i in range(10):
        samen = [g for g in gevonden if f"orig-{i}" in g.shas]
        assert len(samen) == 1, (i, samen)
        assert {f"orig-{i}-klein", f"orig-{i}-q40"} <= set(samen[0].shas), (i, samen)
        assert {_origineel(s) for s in samen[0].shas} == {f"orig-{i}"}, (i, samen)
    # Elk paar van verschillende originelen met pHash ≤ 6, ook buiten de kandidaten (die zijn
    # per inhoud hooguit MAX_CANDIDATES): de tweede controle zakt
    dicht = [
        (a, x)
        for (a, ha), (x, hx) in itertools.combinations(sorted(hashes.items()), 2)
        if ha.value is not None and hx.value is not None and _origineel(a) != _origineel(x)
        and phash.hamming(ha.value, hx.value) <= phash.SURE
    ]  # fmt: skip
    assert len(dicht) > 1000, len(dicht)  # brieven en formulieren op één sjabloon
    metingen = b.meet(dicht)
    door = [paar for paar in dicht if phash.check_ok(metingen.get(paar))]
    assert door == [], door[:5]
    # De marge: per blok wijken ze allemaal ver genoeg af (gemeten minimum 6,34)
    assert min(metingen[p]["block"] for p in dicht) >= ANDERS_BLOK


async def test_tekst_telt_mee():
    """Hebben beide tekst en is die anders, dan is een paar nooit "zeker" (Keuze 35)."""
    paar = ("a" * 64, "b" * 64)
    goed = {paar: {"aspect": True, "dhash": 2, "mad": 0.4, "block": 1.0}}
    zelfde = phash.text_key("Factuur 2024", None)
    ander = phash.text_key("Factuur 2025", None)
    assert zelfde == phash.text_key("  FACTUUR\t2024 ", "")
    for teksten, niveau in (
        ({}, "zeker"),
        ({paar[0]: zelfde}, "zeker"),  # één met tekst: geen verschil te zien
        ({paar[0]: zelfde, paar[1]: zelfde}, "zeker"),
        ({paar[0]: zelfde, paar[1]: ander}, "lijkt"),
    ):
        assert photo_hash.levels({paar: 3}, goed, teksten)[0]["p_level"] == niveau, teksten


async def test_de_tweede_controle_per_grens():
    """Elke grens van de tweede controle apart, en wat het renderproces kapot teruggeeft."""
    goed = {"aspect": True, "dhash": 10, "mad": 2.0, "block": 5.0}
    assert phash.check_ok(goed)
    for fout in ({"aspect": False}, {"dhash": 11}, {"mad": 2.001}, {"block": 5.001},
                 {"mad": -1.0}, {"mad": "0"}, {"dhash": True}, {"block": None}):  # fmt: skip
        assert not phash.check_ok({**goed, **fout}), fout
    assert not phash.check_ok(None) and not phash.check_ok({"error": True})
    assert phash.level(6, True) == "zeker" and phash.level(7, True) == "lijkt"
    assert phash.level(6, False) == "lijkt" and phash.level(10, False) == "lijkt"
    assert phash.level(11, True) is None
    # Niet gemeten: dup_pair.mad is NO_MAD, het niveau hooguit "lijkt"
    rij = photo_hash.levels({("a", "b"): 2}, {}, {})[0]
    assert rij["p_mad"] == phash.NO_MAD and rij["p_level"] == "lijkt"


async def test_de_kandidaten_delen_een_stuk_en_zijn_dichtbij():
    """Een kandidaat deelt een stuk van 16 bits op dezelfde plek en verschilt ≤ NEAR bits."""
    basis = 0x0123_4567_89AB_CDEF
    tien = basis ^ 0b11_1111_1111  # 10 bits anders, alle in h0: de andere drie stukken gelijk
    elf = basis ^ 0b111_1111_1111
    # 8 bits anders, 2 in elk stuk: geen stuk gelijk, dus geen kandidaat (Keuze 35)
    verspreid = basis ^ sum(0b11 << (16 * k) for k in range(4))
    waarden = {"basis": basis, "tien": tien, "elf": elf, "verspreid": verspreid}
    hashed = [photo_hash.Hashed(naam, "ok", waarde) for naam, waarde in waarden.items()]
    hashed.append(photo_hash.Hashed("vlak", "flat", None))

    class Leeg:
        async def execute(self, _sel):
            return []

    paren = await photo_hash.candidates(Leeg(), hashed)  # type: ignore[arg-type]
    assert paren == {("basis", "tien"): 10, ("elf", "tien"): 1}, paren
