"""Het stukje tekst rond de eerste treffer (bouwontwerp fase 6, Keuze 8 en 13). Z1.

    found = await make(db, user, node_ids, parsed)    # {node-id: Snip(match, snippet)}

- Alleen voor de treffers op de pagina, en pas na de tweede controle (Keuze 15): engine.hits()
  geeft de nodes die doorkwamen.
- De SQL leest per node alleen het begin van de tekst, de OCR en de meta (substr van de bytes:
  4 × 20.000, daarna de eerste 20.000 tekens; substr van tekst decodeert de hele waarde en is bij
  30 teksten van 1 MB 11–14 ms, de bytes 8 ms), de titel uit de mediabibliotheek, en de OCR
  van een foto alleen als de node van wie vraagt is
  (Keuze 16 en 25a: een treffer, zijn rang en zijn snippet komen nooit uit de foto-OCR van een
  ander).
- Vouwen (fold() per teken, met een tabel van plekken terug naar de tekst) in stappen: eerst de
  eerste 2.000 tekens, dan tot 8.000, dan de rest van het venster, tot de eerste treffer met
  zijn stuk erin past (de meeste treffers staan vooraan). Elke stap vouwt alleen wat er nieuw
  bij komt, en zoekt pas vanaf waar de vorige stap nog niet zeker was: zonder treffer kost het
  venster zo één keer vouwen en één keer zoeken (±0,45 ms per 20.000 tekens, in stappen opnieuw
  was dat ±0,75). Eén reguliere expressie met de gevouwen woorden van de vraag (het laatste als
  voorvoegsel, zoals parse() het zegt). Het stuk van ±160 tekens rond de eerste treffer, met
  elke treffer daarin gemarkeerd. Zo vindt "strasse" ook "Straße", en is in de snippet "Straße"
  gemarkeerd.
- match: "name" als elk woord in de naam staat (geen snippet: de naam staat er al); anders de
  eerste kolom met een treffer in zijn venster, in de volgorde van bm25 (Keuze 6): title, text,
  ocr (ook de OCR van een eigen foto), meta. Staat het woord nergens in de eerste 20.000 tekens,
  dan snippet null en match "text": de web zegt dan "gevonden in de tekst".
- Als stukken: [{"t": "…de ", "m": false}, {"t": "factuur", "m": true}, …]. Gewone tekst, nooit
  HTML: wat er in de tekst staat (ook "<b>") komt er letterlijk in; stuurtekens, tekens die de
  richting van tekst omdraaien en regeleinden worden een spatie. De web zet de stukken als tekst
  in de pagina.
- De klok kijkt vóór elk document: hooguit 40 ms per verzoek, en voor de hele app hooguit
  100 ms per seconde (Keuze 13). De 40 ms gelden voor de hele stap snip (Server-Timing, §4.4):
  de SQL, het wachten op de thread en elk document tellen mee (de wandklok vanaf het begin van
  de stap). Een document begint alleen als het nog past, geschat op het duurste document tot nu
  toe in dit verzoek; een document wordt nooit halverwege gestopt. Wat niet meer past, krijgt
  snippet null en match "text" (de treffer en zijn naam blijven). De klok is CLOCK (de tests
  zetten een nepklok).
- Het werk draait in een thread (anyio.to_thread) met een eigen limiter van 1, zodat de event
  loop tussendoor andere verzoeken kan doen.
"""

from __future__ import annotations

import asyncio
import bisect
import re
import threading
import time
import uuid
from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass

import anyio
from sqlalchemy import LargeBinary, case, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import BlobText, MediaItem, Node, PhotoOcr, User
from app.search.query import Parsed
from app.search.types import Match, SnippetPart
from app.text import CONTROL, fold

# Zoveel tekens van elke kolom leest de SQL (C5: de hele tekst van 1 MB kostte 88–105 ms)
WINDOW = 20_000
# De lengte van een snippet, en hoeveel daarvan vóór de eerste treffer staat
SPAN = 160
# Vouwen in stappen (snippet()); MARGIN: wat er na het stuk nog gevouwen moet zijn, zodat een
# woord van de vraag (hooguit 200 tekens) niet op de rand van een stap valt
STEPS = (2_000, 8_000)
MARGIN = 200
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
_odd_rx: tuple[int, re.Pattern[str]] = (-1, re.compile("(?!)"))


class _Table(dict):
    """fold() per teken voor str.translate, gevuld zodra een teken voor het eerst komt."""

    def __missing__(self, code: int) -> str:
        out = fold(chr(code))
        if len(out) != 1:
            _ODD.add(chr(code))
        self[code] = out
        return out


_TABLE = _Table({code: fold(chr(code)) for code in range(128)})


def _odd() -> re.Pattern[str]:
    """Eén klasse met de tekens van _ODD, opnieuw gemaakt als er een bij kwam."""
    global _odd_rx
    chars = sorted(_ODD)
    if _odd_rx[0] != len(chars):
        body = "".join(re.escape(c) for c in chars)
        _odd_rx = (len(chars), re.compile(f"[{body}]" if body else "(?!)"))
    return _odd_rx[1]


