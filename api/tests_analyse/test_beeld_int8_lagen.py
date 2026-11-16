"""BZ4, het rapport van int8: welke toren, welke Linears en welke variant (fase 6, X3a; marker
beeld_echt; x3a.md, "CI op 3a9b07d").

Alleen in de job beeld-model, in een eigen stap die mag falen (continue-on-error) en geen poort
is: geen test eist een getal, alleen de laatste dat bz4r.json er is. Elke test vangt zijn eigen
fout en schrijft dan `BZ4 … (rapport): mislukt (<soort>)`. Omgeving: BEELD_BRON (de gewichten en
config.json, voor de varianten van de omzetter), BEELD_MODEL (de uitvoer met --fp32), BEELD_SET,
BEELD_SET_TSV en BEELD_RAPPORT (beeld_echt.py).

De regels, ook in bz4r.json:

1. `BZ4 cpu`: de CPU van de runner, en of hij VNNI heeft;
2. `BZ4 gewicht int8 <toren>`: ‖W − deq(q)‖ / ‖W‖ per Linear, met |q| ≤ 64 (Q_MAX) en met 127;
3. `BZ4 invoer <toren>`: een probe (int8=True met elke Linear in fp32 en meet; bouw.py) met de
   eerste 16 foto's van de set of de 24 vragen in het Nederlands. Per invoer van een Linear: max
   |x| tegen de rms, waar de max zit, en de fout ‖(Q(x) − x)·Wᵀ‖ / ‖x·Wᵀ‖ als Q naar uint8 gaat
   per tensor (over een batch van 16, zoals DynamicQuantizeMatMul nu; en per item alleen), per
   token, of per token na glad. De drie slechtste Linears, op de fout per tensor in de batch;
4. `BZ4 afwijking per laag <toren>`: de cosinus van de residu na elke laag, de int8-graaf tegen
   de probe van 3 (beide met de int8-embedding van de tekst): een sprong bij laag k wijst naar
   de Linears van laag k;
5. `BZ4 variant <naam>`: de omzetter met een optie van bouw.py, met de 120 foto's en 36 vragen
   zoals de poort (batches van 16, één draad). Eerst fp32 en "zoals nu", dan de varianten, de
   nuttigste eerst, samen hooguit TIJD_S.

De factoren van glad (α = 0,5, machten van 2) komen alleen uit de 64 synthetische foto's van BZ6
(test_beeld_echt_perf.py) en de vaste zinnen van IJK_ZINNEN, zonder een categorie; nooit uit de
set. In het verslag staan alleen getallen, de namen van Linears, de vaste vragen en sleutels;
nooit een vector, een factor of een pixel.
"""

from __future__ import annotations

import json
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

import numpy as np
import pytest

from app import render
from app.analyse.onnxw import Graph
from app.analyse.onnxw import Model as Onnx
from app.analyse.tokenizer import LENGTE, Tokenizer
from app.analyse.worker import omzetten
from app.analyse.worker.omzetten import bouw
from app.analyse.worker.omzetten.safetensors import Safetensors
from tests_analyse.beeld_echt import (
    BATCH,
    Verslag,
    kwantielen,
    omgeving,
    sessie,
    sha256_van,
    tool,
    voorbewerk,
    vragen,
)
from tests_analyse.test_beeld_kwaliteit import (
    COMBI_TOP,
    MAX_PIXELS,
    _cos,
    _meer_zoals,
    _plekken,
    _precisie,
)

pytestmark = pytest.mark.beeld_echt

TIJD_S = 8 * 60  # de varianten samen; de stap zelf heeft timeout-minutes 15
PROBE = 16  # foto's in de probe: de eerste batch van de poort
ALFA = 0.5  # glad: f = max |x|^α / max |w|^(1 − α)
GLAD_EXP = 20  # glad: 2^−20 ≤ f ≤ 2^20
SLECHTSTE = 3
RIJEN = 512  # rijen per Linear voor de gesimuleerde fout (met de rij van de max)
# Zinnen zonder een categorie van beeld_vragen.tsv, alleen om glad te ijken
IJK_ZINNEN = (
    "een foto",
    "een afbeelding",
    "wat staat hierop",
    "een kamer met een tafel",
    "een stoel bij het raam",
    "twee mensen praten",
    "een oude brief",
    "een lamp aan het plafond",
    "een blauwe deur",
    "een boek op een plank",
    "een klok aan de muur",
    "kinderen spelen buiten",
    "a photo",
    "a picture of something",
    "an old letter",
    "two people talking",
)
GLAD = "glad"  # in een variant: de factoren van glad voor die toren (pas bij het bouwen)
TORENS = ("beeld", "tekst")


