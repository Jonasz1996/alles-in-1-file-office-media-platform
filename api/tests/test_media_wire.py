"""Het protocol tussen de app en de mediaworker (bouwontwerp fase 5, Keuze 12; §3.8): lengte
plus JSON, fd's als SCM_RIGHTS alleen op de eerste byte, ontvangen fd's meteen CLOEXEC, en de
weigeringen (te groot, te veel fd's, fd's midden in een boodschap, afgekapt, geen JSON). Plus
de argumenten van de worker: geen waarden in een fout, en de tabel van soort, processen en fd's.

Zonder ffmpeg: socketparen in dit proces."""

from __future__ import annotations

import array
import asyncio
import fcntl
import json
import os
import socket
import struct
import termios
from pathlib import Path

import pytest

from app.media import wire
from app.media.wire import Code, Op, WorkClass
from app.media.worker import args as A

pytestmark = pytest.mark.anyio


def pair() -> tuple[socket.socket, socket.socket]:
    a, b = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    for s in (a, b):
        s.setblocking(False)
    return a, b


def open_fds() -> int:
    return len(os.listdir("/proc/self/fd"))


def cloexec(fd: int) -> bool:
    return bool(fcntl.fcntl(fd, fcntl.F_GETFD) & fcntl.FD_CLOEXEC)


def raw(message: dict) -> bytes:
    body = json.dumps(message).encode()
    return struct.pack(">I", len(body)) + body


async def test_heen_en_weer_met_fds_die_cloexec_zijn(tmp_path):
    a, b = pair()
    source = tmp_path / "bron.bin"
    source.write_bytes(b"x" * 10)
    file_fd = os.open(source, os.O_RDONLY)
    r, w = os.pipe()
    try:
        message = {"v": 1, "op": "probe", "args": {"demuxer": None}, "tekst": "één"}
        await wire.send(a, message, [file_fd, w])
        got, fds = await wire.receive(b)
        assert got == message
        assert len(fds) == 2
        assert all(cloexec(fd) for fd in fds)
        assert os.fstat(fds[0]).st_ino == os.fstat(file_fd).st_ino
        os.write(fds[1], b"ok")  # dezelfde pipe
        assert os.read(r, 2) == b"ok"
        wire.close_all(fds)
        # Een tweede boodschap zonder fd's op dezelfde verbinding
        await wire.send(a, {"ev": "done"})
        assert await wire.receive(b) == ({"ev": "done"}, [])
    finally:
        wire.close_all([file_fd, r, w])
        a.close()
        b.close()


async def test_grote_boodschap_in_stukken():
    a, b = pair()
    a.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4096)
    message = {"data": "x" * (wire.MAX_MESSAGE - 100)}
    try:
        sending = asyncio.create_task(wire.send(a, message))
        got, fds = await wire.receive(b)
        await sending
        assert got == message and fds == []
    finally:
        a.close()
        b.close()


async def test_kop_in_losse_bytes():
    a, b = pair()
    data = raw({"ev": "started"})
    try:
        receiving = asyncio.create_task(wire.receive(b))
        for i in range(len(data)):
            a.send(data[i : i + 1])
            await asyncio.sleep(0)
        assert await receiving == ({"ev": "started"}, [])
    finally:
        a.close()
        b.close()


def test_te_groot_om_te_sturen():
    with pytest.raises(wire.WireError) as exc:
        wire.encode({"data": "x" * wire.MAX_MESSAGE})
    assert exc.value.code == Code.TOO_LARGE


async def test_te_groot_om_te_ontvangen():
    a, b = pair()
    try:
        a.send(struct.pack(">I", wire.MAX_MESSAGE + 1))
        with pytest.raises(wire.WireError) as exc:
            await wire.receive(b)
        assert exc.value.code == Code.TOO_LARGE
    finally:
        a.close()
        b.close()


