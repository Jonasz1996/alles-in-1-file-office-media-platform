"""BZ4: de kwaliteitspoort van Keuze 83 met het echte model (fase 6, X3a; marker beeld_echt).

Alleen in de job beeld-model (of later op de LXC met `app.cli beeld-kwaliteit`, X3b). Omgeving:
BEELD_MODEL, BEELD_SET, BEELD_SET_TSV, BEELD_RAPPORT (beeld_echt.py).

Zoals de app: van elke foto maakt app.render.image (de previews van de app, exif uit) 256.webp
en 1024.webp; beeld rekent uit 256.webp (Keuze 77), met de voorbewerking van meta.json, in int8
op één draad. Rangschikken gaat op de cosinus (sigmoid(scale · cos + bias) heeft dezelfde
volgorde). Een foto telt als goed voor een categorie als die in zijn kolom categorieen staat; de
60 andere foto's hebben er geen.

De poorten (Keuze 83, vastgelegd vóór de eerste echte run; lager alleen met een nieuwe keuze in
het ontwerp, nooit hier). Twee ervan zijn sinds Keuze 85 (Jonas, 2026-11-16: "Toch uitbrengen")
ondergrenzen, omdat ook fp32 ze niet haalt:

- 24 vragen in het Nederlands met één begrip: gemiddelde precisie@5 ≥ 0,6, en geen vraag met 0
  goede foto's in de top 5, behalve de twee bekend zwakke (BEKEND_ZWAK; Keuze 85);
- 6 combinaties: de bedoelde foto in de top 10 bij ≥ 5 van de 6;
- int8 tegen fp32 (dezelfde set, dezelfde vragen): precisie@5 zakt ≤ 0,05;
- "Meer zoals deze": gemiddeld ≥ 0,40 van de top 4 uit dezelfde categorie (MEER_ONDER; Keuze 85).
  Het doel van Keuze 83, 0,7 (MEER_ZOALS), staat ernaast in de regel;
- de zelftest van de container (Keuze 83): een rood, een blauw en een groen vlak van Pillow, en
  bij "rood", "blauw" en "groen" de goede bovenaan.

Alleen een rapport: de 6 vragen in het Engels; 256.webp tegen 1024.webp (zakt precisie@5 meer
dan 0,02, dan rekent X3 uit 1024.webp, Keuze 77); de verdeling van de scores voor MIN_TEKST en
MIN_FOTO (goed tegen niet goed). "Meer zoals deze": elke foto met een categorie vraagt, de foto
zelf telt niet mee, en een buur is goed als hij een categorie met de vraag deelt.

Ook alleen een rapport, sinds CI op 3a9b07d (x3a.md): waar int8 het verlies maakt (`_rapport_int8`;
in bz4.json onder int8_rapport). Het rekent met wat de poort al heeft, binnen de try van
`_rapport_veilig`: een fout geeft een regel `mislukt`, nooit een andere uitslag van een poort.
Sinds CI op 011bf2d daarbij, elk in een eigen try: "Meer zoals deze" per categorie (met waar de
foute buren vandaan komen), de zwakke vragen (hooguit 1 goede foto in de top 5, in int8 of fp32;
met hun top 5) en hoe vaak de naaste buur uit dezelfde categorie komt.
"""

from __future__ import annotations

import re
import tempfile
from collections import Counter
from pathlib import Path

import numpy as np
import pytest

