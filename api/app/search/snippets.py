"""Het stukje tekst rond de eerste treffer (bouwontwerp fase 6, Keuze 8 en 13). Z1.

    found = await make(db, user, node_ids, parsed)    # {node-id: Snip(match, snippet)}

- Alleen voor de treffers op de pagina, en pas na de tweede controle (Keuze 15): engine.hits()
  geeft de nodes die doorkwamen.
- De SQL leest per node alleen de eerste 20.000 tekens (substr) van de tekst, de OCR en de meta,
  de titel uit de mediabibliotheek, en de OCR van een foto alleen als de node van wie vraagt is
  (Keuze 16 en 25a: een treffer, zijn rang en zijn snippet komen nooit uit de foto-OCR van een
  ander).
- Eén keer vouwen (fold() per teken, met een tabel van plekken terug naar de tekst), dan één
  reguliere expressie met de gevouwen woorden van de vraag (het laatste als voorvoegsel, zoals
  parse() het zegt). Het stuk van ±160 tekens rond de eerste treffer, met elke treffer daarin
  gemarkeerd. Zo vindt "strasse" ook "Straße", en is in de snippet "Straße" gemarkeerd.
- match: "name" als elk woord in de naam staat (geen snippet: de naam staat er al); anders de
  eerste kolom met een treffer in zijn venster, in de volgorde van bm25 (Keuze 6): title, text,
  ocr (ook de OCR van een eigen foto), meta. Staat het woord nergens in de eerste 20.000 tekens,
  dan snippet null en match "text": de web zegt dan "gevonden in de tekst".
- Als stukken: [{"t": "…de ", "m": false}, {"t": "factuur", "m": true}, …]. Gewone tekst, nooit
  HTML: wat er in de tekst staat (ook "<b>") komt er letterlijk in; stuurtekens, tekens die de
  richting van tekst omdraaien en regeleinden worden een spatie. De web zet de stukken als tekst
  in de pagina.
- De klok loopt per document: hooguit 40 ms per verzoek, en voor de hele app hooguit 100 ms per
  seconde (Keuze 13). Wat daarna nog komt, krijgt snippet null en match "text" (de treffer en
  zijn naam blijven). De klok is de wandklok van de thread (CLOCK; de tests zetten een nepklok).
  Met de limiter van 1 hieronder is dat de tijd van het werk zelf, of iets meer als de event
  loop de GIL even heeft: nooit minder dan de CPU die het kost.
- Het werk draait in een thread (anyio.to_thread) met een eigen limiter van 1, zodat de event
  loop tussendoor andere verzoeken kan doen.
"""

from __future__ import annotations

import asyncio
import re
import threading
import time
import uuid
from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass

import anyio
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import BlobText, MediaItem, Node, PhotoOcr, User
from app.search.query import Parsed
from app.search.types import Match, SnippetPart
from app.text import CONTROL, fold

# Zoveel tekens van elke kolom leest de SQL (C5: de hele tekst van 1 MB kostte 88–105 ms)
WINDOW = 20_000
# De lengte van een snippet, en hoeveel daarvan vóór de eerste treffer staat
SPAN = 160
BEFORE = 50
# Het budget (Keuze 8 en 13): per verzoek, en voor de hele app per APP_WINDOW_S seconden
REQUEST_MS = 40.0
APP_MS = 100.0
APP_WINDOW_S = 1.0
# De klok van het budget, in seconden (de tests zetten een nepklok)
CLOCK = time.perf_counter
ELLIPSIS = "…"
_SPACE = re.compile(r"\s+")
# Een letter of cijfer; een woord begint en eindigt waar dat niet zo is (zoals parse())
_NOT_WORD_BEFORE = r"(?<![^\W_])"
_NOT_WORD_AFTER = r"(?![^\W_])"


@dataclass(frozen=True)
class Snip:
    match: Match
    snippet: list[SnippetPart] | None


@dataclass(frozen=True)
class Source:
    """Wat de SQL per node leest: de gevouwen naam en de vensters van de kolommen."""

    id: uuid.UUID
    name: str
    title: str | None = None
    body: str | None = None
    ocr: str | None = None
    pocr: str | None = None  # de OCR van een foto: alleen gelezen voor de eigenaar
    meta: str | None = None