async def test_te_veel_fds_sturen_mag_niet():
    a, b = pair()
    try:
        with pytest.raises(wire.WireError) as exc:
            await wire.send(a, {"v": 1}, [0] * (wire.MAX_FDS + 1))
        assert exc.value.code == Code.BAD_FDS
    finally:
        a.close()
        b.close()


async def test_te_veel_fds_ontvangen_en_alles_dicht():
    a, b = pair()
    r, w = os.pipe()
    try:
        before = open_fds()
        anc = [(socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array("i", [r] * 5).tobytes())]
        a.sendmsg([raw({"v": 1, "op": "probe", "args": {}})], anc)
        with pytest.raises(wire.WireError) as exc:
            await wire.receive(b, max_fds=2)
        assert exc.value.code == Code.BAD_FDS
        assert open_fds() == before  # de twee die pasten zijn weer dicht
    finally:
        wire.close_all([r, w])
        a.close()
        b.close()


def pending(sock: socket.socket) -> int:
    return struct.unpack("i", fcntl.ioctl(sock, termios.FIONREAD, b"\0" * 4))[0]


async def test_fds_midden_in_de_kop():
    """De ontvanger heeft al 2 bytes van de kop gelezen als de fd's komen. (Zonder die pauze
    plakt de kernel beide stukken aan elkaar, en zitten de fd's gewoon bij de eerste byte.)"""
    a, b = pair()
    r, w = os.pipe()
    data = raw({"v": 1})
    try:
        before = open_fds()
        a.send(data[:2])
        receiving = asyncio.create_task(wire.receive(b))
        for _ in range(200):
            if pending(b) == 0:
                break
            await asyncio.sleep(0.005)
        assert pending(b) == 0
        anc = [(socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array("i", [r]).tobytes())]
        a.sendmsg([data[2:]], anc)
        with pytest.raises(wire.WireError) as exc:
            await receiving
        assert exc.value.code == Code.BAD_FDS
        assert open_fds() == before
    finally:
        wire.close_all([r, w])
        a.close()
        b.close()


async def test_fds_bij_de_inhoud():
    a, b = pair()
    r, w = os.pipe()
    data = raw({"v": 1})
    try:
        before = open_fds()
        a.send(data[:4])
        anc = [(socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array("i", [r]).tobytes())]
        a.sendmsg([data[4:]], anc)
        with pytest.raises(wire.WireError) as exc:
            await wire.receive(b)
        assert exc.value.code == Code.BAD_FDS
        assert open_fds() == before
    finally:
        wire.close_all([r, w])
        a.close()
        b.close()


async def test_fds_op_een_latere_boodschap_weigert_de_ontvanger():
    """De client leest antwoorden met max_fds=0: een antwoord met een fd is fout."""
    a, b = pair()
    r, w = os.pipe()
    try:
        before = open_fds()
        await wire.send(a, {"ev": "started"}, [r])
        with pytest.raises(wire.WireError) as exc:
            await wire.receive(b, max_fds=0)
        assert exc.value.code == Code.BAD_FDS
        assert open_fds() == before
    finally:
        wire.close_all([r, w])
        a.close()
        b.close()


async def test_dicht_voor_de_eerste_byte_is_closed():
    a, b = pair()
    a.close()
    with pytest.raises(wire.Closed):
        await wire.receive(b)
    b.close()


@pytest.mark.parametrize("cut", [2, 4, 10])
async def test_dicht_midden_in_een_boodschap(cut):
    a, b = pair()
    a.send(raw({"v": 1, "op": "caps"})[:cut])
    a.close()
    with pytest.raises(wire.WireError) as exc:
        await wire.receive(b)
    assert exc.value.code == Code.PROTOCOL
    b.close()


@pytest.mark.parametrize("body", [b"geen json", b"[1, 2]", b"\xff\xfe", b'"tekst"'])
async def test_geen_json_object(body):
    a, b = pair()
    try:
        a.send(struct.pack(">I", len(body)) + body)
        with pytest.raises(wire.WireError) as exc:
            await wire.receive(b)
        assert exc.value.code == Code.PROTOCOL
    finally:
        a.close()
        b.close()


