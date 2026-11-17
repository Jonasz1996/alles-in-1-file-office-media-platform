"""Audio en video (bouwontwerp fase 6, Keuze 17): titel, artiest en album als meta, met mutagen
(in het kind, op de fd). Geen tekst in body: zoeken vindt het nummer op zijn titel.
"""

from __future__ import annotations

from typing import BinaryIO

from app.extract.collect import Collector
from app.extract.normalize import fold_meta

FIELDS = ("title", "artist", "album")


def read(f: BinaryIO, out: Collector) -> None:
    import mutagen

    f.seek(0)
    try:
        found = mutagen.File(f, easy=True)
    except (mutagen.MutagenError, ValueError, KeyError, IndexError, EOFError, OSError):
        found = None
    if found is None or not found.tags:
        return
    values = []
    for key in FIELDS:
        try:
            value = found.tags.get(key)
        except (ValueError, KeyError):
            value = None
        if isinstance(value, list):
            value = " ".join(str(v) for v in value[:4])
        values.append(value)
    out.meta = fold_meta(*values)