# --- Vouwen met een tabel terug ---------------------------------------------------------------

# Tekens waarvan fold() niet precies één teken geeft (ß → "ss", een accent los → "")
_ODD: set[str] = set()


class _Table(dict):
    """fold() per teken voor str.translate, gevuld zodra een teken voor het eerst komt."""

    def __missing__(self, code: int) -> str:
        out = fold(chr(code))
        if len(out) != 1:
            _ODD.add(chr(code))
        self[code] = out
        return out


_TABLE = _Table({code: fold(chr(code)) for code in range(128)})


class Folded:
    """Een tekst gevouwen zoals de vraag (fold() per teken), met de weg terug: back(i) is de
    plek in de tekst van teken i van de gevouwen tekst. Geeft elk teken precies één teken (het
    gewone geval), dan is dat dezelfde plek, en wordt er niets geteld."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.out = text.translate(_TABLE)
        self._same = _ODD.isdisjoint(text)
        self._starts: list[int] = []
        self._i = 0

    def back(self, pos: int) -> int:
        if self._same:
            return min(pos, len(self.text))
        while len(self._starts) <= pos and self._i < len(self.text):
            self._starts.extend([self._i] * len(_TABLE[ord(self.text[self._i])]))
            self._i += 1
        return self._starts[pos] if pos < len(self._starts) else len(self.text)

    def span(self, start: int, end: int) -> tuple[int, int]:
        """[start, end) in de gevouwen tekst als [begin, eind) in de tekst. Een teken dat in
        stukken uiteenvalt (ß → "ss") hoort er helemaal bij, en een accent dat los achter zijn
        letter staat ook."""
        begin = self.back(start)
        stop = self.back(end - 1) + 1 if end > start else begin
        while stop < len(self.text) and _TABLE[ord(self.text[stop])] == "":
            stop += 1
        return begin, stop


def pattern(parsed: Parsed) -> re.Pattern[str]:
    """Eén reguliere expressie voor alle woorden van de vraag (gevouwen), elk als heel woord;
    het laatste als begin van een woord als parse() dat zegt. De langste eerst."""
    last = parsed.words[-1]
    alts = []
    for word in sorted(set(parsed.words), key=lambda w: (-len(w), w)):
        prefix = parsed.prefix and word == last
        alts.append(re.escape(word) + ("" if prefix else _NOT_WORD_AFTER))
    return re.compile(_NOT_WORD_BEFORE + "(?:" + "|".join(alts) + ")")


def _clean(text: str) -> str:
    return _SPACE.sub(" ", CONTROL.sub(" ", text))


def snippet(text: str, rx: re.Pattern[str]) -> list[SnippetPart] | None:
    """Het stuk van ±SPAN tekens rond de eerste treffer in text, als stukken; None zonder
    treffer."""
    f = Folded(text)
    hits = rx.finditer(f.out)
    first = next(hits, None)
    if first is None:
        return None
    begin, stop = f.span(first.start(), first.end())
    start = max(0, begin - BEFORE)
    if start > 0:
        # Niet midden in een woord beginnen
        space = text.find(" ", start, begin)
        if space != -1:
            start = space + 1
    end = max(stop, min(len(text), start + SPAN))
    marks = [(begin, stop)]
    for m in hits:
        b, s = f.span(m.start(), m.end())
        if b >= end:
            break
        marks.append((b, s))
        end = max(end, s)
    if end < len(text):
        # Niet midden in een woord stoppen
        space = text.rfind(" ", marks[-1][1], end)
        if space != -1:
            end = space
    parts: list[SnippetPart] = []

    def add(t: str, m: bool) -> None:
        t = _clean(t)
        if not t:
            return
        if parts and parts[-1].m == m:
            parts[-1] = SnippetPart(t=parts[-1].t + t, m=m)
        else:
            parts.append(SnippetPart(t=t, m=m))

    add(ELLIPSIS if start > 0 else "", False)
    pos = start
    for b, s in marks:
        add(text[pos:b], False)
        add(text[b:s], True)
        pos = s
    add(text[pos:end], False)
    if end < len(text):
        add(ELLIPSIS, False)
    return parts


# --- Het budget -------------------------------------------------------------------------------


class _App:
    """De klok van de hele app: hooguit APP_MS ms snippets per APP_WINDOW_S s (Keuze 13)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._spent: deque[tuple[float, float]] = deque()

    def left(self, now: float) -> float:
        with self._lock:
            while self._spent and self._spent[0][0] <= now - APP_WINDOW_S:
                self._spent.popleft()
            return APP_MS - sum(ms for _, ms in self._spent)

    def add(self, now: float, ms: float) -> None:
        with self._lock:
            self._spent.append((now, ms))

    def clear(self) -> None:
        with self._lock:
            self._spent.clear()