def test_error_kort_het_eind_van_detail_in():
    message = wire.error(Code.FAILED, "a" * 3000 + "einde", **{"class": "short"})
    assert message["ev"] == "error" and message["code"] == "failed"
    assert len(message["detail"]) == wire.DETAIL_CHARS
    assert message["detail"].endswith("einde")
    assert message["class"] == "short"


async def test_lang_pad_voor_de_socket(tmp_path):
    deep = tmp_path / ("map-" + "x" * 60) / ("nog-" + "y" * 60)
    deep.mkdir(parents=True)
    path = deep / "worker.sock"
    assert len(str(path)) > 107
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        with wire.socket_address(path) as address:
            listener.bind(address)
        listener.listen(1)
        assert path.exists()
        conn = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        with wire.socket_address(path) as address:
            conn.connect(address)
        conn.close()
    finally:
        listener.close()


# --- de argumenten van de worker -------------------------------------------------------------


def test_parse_weigert_extra_velden_en_noemt_geen_waarden():
    secret = "/etc/geheim/pad"
    with pytest.raises(ValueError) as exc:
        A.parse(Op.PROBE, {"demuxer": secret})
    assert secret not in str(exc.value)
    assert "demuxer" in str(exc.value)
    with pytest.raises(ValueError) as exc:
        A.parse(Op.PROBE, {"pad": secret})
    assert secret not in str(exc.value)
    with pytest.raises(ValueError):
        A.parse(Op.PROBE, ["geen", "object"])


def test_lavfi_staat_niet_in_de_witte_lijst():
    assert "lavfi" not in A.DEMUXERS
    with pytest.raises(ValueError):
        A.parse(Op.PROBE, {"demuxer": "lavfi"})


def stream(**over) -> A.StreamArgs:
    base = {
        "mode": "transcode",
        "demuxer": "matroska",
        "video_index": 0,
        "audio_index": 1,
        "video": {
            "codec": "libx264",
            "width": 640,
            "height": 360,
            "level": "3.1",
            "maxrate_k": 1500,
        },
        "audio": {"codec": "aac"},
        "grid": "2s",
    }
    base.update(over)
    return A.StreamArgs.model_validate(base)


def test_stream_regels():
    assert stream().burn is False
    burn = stream(video={"codec": "libx264", "width": 640, "height": 360, "level": "3.1",
                         "maxrate_k": 1500, "burn_index": 3, "burn_start_s": 30})  # fmt: skip
    assert burn.burn is True
    assert A.fd_layout(Op.STREAM, burn) == A.Layout(2, 1)
    assert A.fd_layout(Op.STREAM, stream()) == A.Layout(1, 1)
    with pytest.raises(ValueError):
        stream(grid=None)  # transcode werkt op het rooster
    with pytest.raises(ValueError):
        stream(mode="remux")  # remux kopieert het beeld
    with pytest.raises(ValueError):
        stream(audio="none")  # wel een audiospoor
    with pytest.raises(ValueError):
        stream(video={"codec": "libvpx-vp9", "width": 640, "height": 360, "level": "3.1",
                      "maxrate_k": 1500})  # fmt: skip


@pytest.mark.parametrize("level", ["3.0", "3.1", "3.2", "4.0", "4.2"])
def test_stream_kent_elk_level_van_de_ladder(level):
    """Keuze 24: 480 met 3.0 (3.1 boven 25 fps), 720 met 3.1 (3.2 boven 30 fps), 1080 met 4.0
    (4.2 boven 30 fps). Een level dat de tabel kan geven, mag de worker niet weigeren."""
    video = {"codec": "libx264", "width": 854, "height": 480, "level": level, "maxrate_k": 1500}
    assert stream(video=video).video.level == level
    with pytest.raises(ValueError):
        stream(video={**video, "level": "2.1"})


