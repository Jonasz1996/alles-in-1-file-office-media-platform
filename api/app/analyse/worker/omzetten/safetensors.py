"""model.safetensors lezen (fase 6, X3a, Keuze 73), zonder het pakket safetensors.

Het formaat: 8 bytes (u64, little-endian) met de lengte n van de kop, dan n bytes JSON, dan de
gegevens. De kop geeft per tensor `dtype`, `shape` en `data_offsets` [begin, eind), gerekend vanaf
het begin van de gegevens; `__metadata__` is een optioneel object van tekst naar tekst.

De kop wordt met de standaardbibliotheek gelezen en streng nagekeken, vóór er één gewicht gelezen
wordt: n ≤ 100 MB en binnen het bestand; geldige JSON zonder dubbele sleutels; bekende dtypes; een
lengte die past bij de vorm; de stukken op volgorde, zonder gat of overlap, en samen precies tot
het eind van het bestand. Een kop die liegt, geeft OmzetFout, geen crash en geen leesactie buiten
het bestand.

Een tensor komt via numpy.memmap en wordt meteen gekopieerd naar float32 (F32, F16 of BF16) of
int64 (I64, alleen voor position_ids); de map gaat daarna dicht. Zo staat nooit het hele bestand
(1,5 GB) in het geheugen, en met `rijen` ook geen hele embedding (Keuze 73).
"""

from __future__ import annotations

import json
import math
import struct
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from app.analyse.worker.omzetten import OmzetFout

MAX_KOP = 100_000_000
MAX_TENSORS = 100_000
MAX_DIMS = 8
MAX_NAAM = 512

# dtype van safetensors: (numpy-type op schijf, bytes per element)
DTYPES = {"F32": ("<f4", 4), "F16": ("<f2", 2), "BF16": ("<u2", 2), "I64": ("<i8", 8)}
FLOATS = ("F32", "F16", "BF16")


@dataclass(frozen=True)
class Info:
    naam: str
    dtype: str
    vorm: tuple[int, ...]
    begin: int  # vanaf het begin van het bestand
    lengte: int


def _fout(waarom: str) -> OmzetFout:
    return OmzetFout(f"model.safetensors: {waarom}")


def _geen_dubbele(paren: list[tuple[str, object]]) -> dict[str, object]:
    uit: dict[str, object] = {}
    for k, v in paren:
        if k in uit:
            raise ValueError("dubbele sleutel")
        uit[k] = v
    return uit


def _geheel(x: object) -> bool:
    return type(x) is int