# --- de Linears en hun gewicht -------------------------------------------------------------------


def linears(v: bouw.Vorm, toren: str) -> list[str]:
    """Elke Linear van een toren, zoals bouw.py ze noemt, in de volgorde van de graaf."""
    t = v.beeld if toren == "beeld" else v.tekst
    uit = []
    for i in range(t.lagen):
        p = f"{t.naam}.encoder.layers.{i}"
        uit += [f"{p}.self_attn.qkv", f"{p}.self_attn.out_proj", f"{p}.mlp.fc1", f"{p}.mlp.fc2"]
    if toren == "tekst":
        return uit + ["text_model.head"]
    kop = ("attention.q", "attention.kv", "attention.out_proj", "mlp.fc1", "mlp.fc2")
    return uit + [f"vision_model.head.{n}" for n in kop]


def gewicht(st: Safetensors, v: bouw.Vorm, label: str) -> np.ndarray:
    """Wᵀ [in, uit] van een Linear, met dezelfde stukken als bouw.py."""
    e = v.beeld.breedte
    inw, inb = (
        "vision_model.head.attention.in_proj_weight",
        "vision_model.head.attention.in_proj_bias",
    )
    if label.endswith(".self_attn.qkv"):
        p = label.removesuffix(".qkv")
        stukken = [bouw._linear(f"{p}.{n}_proj") for n in ("q", "k", "v")]
    elif label == "vision_model.head.attention.q":
        stukken = [bouw.Stuk(inw, inb, 0, e)]
    elif label == "vision_model.head.attention.kv":
        stukken = [bouw.Stuk(inw, inb, e, 3 * e)]
    else:
        stukken = [bouw._linear(label)]
    return bouw.Bouwer(st, "rapport", int8=False)._wt(stukken)


def kort_label(label: str) -> str:
    """`laag 5 fc2`, `laag 0 qkv`, `kop kv`, of `kop` (text_model.head)."""
    if ".encoder.layers." in label:
        i, rest = label.split(".encoder.layers.", 1)[1].split(".", 1)
        return f"laag {i} {rest.rsplit('.', 1)[-1]}"
    rest = label.split(".head", 1)[1].lstrip(".")
    return f"kop {rest.rsplit('.', 1)[-1]}" if rest else "kop"


def laag_van(label: str) -> int | None:
    if ".encoder.layers." not in label:
        return None
    return int(label.split(".encoder.layers.", 1)[1].split(".", 1)[0])


def gewicht_fout(wt: np.ndarray, q_max: int) -> float:
    """‖W − deq(q)‖ / ‖W‖ met de schaal per kolom van bouw.per_kolom."""
    q, s = bouw.per_kolom(wt, q_max)
    terug = q.astype(np.float32) * s[None, :]
    return float(np.linalg.norm(wt - terug) / max(float(np.linalg.norm(wt)), 1e-30))


# --- de kwantisering van de invoer, in numpy -----------------------------------------------------


def q_tensor(x: np.ndarray) -> np.ndarray:
    """uint8 met één schaal en nulpunt voor de hele tensor (DynamicQuantizeLinear, zoals
    DynamicQuantizeMatMul), terug naar float."""
    laag, hoog = min(float(x.min()), 0.0), max(float(x.max()), 0.0)
    s = (hoog - laag) / 255 or 1.0
    z = float(np.clip(np.rint(-laag / s), 0, 255))
    return ((np.clip(np.rint(x / s) + z, 0, 255) - z) * s).astype(np.float32)


