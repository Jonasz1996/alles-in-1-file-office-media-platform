"""Eén blob lezen (bouwontwerp fase 6, Keuze 17 en 21). Alleen in het kind: app.render text.

extract() kiest de lezer, houdt de grenzen bij (tekens, de klok van 30 s CPU, 512 MB) en geeft
Extracted met de opgeschoonde tekst. Een fout van één blob stopt de rest niet: elke fout van de
lezer wordt state failed. Alleen een crash van het hele proces (een segfault in PDFium,
RLIMIT_CPU, RLIMIT_DATA) neemt de batch mee; dat lost de app op door te halveren.

- vol: done, truncated;
- de klok op: met tekst done, truncated, code too_slow; zonder tekst failed, too_slow;
- een zip-bom of te veel onderdelen: failed, zip_limit; DOCTYPE of entiteit: failed,
  xml_refused; meer dan 2.000 pagina's: done, truncated, pages_limit;
- kapot of onleesbaar: failed zonder code (de API kent alleen de codes van §3.4).
"""

from __future__ import annotations

import os
from typing import BinaryIO

from app.extract import MAX_BYTES
from app.extract.collect import Clock, Collector, Full, TooSlow
from app.extract.normalize import clean, cut
from app.extract.result import Extracted

BLOB_CPU_S = 30.0


def _reader(kind: str):
    if kind == "pdf":
        from app.extract import pdf

        return pdf.read
    if kind == "office":
        from app.extract import office

        return office.read
    if kind == "plain":
        from app.extract import plain

        return plain.read
    if kind == "html":
        from app.extract import html

        return html.read
    if kind == "rtf":
        from app.extract import rtf

        return rtf.read
    if kind == "media":
        from app.extract import media

        return media.read
    raise ValueError(f"onbekende soort {kind!r}")


def _failure_code(error: BaseException) -> str | None:
    code = getattr(error, "code", None)
    return code if code in ("zip_limit", "xml_refused") else None


def extract(
    sha: str,
    f: BinaryIO,
    kind: str,
    *,
    max_chars: int,
    cpu_seconds: float = BLOB_CPU_S,
) -> tuple[Extracted, str]:
    size = os.fstat(f.fileno()).st_size
    if size > MAX_BYTES:
        return Extracted(sha, "skip", 0, False, [], "", None), ""
    out = Collector(max_chars, Clock(cpu_seconds))
    code: str | None = None
    try:
        _reader(kind)(f, out)
    except Full:
        pass
    except TooSlow:
        code = "too_slow"
    except MemoryError:
        return Extracted(sha, "failed", 0, False, [], out.meta, None), ""
    except Exception as e:  # noqa: BLE001 - een fout van één blob stopt de rest niet
        return Extracted(sha, "failed", 0, False, [], out.meta, _failure_code(e)), ""
    text, was_cut = cut(clean(out.text()), max_chars)
    truncated = out.full or was_cut or code == "too_slow"
    code = code or out.code
    if code == "too_slow" and not text:
        return Extracted(sha, "failed", 0, False, out.scan_pages, out.meta, code), ""
    state = "done" if text or out.meta else "none"
    return Extracted(sha, state, len(text), truncated, out.scan_pages, out.meta, code), text


def fields(found: Extracted) -> dict:
    """De velden voor frame.write (zonder sha en chars: de app telt zelf)."""
    return {
        "state": found.state,
        "truncated": found.truncated,
        "scan_pages": found.scan_pages,
        "meta": found.meta,
        "code": found.code,
    }
