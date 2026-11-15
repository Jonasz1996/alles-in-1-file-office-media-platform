"""BZ4: de kwaliteitspoort van Keuze 83 met het echte model (fase 6, X3a; marker beeld_echt).

Alleen in de job beeld-model (of later op de LXC met `app.cli beeld-kwaliteit`, X3b). Omgeving:
BEELD_MODEL, BEELD_SET, BEELD_SET_TSV, BEELD_RAPPORT (beeld_echt.py).

Zoals de app: van elke foto maakt app.render.image (de previews van de app, exif uit) 256.webp
en 1024.webp; beeld rekent uit 256.webp (Keuze 77), met de voorbewerking van meta.json, in int8
op één draad. Rangschikken gaat op de cosinus (sigmoid(scale · cos + bias) heeft dezelfde
volgorde). Een foto telt als goed voor een categorie als die in zijn kolom categorieen staat; de
60 andere foto's hebben er geen.

De poorten (Keuze 83, vastgelegd vóór de eerste echte run; lager alleen met een nieuwe keuze in
het ontwerp, nooit hier):

- 24 vragen in het Nederlands met één begrip: gemiddelde precisie@5 ≥ 0,6, en geen vraag met 0
  goede foto's in de top 5;
- 6 combinaties: de bedoelde foto in de top 10 bij ≥ 5 van de 6;
- int8 tegen fp32 (dezelfde set, dezelfde vragen): precisie@5 zakt ≤ 0,05;
- "Meer zoals deze": gemiddeld ≥ 0,7 van de top 4 uit dezelfde categorie;
- de zelftest van de container (Keuze 83): een rood, een blauw en een groen vlak van Pillow, en
  bij "rood", "blauw" en "groen" de goede bovenaan.

Alleen een rapport: de 6 vragen in het Engels; 256.webp tegen 1024.webp (zakt precisie@5 meer
dan 0,02, dan rekent X3 uit 1024.webp, Keuze 77); de verdeling van de scores voor MIN_TEKST en
MIN_FOTO (goed tegen niet goed). "Meer zoals deze": elke foto met een categorie vraagt, de foto
zelf telt niet mee, en een buur is goed als hij een categorie met de vraag deelt.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np
import pytest

from app import render
from tests_analyse.beeld_echt import (
    Model,
    Verslag,
    kwantielen,
    omgeving,
    sha256_van,
    tool,
    voorbewerk,
    vragen,
)

pytestmark = pytest.mark.beeld_echt

NL_PRECISIE = 0.6
COMBI_TOP = 10
COMBI_GENOEG = 5
INT8_ZAKT = 0.05
MEER_ZOALS = 0.7
MEER_TOP = 4
PREVIEW_ZAKT = 0.02  # rapport: daarboven rekent X3 uit 1024.webp (Keuze 77)
KLEUREN = {"rood": (255, 0, 0), "blauw": (0, 0, 255), "groen": (0, 255, 0)}
MAX_PIXELS = 100_000_000  # settings.preview_max_pixels van de app


def _volgorde(scores: np.ndarray) -> np.ndarray:
    """Hoog eerst; bij gelijke score de foto die eerder in de set staat."""
    return np.argsort(-scores, kind="stable")


def _precisie(t: np.ndarray, e: np.ndarray, rijen: list[dict], cats: list[set]) -> list[int]:
    """Per vraag het aantal goede foto's in de top 5."""
    uit = []
    for v, rij in zip(t, rijen, strict=True):
        top = _volgorde(e @ v)[:5]
        uit.append(sum(rij["categorieen"] in cats[i] for i in top))
    return uit


def _meer_zoals(e: np.ndarray, cats: list[set]) -> list[float]:
    uit = []
    for i, c in enumerate(cats):
        if not c:
            continue
        s = e @ e[i]
        s[i] = -np.inf
        top = _volgorde(s)[:MEER_TOP]
        uit.append(sum(bool(c & cats[j]) for j in top) / MEER_TOP)
    return uit