def q_token(x: np.ndarray) -> np.ndarray:
    """Per rij symmetrisch, zoals de keten per_token van bouw.py, terug naar float."""
    a = np.maximum(np.abs(x).max(axis=-1, keepdims=True) / 127, 1e-30)
    return (np.clip(np.rint(x / a), -127, 127) * a).astype(np.float32)


def fout(terug: np.ndarray, x: np.ndarray, wt: np.ndarray, rijen: np.ndarray) -> float:
    """‖(Q(x) − x)·Wᵀ‖ / ‖x·Wᵀ‖ op de gekozen rijen."""
    n = x.shape[-1]
    xs, ts = x.reshape(-1, n)[rijen], terug.reshape(-1, n)[rijen]
    return float(np.linalg.norm((ts - xs) @ wt) / max(float(np.linalg.norm(xs @ wt)), 1e-30))


def factoren(xmax: np.ndarray, wmax: np.ndarray, alfa: float = ALFA) -> np.ndarray:
    """glad (SmoothQuant): f = max |x|^α / max |w|^(1 − α) per invoerkanaal, gedeeld door de
    mediaan en afgerond op een macht van 2 (dan zijn x / f en Wᵀ × f exact)."""
    xm = np.maximum(xmax.astype(np.float64), 1e-12)
    wm = np.maximum(wmax.astype(np.float64), 1e-12)
    f = xm**alfa / wm ** (1 - alfa)
    f = f / np.median(f)
    return np.exp2(np.clip(np.rint(np.log2(f)), -GLAD_EXP, GLAD_EXP)).astype(np.float32)


def cpu() -> dict:
    """De naam van de CPU en of hij VNNI heeft (avx512_vnni of avx_vnni), uit /proc/cpuinfo."""
    naam, vlaggen = "onbekend", set()
    try:
        regels = Path("/proc/cpuinfo").read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        regels = []
    for regel in regels:
        sleutel, _, waarde = regel.partition(":")
        if sleutel.strip() == "model name" and naam == "onbekend":
            naam = waarde.strip()
        elif sleutel.strip() == "flags" and not vlaggen:
            vlaggen = set(waarde.split())
    return {"model": naam, "vnni": bool(vlaggen & {"avx512_vnni", "avx_vnni"})}


def _r(x: float, n: int = 4) -> float:
    return round(float(x), n)


# --- het rapport ---------------------------------------------------------------------------------