APP = _App()


def _one(src: Source, rx: re.Pattern[str]) -> Snip:
    columns: tuple[tuple[Match, str | None], ...] = (
        ("title", src.title),
        ("text", src.body),
        ("ocr", src.ocr),
        ("ocr", src.pocr),
        ("meta", src.meta),
    )
    for match, text in columns:
        if text:
            parts = snippet(text, rx)
            if parts is not None:
                return Snip(match, parts)
    return Snip("text", None)


def build(sources: Sequence[Source], parsed: Parsed) -> dict[uuid.UUID, Snip]:
    """match en snippet per node, binnen het budget van dit verzoek en van de app."""
    rx = pattern(parsed)
    spent = 0.0
    out: dict[uuid.UUID, Snip] = {}
    for src in sources:
        if all(w in src.name for w in parsed.words):
            out[src.id] = Snip("name", None)
            continue
        start = CLOCK()
        if spent >= REQUEST_MS or APP.left(start) <= 0:
            out[src.id] = Snip("text", None)
            continue
        out[src.id] = _one(src, rx)
        ms = (CLOCK() - start) * 1000
        spent += ms
        APP.add(start, ms)
    return out


# --- Met de database --------------------------------------------------------------------------

_limiter: tuple[asyncio.AbstractEventLoop, anyio.CapacityLimiter] | None = None


def _thread() -> anyio.CapacityLimiter:
    """Eén thread tegelijk voor snippets, per event loop (een nieuwe loop, zoals in de tests,
    krijgt een eigen limiter)."""
    global _limiter
    loop = asyncio.get_running_loop()
    if _limiter is None or _limiter[0] is not loop:
        _limiter = (loop, anyio.CapacityLimiter(1))
    return _limiter[1]


def _window(column):
    return func.substr(column, 1, WINDOW)


async def sources(db: AsyncSession, user: User, ids: Sequence[uuid.UUID]) -> list[Source]:
    """De vensters van deze nodes, in de volgorde van ids. De OCR van een foto alleen als de
    node van user is."""
    if not ids:
        return []
    n = Node.__table__.alias("n")
    title = (
        select(func.group_concat(MediaItem.title, " "))
        .where(MediaItem.node_id == n.c.id)
        .scalar_subquery()
    )
    pocr = case((n.c.owner_id == user.id, _window(PhotoOcr.text)), else_=None)
    sql = (
        select(
            n.c.id,
            n.c.search_name,
            title,
            _window(BlobText.body),
            _window(BlobText.ocr),
            pocr,
            _window(BlobText.meta),
        )
        .select_from(n)
        .outerjoin(BlobText, BlobText.blob_sha == n.c.blob_id)
        .outerjoin(PhotoOcr, PhotoOcr.node_id == n.c.id)
        .where(n.c.id.in_(list(ids)))
    )
    found = {
        row[0]: Source(row[0], row[1] or "", row[2], row[3], row[4], row[5], row[6])
        for row in await db.execute(sql)
    }
    return [found[i] for i in ids if i in found]


async def make(
    db: AsyncSession, user: User, ids: Sequence[uuid.UUID], parsed: Parsed
) -> dict[uuid.UUID, Snip]:
    """match en snippet voor de treffers van een pagina (na de tweede controle)."""
    found = await sources(db, user, ids)
    if not found:
        return {}
    return await anyio.to_thread.run_sync(build, found, parsed, limiter=_thread())