def test_soort_processen_en_fds():
    remux = A.StreamArgs.model_validate(
        {"mode": "remux", "demuxer": "mov", "video_index": 0, "video": "copy", "audio": "none"}
    )
    loose = A.SubsArgs(loose="srt")
    tracks = A.SubsArgs(demuxer="matroska", tracks=[{"index": 2, "kind": "webvtt"}] * 3)
    cases = [
        (Op.CAPS, A.CapsArgs(), WorkClass.CONTROL, 0, A.Layout(0, 0)),
        (Op.PROBE, A.ProbeArgs(), WorkClass.SHORT, 1, A.Layout(1, 0)),
        (Op.STREAM, stream(), WorkClass.TRANSCODE, 1, A.Layout(1, 1)),
        (Op.STREAM, remux, WorkClass.REMUX, 1, A.Layout(1, 1)),
        (Op.SUBS, loose, WorkClass.BACKGROUND, 0, A.Layout(1, 1)),  # subs: altijd achtergrond
        (Op.SUBS, tracks, WorkClass.BACKGROUND, 1, A.Layout(1, 9)),
        (Op.SPRITES, A.SpritesArgs(demuxer="mov", video_index=0), WorkClass.BACKGROUND, 1,
         A.Layout(1, 1)),
        (Op.BENCH, A.BenchArgs(), WorkClass.BACKGROUND, 2, A.Layout(0, 0)),
        (Op.SYNTH, A.SynthArgs(source="cellauto", seed=1, duration_s=30, height=720),
         WorkClass.BACKGROUND, 1, A.Layout(0, 1)),
        (Op.KEYFRAMES, A.KeyframesArgs(demuxer="matroska", video_index=0, fallback=True),
         WorkClass.BACKGROUND, 1, A.Layout(1, 0)),
    ]  # fmt: skip
    for op, parsed, wc, procs, layout in cases:
        assert A.work_class(op, parsed) == wc, op
        assert A.procs(op, parsed) == procs, op
        assert A.fd_layout(op, parsed) == layout, op
    assert A.fd_layout(Op.SUBS, tracks).total <= wire.MAX_FDS


def test_niceness_en_geheugen():
    assert A.niceness(Op.STREAM, stream()) == ("nice", "-n", "5")
    assert A.niceness(Op.PROBE, A.ProbeArgs()) == ()
    sprites = A.SpritesArgs(demuxer="mov", video_index=0)
    assert A.niceness(Op.SPRITES, sprites) == ("nice", "-n", "15", "ionice", "-c", "3")
    assert A.memory_mb(Op.STREAM, 1200) == 1200
    assert A.memory_mb(Op.PROBE, 1200) == A.SHORT_MEMORY_MB == 512


def test_demuxer_van_de_container():
    assert A.demuxer_of("mp4") == "mov"
    assert A.demuxer_of("webm") == "matroska"
    assert A.demuxer_of("m2ts") == "mpegts"
    assert A.demuxer_of("onbekend") is None
    assert A.demuxer_of(None) is None


def test_geen_pad_in_de_argumenten():
    """Geen veld van een opdracht is vrije tekst: alleen Literal, getallen en booleans."""
    for model in A.ARGS.values():
        for name, info in model.model_fields.items():
            text = str(info.annotation)
            assert "str" not in text.replace("Literal", ""), (model.__name__, name, text)


def test_socket_pad_van_de_instellingen(tmp_path):
    from app.config import Settings

    s = Settings(data_dir=tmp_path, web_dist=tmp_path / "web", jwt_secret="t" * 32)
    assert s.media_socket == tmp_path / "media" / "run" / "worker.sock"
    s2 = Settings(
        data_dir=tmp_path, web_dist=tmp_path / "web", jwt_secret="t" * 32, media_worker="/x.sock"
    )
    assert s2.media_socket == Path("/x.sock")