@pytest.fixture(scope="module")
def uitkomst() -> dict:
    model_map = omgeving("BEELD_MODEL")
    set_map = omgeving("BEELD_SET")
    tsv = omgeving("BEELD_SET_TSV")
    verslag = Verslag("bz4")
    fotos = tool().lees_set(tsv.read_text(encoding="utf-8"))
    verkeerd = [f.sleutel for f in fotos if sha256_van(set_map / f"{f.sleutel}.jpg") != f.sha256]
    if verkeerd:
        pytest.fail(f"de sha256 klopt niet voor {verkeerd[:5]}")

    int8 = Model(model_map)
    fp32 = Model(model_map, fp32=True)
    vb = int8.meta["beeld"]["voorbewerking"]
    klein, groot = [], []
    with tempfile.TemporaryDirectory() as tmp:
        for f in fotos:
            out = Path(tmp) / f.sleutel
            out.mkdir()
            render.image(set_map / f"{f.sleutel}.jpg", out, MAX_PIXELS)
            # 1024.webp ontbreekt alleen bij een foto met een lange kant ≤ 256 px; de lange kant
            # van elke foto in de set is ≥ 640 (1280 voor een thumbnail, ≥ MIN_ZIJDE voor een
            # smal origineel)
            if not (out / "1024.webp").is_file():
                pytest.fail(f"{f.sleutel}: render.image gaf geen 1024.webp")
            klein.append(voorbewerk(out / "256.webp", vb))
            groot.append(voorbewerk(out / "1024.webp", vb))
    e8 = int8.beelden(klein)
    e32 = fp32.beelden(klein)
    e8_groot = int8.beelden(groot)
    del klein, groot

    rijen = vragen()
    nl = [r for r in rijen if r["soort"] == "nl"]
    combi = [r for r in rijen if r["soort"] == "combinatie"]
    en = [r for r in rijen if r["soort"] == "en"]
    cats = [set(f.categorieen) for f in fotos]
    t8 = {
        s: int8.teksten([r["vraag"] for r in rs])
        for s, rs in (("nl", nl), ("c", combi), ("en", en))
    }
    t32 = fp32.teksten([r["vraag"] for r in nl])

    nl8 = _precisie(t8["nl"], e8, nl, cats)
    nl32 = _precisie(t32, e32, nl, cats)
    nl_groot = _precisie(t8["nl"], e8_groot, nl, cats)
    en8 = _precisie(t8["en"], e8, en, cats)
    sleutels = [f.sleutel for f in fotos]
    plekken = []
    for v, rij in zip(t8["c"], combi, strict=True):
        doel = sleutels.index(rij["doel"])
        plekken.append(int(np.nonzero(_volgorde(e8 @ v) == doel)[0][0]) + 1)
    meer8 = _meer_zoals(e8, cats)
    meer_groot = _meer_zoals(e8_groot, cats)

    kleur_vragen = list(KLEUREN)
    from PIL import Image

    vlakken = [voorbewerk(Image.new("RGB", (256, 256), rgb), vb) for rgb in KLEUREN.values()]
    k = int8.teksten(kleur_vragen) @ int8.beelden(vlakken).T
    kleur_boven = [int(np.argmax(rij)) == i for i, rij in enumerate(k)]

    # De verdeling van de scores: een vraag tegen een foto (MIN_TEKST), een foto tegen een
    # andere foto (MIN_FOTO)
    cos_tekst = e8 @ t8["nl"].T  # [foto, vraag]
    goed_tekst = np.array([[r["categorieen"] in c for r in nl] for c in cats])
    cos_foto = e8 @ e8.T
    zelfde = np.array([[bool(a & b) for b in cats] for a in cats])
    boven = ~np.eye(len(fotos), dtype=bool)

    def gem(lijst) -> float:
        return round(float(np.mean(lijst)), 4)

    u = {
        "fotos": len(fotos),
        "set": tsv.name,
        "nl": {"int8": gem(np.array(nl8) / 5), "fp32": gem(np.array(nl32) / 5), "min": min(nl8)},
        "nl_per_vraag": [
            {"vraag": r["vraag"], "categorie": r["categorieen"], "int8": a, "fp32": b, "1024": c}
            for r, a, b, c in zip(nl, nl8, nl32, nl_groot, strict=True)
        ],
        "combinaties": [
            {"vraag": r["vraag"], "doel": r["doel"], "plek": p}
            for r, p in zip(combi, plekken, strict=True)
        ],
        "combinaties_top": sum(p <= COMBI_TOP for p in plekken),
        "en": {
            "int8": gem(np.array(en8) / 5),
            "per_vraag": [{"vraag": r["vraag"], "goed": a} for r, a in zip(en, en8, strict=True)],
        },
        "meer_zoals_deze": {
            "int8_256": gem(meer8),
            "int8_1024": gem(meer_groot),
            "vragen": len(meer8),
        },
        "preview": {"256": gem(np.array(nl8) / 5), "1024": gem(np.array(nl_groot) / 5)},
        "kleuren": {
            "vragen": kleur_vragen,
            "cos": [[round(float(x), 4) for x in rij] for rij in k],
            "bovenaan": kleur_boven,
        },
        "verdeling": {
            "tekst_kans_goed": kwantielen(int8.kans(cos_tekst[goed_tekst])),
            "tekst_kans_niet": kwantielen(int8.kans(cos_tekst[~goed_tekst])),
            "tekst_cos_goed": kwantielen(cos_tekst[goed_tekst]),
            "tekst_cos_niet": kwantielen(cos_tekst[~goed_tekst]),
            "foto_cos_zelfde": kwantielen(cos_foto[zelfde & boven]),
            "foto_cos_anders": kwantielen(cos_foto[~zelfde & boven]),
        },
    }
    u["int8_zakt"] = round(u["nl"]["fp32"] - u["nl"]["int8"], 4)
    u["preview_zakt"] = round(u["preview"]["1024"] - u["preview"]["256"], 4)
    for sleutel, waarde in u.items():
        verslag.zet(sleutel, waarde)

    def oordeel(goed: bool) -> str:
        return "goed" if goed else "FOUT"

    verslag.kort(f"BZ4 set: {len(fotos)} foto's uit {tsv.name}, previews met app.render.image")
    verslag.kort(
        f"BZ4 nl: precisie@5 {u['nl']['int8']:.3f} (poort ≥ {NL_PRECISIE}), slechtste vraag "
        f"{u['nl']['min']}/5 (poort ≥ 1) — "
        f"{oordeel(u['nl']['int8'] >= NL_PRECISIE and min(nl8) >= 1)}"
    )
    verslag.kort(
        f"BZ4 combinaties: {u['combinaties_top']}/6 in de top {COMBI_TOP} (poort ≥ "
        f"{COMBI_GENOEG}), plekken {plekken} — {oordeel(u['combinaties_top'] >= COMBI_GENOEG)}"
    )
    verslag.kort(
        f"BZ4 int8 tegen fp32: precisie@5 {u['nl']['int8']:.3f} tegen {u['nl']['fp32']:.3f}, zakt "
        f"{u['int8_zakt']:.3f} (poort ≤ {INT8_ZAKT}) — {oordeel(u['int8_zakt'] <= INT8_ZAKT)}"
    )
    verslag.kort(
        f"BZ4 meer zoals deze: {u['meer_zoals_deze']['int8_256']:.3f} van de top {MEER_TOP} "
        f"(poort ≥ {MEER_ZOALS}) — {oordeel(u['meer_zoals_deze']['int8_256'] >= MEER_ZOALS)}"
    )
    verslag.kort(
        f"BZ4 zelftest kleuren: {sum(kleur_boven)}/3 bovenaan — {oordeel(all(kleur_boven))}"
    )
    verslag.kort(f"BZ4 engels (rapport): precisie@5 {u['en']['int8']:.3f}")
    verslag.kort(
        f"BZ4 256 tegen 1024 (rapport): precisie@5 {u['preview']['256']:.3f} tegen "
        f"{u['preview']['1024']:.3f}; zakt {u['preview_zakt']:.3f} "
        + (
            "(> 0,02: X3 rekent uit 1024.webp)"
            if u["preview_zakt"] > PREVIEW_ZAKT
            else "(256 blijft)"
        )
    )
    v = u["verdeling"]
    verslag.kort(
        f"BZ4 MIN_TEKST (rapport): kans goed p5 {v['tekst_kans_goed'].get('p5')}, "
        f"niet goed p95 {v['tekst_kans_niet'].get('p95')}"
    )
    verslag.kort(
        f"BZ4 MIN_FOTO (rapport): cos zelfde categorie p5 {v['foto_cos_zelfde'].get('p5')}, "
        f"anders p95 {v['foto_cos_anders'].get('p95')}"
    )
    return u