class Folded:
    """Een tekst gevouwen zoals de vraag (fold() per teken), met de weg terug: back(i) is de
    plek in de tekst van teken i van de gevouwen tekst. Geeft elk teken precies één teken (het
    gewone geval), dan is dat dezelfde plek. Anders onthoudt Folded alleen de tekens die dat
    niet doen (meestal weinig): hun plek in de tekst, hun plek in de gevouwen tekst en hoeveel
    tekens ze geven; back() zoekt het laatste ervoor op (bisect).

    Folded(text, n) vouwt alleen de eerste n tekens; extend(m) vouwt verder tot teken m. Het
    gevouwen deel staat in out, en n zegt tot waar het komt."""

    def __init__(self, text: str, n: int | None = None) -> None:
        self.text = text
        self.n = 0
        self.out = ""
        self._at: list[int] = []  # plek in de tekst
        self._folded_at: list[int] = []  # plek in de gevouwen tekst
        self._width: list[int] = []  # zoveel tekens geeft fold()
        self.extend(len(text) if n is None else n)

    def extend(self, n: int) -> None:
        n = min(n, len(self.text))
        if n <= self.n:
            return
        piece = self.text[self.n : n]
        shift = len(self.out) - self.n
        self.out += piece.translate(_TABLE)
        # Na translate: dan staan de tekens van dit stuk in _ODD
        for m in _odd().finditer(self.text, self.n, n):
            at = m.start()
            width = len(_TABLE[ord(m.group())])
            self._at.append(at)
            self._folded_at.append(at + shift)
            self._width.append(width)
            shift += width - 1
        self.n = n

    def back(self, pos: int) -> int:
        if pos >= len(self.out):
            return self.n
        k = bisect.bisect_right(self._folded_at, pos) - 1
        if k < 0:
            return pos
        at, folded_at, width = self._at[k], self._folded_at[k], self._width[k]
        if pos < folded_at + width:
            return at
        return at + 1 + pos - folded_at - width

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
    treffer. Gevouwen in stappen (STEPS): een treffer telt pas als hij met zijn stuk en een
    marge (een woord dat over de rand loopt) binnen het gevouwen deel valt. Elke stap vouwt
    alleen het nieuwe stuk, en zoekt vanaf de eerste plek die de vorige stap niet zeker wist:
    de treffer die nog niet telde, of zonder treffer een marge vóór de rand (een woord van de
    vraag dat daar begon, liep misschien over de rand)."""
    # Langer dan elk gevouwen woord van de vraag (het patroon bevat ze allemaal)
    margin = max(MARGIN, len(rx.pattern))
    f = Folded(text, 0)
    first: re.Match[str] | None = None
    pos = 0
    for n in (*STEPS, len(text)):
        f.extend(n)
        first = rx.search(f.out, pos)
        if f.n == len(text):
            break
        if first is None:
            pos = max(pos, len(f.out) - margin)
        elif f.span(first.start(), first.end())[1] + SPAN + margin < f.n:
            break
        else:
            pos = first.start()
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
    for m in rx.finditer(f.out, first.end()):
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

    def left(self, now: float) -> tuple[float, float]:
        """Wat er in het venster nog over is, en het duurste document erin."""
        with self._lock:
            while self._spent and self._spent[0][0] <= now - APP_WINDOW_S:
                self._spent.popleft()
            ms = [ms for _, ms in self._spent]
            return APP_MS - sum(ms), max(ms, default=0.0)

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


def build(sources: Sequence[Source], parsed: Parsed, already: float = 0.0) -> dict[uuid.UUID, Snip]:
    """match en snippet per node, binnen het budget van dit verzoek en van de app. already: de
    ms die de stap al kostte (de SQL van make() en het wachten op de thread).

    Een document begint alleen als het nog past: wat de stap tot nu toe kostte (de wandklok),
    plus het duurste document tot nu toe in dit verzoek, blijft onder REQUEST_MS; en de app
    heeft in zijn venster nog meer over dan het duurste document daarin of in dit verzoek."""
    rx = pattern(parsed)
    begin = CLOCK()
    most = 0.0
    out: dict[uuid.UUID, Snip] = {}
    for src in sources:
        if all(w in src.name for w in parsed.words):
            out[src.id] = Snip("name", None)
            continue
        start = CLOCK()
        spent = already + (start - begin) * 1000
        left, largest = APP.left(start)
        if spent + most >= REQUEST_MS or left <= max(most, largest):
            out[src.id] = Snip("text", None)
            continue
        out[src.id] = _one(src, rx)
        ms = (CLOCK() - start) * 1000
        most = max(most, ms)
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
    """De eerste 4 × WINDOW bytes (UTF-8: genoeg voor WINDOW tekens); _text() maakt er tekst
    van."""
    return func.substr(cast(column, LargeBinary), 1, 4 * WINDOW)


def _text(value: bytes | str | None) -> str | None:
    if value is None or isinstance(value, str):
        return value if value is None else value[:WINDOW]
    return bytes(value).decode("utf-8", "ignore")[:WINDOW]


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
        row[0]: Source(
            row[0],
            row[1] or "",
            row[2],
            _text(row[3]),
            _text(row[4]),
            _text(row[5]),
            _text(row[6]),
        )
        for row in await db.execute(sql)
    }
    return [found[i] for i in ids if i in found]


async def make(
    db: AsyncSession, user: User, ids: Sequence[uuid.UUID], parsed: Parsed
) -> dict[uuid.UUID, Snip]:
    """match en snippet voor de treffers van een pagina (na de tweede controle)."""
    start = CLOCK()
    found = await sources(db, user, ids)
    if not found:
        return {}

    def run() -> dict[uuid.UUID, Snip]:
        # In de thread: het wachten op de limiter telt mee
        return build(found, parsed, (CLOCK() - start) * 1000)

    return await anyio.to_thread.run_sync(run, limiter=_thread())
