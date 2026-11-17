"""Adressen voor en van de document server (Keuze 12 en 13).

internal_url: hoe DS de app bereikt (de interne luisteraar op poort 8081).

rewrite: DS geeft in een callback, een conversie en getForgotten een adres op de host van de
browser (met het prefix /office). De app volgt nooit die host: hij neemt alleen pad en query,
controleert dat het pad bij onze key past, en vraagt het dan aan PLATFORM_OFFICE_URL. Dat mag,
want de handtekening van de link hangt alleen af van pad, expires en het linkgeheim [gemeten].
"""

import re
from typing import Literal
from urllib.parse import urlsplit

from app.config import Settings

# Eén padsegment na de map van de key, zoals output.docx of changes.zip
_SEGMENT = r"[A-Za-z0-9_=-][A-Za-z0-9._=-]*"
_TAIL = rf"(?:/{_SEGMENT})+"
_PREFIX = r"(?:/office)?"


def internal_url(settings: Settings, path: str) -> str:
    """settings.office_app_url + path (path begint met /)."""
    if not path.startswith("/"):
        raise ValueError("path moet met / beginnen")
    return settings.office_app_url.rstrip("/") + path


def _pattern(key: str, kind: str) -> re.Pattern:
    k = re.escape(key)
    if kind == "data":
        body = rf"/cache/files/data/{k}(?:_\d+)?"
    elif kind == "conv":
        body = rf"/cache/files/data/conv_{k}(?:_\d+)?"
    elif kind == "forgotten":
        body = rf"/cache/files/forgotten/{k}"
    else:
        raise ValueError(f"onbekende soort {kind}")
    return re.compile(rf"{_PREFIX}({body}{_TAIL})")


def rewrite(
    settings: Settings, url: str, *, key: str, kind: Literal["data", "conv", "forgotten"]
) -> str:
    """Het adres bij PLATFORM_OFFICE_URL voor een adres uit een antwoord van DS. Alleen pad en
    query; het pad moet bij onze key passen. Anders ValueError."""
    if not url or any(c in url for c in ("\\", "\x00", "\r", "\n", " ", "\t")):
        raise ValueError("ongeldig adres")
    parts = urlsplit(url)
    if parts.scheme not in ("", "http", "https"):
        raise ValueError("ongeldig schema")
    path = parts.path
    # Geen procentcodes in het pad: zo kan niets na decoderen een / of .. worden
    if "%" in path:
        raise ValueError("procentcode in het pad")
    if any(segment in (".", "..") for segment in path.split("/")):
        raise ValueError("relatief pad")
    match = _pattern(key, kind).fullmatch(path)
    if match is None:
        raise ValueError("pad hoort niet bij deze key")
    out = settings.office_url.rstrip("/") + match.group(1)
    if parts.query:
        out += "?" + parts.query
    return out
