"""ocr: tekst uit een scan of een foto van tekst (bouwontwerp fase 6, X1; Keuze 22 en 24).

Per pagina (of foto) twee kinderen na elkaar. De app ziet alleen de blob gaan en de woorden
terugkomen; het beeld ertussen blijft in de worker.

- **Kind 1** (`python -I -c <BOOT> <api-map> ocr_page`, app.render.ocr_child) met de handdruk van
  Keuze 17, net als text: run.spawn_handshake met alleen een socket op fd 3, RLIMIT_DATA 768 MB,
  RLIMIT_CPU child_cpu_s, nice 19 en de idle IO-klasse; in het image uit python-tekst (0711,
  ops/text.py). Na "klaar" krijgt het de blob en de schrijfkant van een pipe naar de worker; daar
  komt een grijze PGM op (app/ocr/pgm.py): de PDF-pagina op 300 dpi of de foto na de EXIF-draaiing,
  rechtgezet. Over de socket zegt het daarna skew, de maten en de bron (app/render.py).
- **De worker** leest eerst de kop (`P5`, breedte × hoogte ≤ pgm.OCR_PIXELS), dan precies
  breedte × hoogte bytes, en dan EOF. Iets anders: failed met ocr_bad_image (kind 1 gaat weg), en
  kind 2 start niet. tesseract krijgt dus nooit de blob zelf (S7).
- **Kind 2**: `tesseract stdin stdout -l nld+eng --psm 3 tsv`, met OMP_THREAD_LIMIT=1 (via env),
  RLIMIT_DATA 512 MB (args.memory_mb: kort werk), nice 15 en ionice -c 3 (de achtergrond), en een
  tijdsgrens van ocr_s (60 s): daarna stoppen en too_slow. stdin is een memfd met de PGM, stdout
  de TSV (hooguit TSV_LIMIT bytes).
- **OSD alleen als het nodig is**: is de gemiddelde zekerheid van de woorden < MIN_CONF, dan
  `tesseract stdin stdout --psm 0`; zegt die "Rotate: 90", 180 of 270, dan draait de worker het
  beeld zo (pgm.rotate, met de klok mee) en herkent hij nog één keer. De ronde met de hoogste
  gemiddelde zekerheid telt; de vakjes van de gedraaide ronde gaan terug naar het beeld van
  kind 1 (pgm.unrotate_box).

Woorden met zekerheid ≥ MIN_CONF gaan naar de app, in de volgorde van de TSV, als
[tekst, links, boven, breedte, hoogte, regel] in pixels van het beeld van kind 1 (rechtgezet; de
app draait de middens terug met skew, app/ocr/geometry.py). regel telt de regels van de pagina
(0, 1, …): de tekst is per regel de woorden met een spatie, de regels met een regeleinde. Ze
komen in stukken: {"ev": "result", "data": {"part": i, "words": [...]}}, elk ≤ PART_BYTES
(wire.MAX_MESSAGE is 64 KiB); het laatste antwoord is de samenvatting:

    {"state": "done" | "none" | "failed", "code": None | "ocr_missing" | "ocr_bad_image" |
     "too_slow", "error": None | soort, "parts", "words", "chars", "conf", "skew", "width",
     "height", "source", "rotate", "osd", "ms"}

done: er is tekst; none: geen woord met genoeg zekerheid, of (code ocr_missing) geen tesseract op
deze machine (een ouder image of een eigen build; caps zegt dan ocr: false); failed: de bron is
niet te lezen (error), de PGM klopt niet (ocr_bad_image) of het duurde te lang (too_slow). Geen
tekst van de pagina in een fout, een log of de staart van stderr.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import shutil
import time
from collections.abc import Iterable

from app.media.worker import guard
from app.media.worker import run as R
from app.media.worker.args import OcrArgs
from app.media.worker.ops import text as text_op
from app.media.worker.server import Context
from app.ocr import pgm

MIN_CONF = 60.0
KIND1_MEMORY_MB = 768
KIND1_WALL_S = 300.0  # de wandklok van kind 1 (RLIMIT_CPU telt alleen CPU; nice 19 wacht)
TSV_LIMIT = 8 * 1024 * 1024
OSD_LIMIT = 64 * 1024
PART_BYTES = 60_000  # een stuk woorden met de envelop blijft ruim onder wire.MAX_MESSAGE
MAX_WORDS = 20_000
MAX_WORD_CHARS = 200
READ_CHUNK = 1024 * 1024
API_ROOT = text_op.API_ROOT
# Zoals text_op.BOOT, met ocr_child: eerst niet-dumpbaar, dan pas app.render
BOOT = (
    "import ctypes,sys\n"
    "if ctypes.CDLL(None).prctl(4,0,0,0,0):sys.exit(3)\n"
    "sys.path.insert(0,sys.argv.pop(1))\n"
    "from app.render import ocr_child\n"
    "sys.exit(ocr_child())"
)
_ROTATE = re.compile(rb"^Rotate: (0|90|180|270)$", re.MULTILINE)


class BadImage(Exception):
    """De PGM van kind 1 klopt niet: ocr_bad_image."""


def program() -> str | None:
    """Het pad van tesseract in de PATH van de kinderen, of None."""
    return shutil.which(guard.OCR, path=R.environment()["PATH"])


# --- de TSV ----------------------------------------------------------------------------------


def parse_tsv(data: bytes) -> tuple[list[list], float]:
    """(woorden, gemiddelde zekerheid) uit de TSV van tesseract. Een woord is een regel van
    niveau 5 met tekst; de gemiddelde zekerheid is over alle woorden (0.0 zonder woorden). Elk
    woord: [tekst, links, boven, breedte, hoogte, zekerheid, (blok, alinea, regel)]."""
    words: list[list] = []
    total = 0.0
    for raw in data.decode("utf-8", "replace").splitlines()[1:]:
        cols = raw.split("\t")
        if len(cols) != 12 or cols[0] != "5":
            continue
        text = cols[11].strip()
        if not text:
            continue
        try:
            left, top, width, height = (int(c) for c in cols[6:10])
            conf = float(cols[10])
            line = (int(cols[2]), int(cols[3]), int(cols[4]))
        except ValueError:
            continue
        if conf < 0 or width < 0 or height < 0:
            continue
        words.append([text[:MAX_WORD_CHARS], left, top, width, height, conf, line])
        total += conf
    return words, (total / len(words) if words else 0.0)


def keep(words: Iterable[list]) -> list[list]:
    """De woorden met zekerheid ≥ MIN_CONF als [tekst, x, y, w, h, regel]; regel telt de regels
    (0, 1, …) in de volgorde van de TSV. Hooguit MAX_WORDS."""
    out: list[list] = []
    lines: dict[tuple, int] = {}
    for text, x, y, w, h, conf, line in words:
        if conf < MIN_CONF:
            continue
        number = lines.setdefault(line, len(lines))
        out.append([text, x, y, w, h, number])
        if len(out) >= MAX_WORDS:
            break
    return out


def text_of(words: list[list]) -> str:
    """De tekst van de woorden van keep(): per regel met een spatie, regels met een regeleinde."""
    lines: dict[int, list[str]] = {}
    for word in words:
        lines.setdefault(word[5], []).append(word[0])
    return "\n".join(" ".join(lines[n]) for n in sorted(lines))


def parse_osd(data: bytes) -> int:
    """De draaiing uit de OSD (met de klok mee: 0, 90, 180 of 270), 0 als er niets staat."""
    m = _ROTATE.search(data.replace(b"\r", b""))
    return int(m.group(1)) if m else 0


def parts(words: list[list]) -> list[list[list]]:
    """De woorden in stukken van hooguit PART_BYTES (zoals wire.encode ze telt)."""
    out: list[list[list]] = []
    current: list[list] = []
    size = 0
    for word in words:
        n = len(json.dumps(word, separators=(",", ":"), ensure_ascii=False).encode()) + 1
        if current and size + n > PART_BYTES:
            out.append(current)
            current, size = [], 0
        current.append(word)
        size += n
    if current:
        out.append(current)
    return out


# --- de PGM van kind 1 ----------------------------------------------------------------------


async def _read(fd: int, n: int) -> bytes:
    """Hooguit n bytes van een niet-blokkerende pipe; b"" is EOF."""
    loop = asyncio.get_running_loop()
    while True:
        try:
            return os.read(fd, n)
        except BlockingIOError:
            pass
        ready = loop.create_future()

        def wake(fut: asyncio.Future = ready) -> None:
            if not fut.done():
                fut.set_result(None)

        loop.add_reader(fd, wake)
        try:
            await ready
        finally:
            loop.remove_reader(fd)


async def read_pgm(fd: int) -> tuple[int, int, bytes]:
    """(breedte, hoogte, pixels) van de pipe: de kop eerst (pgm.read_header), dan precies
    breedte × hoogte bytes en EOF. BadImage bij alles anders; EOFError als er niets kwam."""
    head = b""
    while not pgm.header_done(head):
        chunk = await _read(fd, pgm.HEADER_MAX - len(head))
        if not chunk:
            if not head:
                raise EOFError
            break
        head += chunk
    try:
        width, height, offset = pgm.read_header(head)
    except pgm.BadImage as exc:
        raise BadImage(str(exc)) from None
    want = width * height
    pixels = bytearray(head[offset:])
    if len(pixels) > want:
        raise BadImage("te lang")
    while len(pixels) < want:
        chunk = await _read(fd, min(READ_CHUNK, want - len(pixels)))
        if not chunk:
            raise BadImage("te kort")
        pixels += chunk
    if await _read(fd, 1):
        raise BadImage("te lang")
    return width, height, bytes(pixels)


def _answer(said: bytes, width: int, height: int) -> dict:
    """Het antwoord van kind 1, gecontroleerd: skew, source en ms; ValueError als het niet klopt."""
    answer = json.loads(said)
    if not isinstance(answer, dict):
        raise ValueError("geen object")
    if answer.get("error"):
        raise ValueError(str(answer["error"])[:80])
    if answer.get("width") != width or answer.get("height") != height:
        raise BadImage("andere maat dan de kop")
    skew = float(answer.get("skew", 0.0))
    source = answer.get("source")
    if not (
        isinstance(source, list)
        and len(source) == 2
        and all(isinstance(v, int) and 0 < v <= pgm.MAX_SIDE for v in source)
        and -45.0 <= skew <= 45.0
    ):
        raise ValueError("onleesbaar antwoord")
    ms = answer.get("ms") if isinstance(answer.get("ms"), dict) else {}
    return {
        "skew": round(skew, 2),
        "source": source,
        "ms": {
            k: int(v) for k, v in ms.items() if k in ("render", "deskew") and isinstance(v, int)
        },
    }


# --- kind 2 ----------------------------------------------------------------------------------


def _memfd(data: Iterable[bytes]) -> int:
    fd = os.memfd_create("ocr", os.MFD_CLOEXEC)
    try:
        for block in data:
            view = memoryview(block)
            while view:
                view = view[os.write(fd, view) :]
        os.lseek(fd, 0, os.SEEK_SET)
    except BaseException:
        os.close(fd)
        raise
    return fd


class TooSlow(Exception):
    pass


async def _tesseract(ctx: Context, exe: str, image: int, extra: list[str], limit: int) -> bytes:
    """Eén keer tesseract op de PGM in de memfd image; geeft stdout. TooSlow na ocr_s;
    RuntimeError bij een fout (zonder de staart van stderr: daar kan tekst in staan)."""
    args = ctx.args
    assert isinstance(args, OcrArgs)
    os.lseek(image, 0, os.SEEK_SET)
    child = ctx.spawn(
        ["env", "OMP_THREAD_LIMIT=1", exe, "stdin", "stdout", *extra],
        stdin=image,
        capture_stdout=limit,
    )
    try:
        async with asyncio.timeout(args.ocr_s):
            done = await child.wait()
    except TimeoutError:
        await child.stop()
        raise TooSlow from None
    if child.stdout_overflow:
        raise RuntimeError("te veel uitvoer")
    if done.rc != 0:
        raise RuntimeError(f"code {done.rc}")
    return bytes(child.stdout)


async def recognize(ctx: Context, exe: str, width: int, height: int, pixels: bytes) -> dict:
    """De herkenning (kind 2) met OSD als het nodig is: {"words", "conf", "rotate", "osd", "ms"}."""
    args = ctx.args
    assert isinstance(args, OcrArgs)
    ocr = ["-l", "nld+eng", "--psm", "3", "tsv"]
    started = time.monotonic()
    image = _memfd([pgm.header(width, height), pixels])
    try:
        words, conf = parse_tsv(await _tesseract(ctx, exe, image, ocr, TSV_LIMIT))
        rotate, osd = 0, False
        if conf < MIN_CONF and args.osd:
            osd = True
            try:
                rotate = parse_osd(await _tesseract(ctx, exe, image, ["--psm", "0"], OSD_LIMIT))
            except RuntimeError:
                rotate = 0  # te weinig tekens voor OSD: het blijft bij de eerste ronde
    finally:
        os.close(image)
    if rotate:
        turned, w2, h2 = pgm.rotate(pixels, width, height, rotate)
        image = _memfd([pgm.header(w2, h2), turned])
        del turned
        try:
            words2, conf2 = parse_tsv(await _tesseract(ctx, exe, image, ocr, TSV_LIMIT))
        finally:
            os.close(image)
        if conf2 > conf:
            for word in words2:
                word[1:5] = pgm.unrotate_box(tuple(word[1:5]), width, height, rotate)
            words, conf = words2, conf2
        else:
            rotate = 0
    return {
        "words": keep(words),
        "conf": round(conf, 1),
        "rotate": rotate,
        "osd": osd,
        "ms": round((time.monotonic() - started) * 1000),
    }


# --- de opdracht -----------------------------------------------------------------------------


def _summary(state: str, code: str | None = None, error: str | None = None, **extra) -> dict:
    out = {
        "state": state, "code": code, "error": error, "parts": 0, "words": 0, "chars": 0,
        "conf": None, "skew": 0.0, "width": 0, "height": 0, "source": None, "rotate": 0,
        "osd": False, "ms": {},
    }  # fmt: skip
    out.update(extra)
    return out


async def _kind1(ctx: Context) -> tuple[int, int, bytes, dict] | dict:
    """Kind 1: de PGM en zijn antwoord, of een samenvatting met de fout."""
    args = ctx.args
    assert isinstance(args, OcrArgs)
    executable = text_op.python(ctx.worker.caps.get("sandbox") == "on")
    if executable is None:
        return _summary("failed", error=text_op.NO_COPY)
    child, sock = R.spawn_handshake(
        [executable, "-I", "-c", BOOT, API_ROOT, "ocr_page"],
        memory_mb=KIND1_MEMORY_MB,
        cpu_s=args.child_cpu_s,
    )
    ctx.children.append(child)
    # Alleen de leeskant niet-blokkerend: de vlag hoort bij het open bestand, en een
    # niet-blokkerende schrijfkant gaf kind 1 BlockingIOError zodra de pijp vol was
    r, w = os.pipe2(os.O_CLOEXEC)
    os.set_blocking(r, False)
    try:
        request = {"kind": args.kind, "page": args.page, "max_pixels": args.max_pixels}
        try:
            await R.handshake(sock, [ctx.inputs[0], w], json.dumps(request).encode())
        except (R.ChildError, TimeoutError, OSError) as exc:
            await child.stop()
            return _summary("failed", error=f"handdruk: {exc}"[:200])
        finally:
            os.close(w)
        try:
            async with asyncio.timeout(KIND1_WALL_S):
                try:
                    width, height, pixels = await read_pgm(r)
                except EOFError:
                    width = height = 0
                    pixels = b""
                done = await child.wait()
        except TimeoutError:
            await child.stop()
            return _summary("failed", "too_slow")
        except BadImage:
            await child.stop()
            return _summary("failed", "ocr_bad_image")
        said = await R.report(sock)
    finally:
        os.close(r)
        sock.close()
    if not pixels:
        # niets op de pipe: kind 1 kon de bron niet lezen (of zweeg)
        error = "kind 1 gaf niets"
        with contextlib.suppress(ValueError, TypeError, AttributeError):
            error = str(json.loads(said).get("error") or error)[:80]
        return _summary("failed", error=error)
    try:
        answer = _answer(said, width, height)
    except BadImage:
        return _summary("failed", "ocr_bad_image")
    except (ValueError, TypeError):
        return _summary("failed", error=f"kind 1: code {done.rc}")
    if done.rc != 0:
        return _summary("failed", error=f"kind 1: code {done.rc}")
    return width, height, pixels, answer


async def run(ctx: Context) -> dict:
    args = ctx.args
    assert isinstance(args, OcrArgs)
    exe = program()
    if exe is None:
        return _summary("none", "ocr_missing")
    first = await _kind1(ctx)
    if isinstance(first, dict):
        return first
    width, height, pixels, answer = first
    page = {"skew": answer["skew"], "width": width, "height": height, "source": answer["source"]}
    try:
        found = await recognize(ctx, exe, width, height, pixels)
    except TooSlow:
        return _summary("failed", "too_slow", **page, ms=answer["ms"])
    except RuntimeError as exc:
        return _summary("failed", error=f"herkenning: {exc}"[:200], **page, ms=answer["ms"])
    finally:
        del pixels
    words = found["words"]
    chunks = parts(words)
    for i, chunk in enumerate(chunks):
        await ctx.send({"ev": "result", "data": {"part": i, "words": chunk}})
    return _summary(
        "done" if words else "none",
        parts=len(chunks),
        words=len(words),
        chars=len(text_of(words)),
        conf=found["conf"],
        rotate=found["rotate"],
        osd=found["osd"],
        ms={**answer["ms"], "ocr": found["ms"]},
        **page,
    )
