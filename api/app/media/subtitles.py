"""De ondertitels: ingebed, los, en geforceerd (bouwontwerp fase 5, Keuze 29). M5.

- **tracks(probe)** geeft de ingebedde sporen voor play: een tekstspoor (`subrip`, `ass`, `ssa`,
  `webvtt`, `mov_text`, `text`) als `e<index>` met de staat uit de cache (ready, failed of
  pending), een beeldspoor (PGS, VobSub, DVB) met staat `burn` (alleen ingebrand). Andere sporen
  (608, teletekst) laten we weg: de speler toont nooit een spoor dat niet kan.
- **Geforceerd:** heeft de gebruiker geen ondertitel gekozen, dan staat het eerste geforceerde
  **tekst**spoor in de taal van het audiospoor aan (default). `und` telt als geen taal (m3.md,
  afwijking 19), ook hier: een spoor of audio zonder taal past dan. Een geforceerd beeldspoor gaat
  nooit vanzelf aan (dat doet decide met forced_available).
- **Losse bestanden** (`.srt`, `.vtt`, `.ass`, `.ssa`) horen bij een video als hun naam ermee
  begint, met de vlaggen van Jellyfin (pair/match, voor de scanner van M2). `.sup`, `.sub` en
  `.idx` koppelen we niet (Keuze 5).
- **De controle (check_vtt):** elke VTT die van de worker komt, gaat er eerst door: hooguit 5 MB,
  20.000 cues en 2.000 tekens per cue, precies de vorm van subconv, alleen de tags en klassen
  van de lijst. Daarbij gaan de tijden van een ingebed spoor naar mediatijd (min de start van de
  bron: ffmpeg pakt uit met -copyts). Pas dan komt hij in de cache.
- **De cache:** `media/subs/<sha>/e<index>.vtt` per ingebed spoor, `file.vtt` voor een los
  bestand (per blob), en `<naam>.failed` als het niet lukte (blijvend, tot de blob weggaat).
- **Uitpakken:** de stap `subs` (achtergrond, alleen in een bibliotheek) pakt alle tekstsporen uit
  in één run (hooguit 8 per run). Buiten een bibliotheek pas als iemand de ondertitel opvraagt
  (`ensure_*`): dan pakt de worker meteen de tekstsporen van die blob uit, en zegt de route 202
  met `Retry-After: 5` tot hij klaar is. Is de worker druk (de achtergrond heeft één plaats), dan
  probeert de taak het elke 5 s opnieuw.
- **De pipes zijn van de app** (Keuze 11): per spoor maakt de app een pipe waar ffmpeg in schrijft
  en de worker uit leest, en een pipe voor de VTT terug. Zo maakt de worker zelf niets.

Geen databaseverbinding blijft open terwijl er op de worker gewacht wordt (regel 8).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
import shutil
import uuid
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import access
from app.config import Settings
from app.db import get_engine
from app.media import analysis, client, hooks, runtime, subconv
from app.media.types import IMAGE_SUBTITLES, TEXT_SUBTITLES, Probe, Stream
from app.media.wire import Code, Op
from app.media.worker.args import SubsArgs, SubTrack, demuxer_of
from app.models import MediaProbe, Node, Share, User

log = logging.getLogger("platform.media")

State = Literal["ready", "pending", "failed"]

MAX_VTT_BYTES = subconv.MAX_VTT_BYTES
MAX_CUES = subconv.MAX_CUES
MAX_CUE_CHARS = subconv.MAX_CUE_CHARS
MAX_LOOSE = subconv.MAX_LOOSE
PER_RUN = 8  # SubsArgs.tracks: hooguit 8 sporen per run
LOOSE_EXT = frozenset({"srt", "vtt", "ass", "ssa"})
# Wat de worker uitvoert per codec (§3.3): ASS en SSA in hun eigen vorm, WebVTT ook, de rest
# als SRT
_KIND = {"ass": "ass", "ssa": "ass", "webvtt": "webvtt"}
# Een run van de worker duurt hooguit 1800 s (server.TIMEOUT); de app wacht iets langer
RUN_LIMIT = 1800 + 60
LOOSE_LIMIT = 60.0
BUSY_PAUSE = 5.0  # de worker is druk: zo lang wachten en opnieuw (op verzoek)
BUSY_TRIES = 120  # ± 10 minuten; daarna begint het volgende verzoek opnieuw
# Fouten van de bron of van het spoor zelf: die worden een blijvend failed. Al het andere
# (de worker weg, ffmpeg ontbreekt, een andere versie) gaat door, en een volgende keer opnieuw
FILE_ERRORS = frozenset(
    {Code.UNREADABLE, Code.UNKNOWN_FORMAT, Code.TOO_LARGE, Code.FAILED, Code.TIMEOUT}
)

_SHA = re.compile(r"[0-9a-f]{64}")


# --- de controle van een VTT --------------------------------------------------------------------


class VttInvalid(ValueError):
    """De VTT van de worker houdt zich niet aan de grenzen of de vorm."""


_PERCENT = r"(?:100(?:\.0{1,3})?|\d{1,2}(?:\.\d{1,3})?)%"
_TIMING = re.compile(
    r"(\d{2}):([0-5]\d):([0-5]\d)\.(\d{3}) --> (\d{2}):([0-5]\d):([0-5]\d)\.(\d{3})"
    r"((?: [a-z]{4,8}:[^ ]{1,12}){0,4})"
)
_SETTINGS = {
    "line": re.compile(rf"-?\d{{1,3}}|{_PERCENT}"),
    "position": re.compile(_PERCENT),
    "size": re.compile(_PERCENT),
    "align": re.compile(r"start|center|end|left|right"),
}
_CLASS = "|".join(sorted(subconv.CLASSES))
_LINE = re.compile(
    r"(?:[^<>&\n\r\x00-\x08\x0b\x0c\x0e-\x1f\x7f]"
    r"|&(?:amp|lt|gt|nbsp|lrm|rlm);"
    r"|</?[biu]>|</c>"
    rf"|<c(?:\.(?:{_CLASS}))+>)+"
)


@dataclass(frozen=True)
class Checked:
    start_ms: int
    end_ms: int
    settings: str
    text: str


def _ms(groups: tuple[str, ...]) -> int:
    h, m, s, ms = (int(g) for g in groups)
    return ((h * 60 + m) * 60 + s) * 1000 + ms


def parse_vtt(data: bytes) -> list[Checked]:
    """Controleert een VTT van de worker (de vorm van subconv) en geeft zijn cues. VttInvalid
    als iets niet klopt: te groot, geen UTF-8, een andere vorm, een tag of klasse die niet op de
    lijst staat, te veel cues of tekens."""
    if len(data) > MAX_VTT_BYTES:
        raise VttInvalid(f"meer dan {MAX_VTT_BYTES} bytes")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise VttInvalid("geen UTF-8") from None
    if not text.startswith(subconv.HEADER):
        raise VttInvalid("geen kop WEBVTT")
    body = text[len(subconv.HEADER) :]
    if not body:
        return []
    if not body.startswith("\n") or not body.endswith("\n"):
        raise VttInvalid("geen lege regel tussen de cues")
    out: list[Checked] = []
    for block in body[1:-1].split("\n\n"):
        if len(out) >= MAX_CUES:
            raise VttInvalid(f"meer dan {MAX_CUES} cues")
        lines = block.split("\n")
        timing = _TIMING.fullmatch(lines[0])
        if timing is None or len(lines) < 2:
            raise VttInvalid("een cue zonder tijd of tekst")
        start, end = _ms(timing.groups()[0:4]), _ms(timing.groups()[4:8])
        if end <= start:
            raise VttInvalid("een cue eindigt niet na zijn begin")
        seen: set[str] = set()
        for token in timing.group(9).split():
            name, _, value = token.partition(":")
            pattern = _SETTINGS.get(name)
            if pattern is None or name in seen or pattern.fullmatch(value) is None:
                raise VttInvalid("een instelling die niet op de lijst staat")
            seen.add(name)
        payload = "\n".join(lines[1:])
        if len(payload) > MAX_CUE_CHARS:
            raise VttInvalid(f"een cue met meer dan {MAX_CUE_CHARS} tekens")
        if any(_LINE.fullmatch(line) is None for line in lines[1:]):
            raise VttInvalid("tekst met een tag, klasse of teken dat niet op de lijst staat")
        out.append(Checked(start, end, timing.group(9).strip(), payload))
    return out


def check_vtt(data: bytes, shift_ms: int = 0) -> bytes:
    """De controle van Keuze 29, en de tijden min shift_ms (de start van de bron bij een
    ingebed spoor): wat dan vóór 0 eindigt, valt weg. Geeft de VTT zoals de app hem bewaart."""
    blocks = [subconv.HEADER]
    for cue in parse_vtt(data):
        start, end = cue.start_ms - shift_ms, cue.end_ms - shift_ms
        if end <= 0:
            continue
        timing = f"{subconv.stamp(max(0, start))} --> {subconv.stamp(end)}"
        if cue.settings:
            timing += f" {cue.settings}"
        blocks.append(f"\n{timing}\n{cue.text}\n")
    return "".join(blocks).encode()


# --- talen ---------------------------------------------------------------------------------------

# ISO 639-2/T met wat er in bestandsnamen en containers voor staat (639-1, 639-2/B en namen). Twee
# letters die ook een vlag zijn (hi, cc) staan er niet in
_LANGUAGES: dict[str, tuple[str, ...]] = {
    "nld": ("nl", "dut", "dutch", "nederlands", "vlaams", "flemish"),
    "eng": ("en", "english", "engels"),
    "deu": ("de", "ger", "german", "deutsch", "duits"),
    "fra": ("fr", "fre", "french", "francais", "frans"),
    "spa": ("es", "spanish", "espanol", "spaans"),
    "ita": ("it", "italian", "italiano", "italiaans"),
    "por": ("pt", "portuguese", "portugues", "portugees"),
    "swe": ("sv", "swedish", "svenska", "zweeds"),
    "nor": ("no", "nb", "nob", "norwegian", "norsk", "noors"),
    "dan": ("da", "danish", "dansk", "deens"),
    "fin": ("fi", "finnish", "suomi", "fins"),
    "pol": ("pl", "polish", "polski", "pools"),
    "tur": ("tr", "turkish", "turkce", "turks"),
    "rus": ("ru", "russian", "russisch"),
    "ukr": ("uk", "ukrainian", "oekraiens"),
    "ara": ("ar", "arabic", "arabisch"),
    "heb": ("he", "hebrew", "hebreeuws"),
    "hin": ("hindi",),
    "jpn": ("ja", "japanese", "japans"),
    "zho": ("zh", "chi", "chinese", "chinees"),
    "kor": ("ko", "korean", "koreaans"),
    "ell": ("el", "gre", "greek", "grieks"),
    "ces": ("cs", "cze", "czech", "tsjechisch"),
    "slk": ("sk", "slo", "slovak", "slowaaks"),
    "hun": ("hu", "hungarian", "magyar", "hongaars"),
    "ron": ("ro", "rum", "romanian", "roemeens"),
    "bul": ("bg", "bulgarian", "bulgaars"),
    "hrv": ("hr", "croatian", "kroatisch"),
    "srp": ("sr", "serbian", "servisch"),
    "slv": ("sl", "slovenian", "sloveens"),
    "est": ("et", "estonian", "ests"),
    "lav": ("lv", "latvian", "lets"),
    "lit": ("lt", "lithuanian", "litouws"),
    "isl": ("is", "ice", "icelandic", "ijslands"),
    "cat": ("ca", "catalan", "catalaans"),
    "eus": ("eu", "baq", "basque", "baskisch"),
    "glg": ("gl", "galician", "galicisch"),
    "fry": ("fy", "frisian", "fries", "frysk"),
    "afr": ("af", "afrikaans"),
    "ind": ("id", "indonesian", "indonesisch"),
    "msa": ("ms", "may", "malay", "maleis"),
    "tha": ("th", "thai", "thais"),
    "vie": ("vi", "vietnamese", "vietnamees"),
    "fas": ("fa", "per", "persian", "farsi", "perzisch"),
}
LANGUAGE: dict[str, str] = {}
for _code, _names in _LANGUAGES.items():
    LANGUAGE[_code] = _code
    for _name in _names:
        LANGUAGE[_name] = _code


def lang_code(value: str | None) -> str | None:
    """Een taal als ISO 639-2/T (nld, eng), of None: `und` (onbepaald), leeg of onbekend tellen
    als geen taal. Een onbekende code van drie letters blijft zoals hij is."""
    if not value:
        return None
    word = value.strip().lower()
    if word in ("und", "zxx", "mis", "mul", ""):
        return None
    if word in LANGUAGE:
        return LANGUAGE[word]
    base = re.split(r"[-_]", word, maxsplit=1)[0]  # nl-BE, pt_BR
    if base in LANGUAGE:
        return LANGUAGE[base]
    return word if re.fullmatch(r"[a-z]{3}", word) else None


# --- losse bestanden koppelen (voor de scanner van M2) --------------------------------------------

FLAGS_FORCED = frozenset({"forced", "foreign"})
FLAGS_SDH = frozenset({"sdh", "hi", "cc"})
FLAGS_DEFAULT = frozenset({"default"})


def _stem(name: str) -> str:
    return name.rsplit(".", 1)[0] if "." in name else name


def match(video: str, name: str) -> dict[str, Any] | None:
    """Hoort de ondertitel `name` bij de video `video` (allebei een bestandsnaam)? Dan
    {name, kind, lang, forced, sdh, default}: `Film (2010).nl.forced.srt` bij
    `Film (2010).mkv` geeft lang nld en forced. Alleen .srt, .vtt, .ass en .ssa."""
    stem, dot, ext = name.rpartition(".")
    if not dot or ext.lower() not in LOOSE_EXT:
        return None
    video_stem = _stem(video).lower()
    if stem.lower() == video_stem:
        rest: list[str] = []
    elif stem.lower().startswith(video_stem + "."):
        rest = stem[len(video_stem) + 1 :].split(".")
    else:
        return None
    words = [w.strip().lower() for w in rest if w.strip()]
    lang = next((lang_code(w) for w in words if w in LANGUAGE), None)
    return {
        "name": name,
        "kind": ext.lower(),
        "lang": lang,
        "forced": any(w in FLAGS_FORCED for w in words),
        "sdh": any(w in FLAGS_SDH for w in words),
        "default": any(w in FLAGS_DEFAULT for w in words),
    }


def pair(videos: Iterable[str], names: Iterable[str]) -> dict[str, list[dict[str, Any]]]:
    """Per video de losse ondertitels in dezelfde map. Past een ondertitel bij twee video's
    (`Film.srt` en `Film.2.srt` bij `Film.mkv` en `Film.2.mkv`), dan bij de langste naam."""
    videos = list(videos)
    out: dict[str, list[dict[str, Any]]] = {v: [] for v in videos}
    for name in sorted(names):
        found = [(len(_stem(v)), v, m) for v in videos if (m := match(v, name)) is not None]
        if found:
            _, video, item = max(found, key=lambda f: f[0])
            out[video].append(item)
    return out


def loose_kind(name: str, data_head: bytes = b"") -> str | None:
    """De soort van een los bestand: uit de extensie, anders uit het begin van de inhoud."""
    ext = name.rpartition(".")[2].lower() if "." in name else ""
    if ext in LOOSE_EXT:
        return ext
    head = data_head.lstrip(b"\xef\xbb\xbf \t\r\n")[:64].lower()
    if head.startswith(b"webvtt"):
        return "vtt"
    if head.startswith(b"[script info]"):
        return "ass"
    return None


# --- de sporen voor play -------------------------------------------------------------------------


def _settings(settings: Settings | None) -> Settings:
    return settings if settings is not None else runtime.settings()


def subs_dir(settings: Settings, sha: str) -> Path:
    if _SHA.fullmatch(sha) is None:
        raise ValueError("geen sha")
    return settings.media_dir / "subs" / sha


def cached(settings: Settings, sha: str, name: str) -> Path:
    """De VTT in de cache: name is e<index> of file."""
    return subs_dir(settings, sha) / f"{name}.vtt"


def state(settings: Settings, sha: str, name: str) -> State:
    """ready als de VTT er is, failed als het blijvend niet lukte, anders pending."""
    try:
        folder = subs_dir(settings, sha)
    except ValueError:
        return "failed"
    if (folder / f"{name}.vtt").is_file():
        return "ready"
    if (folder / f"{name}.failed").exists():
        return "failed"
    return "pending"


def is_text(stream: Stream) -> bool:
    return stream.type == "subtitle" and (stream.codec or "") in TEXT_SUBTITLES


def is_image(stream: Stream) -> bool:
    return stream.type == "subtitle" and (stream.codec or "") in IMAGE_SUBTITLES


def _label(stream: Stream, n: int) -> str:
    title = " ".join((stream.title or "").split())[:100]
    lang = lang_code(stream.lang)
    return title or (lang or "").upper() or f"#{n}"


def _fits(sub: str | None, audio: str | None) -> bool:
    """Past de taal van een geforceerd spoor bij het audiospoor? Zonder taal (of und) past
    alles (m3.md, afwijking 19)."""
    return not sub or not audio or sub == audio


def tracks(
    probe: Probe,
    *,
    audio: Stream | None = None,
    chosen: str | None = None,
    settings: Settings | None = None,
) -> list[dict]:
    """De lijst subtitles van play: [{id, lang, label, forced, sdh, default, state}] voor de
    ingebedde sporen. default: het gekozen spoor (chosen), of zonder keuze het eerste
    geforceerde tekstspoor in de taal van het audiospoor (audio, anders het standaardspoor
    van decide). Additief (§3.3): play van M3 roept tracks(probe) aan; audio en chosen zijn een
    schakelpunt voor bij het samenvoegen."""
    from app.media import decide

    if audio is None:
        audio = decide.default_audio(probe)
    own = lang_code(audio.lang) if audio is not None else None
    sha = probe.blob_sha
    config = _settings(settings) if sha else None
    out: list[dict] = []
    auto: str | None = None
    for n, stream in enumerate(probe.subtitles(), 1):
        if not (is_text(stream) or is_image(stream)):
            continue
        sid = f"e{stream.index}"
        lang = lang_code(stream.lang)
        if is_image(stream):
            current: str = "burn"
        elif config is not None:
            current = state(config, sha, sid)
        else:
            current = "pending"
        if (
            chosen is None
            and auto is None
            and is_text(stream)
            and stream.forced
            and _fits(lang, own)
        ):
            auto = sid
        out.append(
            {
                "id": sid,
                "lang": lang,
                "label": _label(stream, n),
                "forced": stream.forced,
                "sdh": stream.hearing_impaired,
                "default": False,
                "state": current,
            }
        )
    wanted = chosen if chosen is not None else auto
    for item in out:
        item["default"] = item["id"] == wanted
    return out


# --- schrijven in de cache -----------------------------------------------------------------------


def _write_file(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}")
    try:
        tmp.write_bytes(data)
        os.replace(tmp, path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            tmp.unlink()


async def _store(settings: Settings, sha: str, name: str, vtt: bytes) -> None:
    path = cached(settings, sha, name)
    await asyncio.to_thread(_write_file, path, vtt)
    with contextlib.suppress(FileNotFoundError):
        await asyncio.to_thread((path.parent / f"{name}.failed").unlink)


async def _fail(settings: Settings, sha: str, name: str, why: str) -> None:
    log.info("ondertitel %s van %s… mislukt: %s", name, sha[:12], why[:200])
    path = subs_dir(settings, sha) / f"{name}.failed"
    await asyncio.to_thread(_write_file, path, why[:200].encode())


def _remove(settings: Settings, sha: str) -> None:
    with contextlib.suppress(ValueError):
        shutil.rmtree(subs_dir(settings, sha), ignore_errors=True)


async def discard(sha: str) -> None:
    """De blob gaat weg (hooks.on_discard): zijn ondertitels ook."""
    await asyncio.to_thread(_remove, runtime.settings(), sha)


hooks.register_discard(discard)


# --- lezen van een pipe --------------------------------------------------------------------------


async def drain(fd: int, limit: int) -> bytes | None:
    """Alles van een leeskant tot EOF, en dan dicht. None als er meer dan limit bytes komen: dan
    gaat de leeskant meteen dicht (de schrijver krijgt EPIPE)."""
    loop = asyncio.get_running_loop()
    chunks: list[bytes] = []
    size = 0
    try:
        os.set_blocking(fd, False)
        while True:
            try:
                data = os.read(fd, 65536)
            except BlockingIOError:
                ready = loop.create_future()
                loop.add_reader(fd, lambda f=ready: f.done() or f.set_result(None))
                try:
                    await ready
                finally:
                    loop.remove_reader(fd)
                continue
            if not data:
                return b"".join(chunks)
            size += len(data)
            if size > limit:
                return None
            chunks.append(data)
    finally:
        os.close(fd)


# --- uitpakken via de worker ---------------------------------------------------------------------


def text_streams(probe: Probe) -> list[Stream]:
    return [s for s in probe.subtitles() if is_text(s)]


def _batches(streams: list[Stream]) -> list[list[Stream]]:
    return [streams[i : i + PER_RUN] for i in range(0, len(streams), PER_RUN)]


async def _run(path: Path, demuxer: str, streams: list[Stream]) -> tuple[list[bytes | None], dict]:
    """Eén run van de worker voor deze sporen: per spoor de VTT (of None), en het antwoord. De
    app maakt per spoor twee pipes: ffmpeg schrijft in de eerste, de worker leest hem; de VTT
    komt terug over de tweede (een uitvoer van call)."""
    args = SubsArgs(
        demuxer=demuxer,  # type: ignore[arg-type]
        tracks=[SubTrack(index=s.index, kind=_KIND.get(s.codec or "", "srt")) for s in streams],  # type: ignore[arg-type]
    )
    pipes: list[int] = []
    try:
        for _ in streams:
            r, w = os.pipe2(os.O_CLOEXEC)
            pipes += [w, r]  # (ffmpeg_w, worker_r)
        job, reads = await client.call(Op.SUBS, args, source=path, outputs=len(streams), fds=pipes)
    finally:
        for fd in pipes:  # de worker heeft ze nu; onze kopieën dicht, anders geen EOF
            with contextlib.suppress(OSError):
                os.close(fd)
    async with job:
        # Alle leeskanten tegelijk: ffmpeg schrijft de sporen door elkaar. drain sluit elke
        # leeskant zelf, ook bij een fout of afbreken
        vtts = await asyncio.gather(*(drain(fd, MAX_VTT_BYTES) for fd in reads))
        data = await job.result()
    return list(vtts), data


async def extract(settings: Settings, sha: str, path: Path, probe: Probe) -> dict[str, State]:
    """Pakt alle tekstsporen uit die nog niet klaar zijn (hooguit 8 per run) en zet ze in de
    cache, gecontroleerd en in mediatijd. Geeft per spoor ready of failed. Een fout van de bron
    zelf wordt failed voor de sporen van die run; WorkerBusy, WorkerDown en de andere fouten
    gaan door (de stap maakt er Defer van, een verzoek probeert het later opnieuw)."""
    todo = [s for s in text_streams(probe) if state(settings, sha, f"e{s.index}") == "pending"]
    done: dict[str, State] = {}
    demuxer = demuxer_of(probe.container)
    for batch in _batches(todo):
        names = [f"e{s.index}" for s in batch]
        if demuxer is None:
            for name in names:
                await _fail(settings, sha, name, "onbekende container")
                done[name] = "failed"
            continue
        try:
            async with asyncio.timeout(RUN_LIMIT):
                vtts, data = await _run(path, demuxer, batch)
        except client.WorkerFailed as exc:
            if exc.code not in FILE_ERRORS:
                raise
            for name in names:
                await _fail(settings, sha, name, f"{exc.code}: {exc.detail}")
                done[name] = "failed"
            continue
        errors = {
            f"e{item.get('index')}": str(item.get("error"))
            for item in data.get("tracks") or []
            if isinstance(item, dict) and item.get("error")
        }
        for name, vtt in zip(names, vtts, strict=True):
            if name in errors or vtt is None:
                await _fail(settings, sha, name, errors.get(name) or "te groot")
                done[name] = "failed"
                continue
            try:
                checked = check_vtt(vtt, probe.start_ms)
            except VttInvalid as exc:
                await _fail(settings, sha, name, f"controle: {exc}")
                done[name] = "failed"
                continue
            await _store(settings, sha, name, checked)
            done[name] = "ready"
    return done


async def convert_loose(settings: Settings, sha: str, path: Path, kind: str) -> State:
    """Een los bestand (hooguit 5 MB) naar VTT via de worker, en in de cache als file.vtt."""
    try:
        size = (await asyncio.to_thread(path.stat)).st_size
    except FileNotFoundError:
        await _fail(settings, sha, "file", "de inhoud is weg")
        return "failed"
    if size > MAX_LOOSE:
        await _fail(settings, sha, "file", f"meer dan {MAX_LOOSE} bytes")
        return "failed"
    try:
        async with asyncio.timeout(LOOSE_LIMIT):
            job, (out,) = await client.call(
                Op.SUBS,
                SubsArgs(loose=kind),  # type: ignore[arg-type]
                source=path,
                outputs=1,
            )
            async with job:
                vtt = await drain(out, MAX_VTT_BYTES)
                await job.result()
    except client.WorkerFailed as exc:
        if exc.code not in FILE_ERRORS:
            raise
        await _fail(settings, sha, "file", f"{exc.code}: {exc.detail}")
        return "failed"
    if vtt is None:
        await _fail(settings, sha, "file", "te groot")
        return "failed"
    try:
        checked = check_vtt(vtt, 0)
    except VttInvalid as exc:
        await _fail(settings, sha, "file", f"controle: {exc}")
        return "failed"
    await _store(settings, sha, "file", checked)
    return "ready"


# --- op verzoek ----------------------------------------------------------------------------------

_running: dict[tuple[str, str], asyncio.Task] = {}


async def _patient[T](work: Callable[[], Awaitable[T]]) -> T:
    """Opnieuw zolang de worker druk is (de achtergrond heeft één plaats)."""
    for _ in range(BUSY_TRIES - 1):
        try:
            return await work()
        except client.WorkerBusy:
            await asyncio.sleep(BUSY_PAUSE)
    return await work()


def _task(key: tuple[str, str], work: Callable[[], Awaitable[Any]]) -> asyncio.Task:
    """Eén taak per blob en soort, gedeeld door elk verzoek. Ze loopt door als het verzoek
    stopt; de verbinding met de worker gaat dicht als de app stopt (de taak wordt dan
    afgebroken)."""
    task = _running.get(key)
    if task is None or task.done():
        task = asyncio.get_running_loop().create_task(_patient(work))
        _running[key] = task

        def forget(done: asyncio.Task, key: tuple[str, str] = key) -> None:
            if _running.get(key) is done:
                _running.pop(key, None)
            if not done.cancelled() and done.exception() is not None:
                log.info("ondertitels uitpakken: %s", done.exception().__class__.__name__)

        task.add_done_callback(forget)
    return task


def running(sha: str, what: str) -> asyncio.Task | None:
    task = _running.get((sha, what))
    return task if task is not None and not task.done() else None


def ensure_embedded(settings: Settings, sha: str, path: Path, probe: Probe, index: int):
    """Het ingebedde spoor e<index>: (staat, taak). Is het nog niet klaar, dan pakt een taak
    alle tekstsporen van deze blob uit (één keer de hele bron lezen)."""
    name = f"e{index}"
    now = state(settings, sha, name)
    if now != "pending":
        return now, None
    return now, _task((sha, "embedded"), lambda: extract(settings, sha, path, probe))


def ensure_loose(settings: Settings, sha: str, path: Path, kind: str):
    """Een los bestand: (staat, taak)."""
    now = state(settings, sha, "file")
    if now != "pending":
        return now, None
    return now, _task((sha, "file"), lambda: convert_loose(settings, sha, path, kind))


def reset() -> None:
    """Alleen voor tests: de taken vergeten (en stoppen)."""
    for task in _running.values():
        task.cancel()
    _running.clear()


# --- toegang tot een los bestand bij het serveren ------------------------------------------------


async def readable(settings: Settings, session: Any, entry: dict[str, Any]) -> Node | None:
    """Mag de kijker van deze sessie het losse bestand nu nog lezen? Dezelfde regels als bij
    play (media_play.allowed_subs): bij een publieke link moet het in de link liggen, anders
    is het van de gebruiker (niet in de prullenbak) of gedeeld met hem. En de node wijst nog
    naar de inhoud die play vastlegde. Eén korte databasesessie."""
    try:
        node_id = uuid.UUID(str(entry.get("node")))
    except ValueError:
        return None
    maker = async_sessionmaker(get_engine(settings), expire_on_commit=False)
    async with maker() as db:
        node = await db.get(Node, node_id, populate_existing=True)
        if node is None or node.kind != "file" or not node.blob_id:
            return None
        if node.blob_id != entry.get("sha"):
            return None
        if getattr(session, "loadtest", False):
            return None
        if session.link_id is not None:
            return node if await _in_link(db, session.link_id, node_id) else None
        user = await db.get(User, session.user_id, populate_existing=True)
        if user is None or user.disabled_at is not None:
            return None
        if node.owner_id == user.id:
            return None if await access.trashed(db, node_id) else node
        return node if node_id in await access.resolve(db, user.id, [node_id]) else None


async def _in_link(db: AsyncSession, link_id: uuid.UUID, node_id: uuid.UUID) -> bool:
    from app.routers import public

    share = await db.get(Share, link_id, populate_existing=True)
    if share is None or share.token is None or public.expired(share):
        return False
    root = await db.get(Node, share.node_id, populate_existing=True)
    if root is None:
        return False
    return node_id in await public.inside(db, public.Link(share, root), [node_id])


# --- de stap subs (achtergrond, alleen in een bibliotheek) ----------------------------------------


async def _set_state(maker: async_sessionmaker[AsyncSession], sha: str, value: str) -> None:
    async with maker() as session:
        await session.execute(
            update(MediaProbe).where(MediaProbe.blob_sha == sha).values(subs_state=value)
        )
        await session.commit()


def _applies(probe: Probe, kind: str | None) -> bool:
    return kind in ("movies", "shows", "videos") and bool(text_streams(probe))


async def _step(ctx: analysis.StepContext) -> None:
    probe = ctx.probe
    assert probe is not None
    names = [f"e{s.index}" for s in text_streams(probe)]
    if probe.subs_state in ("ready", "failed") and all(
        state(ctx.settings, ctx.sha, n) != "pending" for n in names
    ):
        return
    await _set_state(ctx.maker(), ctx.sha, "pending")
    await extract(ctx.settings, ctx.sha, ctx.path, probe)
    states = [state(ctx.settings, ctx.sha, n) for n in names]
    final = "ready" if "ready" in states else "failed"
    await _set_state(ctx.maker(), ctx.sha, final)


analysis.step("subs", 20, _applies)(_step)
