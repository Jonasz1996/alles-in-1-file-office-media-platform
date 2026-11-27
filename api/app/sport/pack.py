"""De ruwe kolommen van een activiteit (track.Raw) compact in één kolom van de database.

Vorm: zlib over "SPT1" + per kolom één byte typecode van array ('d' of 'f') + de waarden
(little-endian). Een ontbrekende waarde is NaN. Volgorde: t, lat, lon, ele, hr, cad, power, seg.
Een rit van 10.000 punten is zo ±150 kB; geen numpy (de app gebruikt het nergens).
"""

from __future__ import annotations

import math
import struct
import sys
import zlib
from array import array

from app.sport.track import Raw

MAGIC = b"SPT1"
_COLS = (("t", "d"), ("lat", "d"), ("lon", "d"), ("ele", "f"), ("hr", "f"), ("cad", "f"),
         ("power", "f"), ("seg", "f"))  # fmt: skip
NAN = float("nan")


def _col(values, code: str) -> bytes:
    arr = array(code, (NAN if v is None else float(v) for v in values))
    if sys.byteorder != "little":
        arr.byteswap()
    return arr.tobytes()


def pack(raw: Raw) -> bytes:
    n = len(raw)
    out = [MAGIC, struct.pack("<I", n)]
    for name, code in _COLS:
        out.append(code.encode())
        out.append(_col(getattr(raw, name), code))
    return zlib.compress(b"".join(out), 6)


def unpack(blob: bytes) -> Raw:
    data = zlib.decompress(blob)
    if data[:4] != MAGIC:
        raise ValueError("geen spoor van sport")
    (n,) = struct.unpack_from("<I", data, 4)
    pos = 8
    cols: dict[str, list] = {}
    for name, _ in _COLS:
        code = chr(data[pos])
        pos += 1
        arr = array(code)
        size = arr.itemsize * n
        arr.frombytes(data[pos : pos + size])
        if sys.byteorder != "little":
            arr.byteswap()
        pos += size
        cols[name] = [None if math.isnan(v) else v for v in arr]
    return Raw(
        t=cols["t"],
        lat=[float(v) for v in cols["lat"]],
        lon=[float(v) for v in cols["lon"]],
        ele=cols["ele"],
        hr=cols["hr"],
        cad=cols["cad"],
        power=cols["power"],
        seg=[1 if v else 0 for v in cols["seg"]],
    )
