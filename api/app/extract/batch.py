"""Eén batch van de stap text door de mediaworker, aan de kant van de app (bouwontwerp fase 6,
Keuze 17).

De app opent per blob één fd (alleen lezen, O_NOFOLLOW, O_CLOEXEC) en vraagt de worker om
Op.TEXT met per blob de soort lezer. De worker start één kind voor de hele batch en geeft het de
blobs en de schrijfkant van een pipe; de app leest de leeskant met asyncio tot EOF. Per blob
komt er één uitkomst, in de volgorde van de vraag (frame.read controleert elk veld: het kind las
een bestand van een ander en is niet te vertrouwen).

Ran zegt wat er terugkwam: de uitkomsten (hooguit één per blob, vanaf het begin), en of het
kind netjes stopte. Stopte het niet netjes (een crash, RLIMIT_CPU, een kapotte pipe), dan hoort
de fout bij een blob zonder uitkomst; step.py halveert die rest. Stopte het netjes met minder
uitkomsten dan blobs, dan was de batch-klok op (batch_cpu_s): de rest komt in een volgende
batch.

Dit bestand importeert geen lezer (test_extract_no_pdfium_in_app.py).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field

from app.extract import frame
from app.media import client
from app.media.wire import Op
from app.media.worker.args import TextArgs, TextItem

log = logging.getLogger("platform.extract")

# De worker stopt een opdracht text na 900 s (server.TIMEOUT); de app wacht iets langer
LIMIT_S = 930.0
READ_LIMIT = 1 << 20  # de buffer van de StreamReader (readexactly leest ook meer)


# --- de plaats background van de worker: de index gaat vóór OCR ----------------------------------
#
# Op.TEXT (de index) en Op.OCR delen de ene plaats background van de worker (Keuze 12, Keuze 22).
# Zonder afspraak pakte de pomp van ocr na elke pagina meteen de volgende, en vond de index de
# plaats bijna altijd bezet (busy, dan WORKER_DEFER_S later opnieuw): een grote achterstand van
# OCR hield de index zo uren op (review golf 4, uitrol F1). Daarom: zolang de stap text een batch
# heeft (index_first), begint OCR geen nieuwe opdracht in de worker (ocr/step.py wijkt), en
# wacht text op een pagina die OCR nog leest (ocr_holds). Zo wacht de index hooguit één pagina.
_index = 0  # batches van de stap text die nu de worker gebruiken
_ocr = 0  # opdrachten van OCR die nu in de worker lopen


@contextlib.contextmanager
def index_first() -> Iterator[None]:
    """Om een batch van de stap text met de worker: OCR begint zolang niets nieuws."""
    global _index
    _index += 1
    try:
        yield
    finally:
        _index -= 1


def index_running() -> bool:
    """Heeft de stap text nu een batch met de worker (dan wijkt OCR)?"""
    return _index > 0


@contextlib.contextmanager
def ocr_call() -> Iterator[None]:
    """Om een opdracht van OCR in de worker: een pagina of foto, of welke pagina's scans zijn."""
    global _ocr
    _ocr += 1
    try:
        yield
    finally:
        _ocr -= 1


def ocr_holds() -> bool:
    """Loopt er nu een opdracht van OCR in de worker (dan is de plaats na die pagina vrij)?"""
    return _ocr > 0


@dataclass
class Ran:
    found: list[frame.Found] = field(default_factory=list)
    clean: bool = True  # het kind stopte netjes (exitcode 0, geen fout, de pipe klopte)
    too_slow: bool = False  # het kind stopte door RLIMIT_CPU (SIGXCPU, of SIGKILL erna)
    rc: int | None = None
    error: str | None = None

    def unfinished(self, total: int) -> range:
        """De plaatsen zonder uitkomst."""
        return range(len(self.found), total)


def open_blob(path: os.PathLike | str) -> int:
    """De fd voor de worker: alleen lezen, geen symlink, niet naar een kind van de app."""
    return os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)


def _too_slow(rc: int | None) -> bool:
    return rc in (-signal.SIGXCPU, -signal.SIGKILL)


async def _pipe_reader(fd: int) -> tuple[asyncio.StreamReader, asyncio.BaseTransport]:
    loop = asyncio.get_running_loop()
    reader = asyncio.StreamReader(limit=READ_LIMIT)
    pipe = os.fdopen(fd, "rb", buffering=0)
    try:
        transport, _ = await loop.connect_read_pipe(
            lambda: asyncio.StreamReaderProtocol(reader), pipe
        )
    except BaseException:
        pipe.close()
        raise
    return reader, transport


async def _read_frames(reader: asyncio.StreamReader, total: int, max_chars: int, ran: Ran) -> None:
    try:
        for index in range(total):
            found = await frame.read(reader, index, max_chars)
            if found is None:
                return
            ran.found.append(found)
        if await reader.read(1):
            raise frame.Broken("meer uitkomsten dan blobs")
    except frame.Broken as exc:
        ran.clean = False
        ran.error = f"pipe: {exc}"


async def via_worker(
    fds: Sequence[int],
    kinds: Sequence[str],
    *,
    max_chars: int,
    blob_cpu_s: float | None = None,
    batch_cpu_s: float | None = None,
    child_cpu_s: int | None = None,
    limit_s: float = LIMIT_S,
) -> Ran:
    """Eén batch door de worker. fds: open blobs (de aanroeper sluit ze), kinds: de soort lezer
    per blob. WorkerBusy en WorkerDown gaan door naar de aanroeper (dan later opnieuw)."""
    if len(fds) != len(kinds) or not fds:
        raise ValueError("per blob één fd en één soort")
    extra = {
        name: value
        for name, value in (
            ("blob_cpu_s", blob_cpu_s),
            ("batch_cpu_s", batch_cpu_s),
            ("child_cpu_s", child_cpu_s),
        )
        if value is not None
    }
    args = TextArgs(
        items=[TextItem(kind=kind) for kind in kinds],  # type: ignore[arg-type]
        max_chars=max_chars,
        **extra,
    )
    ran = Ran()
    data: dict = {}
    async with asyncio.timeout(limit_s):
        job, (out,) = await client.call(Op.TEXT, args, inputs=0, fds=list(fds), outputs=1)
        async with job:
            try:
                reader, transport = await _pipe_reader(out)
            except BaseException:
                os.close(out)
                raise
            try:
                await _read_frames(reader, len(kinds), max_chars, ran)
                if not ran.clean:
                    return ran  # het kind schreef onzin; async with stopt de opdracht
                try:
                    data = await job.result()
                except client.WorkerFailed as exc:
                    ran.clean = False
                    ran.error = f"worker: {exc.code}"
                    return ran
            finally:
                transport.close()
    rc = data.get("rc")
    ran.rc = rc if isinstance(rc, int) else None
    error = data.get("error")
    ran.error = str(error)[:200] if error else None
    ran.clean = ran.rc == 0 and not ran.error
    ran.too_slow = _too_slow(ran.rc)
    log.debug(
        "text: %d van %d blobs, rc %s, cpu %s s", len(ran.found), len(kinds), ran.rc,
        data.get("cpu_s"),
    )  # fmt: skip
    return ran