class Rapport:
    """Alle regels van dit rapport. pixels zijn de foto's van de set na de voorbewerking, in de
    volgorde van de set; ijk_beelden en ijk_zinnen zijn alleen voor glad. werk is een lege map
    voor de probes en de varianten (elk bestand weg na gebruik)."""

    def __init__(
        self,
        verslag: Verslag,
        *,
        bron: Path,
        model: Path,
        pixels: list[np.ndarray],
        sleutels: list[str],
        cats: list[set],
        ijk_beelden: list[np.ndarray],
        ijk_zinnen: tuple[str, ...] | list[str] = IJK_ZINNEN,
        werk: Path,
        tijd_s: float = TIJD_S,
    ) -> None:
        self.verslag = verslag
        self.st = Safetensors(bron / omzetten.SAFETENSORS)
        config = json.loads((bron / omzetten.CONFIG).read_text(encoding="utf-8"))
        self.v = bouw.lees_config(config)
        self.model = model
        self.tok = Tokenizer.load(model / omzetten.TOKENIZER_BIN)
        self.pixels, self.sleutels, self.cats = pixels, sleutels, cats
        rijen = vragen()
        self.rijen = {
            s: [r for r in rijen if r["soort"] == soort]
            for s, soort in (("nl", "nl"), ("c", "combinatie"), ("en", "en"))
        }
        self.ijk_beelden, self.ijk_zinnen = ijk_beelden, list(ijk_zinnen)
        self.werk = werk
        self.werk.mkdir(parents=True, exist_ok=True)
        self.tijd_s = tijd_s
        self.cpu = cpu()
        self._probes: dict[str, tuple] = {}
        self._glad: dict[str, dict[str, np.ndarray]] = {}

    # hulp
    def _ids(self, teksten: list[str]) -> np.ndarray:
        return np.asarray([self.tok.encode(t) for t in teksten], np.int64)

    def _graaf(self, toren: str, **opties) -> Graph:
        maak = bouw.beeld_graaf if toren == "beeld" else bouw.tekst_graaf
        return maak(self.st, self.v, int8=True, **opties)

    @staticmethod
    def _draai(s, namen: list[str], invoer: str, data: np.ndarray) -> list[np.ndarray]:
        """In batches van BATCH, zoals de poort; elke uitvoer aan elkaar langs de batch."""
        delen = [s.run(namen, {invoer: data[i : i + BATCH]}) for i in range(0, len(data), BATCH)]
        return [np.concatenate([d[k] for d in delen]) for k in range(len(namen))]

    def _probe_data(self, toren: str) -> tuple[str, np.ndarray]:
        if toren == "beeld":
            return omzetten.INVOER_BEELD, np.stack(self.pixels[:PROBE])
        return omzetten.INVOER_TEKST, self._ids([r["vraag"] for r in self.rijen["nl"]])

    def probe(self, toren: str):
        """int8=True met elke Linear in fp32 en meet: de invoer van elke Linear en de residu na
        elke laag als uitvoer (de tekst houdt zijn int8-embedding)."""
        if toren not in self._probes:
            meet: list = []
            g = self._graaf(toren, fp32_als=lambda _label: True, meet=meet)
            pad = self.werk / f"probe-{toren}.onnx"
            Onnx(g).write(pad)
            self._probes[toren] = (sessie(pad), meet, pad)
        s, meet, _ = self._probes[toren]
        return s, meet

    def los(self) -> None:
        """De probes weg (geheugen en schijf); glad blijft."""
        for _s, _meet, pad in self._probes.values():
            pad.unlink(missing_ok=True)
        self._probes.clear()

    def glad(self, toren: str) -> dict[str, np.ndarray]:
        """De factoren van glad per Linear (niet de vraag van de MAP-kop: een constante), uit
        ijk_beelden of ijk_zinnen door de probe: max |x| per kanaal, en max |w| per rij van Wᵀ."""
        if toren in self._glad:
            return self._glad[toren]
        s, meet = self.probe(toren)
        invoer = [m for m in meet if m[0] == "invoer"]
        if toren == "beeld":
            naam, data = omzetten.INVOER_BEELD, np.stack(self.ijk_beelden)
        else:
            naam, data = omzetten.INVOER_TEKST, self._ids(self.ijk_zinnen)
        xmax: dict[str, np.ndarray] = {}
        for i in range(0, len(data), BATCH):
            uit = s.run([m[2] for m in invoer], {naam: data[i : i + BATCH]})
            for (_soort, label, _naam, n_in), x in zip(invoer, uit, strict=True):
                m = np.abs(x.reshape(-1, n_in)).max(axis=0)
                xmax[label] = m if label not in xmax else np.maximum(xmax[label], m)
        self._glad[toren] = {
            label: factoren(xm, np.abs(gewicht(self.st, self.v, label)).max(axis=1))
            for label, xm in xmax.items()
        }
        return self._glad[toren]

    # 1
    def regel_cpu(self) -> None:
        self.verslag.zet("cpu", self.cpu)
        vnni = "ja" if self.cpu["vnni"] else "nee"
        self.verslag.kort(f"BZ4 cpu (rapport): {self.cpu['model']}; vnni {vnni}")

    # 2
    def regel_gewicht(self) -> None:
        uit = {}
        for toren in TORENS:
            per = {}
            for label in linears(self.v, toren):
                wt = gewicht(self.st, self.v, label)
                per[label] = {"q64": _r(gewicht_fout(wt, bouw.Q_MAX), 5)}
                per[label]["q127"] = _r(gewicht_fout(wt, 127), 5)
            slecht = max(per, key=lambda k: per[k]["q64"])
            m64 = float(np.median([x["q64"] for x in per.values()]))
            m127 = float(np.median([x["q127"] for x in per.values()]))
            uit[toren] = {"per_linear": per, "mediaan_q64": _r(m64, 5), "mediaan_q127": _r(m127, 5)}
            self.verslag.kort(
                f"BZ4 gewicht int8 {toren} (rapport): fout per Linear met |q| ≤ {bouw.Q_MAX} "
                f"mediaan {m64:.4f}, slechtste {per[slecht]['q64']:.4f} ({kort_label(slecht)}); "
                f"met 127 mediaan {m127:.4f}"
            )
        self.verslag.zet("gewicht", uit)

    # 3 en 4
    def regel_lagen(self, toren: str) -> None:
        s, meet = self.probe(toren)
        glad = self.glad(toren)
        naam, data = self._probe_data(toren)
        invoer = [m for m in meet if m[0] == "invoer"]
        rest = [m for m in meet if m[0] == "rest"]
        uit_naam = omzetten.UIT_BEELD if toren == "beeld" else omzetten.UIT_TEKST
        namen = [m[2] for m in invoer] + [m[2] for m in rest] + [uit_naam]
        uit = self._draai(s, namen, naam, data)
        n = len(data)

        per = []
        for k, (_soort, label, _naam, n_in) in enumerate(invoer):
            x = uit[k].reshape(n, -1, n_in).astype(np.float32)
            uit[k] = None  # het geheugen terug
            per.append(self._invoer(toren, label, x, data, glad.get(label)))
        self.verslag.zet(f"invoer_{toren}", per)
        slecht = sorted(per, key=lambda r: -r["fout"]["tensor_batch"])[:SLECHTSTE]
        delen = []
        for r in slecht:
            plek = f"token {r['token']} {r['soort']}, " if r["token"] is not None else ""
            f = r["fout"]
            glad_fout = "-" if f["token_glad"] is None else f"{f['token_glad']:.3f}"
            delen.append(
                f"{kort_label(r['linear'])} max {r['max']:.3g} = {r['verhouding']:.0f}× rms "
                f"({plek}kanaal {r['kanaal']}), fout {f['tensor_batch']:.3f}/"
                f"{f['tensor_alleen']:.3f}/{f['token']:.3f}/{glad_fout}"
            )
        self.verslag.kort(
            f"BZ4 invoer {toren} (rapport; fout per tensor in de batch/per tensor alleen/per "
            f"token/per token na glad): " + "; ".join(delen)
        )

        # 4: de int8-graaf met meet, tegen de residu van de probe
        ref_rest = {m[1]: uit[len(invoer) + i] for i, m in enumerate(rest)}
        ref_uit = uit[-1]
        del uit
        meet8: list = []
        pad8 = self.werk / f"int8-{toren}.onnx"
        Onnx(self._graaf(toren, meet=meet8)).write(pad8)
        try:
            rest8 = [m for m in meet8 if m[0] == "rest"]
            uit8 = self._draai(sessie(pad8), [m[2] for m in rest8] + [uit_naam], naam, data)
        finally:
            pad8.unlink(missing_ok=True)
        cos = []
        for m, a in zip(rest8, uit8[:-1], strict=True):
            b = ref_rest[m[1]]
            if toren == "tekst":  # het laatste token: dat neemt de kop
                c = _cos(a[:, LENGTE - 1], b[:, LENGTE - 1])
            else:  # gemiddeld over de patches, per foto
                e = a.shape[-1]
                c = _cos(a.reshape(-1, e), b.reshape(-1, e)).reshape(n, -1).mean(axis=1)
            cos.append(_r(np.median(c), 5))
        eind = _r(np.median(_cos(uit8[-1], ref_uit)), 5)
        self.verslag.zet(f"afwijking_{toren}", {"na_laag": cos, "uitvoer": eind})
        plek = f"op token {LENGTE - 1}" if toren == "tekst" else "gemiddeld over de patches"
        self.verslag.kort(
            f"BZ4 afwijking per laag {toren} (rapport): cos int8/fp32 {plek} na laag "
            f"0..{len(cos) - 1}: {' '.join(f'{c:.5f}' for c in cos)}; uitvoer {eind:.5f}"
        )

    def _invoer(self, toren, label, x, data, f) -> dict:
        n, t, n_in = x.shape
        a = np.abs(x)
        item, token, kanaal = (int(i) for i in np.unravel_index(int(np.argmax(a)), a.shape))
        hoogste = float(a[item, token, kanaal])
        rms = float(np.sqrt(np.mean(np.square(x, dtype=np.float64))))
        del a
        rijen = np.unique(
            np.r_[np.linspace(0, n * t - 1, min(n * t, RIJEN)).astype(np.int64), item * t + token]
        )
        wt = gewicht(self.st, self.v, label)
        batch = np.concatenate([q_tensor(x[i : i + BATCH]) for i in range(0, n, BATCH)])
        alleen = np.concatenate([q_tensor(x[i : i + 1]) for i in range(n)])
        fouten = {
            "tensor_batch": _r(fout(batch, x, wt, rijen)),
            "tensor_alleen": _r(fout(alleen, x, wt, rijen)),
            "token": _r(fout(q_token(x), x, wt, rijen)),
            "token_glad": None if f is None else _r(fout(q_token(x / f) * f, x, wt, rijen)),
        }
        if t == 1:
            soort, token_uit = "kop", None
        elif toren == "beeld":
            soort, token_uit = "patch", token
        else:
            i = int(data[item, token])
            soort = "eos" if i == self.tok.eos else "pad" if i == self.tok.pad else "inhoud"
            token_uit = token
        return {
            "linear": label,
            "max": _r(hoogste),
            "rms": _r(rms, 6),
            "verhouding": _r(hoogste / max(rms, 1e-30), 1),
            "token": token_uit,
            "soort": soort,
            "kanaal": kanaal,
            "fout": fouten,
        }

    # 5
    def lijst(self) -> list[tuple[str, dict | None, dict | None, str | None]]:
        """(naam, opties beeld, opties tekst, reden om over te slaan); None: de toren van nu.

        Elke variant meet één plek: fc2, mlp en aandacht alleen in de lagen van de encoder, de
        koppen (de MAP-kop van het beeld met zijn aandacht en mlp, en de kop van de tekst) alleen
        in kop fp32 (de review, S3; test_beeld_int8_klein.py zet per variant de Linears vast)."""
        n = self.v.beeld.lagen
        k = min(4, n)

        def lagen(van: int, tot: int) -> Callable[[str], bool]:
            return lambda label: (i := laag_van(label)) is not None and van <= i <= tot

        def kop(label: str) -> bool:
            return label.startswith(("vision_model.head.", "text_model.head"))

        def fc2(label: str) -> bool:
            return laag_van(label) is not None and label.endswith(".mlp.fc2")

        def mlp(label: str) -> bool:
            return laag_van(label) is not None and ".mlp." in label

        def aandacht(label: str) -> bool:
            return laag_van(label) is not None and ".self_attn." in label

        def beide(**o) -> tuple[dict, dict]:
            return dict(o), dict(o)

        vnni = None if self.cpu["vnni"] else "geen VNNI"
        return [
            ("per token", *beide(per_token=True), None),
            ("fc2 fp32", *beide(fp32_als=fc2), None),
            ("kop fp32", *beide(fp32_als=kop), None),
            ("per token + kop fp32", *beide(per_token=True, fp32_als=kop), None),
            ("mlp fp32", *beide(fp32_als=mlp), None),
            ("aandacht fp32", *beide(fp32_als=aandacht), None),
            (f"laag 0-{k - 1} fp32", *beide(fp32_als=lagen(0, k - 1)), None),
            (f"laag {n - k}-{n - 1} fp32", *beide(fp32_als=lagen(n - k, n - 1)), None),
            ("per token + glad", *beide(per_token=True, glad=GLAD), None),
            ("Q_MAX 127", *beide(q_max=127), vnni),
            ("embedding fp32", None, {"embedding_fp32": True}, None),
        ]

    def _inbedden(self, beeld: Path, tekst: Path) -> dict:
        sb, stk = sessie(beeld), sessie(tekst)
        begin = time.perf_counter()
        e = self._draai(sb, [omzetten.UIT_BEELD], omzetten.INVOER_BEELD, np.stack(self.pixels))[0]
        ms_foto = (time.perf_counter() - begin) * 1000 / len(self.pixels)
        ids = {s: self._ids([r["vraag"] for r in rs]) for s, rs in self.rijen.items()}
        begin = time.perf_counter()
        t = {
            s: self._draai(stk, [omzetten.UIT_TEKST], omzetten.INVOER_TEKST, x)[0]
            for s, x in ids.items()
        }
        ms_vraag = (time.perf_counter() - begin) * 1000 / sum(len(x) for x in ids.values())
        return {
            "e": e.astype(np.float32),
            "t": {s: x.astype(np.float32) for s, x in t.items()},
            "beeld_mb": beeld.stat().st_size / 1e6,
            "tekst_mb": tekst.stat().st_size / 1e6,
            "ms_foto": ms_foto,
            "ms_vraag": ms_vraag,
        }

    def _cijfers(self, naam: str, m: dict, ref: dict) -> dict:
        rijen, cats = self.rijen, self.cats
        nl = _precisie(m["t"]["nl"], m["e"], rijen["nl"], cats)
        nl_ref = _precisie(ref["t"]["nl"], ref["e"], rijen["nl"], cats)
        plekken = _plekken(m["t"]["c"], m["e"], rijen["c"], self.sleutels)
        t_alle = np.concatenate([m["t"][s] for s in ("nl", "c", "en")])
        t_ref = np.concatenate([ref["t"][s] for s in ("nl", "c", "en")])
        c = {
            "variant": naam,
            "nl": _r(np.mean(nl) / 5),
            "zakt": _r((np.mean(nl_ref) - np.mean(nl)) / 5),
            "nl_slechtste": int(min(nl)),
            "en": _r(np.mean(_precisie(m["t"]["en"], m["e"], rijen["en"], cats)) / 5),
            "combinaties_top": sum(p <= COMBI_TOP for p in plekken),
            "combinaties": len(plekken),
            "meer_zoals_deze": _r(np.mean(_meer_zoals(m["e"], cats))),
            "cos_beeld": kwantielen(_cos(m["e"], ref["e"])),
            "cos_tekst": kwantielen(_cos(t_alle, t_ref)),
            "beeld_mb": _r(m["beeld_mb"], 1),
            "tekst_mb": _r(m["tekst_mb"], 1),
            "ms_per_foto": _r(m["ms_foto"], 1),
            "ms_per_vraag": _r(m["ms_vraag"], 1),
        }
        self.verslag.kort(
            f"BZ4 variant {naam} (rapport): nl {c['nl']:.3f} (zakt {c['zakt']:.3f}, slechtste "
            f"{c['nl_slechtste']}/5), engels {c['en']:.3f}, combinaties {c['combinaties_top']}/"
            f"{c['combinaties']}, meer zoals deze {c['meer_zoals_deze']:.3f}, cos beeld p5 "
            f"{c['cos_beeld']['p5']:.4f} tekst p5 {c['cos_tekst']['p5']:.4f}; beeld "
            f"{c['beeld_mb']:.0f} MB {c['ms_per_foto']:.1f} ms per foto, tekst "
            f"{c['tekst_mb']:.0f} MB {c['ms_per_vraag']:.1f} ms per vraag"
        )
        return c

    def regel_varianten(self) -> None:
        begin = time.monotonic()
        uit: list[dict] = []
        ref = self._inbedden(self.model / omzetten.BEELD_FP32, self.model / omzetten.TEKST_FP32)
        uit.append(self._cijfers("fp32", ref, ref))
        nu = self._inbedden(self.model / omzetten.BEELD, self.model / omzetten.TEKST)
        uit.append(self._cijfers("zoals nu", nu, ref))
        del nu
        self.verslag.zet("varianten", uit)
        for naam, beeld, tekst, reden in self.lijst():
            if reden is None and time.monotonic() - begin > self.tijd_s:
                reden = f"tijd (na {self.tijd_s / 60:.0f} min)"
            if reden is not None:
                uit.append({"variant": naam, "overgeslagen": reden})
                self.verslag.kort(f"BZ4 variant {naam} (rapport): overgeslagen: {reden}")
            else:
                try:
                    uit.append(self._variant(naam, beeld, tekst, ref))
                except Exception as e:  # een variant die faalt, stopt de rest niet
                    uit.append({"variant": naam, "mislukt": type(e).__name__})
                    self.verslag.kort(f"BZ4 variant {naam} (rapport): mislukt ({type(e).__name__})")
            self.verslag.zet("varianten", uit)

    def _variant(self, naam: str, beeld: dict | None, tekst: dict | None, ref: dict) -> dict:
        with tempfile.TemporaryDirectory(dir=self.werk) as tmp:
            paden = {}
            for toren, opties, nu in (
                ("beeld", beeld, omzetten.BEELD),
                ("tekst", tekst, omzetten.TEKST),
            ):
                if opties is None:
                    paden[toren] = self.model / nu
                    continue
                if opties.get("glad") == GLAD:
                    opties = {**opties, "glad": self.glad(toren)}
                paden[toren] = Path(tmp) / nu
                Onnx(self._graaf(toren, **opties)).write(paden[toren])
            return self._cijfers(naam, self._inbedden(paden["beeld"], paden["tekst"]), ref)


