"""PDF (bouwontwerp fase 6, Keuze 17): de tekstlaag via pypdfium2, hooguit 2.000 pagina's.

Alleen in het kind (app.render text): het app-proces roept pypdfium2 op het pad van de tekst
nooit zelf aan (test_extract_no_pdfium_in_app.py). Een pagina met minder dan SCAN_CHARS tekens
tekst (zonder witruimte) komt in scan_pages: die krijgt OCR (X1, Keuze 23). Wat er wel op staat
(een paginanummer), telt gewoon mee. Na 2.000 pagina's: truncated, code pages_limit. Titel,
auteur en onderwerp uit de metadata van de PDF.
"""

from __future__ import annotations

from typing import BinaryIO

from app.extract.collect import Collector
from app.extract.normalize import fold_meta

MAX_PAGES = 2000
SCAN_CHARS = 20  # minder tekens: een scan (Keuze 23)


class Unreadable(Exception):
    """Geen PDF die PDFium kan openen (kapot, of met een wachtwoord)."""


def read(f: BinaryIO, out: Collector) -> None:
    import pypdfium2 as pdfium

    f.seek(0)
    try:
        document = pdfium.PdfDocument(f)
    except pdfium.PdfiumError as e:
        raise Unreadable(type(e).__name__) from None
    try:
        try:
            info = document.get_metadata_dict(skip_empty=True)
        except Exception:  # noqa: BLE001 - kapotte metadata: dan zonder
            info = {}
        out.meta = fold_meta(info.get("Title"), info.get("Author"), info.get("Subject"))
        count = len(document)
        if count > MAX_PAGES:
            out.code = "pages_limit"
        for i in range(min(count, MAX_PAGES)):
            out.check()
            page = document[i]
            try:
                textpage = page.get_textpage()
                try:
                    text = textpage.get_text_range()
                finally:
                    textpage.close()
            finally:
                page.close()
            if len("".join(text.split())) < SCAN_CHARS:
                out.scan_pages.append(i + 1)
            if text.strip():
                out.add(text)
                out.add("\n")
        if count > MAX_PAGES:
            out.truncate()
    finally:
        document.close()
