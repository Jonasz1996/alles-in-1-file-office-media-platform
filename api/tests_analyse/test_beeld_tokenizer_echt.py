"""BZ3 en BZ7: de tokenizer met het tokenizer.json van SigLIP 2 zelf (fase 6, X3a, Keuze 74).

Marker beeld_echt: alleen in de job beeld-model (BEELD_BRON, BEELD_MODEL, BEELD_RAPPORT; zie
beeld_echt.py). Nooit met het bestand van Gemma (K1-14, K2-14).

- **De lijst** (api/tests/analyse/sets/beeld_tokens.tsv, met de hand afgeleid, nooit met onze
  tokenizer gemaakt). De verwachte ids komen uit de woordenlijst van het echte bestand, gelezen
  als gewone JSON, zonder tokenizer.py: elk stuk opgezocht, dan <eos>, dan <pad> tot 64. Per soort
  eerst de voorwaarde op het echte bestand (beeld_tokens.tsv zegt welke); klopt die niet, dan
  klopt de lijst niet, en de test zegt dat. Poort: 0 verschillen, met Tokenizer.from_json en met
  het tokenizer.bin dat de omzetter schreef.
- **Een eigen BPE** van ±30 regels, los van tokenizer.py geschreven (de langzame vorm: steeds het
  paar met de laagste rang, bij gelijke rang het meest linkse; byte fallback per teken vóór het
  samenvoegen, zoals `tokenizers`), tegen onze tokenizer op 2.000 vaste zinnen. Geen vervanging
  van `tokenizers` (Vraag 12b: nee), wel een tweede blik op hetzelfde bestand.
- **tokenizer.bin** van de omzetter is precies Tokenizer.from_json(echt).to_bin().
- **BZ7**: tokenizer.bin laden in een vers proces: ≤ 0,5 s (de mediaan van drie) en ≤ 100 MB
  erbij (maxrss na het laden min ervoor, het hoogste van drie).
"""

from __future__ import annotations

import json
import random
import statistics
import subprocess
import sys
from pathlib import Path

import pytest

from app.analyse.tokenizer import Tokenizer
from app.analyse.worker import omzetten
from tests_analyse.beeld_echt import API, SETS, Verslag, omgeving

pytestmark = pytest.mark.beeld_echt

LENGTE = 64
SPATIE = "▁"
LAAD_S = 0.5  # BZ7
LAAD_BYTES = 100_000_000  # BZ7: ≤ 100 MB
ZINNEN = 2000


def _byte(s: str) -> bool:
    return len(s) == 6 and s.startswith("<0x") and s.endswith(">")


def _normaal(tekst: str, prepend: bool) -> str:
    t = "".join(c.lower() for c in tekst).replace(" ", SPATIE)
    return SPATIE + t if prepend and t else t


