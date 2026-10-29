"""De ondertitels (Keuze 29, M5): app/media/subtitles.py en de route subs/{id}.vtt.

- de controle van een VTT van de worker (check_vtt): de vorm van subconv, de grenzen (5 MB,
  20.000 cues, 2.000 tekens per cue), de lijst van tags en klassen, en de tijden min de start
  van de bron;
- talen (`und` is geen taal), losse bestanden koppelen (pair/match, voor de scanner van M2), en
  het geforceerde spoor dat vanzelf aangaat (alleen tekst, in de taal van het audiospoor);
- uitpakken via de nep-worker: de pipes zijn van de app (1 + 3N fd's, allemaal FIFO's), een
  spoor dat niet lukt, de worker die druk is, een VTT die te groot of vreemd is;
- de route via een echte sessie: 202 met Retry-After: 5 zolang hij gemaakt wordt, 200 text/vtt
  met nosniff, 409 als het blijvend niet lukte, 404 voor een beeldspoor; een los bestand x<i>
  alleen zolang de kijker het nog mag lezen, en de staat in de sessie van pending naar ready;
- de stap subs (subs_state pending, dan ready) en on_discard die de cache weggooit;
- marker media: de echte worker op subs_text.mkv, hevc10_hdr10.mkv, multi_audio.mkv en de losse
  nl.srt (CP1252), en.ass en bom.srt.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import uuid
from pathlib import Path

import pytest
from sqlalchemy import select

from app.config import MediaConfig, Settings
from app.main import app
from app.media import analysis, client, hooks, runtime, sessions, subconv, subtitles
from app.media.state import MediaPolicy, MediaState
from app.media.types import Probe, Stream
from app.media.wire import Code, Op
from app.media.worker.args import SubsArgs
from app.media.worker.ops import subs as ops_subs
from app.models import MediaProbe
from app.routers import media_subs, public_media
from tests.media.fake_worker import Job, OpError, running
from tests.media.media_contract import failed
from tests.test_files import put, root
from tests.test_media_decide import probe as recorded
from tests.test_media_sessions import (
    blob_of,
    library_item,
    maker,
    play,
    session_of,
    video,
)
from tests.test_shares import person, share_with

STREAM = "/api/v1/stream"
SRT = b"1\n00:00:01,000 --> 00:00:02,500\nHallo, dit is de eerste regel.\n"


# --- de omgeving ----------------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def media():
    """Media aan, de worker gezond, lege tabel; geen taken van een vorige test."""
    sessions.reset()
    public_media.plays.reset()
    subtitles.reset()
    before = {name: getattr(app.state, name, None) for name in ("media", "media_policy")}
    state = app.state.media = MediaState(enabled=True, worker="ok")
    app.state.media_policy = MediaPolicy(MediaConfig())
    yield state
    subtitles.reset()
    for name, value in before.items():
        setattr(app.state, name, value)
    sessions.reset()
    public_media.plays.reset()


@pytest.fixture
async def fake(tmp_path):
    async with running(tmp_path / "w" / "nep.sock") as worker:
        yield worker


def vtt(*cues: tuple[int, int, str], settings: str = "") -> bytes:
    out = [subconv.HEADER]
    for start, end, text in cues:
        timing = f"{subconv.stamp(start)} --> {subconv.stamp(end)}"
        out.append(f"\n{timing}{' ' + settings if settings else ''}\n{text}\n")
    return "".join(out).encode()


class Subs:
    """Het antwoord van de nep op SUBS: per spoor een VTT (zoals de echte worker hem met subconv
    maakt), of een fout. gate: pas antwoorden als de test het zegt. Een los bestand zet hij
    echt om (subconv, zoals ops/subs.py)."""

    def __init__(self, vtts: dict[int, bytes] | None = None, errors: dict[int, str] | None = None):
        self.vtts = vtts or {}
        self.errors = errors or {}
        self.gate: asyncio.Event | None = None
        self.jobs: list[Job] = []
        self.modes: list[list[str]] = []

    async def __call__(self, job: Job) -> dict:
        args = job.args
        assert isinstance(args, SubsArgs)
        self.jobs.append(job)
        self.modes.append([_kind(fd) for fd in job.outputs])
        if self.gate is not None:
            await self.gate.wait()
        if args.loose:
            data = ops_subs.read_file(job.inputs[0], subconv.MAX_LOOSE)
            assert data is not None
            out = subconv.to_vtt(data, args.loose)
            await ops_subs.send(job.outputs[0], out)
            ops_subs.close_fd(job.outputs[0])
            return {"cues": out.count(b" --> "), "bytes": len(out)}
        n = len(args.tracks)
        assert len(job.outputs) == 3 * n
        result = []
        for k, track in enumerate(args.tracks):
            fd = job.outputs[2 * n + k]
            if track.index in self.errors:
                result.append({"index": track.index, "error": self.errors[track.index]})
            else:
                data = self.vtts[track.index]
                await ops_subs.send(fd, data)
                result.append({"index": track.index, "cues": 1, "bytes": len(data)})
            ops_subs.close_fd(fd)
        return {"tracks": result}


def _kind(fd: int) -> str:
    import stat

    return "fifo" if stat.S_ISFIFO(os.fstat(fd).st_mode) else "other"


def text_probe(start_ms: int = -21) -> Probe:
    """h264_aac.mp4 met twee tekstsporen (mov_text) en een beeldspoor: zo speelt hij direct."""
    p = recorded("h264_aac.mp4", start_ms=start_ms)
    p.streams = [
        *p.streams,
        Stream(index=2, type="subtitle", codec="mov_text", lang="nld"),
        Stream(index=3, type="subtitle", codec="mov_text", lang="eng", forced=True),
        Stream(index=4, type="subtitle", codec="hdmv_pgs_subtitle", lang="nld"),
    ]
    return p


async def text_video(client, headers, settings, name: str = "film.mp4") -> dict:
    node = await video(client, headers, settings, name)
    await analysis.save_probe(maker(settings), node["sha"], text_probe())
    return node


def sha_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


async def get_vtt(client, sid: str, track: str):
    return await client.get(f"{STREAM}/{sid}/subs/{track}.vtt")


# --- de controle ----------------------------------------------------------------------------------


def test_check_vtt_schuift_naar_mediatijd() -> None:
    data = vtt(
        (0, 10, "weg"), (5, 1500, "half"), (2000, 3000, "<b>vet</b> &amp; <c.yellow>geel</c>")
    )
    out = subtitles.check_vtt(data, shift_ms=20)
    assert out == vtt((0, 1480, "half"), (1980, 2980, "<b>vet</b> &amp; <c.yellow>geel</c>"))
    # een negatieve start (de priming van de audio) schuift alles naar later
    assert subtitles.check_vtt(vtt((1000, 2000, "x")), -21) == vtt((1021, 2021, "x"))
    assert subtitles.check_vtt(vtt((1000, 2000, "x")), 0) == vtt((1000, 2000, "x"))
    assert subtitles.check_vtt(subconv.HEADER.encode()) == subconv.HEADER.encode()


def test_check_vtt_houdt_de_instellingen() -> None:
    data = vtt((0, 1000, "boven"), settings="line:0 align:left")
    assert subtitles.check_vtt(data) == data
    assert subtitles.parse_vtt(data)[0].settings == "line:0 align:left"


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"WEBVTT",
        b"WEBVTT\n\n00:00:01.000 --> 00:00:02.000\n",  # geen tekst
        b"WEBVTT\n\n00:00:02.000 --> 00:00:01.000\nterug\n",
        b"WEBVTT\n\n00:00:01.000 --> 00:00:01.000\nnul\n",
        b"WEBVTT\n\n00:00:01.000 --> 00:00:02.000 vertical:rl\nx\n",
        b"WEBVTT\n\n00:00:01.000 --> 00:00:02.000 line:0 line:0\nx\n",
        b"WEBVTT\n\n00:00:01.000 --> 00:00:02.000 position:200%\nx\n",
        b"WEBVTT\n\n00:00:01.000 --> 00:00:02.000\n<script>alert(1)</script>\n",
        b"WEBVTT\n\n00:00:01.000 --> 00:00:02.000\n<v Bob>hoi\n",
        b"WEBVTT\n\n00:00:01.000 --> 00:00:02.000\n<c.evil>x</c>\n",
        b"WEBVTT\n\n00:00:01.000 --> 00:00:02.000\n<00:00:01.500>karaoke\n",
        b"WEBVTT\n\n00:00:01.000 --> 00:00:02.000\n&quot;x&quot;\n",
        b"WEBVTT\n\n00:00:01.000 --> 00:00:02.000\na & b\n",
        b"WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nx\x01y\n",
        b"WEBVTT\n\nNOTE commentaar\n\n00:00:01.000 --> 00:00:02.000\nx\n",
        b"WEBVTT\n\n1\n00:00:01.000 --> 00:00:02.000\nmet een id\n",
        b"WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nx\n\n\n00:00:03.000 --> 00:00:04.000\ny\n",
        b"WEBVTT\n\n00:00:01.000 --> 00:00:02.000\n\xff\xfe\n",
        b"WEBVTT\n\n00:00:01,000 --> 00:00:02,000\nkomma\n",
        b"WEBVTT\n\n00:01.000 --> 00:02.000\nkort\n",
        b"WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nx",  # geen eind
        b"STYLE\n\n00:00:01.000 --> 00:00:02.000\nx\n",
    ],
)
def test_check_vtt_weigert(data: bytes) -> None:
    with pytest.raises(subtitles.VttInvalid):
        subtitles.check_vtt(data)


def test_de_grenzen() -> None:
    # 2.000 tekens per cue: precies mag, één meer niet
    assert subtitles.parse_vtt(vtt((0, 1000, "x" * subtitles.MAX_CUE_CHARS)))
    with pytest.raises(subtitles.VttInvalid, match="tekens"):
        subtitles.parse_vtt(vtt((0, 1000, "x" * (subtitles.MAX_CUE_CHARS + 1))))
    # 20.000 cues
    many = [(i * 10, i * 10 + 5, "x") for i in range(subtitles.MAX_CUES)]
    assert len(subtitles.parse_vtt(vtt(*many))) == subtitles.MAX_CUES
    with pytest.raises(subtitles.VttInvalid, match="cues"):
        subtitles.parse_vtt(vtt(*many, (10**7, 10**7 + 5, "x")))
    # 5 MB
    big = vtt(*((i * 10, i * 10 + 5, "x" * 300) for i in range(18_000)))
    assert len(big) > subtitles.MAX_VTT_BYTES
    with pytest.raises(subtitles.VttInvalid, match="bytes"):
        subtitles.parse_vtt(big)


def test_wat_subconv_maakt_komt_door_de_controle() -> None:
    from tests.test_media_subconv import GOLDEN

    for name, kind, data, _ in GOLDEN:
        out = subconv.to_vtt(data, kind)
        assert subtitles.check_vtt(out) == out, name


# --- talen en losse bestanden ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "code"),
    [
        ("nl", "nld"),
        ("dut", "nld"),
        ("NLD", "nld"),
        ("Nederlands", "nld"),
        ("nl-BE", "nld"),
        ("pt_BR", "por"),
        ("en", "eng"),
        ("ger", "deu"),
        ("und", None),
        ("zxx", None),
        ("mul", None),
        ("", None),
        (None, None),
        ("xyz", "xyz"),
        ("klingon", None),
    ],
)
def test_lang_code(value: str | None, code: str | None) -> None:
    assert subtitles.lang_code(value) == code


def test_match_en_de_vlaggen() -> None:
    video = "Film (2010).mkv"
    assert subtitles.match(video, "Film (2010).srt") == {
        "name": "Film (2010).srt",
        "kind": "srt",
        "lang": None,
        "forced": False,
        "sdh": False,
        "default": False,
    }
    found = subtitles.match(video, "Film (2010).nl.forced.srt")
    assert found is not None and found["lang"] == "nld" and found["forced"]
    found = subtitles.match(video, "film (2010).EN.SDH.default.ASS")
    assert found is not None
    assert (found["lang"], found["sdh"], found["default"], found["kind"]) == (
        "eng",
        True,
        True,
        "ass",
    )
    found = subtitles.match(video, "Film (2010).foreign.vtt")
    assert found is not None and found["forced"] and found["lang"] is None
    found = subtitles.match(video, "Film (2010).und.ssa")
    assert found is not None and found["lang"] is None
    for name in (
        "Film (2010).sup",
        "Film (2010).nl.sub",
        "Film (2010).idx",
        "Film (2010).txt",
        "Andere film.srt",
        "Film (2010)2.srt",
        "Film.srt",
        "Film (2010)",
    ):
        assert subtitles.match(video, name) is None, name


def test_pair_kiest_de_langste_naam() -> None:
    videos = ["Film.mkv", "Film.2.mkv", "Serie S01E01.mp4"]
    names = [
        "Film.nl.srt",
        "Film.2.nl.srt",
        "Film.2.srt",
        "Serie S01E01.en.ass",
        "Los.srt",
        "Film.sup",
    ]
    found = subtitles.pair(videos, names)
    assert [s["name"] for s in found["Film.mkv"]] == ["Film.nl.srt"]
    assert [s["name"] for s in found["Film.2.mkv"]] == ["Film.2.nl.srt", "Film.2.srt"]
    assert [(s["name"], s["lang"]) for s in found["Serie S01E01.mp4"]] == [
        ("Serie S01E01.en.ass", "eng")
    ]


def test_loose_kind() -> None:
    assert subtitles.loose_kind("a.SRT") == "srt"
    assert subtitles.loose_kind("a.ssa") == "ssa"
    assert subtitles.loose_kind("a.txt", b"\xef\xbb\xbfWEBVTT\n\n") == "vtt"
    assert subtitles.loose_kind("a", b"\r\n[Script Info]\n") == "ass"
    assert subtitles.loose_kind("a.sup", b"PG") is None
    assert subtitles.loose_kind("a.idx") is None


# --- de sporen voor play --------------------------------------------------------------------------


def test_tracks_met_staat_en_beeldsporen(tmp_path) -> None:
    settings = Settings(data_dir=tmp_path, web_dist=tmp_path / "web", jwt_secret="t" * 32)
    p = text_probe()
    p.blob_sha = "a" * 64
    found = subtitles.tracks(p, settings=settings)
    assert [(t["id"], t["lang"], t["state"], t["forced"]) for t in found] == [
        ("e2", "nld", "pending", False),
        ("e3", "eng", "pending", True),
        ("e4", "nld", "burn", False),
    ]
    subtitles._write_file(subtitles.cached(settings, p.blob_sha, "e2"), b"WEBVTT\n")
    subtitles._write_file(subtitles.subs_dir(settings, p.blob_sha) / "e3.failed", b"kapot")
    found = subtitles.tracks(p, settings=settings)
    assert [t["state"] for t in found] == ["ready", "failed", "burn"]
    assert [t["label"] for t in found] == ["NLD", "ENG", "NLD"]
    # 608 en teletekst laten we weg
    p.streams.append(Stream(index=5, type="subtitle", codec="eia_608"))
    assert len(subtitles.tracks(p, settings=settings)) == 3


def test_geforceerd_gaat_vanzelf_aan_in_de_taal_van_de_audio() -> None:
    p = recorded("multi_audio.mkv")
    nld, eng = (s for s in p.streams if s.type == "audio")
    assert [t["default"] for t in subtitles.tracks(p)] == [True]  # standaard audio is nld
    assert [t["default"] for t in subtitles.tracks(p, audio=nld)] == [True]
    assert [t["default"] for t in subtitles.tracks(p, audio=eng)] == [False]
    # een geforceerd spoor zonder taal (und) past bij elke audio, ook andersom
    sub = next(s for s in p.streams if s.type == "subtitle")
    sub.lang = "und"
    assert [t["default"] for t in subtitles.tracks(p, audio=eng)] == [True]
    sub.lang, eng.lang = "nld", "und"
    assert [t["default"] for t in subtitles.tracks(p, audio=eng)] == [True]
    # een keuze wint, ook "geen" (een ander spoor)
    assert [t["default"] for t in subtitles.tracks(p, chosen="x0")] == [False]
    assert [t["default"] for t in subtitles.tracks(p, chosen="e3")] == [True]


def test_een_geforceerd_beeldspoor_gaat_nooit_vanzelf_aan() -> None:
    p = recorded("pgs.mkv")
    for s in p.streams:
        if s.type == "subtitle":
            s.forced = True
    found = subtitles.tracks(p)
    assert [(t["state"], t["forced"], t["default"]) for t in found] == [("burn", True, False)]
    # het eerste geforceerde tekstspoor, niet het beeldspoor ervoor
    p.streams.append(Stream(index=2, type="subtitle", codec="subrip", lang="nld", forced=True))
    p.streams.append(Stream(index=3, type="subtitle", codec="subrip", lang="nld", forced=True))
    assert [t["default"] for t in subtitles.tracks(p)] == [False, True, False]


def test_de_cache_en_een_vreemde_sha(tmp_path) -> None:
    settings = Settings(data_dir=tmp_path, web_dist=tmp_path / "web", jwt_secret="t" * 32)
    for sha in ("../../etc", "A" * 64, "a" * 63, ""):
        with pytest.raises(ValueError):
            subtitles.subs_dir(settings, sha)
        assert subtitles.state(settings, sha, "e1") == "failed"
    assert subtitles.state(settings, "b" * 64, "e1") == "pending"


# --- uitpakken via de nep-worker ------------------------------------------------------------------


def _fds() -> int:
    return len(os.listdir("/proc/self/fd"))


@pytest.mark.anyio
async def test_extract_pakt_alles_in_een_run_met_de_pipes_van_de_app(settings, fake, tmp_path):
    source = tmp_path / "bron.mp4"
    source.write_bytes(b"x" * 1000)
    sha = sha_of(b"bron")
    handler = Subs({2: vtt((1000, 2500, "Hallo")), 3: vtt((0, 30, "weg"), (2000, 3000, "Bord"))})
    fake.on(Op.SUBS, handler)
    before = _fds()
    done = await subtitles.extract(settings, sha, source, text_probe(start_ms=-21))
    assert done == {"e2": "ready", "e3": "ready"}
    assert _fds() == before  # geen fd van de app blijft open
    # één run, 1 + 3N fd's: de bron, per spoor twee pipes, per spoor een pipe terug
    assert [(r.op, r.fds) for r in fake.requests] == [("subs", 1 + 3 * 2)]
    assert fake.requests[0].args["tracks"] == [
        {"index": 2, "kind": "srt"},
        {"index": 3, "kind": "srt"},
    ]
    assert handler.modes == [["fifo"] * 6]
    # de tijden in mediatijd: + 21 ms
    assert subtitles.cached(settings, sha, "e2").read_bytes() == vtt((1021, 2521, "Hallo"))
    assert subtitles.cached(settings, sha, "e3").read_bytes() == vtt(
        (21, 51, "weg"), (2021, 3021, "Bord")
    )
    # klaar is klaar: geen tweede run
    assert await subtitles.extract(settings, sha, source, text_probe()) == {}
    assert len(fake.requests) == 1


@pytest.mark.anyio
async def test_extract_een_spoor_mislukt_de_rest_niet(settings, fake, tmp_path):
    source = tmp_path / "bron.mp4"
    source.write_bytes(b"x")
    sha = sha_of(b"half")
    fake.on(
        Op.SUBS, Subs({2: b"WEBVTT\n\n00:00:01.000 --> 00:00:02.000\n<script>\n"}, {3: "kapot"})
    )
    done = await subtitles.extract(settings, sha, source, text_probe())
    assert done == {"e2": "failed", "e3": "failed"}
    folder = subtitles.subs_dir(settings, sha)
    assert sorted(p.name for p in folder.iterdir()) == ["e2.failed", "e3.failed"]
    assert b"controle" in (folder / "e2.failed").read_bytes()
    assert (folder / "e3.failed").read_bytes() == b"kapot"
    # failed blijft: geen nieuwe run
    assert await subtitles.extract(settings, sha, source, text_probe()) == {}
    assert len(fake.requests) == 1


@pytest.mark.anyio
async def test_extract_een_vtt_boven_5_mb(settings, fake, tmp_path):
    source = tmp_path / "bron.mp4"
    source.write_bytes(b"x")
    sha = sha_of(b"groot")
    big = vtt(*((i * 10, i * 10 + 5, "x" * 300) for i in range(18_000)))
    fake.on(Op.SUBS, Subs({2: big, 3: vtt((0, 1000, "klein"))}))
    before = _fds()
    done = await asyncio.wait_for(subtitles.extract(settings, sha, source, text_probe(0)), 30)
    assert done == {"e2": "failed", "e3": "ready"}
    assert (subtitles.subs_dir(settings, sha) / "e2.failed").read_bytes() == b"te groot"
    assert _fds() == before


@pytest.mark.anyio
async def test_extract_een_fout_van_de_bron_of_van_de_worker(settings, fake, tmp_path):
    source = tmp_path / "bron.mp4"
    source.write_bytes(b"x")
    # geen antwoord voor SUBS: not_built (een worker van een andere versie): geen failed
    with pytest.raises(client.WorkerFailed):
        await subtitles.extract(settings, sha_of(b"oud"), source, text_probe())
    assert subtitles.state(settings, sha_of(b"oud"), "e2") == "pending"

    async def broken(job: Job) -> dict:
        raise OpError(Code.UNREADABLE, "kapotte bron")

    fake.on(Op.SUBS, broken)
    sha = sha_of(b"kapot")
    done = await subtitles.extract(settings, sha, source, text_probe())
    assert done == {"e2": "failed", "e3": "failed"}
    assert subtitles.state(settings, sha, "e3") == "failed"
    # een onbekende container: failed zonder de worker
    count = len(fake.requests)
    p = text_probe()
    p.container = "iets"
    assert await subtitles.extract(settings, sha_of(b"vreemd"), source, p) == {
        "e2": "failed",
        "e3": "failed",
    }
    assert len(fake.requests) == count


@pytest.mark.anyio
async def test_extract_hooguit_8_per_run(settings, fake, tmp_path):
    source = tmp_path / "bron.mkv"
    source.write_bytes(b"x")
    p = recorded("subs_text.mkv")
    p.streams = [
        p.streams[0],
        *(Stream(index=i, type="subtitle", codec="ass", lang="nld") for i in range(1, 11)),
    ]
    fake.on(Op.SUBS, Subs({i: vtt((0, 1000, f"spoor {i}")) for i in range(1, 11)}))
    done = await subtitles.extract(settings, sha_of(b"tien"), source, p)
    assert list(done.values()) == ["ready"] * 10
    assert [r.fds for r in fake.requests] == [1 + 3 * 8, 1 + 3 * 2]
    assert all(t["kind"] == "ass" for t in fake.requests[0].args["tracks"])


@pytest.mark.anyio
async def test_op_verzoek_opnieuw_als_de_worker_druk_is(settings, fake, tmp_path, monkeypatch):
    monkeypatch.setattr(subtitles, "BUSY_PAUSE", 0.01)
    source = tmp_path / "bron.mp4"
    source.write_bytes(b"x")
    sha = sha_of(b"druk")
    fake.on(Op.SUBS, Subs({2: vtt((0, 1000, "a")), 3: vtt((0, 1000, "b"))}))
    fake.fault("busy", op=Op.SUBS, times=2)
    state, task = subtitles.ensure_embedded(settings, sha, source, text_probe(), 2)
    assert state == "pending" and task is not None
    # een tweede verzoek deelt de taak
    assert subtitles.ensure_embedded(settings, sha, source, text_probe(), 3)[1] is task
    await asyncio.wait_for(task, 10)
    assert len(fake.requests) == 3
    assert subtitles.ensure_embedded(settings, sha, source, text_probe(), 2) == ("ready", None)


@pytest.mark.anyio
async def test_een_los_bestand(settings, fake, tmp_path):
    fake.on(Op.SUBS, Subs())
    cp1252 = tmp_path / "nl.srt"
    cp1252.write_bytes("1\r\n00:00:01,000 --> 00:00:02,000\r\nCafé, één\r\n".encode("cp1252"))
    sha = sha_of(b"los")
    assert await subtitles.convert_loose(settings, sha, cp1252, "srt") == "ready"
    assert subtitles.cached(settings, sha, "file").read_bytes() == vtt((1000, 2000, "Café, één"))
    assert fake.requests[-1].args["loose"] == "srt" and fake.requests[-1].fds == 2
    # boven 5 MB: failed zonder de worker
    big = tmp_path / "groot.srt"
    big.write_bytes(b"x" * (subtitles.MAX_LOOSE + 1))
    count = len(fake.requests)
    assert await subtitles.convert_loose(settings, sha_of(b"groot"), big, "srt") == "failed"
    assert len(fake.requests) == count
    # de inhoud is weg
    assert (
        await subtitles.convert_loose(settings, sha_of(b"weg"), tmp_path / "x", "srt") == "failed"
    )


@pytest.mark.anyio
async def test_on_discard_gooit_de_cache_weg(settings, monkeypatch):
    monkeypatch.setattr(runtime, "_settings", settings)
    sha = sha_of(b"weg")
    subtitles._write_file(subtitles.cached(settings, sha, "e1"), b"WEBVTT\n")
    keep = sha_of(b"blijft")
    subtitles._write_file(subtitles.cached(settings, keep, "file"), b"WEBVTT\n")
    assert subtitles.discard in hooks._discard
    await hooks.on_discard(sha)
    assert not subtitles.subs_dir(settings, sha).exists()
    assert subtitles.state(settings, keep, "file") == "ready"


@pytest.mark.anyio
async def test_de_stap_subs(client, admin, settings, fake, tmp_path):
    headers, _ = admin
    node = await video(client, headers, settings, "stap.mkv", "subs_text.mkv")
    sha = node["sha"]  # media_probe hoort bij een echte blob
    source = tmp_path / "bron.mkv"
    source.write_bytes(b"x")
    p = recorded("subs_text.mkv")
    await analysis.save_probe(maker(settings), sha, p)
    loaded = await analysis.load(maker(settings), sha)
    assert loaded is not None and loaded.subs_state == "none"
    assert subtitles._applies(loaded, "movies") and subtitles._applies(loaded, "videos")
    assert not subtitles._applies(loaded, "music") and not subtitles._applies(loaded, None)
    assert not subtitles._applies(recorded("pgs.mkv"), "movies")  # alleen een beeldspoor
    step = next(s for s in analysis.steps() if s.name == "subs")
    assert step.order == 20
    seen: list[str] = []
    handler = Subs({1: vtt((0, 1000, "nl")), 2: vtt((0, 1000, "en"))})

    async def watch(job: Job) -> dict:
        async with maker(settings)() as db:
            row = await db.scalar(select(MediaProbe.subs_state).where(MediaProbe.blob_sha == sha))
        seen.append(str(row))
        return await handler(job)

    fake.on(Op.SUBS, watch)
    ctx = analysis.StepContext(settings, sha, source, None, "movies", loaded)
    await step.run(ctx)
    assert seen == ["pending"]
    assert (await analysis.load(maker(settings), sha)).subs_state == "ready"  # type: ignore[union-attr]
    # nog een keer: niets te doen
    await step.run(
        analysis.StepContext(
            settings, sha, source, None, "movies", await analysis.load(maker(settings), sha)
        )
    )
    assert len(fake.requests) == 1


# --- de route -------------------------------------------------------------------------------------


@pytest.mark.anyio
async def test_de_route_202_dan_200(client, admin, settings, fake, monkeypatch):
    monkeypatch.setattr(media_subs, "FAST_WAIT", 0.05)
    # play (M3) vraagt subtitles.tracks(probe) zonder settings: dan die van runtime
    monkeypatch.setattr(runtime, "_settings", settings)
    headers, _ = admin
    node = await text_video(client, headers, settings)
    handler = Subs({2: vtt((1000, 2000, "Hallo")), 3: vtt((0, 1000, "Bord"))})
    handler.gate = asyncio.Event()
    fake.on(Op.SUBS, handler)
    out = await play(client, headers, node["id"])
    assert out["mode"] == "direct"
    subs = out["tracks"]["subtitles"]
    # e3 is geforceerd (eng) en de audio is und (geen taal): hij past, dus hij staat aan
    assert [(t["id"], t["state"], t["default"]) for t in subs] == [
        ("e2", "pending", False),
        ("e3", "pending", True),
        ("e4", "burn", False),
    ]
    sid = out["sid"]
    try:
        for track in ("e2", "e3", "e2"):
            r = await get_vtt(client, sid, track)
            assert r.status_code == 202, r.text
            assert r.json() == {"state": "pending"}
            assert r.headers["retry-after"] == "5"
            assert r.headers["cache-control"] == "no-store"
            assert r.headers["x-content-type-options"] == "nosniff"
        assert len(fake.requests) == 1  # één taak voor alle verzoeken
    finally:
        handler.gate.set()
    task = subtitles.running(node["sha"], "embedded")
    assert task is not None
    await asyncio.wait_for(task, 10)
    r = await get_vtt(client, sid, "e2")
    assert r.status_code == 200
    assert r.content == vtt((1021, 2021, "Hallo"))
    assert r.headers["content-type"] == "text/vtt; charset=utf-8"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["cache-control"] == "private, max-age=3600"
    assert r.headers["cross-origin-resource-policy"] == "same-origin"
    # play ziet de staat nu
    again = await play(client, headers, node["id"])
    assert [t["state"] for t in again["tracks"]["subtitles"]] == ["ready", "ready", "burn"]


@pytest.mark.anyio
async def test_de_route_meteen_klaar_404_en_409(client, admin, settings, fake):
    headers, _ = admin
    node = await text_video(client, headers, settings)
    fake.on(Op.SUBS, Subs({2: vtt((1000, 2000, "Hallo"))}, {3: "kapot"}))
    sid = (await play(client, headers, node["id"]))["sid"]
    r = await get_vtt(client, sid, "e2")  # de taak is binnen FAST_WAIT klaar
    assert r.status_code == 200 and r.content == vtt((1021, 2021, "Hallo"))
    r = await get_vtt(client, sid, "e3")
    failed(r, 409, "media_unavailable", "subtitle")
    for track in ("e4", "e0", "e1", "e9", "x0"):  # beeldspoor, video, audio, niet, geen lijst
        failed(await get_vtt(client, sid, track), 404, "not_found")
    r = await get_vtt(client, sid, "e2000")
    assert r.status_code == 422
    assert len(fake.requests) == 1
    # een onbekende sid: 410 zoals elke route onder /stream/
    r = await get_vtt(client, "film-mp4." + "A" * 32, "e2")
    assert r.status_code == 410


@pytest.mark.anyio
async def test_de_route_zonder_worker(client, admin, settings, tmp_path):
    headers, _ = admin
    node = await text_video(client, headers, settings)
    sid = (await play(client, headers, node["id"]))["sid"]
    from app.media import client as worker

    worker.bind(tmp_path / "geen.sock", "k" * 64)  # de worker is er niet
    try:
        r = await get_vtt(client, sid, "e2")
    finally:
        worker.bind(None)
    failed(r, 503, "media_worker_down")
    assert subtitles.state(settings, node["sha"], "e2") == "pending"


async def loose_item(client, headers, settings, subs: list[dict]) -> tuple[dict, str]:
    me = (await client.get("/api/v1/auth/me", headers=headers)).json()
    top = (await root(client, headers))["folder"]["id"]
    node = await text_video(client, headers, settings, f"los-{uuid.uuid4().hex[:6]}.mp4")
    await library_item(settings, me["id"], node, top, subs)
    return node, top


@pytest.mark.anyio
async def test_een_los_bestand_via_de_route(client, admin, settings, fake, monkeypatch):
    monkeypatch.setattr(media_subs, "FAST_WAIT", 0.05)
    headers, _ = admin
    top = (await root(client, headers))["folder"]["id"]
    sub = (
        await put(
            client,
            headers,
            top,
            "film.nl.srt",
            "1\n00:00:01,000 --> 00:00:02,000\nCafé\n".encode("cp1252"),
        )
    ).json()
    node, _ = await loose_item(client, headers, settings, [{"node": sub["id"], "lang": "nl"}])
    handler = Subs()
    handler.gate = asyncio.Event()
    fake.on(Op.SUBS, handler)
    out = await play(client, headers, node["id"])
    sid = out["sid"]
    assert [t["id"] for t in out["tracks"]["subtitles"]][-1] == "x0"
    entry = session_of(sid).subs[0]
    try:
        r = await get_vtt(client, sid, "x0")
        assert r.status_code == 202
        assert entry["state"] == "pending"
    finally:
        handler.gate.set()
    sha = await blob_of(settings, sub["id"])
    task = subtitles.running(sha, "file")
    if task is not None:
        await asyncio.wait_for(task, 10)
    r = await get_vtt(client, sid, "x0")
    assert r.status_code == 200, r.text
    assert r.content == vtt((1000, 2000, "Café"))  # niet geschoven: een los bestand
    assert r.headers["x-content-type-options"] == "nosniff"
    assert entry["state"] == "ready"
    assert fake.requests[-1].args["loose"] == "srt"
    # in de prullenbak: 404, ook al staat hij in de cache
    r = await client.delete(f"/api/v1/files/{sub['id']}", headers=headers)
    assert r.status_code in (200, 204), r.text
    failed(await get_vtt(client, sid, "x0"), 404, "not_found")


@pytest.mark.anyio
async def test_los_bestand_een_nieuwe_versie_en_een_ingetrokken_deling(
    client, admin, settings, fake
):
    admin_headers, _ = admin
    fake.on(Op.SUBS, Subs())
    me = (await client.get("/api/v1/auth/me", headers=admin_headers)).json()
    bert_headers, _ = await person(client, admin_headers, "bert@example.nl")
    bert_root = (await root(client, bert_headers))["folder"]["id"]
    foreign = (await put(client, bert_headers, bert_root, "film.en.srt", SRT)).json()
    share = await share_with(
        client, bert_headers, foreign["id"], user_id=me["id"], permission="read"
    )
    top = (await root(client, admin_headers))["folder"]["id"]
    mine = (await put(client, admin_headers, top, "film.nl.srt", SRT)).json()
    node, _ = await loose_item(
        client, admin_headers, settings, [{"node": foreign["id"]}, {"node": mine["id"]}]
    )
    sid = (await play(client, admin_headers, node["id"]))["sid"]
    for track in ("x0", "x1"):
        r = await get_vtt(client, sid, track)
        assert r.status_code == 200, (track, r.text)
    # een nieuwe versie van x1: de node wijst niet meer naar wat play vastlegde
    r = await client.put(
        f"/api/v1/files/{mine['id']}/content", content=SRT + b"\n", headers=admin_headers
    )
    assert r.status_code == 200, r.text
    failed(await get_vtt(client, sid, "x1"), 404, "not_found")
    # Bert trekt zijn deling in: x0 meteen 404 (de app kijkt bij elk verzoek)
    r = await client.delete(f"/api/v1/shares/{share['id']}", headers=bert_headers)
    assert r.status_code == 204, r.text
    failed(await get_vtt(client, sid, "x0"), 404, "not_found")
    failed(await get_vtt(client, sid, "x2"), 404, "not_found")


@pytest.mark.anyio
async def test_los_bestand_dat_niet_lukt(client, admin, settings, fake):
    headers, _ = admin

    async def broken(job: Job) -> dict:
        raise OpError(Code.UNKNOWN_FORMAT, "geen ondertitel")

    fake.on(Op.SUBS, broken)
    top = (await root(client, headers))["folder"]["id"]
    sub = (await put(client, headers, top, "kapot.srt", b"\x00\x01")).json()
    node, _ = await loose_item(client, headers, settings, [{"node": sub["id"]}])
    sid = (await play(client, headers, node["id"]))["sid"]
    failed(await get_vtt(client, sid, "x0"), 409, "media_unavailable", "subtitle")
    assert session_of(sid).subs[0]["state"] == "failed"
    failed(await get_vtt(client, sid, "x0"), 409, "media_unavailable", "subtitle")
    assert len(fake.requests) == 1


# --- de echte worker (marker media) ---------------------------------------------------------------


@pytest.fixture(scope="module")
def anyio_backend():
    return "asyncio"


@pytest.fixture(scope="module")
def built() -> dict[str, Path]:
    from tests.media import fixtures as F

    names = ("subs_text.mkv", "hevc10_hdr10.mkv", "multi_audio.mkv", "nl.srt", "en.ass", "bom.srt")
    return {name: F.make(name) for name in names}


@pytest.fixture(scope="module")
async def worker(tmp_path_factory, built):
    from app.media import local

    base = tmp_path_factory.mktemp("subs-worker")
    config = Settings(data_dir=base, web_dist=base / "web", jwt_secret="t" * 32)
    w = await local.start(config, base / "run" / "worker.sock")
    client.bind(w.socket, w.key)
    try:
        yield config
    finally:
        await local.stop(w)
        client.bind(None)


async def real(
    worker: Settings, built: dict[str, Path], name: str
) -> tuple[dict[str, str], str, Probe]:
    path = built[name]
    sha = sha_of(path.read_bytes())
    found = await client.probe(path)
    subtitles._remove(worker, sha)
    return await subtitles.extract(worker, sha, path, found), sha, found


@pytest.mark.media
@pytest.mark.anyio
async def test_echt_subs_text_mkv(worker, built):
    done, sha, found = await real(worker, built, "subs_text.mkv")
    assert done == {"e1": "ready", "e2": "ready"}
    assert found.start_ms == 0
    srt = subtitles.cached(worker, sha, "e1").read_bytes()
    assert srt == vtt(
        (1000, 2500, "Hallo, dit is de eerste regel."), (3000, 4500, "En dit is de tweede.")
    )
    ass = subtitles.cached(worker, sha, "e2").read_bytes().decode()
    # \an8: bovenaan in beeld
    assert "00:00:01.000 --> 00:00:02.500 line:0\nBovenaan in beeld\n" in ass
    assert "00:00:03.000 --> 00:00:04.500\nOnderaan, gewoon\n" in ass


@pytest.mark.media
@pytest.mark.anyio
async def test_echt_hevc10_hdr10_mkv_in_mediatijd(worker, built):
    done, sha, found = await real(worker, built, "hevc10_hdr10.mkv")
    assert done == {"e3": "ready", "e4": "ready"}
    shift = -(found.start_ms or 0)
    assert shift > 0  # de bron begint vóór 0 (de priming van de audio)
    srt = subtitles.cached(worker, sha, "e3").read_bytes().decode()
    first = f"{subconv.stamp(1000 + shift)} --> {subconv.stamp(2500 + shift)}\nHallo"
    assert first in srt
    ass = subtitles.cached(worker, sha, "e4").read_bytes().decode()
    assert f"{subconv.stamp(1000 + shift)} --> {subconv.stamp(2500 + shift)} line:0\n" in ass


@pytest.mark.media
@pytest.mark.anyio
async def test_echt_multi_audio_geforceerd(worker, built):
    done, sha, found = await real(worker, built, "multi_audio.mkv")
    assert done == {"e3": "ready"}
    found.blob_sha = sha  # zoals in media_probe
    tracks = subtitles.tracks(found, settings=worker)
    assert [(t["id"], t["lang"], t["forced"], t["default"], t["state"]) for t in tracks] == [
        ("e3", "nld", True, True, "ready")
    ]
    shift = -(found.start_ms or 0)
    data = subtitles.cached(worker, sha, "e3").read_bytes().decode()
    assert (
        f"{subconv.stamp(2000 + shift)} --> {subconv.stamp(3000 + shift)}\n[Een bord: UITGANG]"
        in data
    )


@pytest.mark.media
@pytest.mark.anyio
@pytest.mark.parametrize(
    ("name", "kind", "wanted"),
    [
        ("nl.srt", "srt", "00:00:01.000 --> 00:00:02.000\nCafé, één, ë en ü\n"),
        ("en.ass", "ass", "00:00:01.000 --> 00:00:02.500 line:0\nBovenaan in beeld\n"),
        (
            "bom.srt",
            "srt",
            "WEBVTT\n\n00:00:01.000 --> 00:00:02.500\nHallo, dit is de eerste regel.",
        ),
    ],
)
async def test_echt_een_los_bestand(worker, built, name, kind, wanted):
    path = built[name]
    sha = sha_of(path.read_bytes())
    subtitles._remove(worker, sha)
    assert subtitles.loose_kind(name) == kind
    assert await subtitles.convert_loose(worker, sha, path, kind) == "ready"
    data = subtitles.cached(worker, sha, "file").read_bytes()
    assert wanted in data.decode()
    assert not data.startswith(b"\xef\xbb\xbf")
    assert subtitles.check_vtt(data) == data


@pytest.mark.anyio
async def test_de_route_als_de_app_stopt(client, admin, settings, fake, media):
    """F3: elk wachten onder /stream/ gaat via until_stopped; stopt de app, dan 503 media_busy
    met reden worker, ook voor een ondertitel die al klaar is."""
    headers, _ = admin
    node = await text_video(client, headers, settings)
    fake.on(Op.SUBS, Subs({2: vtt((0, 1000, "a")), 3: vtt((0, 1000, "b"))}))
    sid = (await play(client, headers, node["id"]))["sid"]
    assert (await get_vtt(client, sid, "e2")).status_code == 200
    media.stopping = True
    try:
        for track in ("e2", "e3"):
            r = await get_vtt(client, sid, track)
            failed(r, 503, "media_busy", "worker")
            assert r.headers["retry-after"] == "2"
    finally:
        media.stopping = False
