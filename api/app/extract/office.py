"""docx, xlsx, pptx, odt, ods en odp (bouwontwerp fase 6, Keuze 17): de stand "alle tekst" van
de kantoorlezer (app/office/reader.all_text), met zijn grenzen op het zip-bestand en zijn
weigering van DOCTYPE en entiteiten. Alleen in het kind (app.render text).
"""

from __future__ import annotations

from typing import BinaryIO

from app.extract.collect import Collector
from app.extract.normalize import fold_meta


def read(f: BinaryIO, out: Collector) -> None:
    from app.office import reader

    def on_meta(meta: dict[str, str]) -> None:
        out.meta = fold_meta(meta.get("title"), meta.get("author"), meta.get("subject"))

    f.seek(0)
    reader.all_text(f, out.add, out.check, on_meta)