def probeer(verslag: Verslag, naam: str, werk: Callable[[], None]) -> None:
    """Alleen een rapport: een fout wordt een regel, nooit een andere uitslag."""
    try:
        werk()
    except Exception as e:
        verslag.kort(f"BZ4 {naam} (rapport): mislukt ({type(e).__name__})")


# --- de tests (job beeld-model) ------------------------------------------------------------------


@pytest.fixture(scope="module")
def rapport():
    from tests_analyse.test_beeld_echt_perf import _maak_fotos

    bron = omgeving("BEELD_BRON")
    model = omgeving("BEELD_MODEL")
    set_map = omgeving("BEELD_SET")
    tsv = omgeving("BEELD_SET_TSV")
    verslag = Verslag("bz4r")
    meta = json.loads((model / omzetten.META).read_text(encoding="utf-8"))
    vb = meta["beeld"]["voorbewerking"]
    fotos = tool().lees_set(tsv.read_text(encoding="utf-8"))
    verkeerd = [f.sleutel for f in fotos if sha256_van(set_map / f"{f.sleutel}.jpg") != f.sha256]
    if verkeerd:
        pytest.fail(f"de sha256 klopt niet voor {verkeerd[:5]}")
    with tempfile.TemporaryDirectory() as tmp:
        werk = Path(tmp)
        pixels = []
        for f in fotos:  # zoals de poort: app.render.image, dan 256.webp
            out = werk / "set" / f.sleutel
            out.mkdir(parents=True)
            render.image(set_map / f"{f.sleutel}.jpg", out, MAX_PIXELS)
            pixels.append(voorbewerk(out / "256.webp", vb))
        (werk / "ijk").mkdir()
        ijk = [voorbewerk(p, vb) for p in _maak_fotos(werk / "ijk")]
        yield Rapport(
            verslag,
            bron=bron,
            model=model,
            pixels=pixels,
            sleutels=[f.sleutel for f in fotos],
            cats=[set(f.categorieen) for f in fotos],
            ijk_beelden=ijk,
            werk=werk / "modellen",
        )


def test_cpu(rapport):
    probeer(rapport.verslag, "cpu", rapport.regel_cpu)


def test_gewicht_per_linear(rapport):
    probeer(rapport.verslag, "gewicht int8", rapport.regel_gewicht)


def test_invoer_en_afwijking_per_laag(rapport):
    try:
        for toren in TORENS:
            probeer(rapport.verslag, f"invoer {toren}", lambda t=toren: rapport.regel_lagen(t))
    finally:
        rapport.los()


def test_varianten(rapport):
    probeer(rapport.verslag, "varianten", rapport.regel_varianten)


def test_het_verslag_is_er(rapport):
    """De enige eis: bz4r.json (de regels staan ook in kort.txt)."""
    assert rapport.verslag.pad.is_file()