class Echt:
    """Het echte tokenizer.json als gewone JSON (geen tokenizer.py)."""

    def __init__(self, pad: Path) -> None:
        self.pad = pad
        doc = json.loads(pad.read_bytes())
        model = doc["model"]
        self.vocab: dict[str, int] = model["vocab"]
        paren = [tuple(m.split(" ")) if isinstance(m, str) else tuple(m) for m in model["merges"]]
        self.rang = {p: i for i, p in enumerate(paren)}  # bij een dubbel paar telt de laatste
        self.merge_stukken = {s for p in paren for s in p}
        self.toegevoegd = [t["content"] for t in doc.get("added_tokens") or []]
        self.pad_id = self.vocab["<pad>"]
        self.eos_id = self.vocab["<eos>"]
        self.prepend = self._normalizer(doc.get("normalizer"))
        pre = doc.get("pre_tokenizer")
        # Geen, of een Split op " " die na Replace(" ", "▁") niets meer doet
        split = isinstance(pre, dict) and pre.get("type") == "Split"
        assert pre is None or (split and pre.get("pattern") == {"String": " "}), pre
        self.byte_fallback = bool(model.get("byte_fallback"))
        self.langste = max(len(k) for k in self.vocab)

    @staticmethod
    def _normalizer(n) -> bool:
        """Of er een ▁ vóór de tekst komt. Kleine letters doet de test altijd (Keuze 74)."""
        if n is None:
            return False
        delen = n["normalizers"] if n.get("type") == "Sequence" else [n]
        prepend = False
        for d in delen:
            if d["type"] == "Prepend":
                assert d["prepend"] == SPATIE, d
                prepend = True
            elif d["type"] == "Replace":
                assert d["pattern"] == {"String": " "} and d["content"] == SPATIE, d
            else:
                assert d["type"] == "Lowercase", f"de eigen BPE kent normalizer {d!r} niet"
        return prepend

    def bpe(self, tekst: str) -> list[int]:
        """De eigen BPE: traag en eenvoudig."""
        symbolen: list[str] = []
        for c in _normaal(tekst, self.prepend):
            if c in self.vocab:
                symbolen.append(c)
                continue
            assert self.byte_fallback, "zonder byte fallback kent de eigen BPE dit teken niet"
            bs = [f"<0x{b:02X}>" for b in c.encode("utf-8")]
            assert all(b in self.vocab for b in bs), f"U+{ord(c):04X}: een byte ontbreekt"
            symbolen.extend(bs)
        while True:
            beste = None
            for i in range(len(symbolen) - 1):
                r = self.rang.get((symbolen[i], symbolen[i + 1]))
                if r is not None and (beste is None or r < beste[0]):
                    beste = (r, i)
            if beste is None:
                return [self.vocab[s] for s in symbolen]
            i = beste[1]
            symbolen[i : i + 2] = [symbolen[i] + symbolen[i + 1]]


@pytest.fixture(scope="module")
def echt() -> Echt:
    return Echt(omgeving("BEELD_BRON") / omzetten.TOKENIZER_JSON)


@pytest.fixture(scope="module")
def bin_pad() -> Path:
    return omgeving("BEELD_MODEL") / omzetten.TOKENIZER_BIN


@pytest.fixture(scope="module")
def ons(echt) -> Tokenizer:
    return Tokenizer.from_json(echt.pad)


def _lijst() -> list[tuple[str, str, list[str]]]:
    regels = (SETS / "beeld_tokens.tsv").read_text(encoding="utf-8").splitlines()
    rijen = [r.split("\t") for r in regels if r and not r.startswith("#")]
    assert rijen[0] == ["soort", "tekst", "stukken", "waarom"]
    return [(s, json.loads(t), json.loads(p)) for s, t, p, _ in rijen[1:]]


def _over_een_grens(echt: Echt, stukken: list[str]) -> list[str]:
    """Stukken uit de woordenlijst die over een grens tussen twee verwachte stukken lopen: dan
    kan BPE daar samenvoegen, en geldt de aanname van de soort woorden niet."""
    tekst = "".join(stukken)
    grenzen, plek = [], 0
    for s in stukken[:-1]:
        plek += len(s)
        grenzen.append(plek)
    fout = set()
    for g in grenzen:
        for begin in range(max(0, g - echt.langste + 1), g):
            for eind in range(g + 1, min(len(tekst), begin + echt.langste) + 1):
                if tekst[begin:eind] in echt.vocab:
                    fout.add(tekst[begin:eind])
    return sorted(fout)


