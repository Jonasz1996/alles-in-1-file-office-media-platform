"""De uitkomsten van het kind op de pipe naar de app (bouwontwerp fase 6, Keuze 17).

Per blob, in de volgorde van de vraag, twee stukken met elk vier bytes lengte vooraf (big
endian): eerst JSON {"i", "state", "truncated", "scan_pages", "meta", "code"}, dan de tekst in
UTF-8 (de app telt de tekens zelf). De sha gaat niet mee: de app weet welke blob op plaats i
stond. De worker leest de pipe niet; hij gaat van het kind rechtstreeks naar de app.

De app vertrouwt het kind niet (het las een kwaadaardig bestand): read() controleert elke
grens en elk veld, en geeft bij elke afwijking Broken. read_file() doet hetzelfde met een
bestand: met media uit schrijft het renderproces van de app de stukken in een bestand (de
terugval van previews.render_text, Z2).
"""

from __future__ import annotations

import asyncio
import json
import os
import struct
from dataclasses import dataclass
from typing import BinaryIO

from app.extract.normalize import META_CHARS

HEADER = struct.Struct(">I")
MAX_JSON = 64 * 1024
MAX_SCAN_PAGES = 2000
STATES = ("done", "none", "skip", "failed")
CODES = (None, "too_slow", "zip_limit", "xml_refused", "pages_limit")


class Broken(Exception):
    """Wat er op de pipe stond, klopt niet."""


@dataclass(frozen=True)
class Found:
    """Eén uitkomst zoals de app hem leest (Extracted zonder sha, plus de tekst)."""

    index: int
    state: str
    truncated: bool
    scan_pages: list[int]
    meta: str
    code: str | None
    text: str


def encode(index: int, fields: dict, text: str) -> bytes:
    head = json.dumps({"i": index, **fields}, ensure_ascii=False).encode()
    body = text.encode("utf-8")
    return HEADER.pack(len(head)) + head + HEADER.pack(len(body)) + body


def write(fd: int, index: int, fields: dict, text: str) -> None:
    data = memoryview(encode(index, fields, text))
    while data:
        data = data[os.write(fd, data) :]


async def read(reader: asyncio.StreamReader, index: int, max_chars: int) -> Found | None:
    """De volgende uitkomst, of None bij een nette EOF (tussen twee uitkomsten)."""
    try:
        raw = await reader.readexactly(HEADER.size)
    except asyncio.IncompleteReadError as e:
        if not e.partial:
            return None
        raise Broken("afgebroken lengte") from None
    (size,) = HEADER.unpack(raw)
    if size > MAX_JSON:
        raise Broken("JSON te groot")
    try:
        head = json.loads(await reader.readexactly(size))
        (size,) = HEADER.unpack(await reader.readexactly(HEADER.size))
        if size > max_chars * 4:
            raise Broken("tekst te groot")
        text = (await reader.readexactly(size)).decode("utf-8")
    except (asyncio.IncompleteReadError, ValueError, UnicodeDecodeError):
        raise Broken("afgebroken of geen JSON") from None
    return _check(head, index, text, max_chars)


def read_file(f: BinaryIO, index: int, max_chars: int) -> Found | None:
    """Zoals read(), uit een bestand; None bij een nette EOF (tussen twee uitkomsten)."""
    raw = f.read(HEADER.size)
    if not raw:
        return None
    if len(raw) < HEADER.size:
        raise Broken("afgebroken lengte")
    (size,) = HEADER.unpack(raw)
    if size > MAX_JSON:
        raise Broken("JSON te groot")
    try:
        head = json.loads(_exact(f, size))
        (size,) = HEADER.unpack(_exact(f, HEADER.size))
        if size > max_chars * 4:
            raise Broken("tekst te groot")
        text = _exact(f, size).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        raise Broken("afgebroken of geen JSON") from None
    return _check(head, index, text, max_chars)


def _exact(f: BinaryIO, size: int) -> bytes:
    data = f.read(size)
    if len(data) != size:
        raise Broken("afgebroken")
    return data


def _check(head: object, index: int, text: str, max_chars: int) -> Found:
    if not isinstance(head, dict) or head.get("i") != index:
        raise Broken("verkeerde volgorde")
    state, code, meta = head.get("state"), head.get("code"), head.get("meta", "")
    pages = head.get("scan_pages", [])
    truncated = head.get("truncated", False)
    if state not in STATES or code not in CODES or not isinstance(truncated, bool):
        raise Broken("onbekende waarde")
    if not isinstance(meta, str) or len(meta) > META_CHARS:
        raise Broken("meta")
    if not isinstance(pages, list) or len(pages) > MAX_SCAN_PAGES:
        raise Broken("scan_pages")
    if not all(isinstance(p, int) and not isinstance(p, bool) and 1 <= p for p in pages):
        raise Broken("scan_pages")
    if len(text) > max_chars:
        raise Broken("tekst te lang")
    return Found(index, state, truncated, pages, meta, code, text)