class Safetensors:
    """De kop van één bestand, nagekeken; lees() haalt een tensor (of een deel van zijn rijen)."""

    def __init__(self, pad: str | Path) -> None:
        self.pad = Path(pad)
        try:
            with self.pad.open("rb") as f:
                grootte = self.pad.stat().st_size
                kop8 = f.read(8)
                if len(kop8) != 8:
                    raise _fout("korter dan 8 bytes")
                (n,) = struct.unpack("<Q", kop8)
                if not 2 <= n <= MAX_KOP:
                    raise _fout(f"de kop zegt {n} bytes; verwacht 2 tot {MAX_KOP}")
                if 8 + n > grootte:
                    raise _fout(f"de kop zegt {n} bytes, het bestand is {grootte} bytes")
                kop = f.read(n)
        except OSError as e:
            raise _fout(f"kan niet gelezen worden ({e.strerror or type(e).__name__})") from None
        if len(kop) != n:
            raise _fout("het bestand werd korter tijdens het lezen")
        try:
            doc = json.loads(kop.decode("utf-8"), object_pairs_hook=_geen_dubbele)
        except (UnicodeDecodeError, ValueError, RecursionError):
            raise _fout("de kop is geen geldige JSON (of heeft een sleutel twee keer)") from None
        if not isinstance(doc, dict):
            raise _fout("de kop is geen object")
        meta = doc.pop("__metadata__", None)
        if meta is not None and not (
            isinstance(meta, dict) and all(isinstance(v, str) for v in meta.values())
        ):
            raise _fout("__metadata__ is geen object van tekst naar tekst")
        if len(doc) > MAX_TENSORS:
            raise _fout(f"{len(doc)} tensors; hooguit {MAX_TENSORS}")
        self.begin_gegevens = 8 + n
        lengte_gegevens = grootte - self.begin_gegevens
        infos: list[tuple[int, int, Info]] = []
        for naam, t in doc.items():
            infos.append(self._tensor(naam, t))
        infos.sort(key=lambda x: (x[0], x[1]))
        pos = 0
        for a, b, info in infos:
            if a != pos:
                soort = "overlapt" if a < pos else "laat een gat"
                raise _fout(f"tensor {info.naam!r} {soort} (offset {a}, verwacht {pos})")
            pos = b
        if pos != lengte_gegevens:
            raise _fout(
                f"de tensors eindigen op {pos} bytes, de gegevens zijn {lengte_gegevens} bytes"
            )
        self.metadata = dict(meta or {})
        self._info = {info.naam: info for _, _, info in infos}

    def _tensor(self, naam: str, t: object) -> tuple[int, int, Info]:
        if not naam or len(naam) > MAX_NAAM or not naam.isprintable():
            raise _fout("een tensor zonder geldige naam")
        if not isinstance(t, dict) or set(t) != {"dtype", "shape", "data_offsets"}:
            raise _fout(f"tensor {naam!r}: verwacht precies dtype, shape en data_offsets")
        dtype, vorm, offsets = t["dtype"], t["shape"], t["data_offsets"]
        if dtype not in DTYPES:
            raise _fout(f"tensor {naam!r}: dtype {dtype!r} kan niet (wel {', '.join(DTYPES)})")
        if (
            not isinstance(vorm, list)
            or len(vorm) > MAX_DIMS
            or not all(_geheel(d) and d >= 0 for d in vorm)
        ):
            raise _fout(f"tensor {naam!r}: shape is geen lijst van gehele getallen ≥ 0")
        if (
            not isinstance(offsets, list)
            or len(offsets) != 2
            or not all(_geheel(o) for o in offsets)
            or not 0 <= offsets[0] <= offsets[1]
        ):
            raise _fout(f"tensor {naam!r}: data_offsets is geen paar 0 ≤ begin ≤ eind")
        a, b = offsets
        nodig = math.prod(vorm) * DTYPES[dtype][1]
        if b - a != nodig:
            raise _fout(f"tensor {naam!r}: {b - a} bytes; de vorm {vorm} als {dtype} is {nodig}")
        info = Info(naam, dtype, tuple(vorm), self.begin_gegevens + a, b - a)
        return a, b, info

    def namen(self) -> set[str]:
        return set(self._info)

    def info(self, naam: str) -> Info:
        try:
            return self._info[naam]
        except KeyError:
            raise _fout(f"tensor {naam!r} ontbreekt") from None

    def lees(self, naam: str, rijen: tuple[int, int] | None = None) -> np.ndarray:
        """De tensor als float32 (of int64 voor I64), een eigen kopie. rijen = (van, tot) leest
        alleen die rijen van de eerste as (voor de embedding van 256.000 rijen)."""
        info = self.info(naam)
        code, grootte = DTYPES[info.dtype]
        vorm, begin = info.vorm, info.begin
        if rijen is not None:
            van, tot = rijen
            if not vorm or not 0 <= van <= tot <= vorm[0]:
                raise _fout(f"tensor {naam!r}: rijen {van}..{tot} bestaan niet")
            begin += van * math.prod(vorm[1:]) * grootte
            vorm = (tot - van, *vorm[1:])
        doel = np.int64 if info.dtype == "I64" else np.float32
        if math.prod(vorm) == 0:
            return np.zeros(vorm, dtype=doel)
        try:
            kaart = np.memmap(self.pad, dtype=np.dtype(code), mode="r", offset=begin, shape=vorm)
        except (OSError, ValueError) as e:
            raise _fout(f"tensor {naam!r} kan niet gelezen worden ({type(e).__name__})") from None
        try:
            if info.dtype == "BF16":
                return (kaart.astype(np.uint32) << 16).view(np.float32)
            return np.array(kaart, dtype=doel)
        finally:
            mm = getattr(kaart, "_mmap", None)
            del kaart
            if mm is not None:
                try:
                    mm.close()
                except BufferError:
                    pass  # nog een view: de map gaat dicht met het laatste object