def _voorwaarde(echt: Echt, soort: str, tekst: str, stukken: list[str]) -> list[str]:
    """Wat er aan het echte bestand niet klopt voor deze rij van de lijst (leeg: goed)."""
    fout = [f"{s!r} staat niet in de woordenlijst" for s in stukken if s not in echt.vocab]
    if any(_byte(s) for s in stukken):
        # Byte fallback: het teken staat in geen enkel stuk (dus ook in geen merge), en met de
        # bytes zelf voegt BPE niets samen
        for c in {c for c in _normaal(tekst, echt.prepend) if not c.isascii() and c != SPATIE}:
            if any(c in k for k in echt.vocab):
                fout.append(f"U+{ord(c):04X} staat in een stuk van de woordenlijst")
        for b in sorted({s for s in stukken if _byte(s)} & echt.merge_stukken):
            fout.append(f"{b} staat in een merge")
    elif soort in ("woorden", "afkappen"):
        # 100 keer achter elkaar: ook de grens tussen twee keer telt
        reeks = (
            stukken * (2 + echt.langste // len("".join(stukken)))
            if soort == "afkappen"
            else stukken
        )
        fout += [f"{k!r} loopt over een grens" for k in _over_een_grens(echt, reeks)]
    if echt.prepend:
        fout.append("het bestand zet ▁ vóór de tekst; de lijst gaat daar niet van uit")
    return fout


def _verwacht(echt: Echt, soort: str, stukken: list[str]) -> list[int]:
    ids = [echt.vocab[s] for s in stukken]
    if soort == "afkappen":
        return (ids * 100)[: LENGTE - 1] + [echt.eos_id]
    return ids + [echt.eos_id] + [echt.pad_id] * (LENGTE - 1 - len(ids))


def test_bz3_de_lijst_met_het_echte_bestand(echt, ons, bin_pad):
    """0 verschillen op de lijst in de repository, met from_json en met tokenizer.bin."""
    verslag = Verslag("bz3")
    uit_bin = Tokenizer.load(bin_pad)
    lijst = _lijst()
    voorwaarden, verschillen = [], []
    for nr, (soort, tekst, stukken) in enumerate(lijst, 1):
        fout = _voorwaarde(echt, soort, tekst, stukken)
        if fout:
            voorwaarden.append(f"rij {nr} ({soort}): {'; '.join(fout)}")
            continue
        verwacht = _verwacht(echt, soort, stukken)
        invoer = tekst * 100 if soort == "afkappen" else tekst
        for naam, tok in (("from_json", ons), ("tokenizer.bin", uit_bin)):
            kreeg = tok.encode(invoer)
            if kreeg != verwacht:
                eerste = next(
                    i for i, (a, b) in enumerate(zip(kreeg, verwacht, strict=True)) if a != b
                )
                verschillen.append(f"rij {nr} ({soort}), {naam}: eerste verschil op plek {eerste}")
    verslag.zet(
        "lijst", {"rijen": len(lijst), "voorwaarden": voorwaarden, "verschillen": verschillen}
    )
    verslag.zet("ids", {"pad": echt.pad_id, "eos": echt.eos_id, "stukken": len(echt.vocab)})
    goed = not voorwaarden and not verschillen
    verslag.kort(
        f"BZ3 lijst: {len(lijst)} rijen, {len(voorwaarden)} voorwaarden niet gehaald, "
        f"{len(verschillen)} verschillen — {'goed' if goed else 'FOUT'}"
    )
    assert not voorwaarden, "de lijst past niet bij het echte bestand:\n" + "\n".join(voorwaarden)
    assert not verschillen, "\n".join(verschillen)


def _zinnen(n: int = ZINNEN, seed: int = 74) -> list[str]:
    """Vaste zinnen: Nederlandse en Engelse woorden, hoofdletters, cijfers, leestekens, accenten,
    een paar andere schriften, emoji en tekens voor privégebruik (byte fallback)."""
    rnd = random.Random(seed)
    woorden = (
        "hond kat strand sneeuw auto fiets berg bos stad eten boot zonsondergang huis boom water "
        "zee lucht wolk regen zon maan bloem vogel paard trein vliegtuig brug kerk boek klok stoel "
        "gitaar insect op het de een in bij van met naar onder boven rood blauw groen geel wit "
        "zwart oud nieuw groot klein mooi winter zomer herfst lente feest verjaardag vakantie "
        "dog cat beach snow car bicycle mountain forest city food boat sunset the a of on at "
        "fotografie straatlantaarn kinderwagen ijsbeer"
    ).split()
    extra = list("éëïöüàçñßøåæ") + ["σ", "ς", "Σ", "ж", "Ж", "ש", "日本", "😀", "", "\U000f0000"]
    lees = list(".,!?-'():;&%/")
    zinnen = []
    for _ in range(n):
        delen = []
        for _ in range(rnd.randint(1, 12)):
            w = rnd.choice(woorden)
            keuze = rnd.random()
            if keuze < 0.15:
                w = w.upper()
            elif keuze < 0.3:
                w = w.capitalize()
            elif keuze < 0.4:
                w = str(rnd.randint(0, 99999))
            elif keuze < 0.5:
                w = w[: rnd.randint(1, len(w))] + rnd.choice(extra) + w[rnd.randint(0, len(w)) :]
            if rnd.random() < 0.15:
                w += rnd.choice(lees)
            delen.append(w)
        zinnen.append(" ".join(delen))
    return zinnen


def test_bz3_een_eigen_bpe_op_2000_zinnen(echt, ons):
    zinnen = _zinnen()
    # De eigen BPE kent geen toegevoegde tokens: de zinnen mogen er geen bevatten
    ruw = [t for t in echt.toegevoegd if any(t in z for z in zinnen)]
    norm = [t for t in echt.toegevoegd if any(t in _normaal(z, echt.prepend) for z in zinnen)]
    assert not ruw and not norm, f"een vaste zin bevat een toegevoegd token: {(ruw + norm)[:5]}"
    referentie = [echt.bpe(z) for z in zinnen]
    verschil = [
        i for i, (z, r) in enumerate(zip(zinnen, referentie, strict=True)) if ons.stukken(z) != r
    ]
    afgekapt = [
        i
        for i, (z, r) in enumerate(zip(zinnen, referentie, strict=True))
        if ons.encode(z) != (r[: LENGTE - 1] + [echt.eos_id] + [echt.pad_id] * LENGTE)[:LENGTE]
    ]
    tokens = sum(len(r) for r in referentie)
    verslag = Verslag("bz3-bpe")
    verslag.zet("zinnen", {"aantal": len(zinnen), "tokens": tokens, "verschil": verschil[:20]})
    verslag.kort(
        f"BZ3 eigen BPE: {len(zinnen)} zinnen ({tokens} tokens), {len(verschil)} verschillen, "
        f"{len(afgekapt)} na afkappen — {'goed' if not verschil and not afgekapt else 'FOUT'}"
    )
    assert not verschil, f"zin {verschil[:10]} geeft andere ids dan de eigen BPE"
    assert not afgekapt, f"zin {afgekapt[:10]}: encode wijkt af"


def test_bz3_tokenizer_bin_is_from_json(echt, ons, bin_pad):
    gelijk = ons.to_bin() == bin_pad.read_bytes()
    Verslag("bz3-bin").kort(
        f"BZ3 tokenizer.bin: gelijk aan from_json(tokenizer.json).to_bin() — "
        f"{'goed' if gelijk else 'FOUT'}"
    )
    assert gelijk


_LAAD = (
    "import json,resource,sys,time\n"
    "sys.path.insert(0, sys.argv[1])\n"
    "from app.analyse.tokenizer import Tokenizer\n"
    "voor = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss\n"
    "t = time.perf_counter()\n"
    "tok = Tokenizer.load(sys.argv[2])\n"
    "s = time.perf_counter() - t\n"
    "na = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss\n"
    "tok.encode('hond op het strand')\n"
    "print(json.dumps({'s': s, 'erbij': (na - voor) * 1024, 'maxrss': na * 1024}))\n"
)


def test_bz7_tokenizer_bin_laden(bin_pad):
    metingen = []
    for _ in range(3):
        uit = subprocess.run(
            [sys.executable, "-I", "-c", _LAAD, str(API), str(bin_pad)],
            capture_output=True,
            text=True,
            timeout=60,
            check=True,
        )
        metingen.append(json.loads(uit.stdout))
    s = statistics.median(m["s"] for m in metingen)
    erbij = max(m["erbij"] for m in metingen)
    verslag = Verslag("bz7")
    verslag.zet("laden", {"metingen": metingen, "bytes": bin_pad.stat().st_size})
    goed = s <= LAAD_S and erbij <= LAAD_BYTES
    verslag.kort(
        f"BZ7 tokenizer.bin ({bin_pad.stat().st_size / 1e6:.1f} MB): laden {s:.3f} s (mediaan van "
        f"3; poort ≤ {LAAD_S} s), {erbij / 1e6:.1f} MB erbij (poort ≤ 100 MB) — "
        f"{'goed' if goed else 'FOUT'}"
    )
    assert s <= LAAD_S, metingen
    assert erbij <= LAAD_BYTES, metingen