from app import render
from tests_analyse.beeld_echt import (
    BATCH,
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
MEER_ZOALS = 0.7  # het doel van Keuze 83; alleen in de regel (Keuze 85)
MEER_TOP = 4
# Keuze 85: twee poorten van Keuze 83 zijn ondergrenzen. "Meer zoals deze" mat int8 0,450 tot 0,475
# (fp32 0,442 tot 0,463) en schoof 0,021 tussen twee runs met dezelfde pins; 0,40 ligt daar 0,05
# onder, en boven de kapotte varianten tot nu (0,258 tot 0,338). Alleen deze twee
# vragen mogen 0 van 5 halen: ze haalden in elke run hooguit 1, ook in fp32. Ze moeten in
# beeld_vragen.tsv staan, en blijven in het rapport van de zwakke vragen.
MEER_ONDER = 0.40
BEKEND_ZWAK = frozenset({"bergen", "boot"})
PREVIEW_ZAKT = 0.02  # rapport: daarboven rekent X3 uit 1024.webp (Keuze 77)
KLEUREN = {"rood": (255, 0, 0), "blauw": (0, 0, 255), "groen": (0, 255, 0)}
MAX_PIXELS = 100_000_000  # settings.preview_max_pixels van de app
# rapport (sinds CI op 011bf2d): een zwakke vraag heeft hooguit ZWAK goede foto's in de top 5
# (int8 of fp32); van de foute buren de HERKOMST vaakste; geen lijst langer dan MAX_LIJST
ZWAK = 1
HERKOMST = 3
MAX_LIJST = 24


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


def _plekken(t: np.ndarray, e: np.ndarray, combi: list[dict], sleutels: list[str]) -> list[int]:
    """Per combinatie de plek (vanaf 1) van de bedoelde foto."""
    uit = []
    for v, rij in zip(t, combi, strict=True):
        doel = sleutels.index(rij["doel"])
        uit.append(int(np.nonzero(_volgorde(e @ v) == doel)[0][0]) + 1)
    return uit


def _cos(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """De cosinus per rij."""
    n = np.linalg.norm(a, axis=1) * np.linalg.norm(b, axis=1)
    return np.sum(a * b, axis=1) / np.maximum(n, 1e-30)


def _buren_uit_de_batch(e: np.ndarray, cats: list[set]) -> tuple[float, float]:
    """Van de top MEER_TOP van "Meer zoals deze": het deel uit dezelfde batch van BATCH (zoals
    Model.beelden ze rekent), en wat toeval zou geven (de andere foto's van die batch gedeeld door
    alle andere foto's), over dezelfde vragende foto's."""
    n, deel, toeval = len(cats), [], []
    for i, c in enumerate(cats):
        if not c:
            continue
        s = e @ e[i]
        s[i] = -np.inf
        top = _volgorde(s)[:MEER_TOP]
        deel.append(sum(int(j) // BATCH == i // BATCH for j in top) / MEER_TOP)
        batch = min(n, (i // BATCH + 1) * BATCH) - (i // BATCH) * BATCH
        toeval.append((batch - 1) / (n - 1))
    return float(np.mean(deel)), float(np.mean(toeval))


def _herkomst(cats_j: set, sleutel: str) -> list[str]:
    """Waar een foute buur vandaan komt: zijn categorieën (een combinatie telt bij allebei), of bij
    een foto zonder categorie (een afleider) zijn sleutel zonder nummer: anders-vogel-3 wordt
    anders-vogel."""
    return sorted(cats_j) if cats_j else [re.sub(r"-\d+$", "", sleutel)]


def _namen(nl: list[dict], cats: list[set]) -> list[str]:
    """De categorieën in de volgorde van de vragen in het Nederlands, daarna de rest."""
    return list(dict.fromkeys([x["categorieen"] for x in nl] + sorted(set().union(*cats))))


def _meer_per_categorie(
    e: np.ndarray, cats: list[set], sleutels: list[str], namen: list[str]
) -> dict[str, dict]:
    """Per categorie "Meer zoals deze": over de foto's met die categorie het deel van de top
    MEER_TOP dat een categorie met de vragende foto deelt (zoals `_meer_zoals`; een combinatie
    vraagt bij allebei), en van de foute buren de HERKOMST vaakste herkomsten met hun aantal (bij
    gelijk aantal op naam)."""
    tops = {}
    for i, c in enumerate(cats):
        if c:
            s = e @ e[i]
            s[i] = -np.inf
            tops[i] = _volgorde(s)[:MEER_TOP]
    uit = {}
    for naam in namen:
        vragers = [i for i in tops if naam in cats[i]]
        if not vragers:
            continue
        deel, fout = [], Counter()
        for i in vragers:
            deel.append(sum(bool(cats[i] & cats[j]) for j in tops[i]) / MEER_TOP)
            for j in tops[i]:
                if not cats[i] & cats[j]:
                    fout.update(_herkomst(cats[j], sleutels[j]))
        vaakst = sorted(fout.items(), key=lambda kv: (-kv[1], kv[0]))[:HERKOMST]
        uit[naam] = {
            "n": len(vragers),
            "deel": round(float(np.mean(deel)), 4),
            "fout": dict(vaakst),
        }
    return uit


def _top1(e: np.ndarray, cats: list[set]) -> tuple[int, int]:
    """Van de foto's met een categorie: hoeveel hun naaste buur (zichzelf niet) een categorie met
    ze delen, en hoeveel het er zijn."""
    goed = n = 0
    for i, c in enumerate(cats):
        if not c:
            continue
        s = e @ e[i]
        s[i] = -np.inf
        goed += bool(c & cats[int(_volgorde(s)[0])])
        n += 1
    return goed, n


def _top5(t: np.ndarray, e: np.ndarray, sleutels: list[str]) -> list[list[str]]:
    """Per vraag de sleutels van de top 5."""
    return [[sleutels[int(i)] for i in _volgorde(e @ v)[:5]] for v in t]


def _zwakke_vragen(
    rijen: list[dict],
    n8: list[int],
    n32: list[int],
    top8: list[list[str]],
    top32: list[list[str]],
) -> list[dict]:
    """De vragen met hooguit ZWAK goede foto's in de top 5, in int8 of in fp32, met hun top 5 in
    allebei; de zwakste eerst (het minimum, dan de som; verder in de volgorde van de vragen)."""
    zwak = [
        {
            "vraag": x["vraag"],
            "soort": x["soort"],
            "int8": a,
            "fp32": b,
            "top5_int8": t8,
            "top5_fp32": t32,
        }
        for x, a, b, t8, t32 in zip(rijen, n8, n32, top8, top32, strict=True)
        if min(a, b) <= ZWAK
    ]
    return sorted(zwak, key=lambda z: (min(z["int8"], z["fp32"]), z["int8"] + z["fp32"]))


def _fout_tekst(fout: dict[str, int]) -> str:
    return ", ".join(f"{k} {v}" for k, v in fout.items()) or "geen"


def _rapport_veilig(verslag: Verslag, **kw) -> None:
    """Het int8-rapport in het verslag; een fout wordt één regel `mislukt`, nooit een andere uitslag
    van een poort (de review, S4; test_beeld_int8_klein.py faalt het met opzet)."""
    try:
        verslag.zet("int8_rapport", _rapport_int8(verslag, **kw))
    except Exception as e:  # alleen een rapport: nooit een andere uitslag van een poort
        verslag.kort(f"BZ4 int8-rapport (rapport): mislukt ({type(e).__name__})")


def _rapport_int8(
    verslag: Verslag,
    *,
    int8: Model,
    fp32: Model,
    pixels: list[np.ndarray],
    e8: np.ndarray,
    e32: np.ndarray,
    t8: dict[str, np.ndarray],
    t32: np.ndarray,
    rijen: dict[str, list[dict]],
    cats: list[set],
    sleutels: list[str],
) -> dict:
    """Alleen een rapport (x3a.md, "CI op 3a9b07d"): welke toren het verlies van int8 draagt, wat
    fp32 haalt bij de andere getallen, de cosinus per vraag en per foto, een vraag of foto alleen
    tegen in een batch, en de precisie per vraag. Regels met `(rapport)`; geen vector, alleen de
    vaste vragen en de sleutels van de set."""
    nl, combi, en = rijen["nl"], rijen["c"], rijen["en"]

    def gem(lijst) -> float:
        return round(float(np.mean(lijst)), 4)

    def p5(lijst) -> float:
        return gem(np.asarray(lijst) / 5)

    def r4(x) -> float:
        return round(float(x), 4)

    nl8, nl32 = _precisie(t8["nl"], e8, nl, cats), _precisie(t32, e32, nl, cats)
    tekst32 = _precisie(t32, e8, nl, cats)  # tekst fp32 + beeld int8
    beeld32 = _precisie(t8["nl"], e32, nl, cats)  # tekst int8 + beeld fp32
    r: dict = {
        "torens": {
            "tekst_int8_beeld_fp32": p5(beeld32),
            "tekst_fp32_beeld_int8": p5(tekst32),
            "int8": p5(nl8),
            "fp32": p5(nl32),
        }
    }
    t = r["torens"]
    verslag.kort(
        f"BZ4 torens (rapport): precisie@5 nl tekst int8 + beeld fp32 "
        f"{t['tekst_int8_beeld_fp32']:.3f}, tekst fp32 + beeld int8 "
        f"{t['tekst_fp32_beeld_int8']:.3f} (beide int8 {t['int8']:.3f}, beide fp32 {t['fp32']:.3f})"
    )

    # fp32 bij de getallen die de poort alleen in int8 rekent
    t32_en = fp32.teksten([x["vraag"] for x in en])
    t32_c = fp32.teksten([x["vraag"] for x in combi])
    en8, en32 = _precisie(t8["en"], e8, en, cats), _precisie(t32_en, e32, en, cats)
    plekken32 = _plekken(t32_c, e32, combi, sleutels)
    cos_foto = e32 @ e32.T
    zelfde = np.array([[bool(a & b) for b in cats] for a in cats])
    boven = ~np.eye(len(cats), dtype=bool)
    cos_tekst = e32 @ t32.T
    goed = np.array([[x["categorieen"] in c for x in nl] for c in cats])
    r["fp32"] = {
        "en": p5(en32),
        "en_int8": p5(en8),
        "meer_zoals_deze": gem(_meer_zoals(e32, cats)),
        "meer_zoals_deze_int8": gem(_meer_zoals(e8, cats)),
        "foto_cos_zelfde": kwantielen(cos_foto[zelfde & boven]),
        "foto_cos_anders": kwantielen(cos_foto[~zelfde & boven]),
        "tekst_kans_goed": kwantielen(fp32.kans(cos_tekst[goed])),
        "tekst_kans_niet": kwantielen(fp32.kans(cos_tekst[~goed])),
        "combinaties_top": sum(p <= COMBI_TOP for p in plekken32),
        "combinaties_plekken": plekken32,
    }
    f = r["fp32"]
    verslag.kort(
        f"BZ4 fp32 (rapport): engels {f['en']:.3f} (int8 {f['en_int8']:.3f}), meer zoals deze "
        f"{f['meer_zoals_deze']:.3f} (int8 {f['meer_zoals_deze_int8']:.3f}), MIN_FOTO zelfde p5 "
        f"{f['foto_cos_zelfde'].get('p5')} anders p95 {f['foto_cos_anders'].get('p95')}, "
        f"MIN_TEKST kans goed p5 {f['tekst_kans_goed'].get('p5')} niet goed p95 "
        f"{f['tekst_kans_niet'].get('p95')}, combinaties {f['combinaties_top']}/{len(combi)} "
        f"plekken {plekken32}"
    )

    # de cosinus van int8 met fp32, per vraag en per foto
    ct, cb = _cos(t8["nl"], t32), _cos(e8, e32)
    r["cos"] = {
        "tekst_nl": kwantielen(ct),
        "tekst_laagste": nl[int(np.argmin(ct))]["vraag"],
        "beeld": kwantielen(cb),
        "beeld_laagste": sleutels[int(np.argmin(cb))],
    }
    c = r["cos"]
    verslag.kort(
        f"BZ4 cos int8 tegen fp32 (rapport): tekst nl p5 {c['tekst_nl']['p5']} mediaan "
        f"{c['tekst_nl']['p50']} laagste {c['tekst_nl']['p0']} ({c['tekst_laagste']}); beeld p5 "
        f"{c['beeld']['p5']} mediaan {c['beeld']['p50']} laagste {c['beeld']['p0']} "
        f"({c['beeld_laagste']})"
    )

    # een per keer, zoals een vraag in productie (GET /search) en zonder buren in de batch
    t_alleen = np.concatenate([int8.teksten([x["vraag"]]) for x in nl])
    e_alleen = np.concatenate([int8.beelden([p]) for p in pixels])
    r["een_per_keer"] = {
        "tekst_nl": p5(_precisie(t_alleen, e8, nl, cats)),
        "tekst_nl_batch": p5(nl8),
        "tekst_cos_batch": kwantielen(_cos(t_alleen, t8["nl"])),
        "beeld_cos_batch": kwantielen(_cos(e_alleen, e8)),
        "meer_zoals_deze": gem(_meer_zoals(e_alleen, cats)),
    }
    a = r["een_per_keer"]
    verslag.kort(
        f"BZ4 één per keer (rapport): tekst int8 precisie@5 {a['tekst_nl']:.3f} (in batches van "
        f"{BATCH}: {a['tekst_nl_batch']:.3f}), cos tegen batch p5 {a['tekst_cos_batch']['p5']}; "
        f"beeld int8 cos tegen batch p5 {a['beeld_cos_batch']['p5']}, meer zoals deze "
        f"{a['meer_zoals_deze']:.3f}"
    )

    b8, toeval = _buren_uit_de_batch(e8, cats)
    b32, _ = _buren_uit_de_batch(e32, cats)
    r["buren_uit_de_batch"] = {"int8": r4(b8), "fp32": r4(b32), "toeval": r4(toeval)}
    verslag.kort(
        f"BZ4 buren uit dezelfde batch (rapport): int8 {b8:.3f}, fp32 {b32:.3f} (toeval "
        f"{toeval:.3f})"
    )

    # per vraag: int8, fp32, tekst fp32 + beeld int8, tekst int8 + beeld fp32
    r["nl_per_vraag"] = [
        {"vraag": x["vraag"], "int8": a8, "fp32": a32, "tekst_fp32": tf, "beeld_fp32": bf}
        for x, a8, a32, tf, bf in zip(nl, nl8, nl32, tekst32, beeld32, strict=True)
    ]
    per = [
        f"{x['vraag']} {a8}/{a32}/{tf}/{bf}"
        for x, a8, a32, tf, bf in zip(nl, nl8, nl32, tekst32, beeld32, strict=True)
    ]
    delen = [per[i : i + 8] for i in range(0, len(per), 8)]
    for i, deel in enumerate(delen, 1):
        verslag.kort(
            f"BZ4 nl per vraag (rapport, int8/fp32/tekst-fp32/beeld-fp32) {i}/{len(delen)}: "
            + ", ".join(deel)
        )
    r["en_per_vraag"] = [
        {"vraag": x["vraag"], "int8": a8, "fp32": a32}
        for x, a8, a32 in zip(en, en8, en32, strict=True)
    ]
    verslag.kort(
        "BZ4 en per vraag (rapport, int8/fp32): "
        + ", ".join(f"{x['vraag']} {a8}/{a32}" for x, a8, a32 in zip(en, en8, en32, strict=True))
    )

    # Sinds CI op 011bf2d (x3a.md): waar "Meer zoals deze" en de zwakste vragen het verlies maken.
    # Elk deel in zijn eigen try: een fout is één regel `mislukt` (en in bz4.json {"mislukt": ...}),
    # de andere delen gaan door, en de regels komen pas als het hele deel gerekend is.
    def deel(naam: str, sleutel: str, reken) -> None:
        try:
            waarde, regels = reken()
        except Exception as e:  # alleen een rapport: nooit een andere uitslag van een poort
            verslag.kort(f"BZ4 {naam} (rapport): mislukt ({type(e).__name__})")
            r[sleutel] = {"mislukt": type(e).__name__}
            return
        r[sleutel] = waarde
        for regel in regels:
            verslag.kort(regel)

    def per_categorie() -> tuple[dict, list[str]]:
        namen = _namen(nl, cats)
        m8 = _meer_per_categorie(e8, cats, sleutels, namen)
        m32 = _meer_per_categorie(e32, cats, sleutels, namen)
        w = {
            naam: {
                "n": m8[naam]["n"],
                "int8": m8[naam]["deel"],
                "fp32": m32[naam]["deel"],
                "fout_int8": m8[naam]["fout"],
                "fout_fp32": m32[naam]["fout"],
            }
            for naam in m8
        }
        per = [
            f"{naam} {x['int8']:.3f}/{x['fp32']:.3f} (fout int8: {_fout_tekst(x['fout_int8'])}; "
            f"fp32: {_fout_tekst(x['fout_fp32'])})"
            for naam, x in w.items()
        ]
        delen = [per[i : i + 3] for i in range(0, len(per), 3)]
        regels = [
            f"BZ4 meer zoals deze per categorie (rapport, int8/fp32) {i}/{len(delen)}: "
            + " | ".join(d)
            for i, d in enumerate(delen, 1)
        ]
        return w, regels or ["BZ4 meer zoals deze per categorie (rapport): geen foto met categorie"]

    def zwakke() -> tuple[dict, list[str]]:
        top8 = _top5(np.concatenate([t8["nl"], t8["en"]]), e8, sleutels)
        top32 = _top5(np.concatenate([t32, t32_en]), e32, sleutels)
        zwak = _zwakke_vragen(nl + en, nl8 + en8, nl32 + en32, top8, top32)
        regels = [
            f"BZ4 zwakke vragen (rapport) {i}/{len(zwak)}: {z['vraag']} ({z['soort']}; int8 "
            f"{z['int8']}/5, fp32 {z['fp32']}/5): int8 {' '.join(z['top5_int8'])}; fp32 "
            f"{' '.join(z['top5_fp32'])}"
            for i, z in enumerate(zwak[:MAX_LIJST], 1)
        ]
        if len(zwak) > MAX_LIJST:
            regels.append(
                f"BZ4 zwakke vragen (rapport): nog {len(zwak) - MAX_LIJST}, niet uitgeschreven"
            )
        if not zwak:
            regels = [
                f"BZ4 zwakke vragen (rapport): geen (elke vraag nl en en ≥ {ZWAK + 1} van 5 in "
                "int8 en in fp32)"
            ]
        return {"aantal": len(zwak), "vragen": zwak[:MAX_LIJST]}, regels

    def top1() -> tuple[dict, list[str]]:
        g8, n = _top1(e8, cats)
        g32, _ = _top1(e32, cats)
        w = {
            "n": n,
            "int8_goed": g8,
            "fp32_goed": g32,
            "int8": r4(g8 / max(n, 1)),
            "fp32": r4(g32 / max(n, 1)),
        }
        return w, [
            f"BZ4 meer zoals deze top 1 (rapport): int8 {w['int8']:.3f} ({g8}/{n}), fp32 "
            f"{w['fp32']:.3f} ({g32}/{n})"
        ]

    deel("meer zoals deze per categorie", "meer_zoals_per_categorie", per_categorie)
    deel("zwakke vragen", "zwakke_vragen", zwakke)
    deel("meer zoals deze top 1", "meer_zoals_top1", top1)
    return r


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
    del groot  # klein houdt het rapport van int8 nog (één per keer)

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

    rest8 = [a for r, a in zip(nl, nl8, strict=True) if r["vraag"] not in BEKEND_ZWAK]
    u = {
        "fotos": len(fotos),
        "set": tsv.name,
        "nl": {
            "int8": gem(np.array(nl8) / 5),
            "fp32": gem(np.array(nl32) / 5),
            "min": min(nl8),
            # de poort van Keuze 85: de slechtste vraag buiten BEKEND_ZWAK
            "min_rest": min(rest8),
        },
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
    bekend = ", ".join(
        f"{r['vraag']} {a}/5" for r, a in zip(nl, nl8, strict=True) if r["vraag"] in BEKEND_ZWAK
    )
    # zoals test_nl_precisie_at_5: een uitzondering voor een vraag die er niet is, is oud
    ontbreekt = sorted(BEKEND_ZWAK - {r["vraag"] for r in nl})
    if ontbreekt:
        bekend += f"; niet in de vragen: {', '.join(ontbreekt)}"
    verslag.kort(
        f"BZ4 nl: precisie@5 {u['nl']['int8']:.3f} (poort ≥ {NL_PRECISIE}), slechtste vraag "
        f"{u['nl']['min_rest']}/5 buiten de bekend zwakke (poort ≥ 1; Keuze 85: {bekend}) — "
        f"{oordeel(u['nl']['int8'] >= NL_PRECISIE and u['nl']['min_rest'] >= 1 and not ontbreekt)}"
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
        f"(poort ≥ {MEER_ONDER:.2f}, Keuze 85; doel {MEER_ZOALS}) — "
        f"{oordeel(u['meer_zoals_deze']['int8_256'] >= MEER_ONDER)}"
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
    _rapport_veilig(
        verslag,
        int8=int8,
        fp32=fp32,
        pixels=klein,
        e8=e8,
        e32=e32,
        t8=t8,
        t32=t32,
        rijen={"nl": nl, "c": combi, "en": en},
        cats=cats,
        sleutels=sleutels,
    )
    del klein
    return u


def test_nl_precisie_at_5(uitkomst):
    # een uitzondering voor een vraag die er niet (meer) is, is oud (Keuze 85)
    assert BEKEND_ZWAK <= {r["vraag"] for r in uitkomst["nl_per_vraag"]}, sorted(BEKEND_ZWAK)
    assert uitkomst["nl"]["int8"] >= NL_PRECISIE, uitkomst["nl_per_vraag"]
    assert uitkomst["nl"]["min_rest"] >= 1, [
        r for r in uitkomst["nl_per_vraag"] if r["int8"] == 0 and r["vraag"] not in BEKEND_ZWAK
    ]


def test_combinaties_in_de_top_10(uitkomst):
    assert uitkomst["combinaties_top"] >= COMBI_GENOEG, uitkomst["combinaties"]


def test_int8_tegen_fp32(uitkomst):
    assert uitkomst["int8_zakt"] <= INT8_ZAKT, uitkomst["nl"]


def test_meer_zoals_deze(uitkomst):
    assert uitkomst["meer_zoals_deze"]["int8_256"] >= MEER_ONDER, uitkomst["meer_zoals_deze"]


def test_zelftest_met_drie_kleuren(uitkomst):
    assert all(uitkomst["kleuren"]["bovenaan"]), uitkomst["kleuren"]


def test_het_rapport_heeft_engels_previews_en_verdelingen(uitkomst):
    """Geen poort: wel moet het rapport er zijn, met getallen."""
    assert len(uitkomst["en"]["per_vraag"]) == 6
    assert set(uitkomst["preview"]) == {"256", "1024"}
    assert all(uitkomst["verdeling"][k] for k in uitkomst["verdeling"])