def test_nl_precisie_at_5(uitkomst):
    assert uitkomst["nl"]["int8"] >= NL_PRECISIE, uitkomst["nl_per_vraag"]
    assert uitkomst["nl"]["min"] >= 1, [r for r in uitkomst["nl_per_vraag"] if r["int8"] == 0]


def test_combinaties_in_de_top_10(uitkomst):
    assert uitkomst["combinaties_top"] >= COMBI_GENOEG, uitkomst["combinaties"]


def test_int8_tegen_fp32(uitkomst):
    assert uitkomst["int8_zakt"] <= INT8_ZAKT, uitkomst["nl"]


def test_meer_zoals_deze(uitkomst):
    assert uitkomst["meer_zoals_deze"]["int8_256"] >= MEER_ZOALS, uitkomst["meer_zoals_deze"]


def test_zelftest_met_drie_kleuren(uitkomst):
    assert all(uitkomst["kleuren"]["bovenaan"]), uitkomst["kleuren"]


def test_het_rapport_heeft_engels_previews_en_verdelingen(uitkomst):
    """Geen poort: wel moet het rapport er zijn, met getallen."""
    assert len(uitkomst["en"]["per_vraag"]) == 6
    assert set(uitkomst["preview"]) == {"256", "1024"}
    assert all(uitkomst["verdeling"][k] for k in uitkomst["verdeling"])
