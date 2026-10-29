"""Scrub-sprites en trick.vtt (Keuze 30, M5): app/media/trick.py, ops/sprites.py en de routes
trick.vtt en trick/{i}.jpg.

- JPEG's knippen (split_jpegs): echte JPEG's van Pillow, en een fuzz-test met een vast zaad op
  kapotte, afgebroken en gemengde invoer (alleen BadSprites, nooit iets anders);
- de maat van de tegels, het aantal beelden, en trick.vtt die precies bij de tegels past;
- make via de nep-worker, de stap sprites (trick_state pending, dan ready of failed);
- de routes via een echte sessie: elk adres uit trick.vtt geeft 200, na uitloggen 403;
- marker media: de echte worker op vp9_opus.webm, long_gop.mkv (alleen keyframes) en een video
  van 1055 s (twee tegels).
"""

from __future__ import annotations

import asyncio
import io
import random
import re
import subprocess
from pathlib import Path

import pytest
from PIL import Image

from app.config import MediaConfig, Settings
from app.main import app
from app.media import analysis, client, hooks, runtime, sessions, trick
from app.media.client import WorkerFailed
from app.media.state import MediaPolicy, MediaState
from app.media.wire import Code, Op
from app.media.worker.args import SpritesArgs
from app.media.worker.ops import sprites as ops_sprites
from app.media.worker.ops import subs as ops_subs
from app.routers import public_media
from tests.media.fake_worker import Job, OpError, running
from tests.media.media_contract import failed
from tests.test_media_decide import probe as recorded
from tests.test_media_sessions import maker, play, video

STREAM = "/api/v1/stream"
SEED = 20261030


@pytest.fixture(autouse=True)
def media():
    sessions.reset()
    public_media.plays.reset()
    before = {name: getattr(app.state, name, None) for name in ("media", "media_policy")}
    state = app.state.media = MediaState(enabled=True, worker="ok")
    app.state.media_policy = MediaPolicy(MediaConfig())
    yield state
    for name, value in before.items():
        setattr(app.state, name, value)
    sessions.reset()
    public_media.plays.reset()


@pytest.fixture
async def fake(tmp_path):
    async with running(tmp_path / "w" / "nep.sock") as worker:
        yield worker


def jpeg(width: int = 1600, height: int = 900, colour=(20, 40, 60), **options) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (width, height), colour).save(out, "JPEG", quality=60, **options)
    return out.getvalue()


def cues(data: bytes) -> list[tuple[str, str]]:
    """[(tijd, adres)] uit trick.vtt."""
    blocks = data.decode().split("\n\n")[1:]
    return [tuple(block.strip().split("\n")) for block in blocks]  # type: ignore[misc]


# --- knippen --------------------------------------------------------------------------------------


def test_split_jpegs_echte_beelden() -> None:
    plain = jpeg()
    progressive = jpeg(colour=(200, 10, 10), progressive=True)
    exif = Image.Exif()
    exif[0x010E] = "een beschrijving \xff\xd9 met een EOI erin"
    with_exif = jpeg(1600, 600, exif=exif.tobytes())
    data = plain + progressive + with_exif
    found = trick.split_jpegs(data)
    assert [(len(j), w, h) for j, w, h in found] == [
        (len(plain), 1600, 900),
        (len(progressive), 1600, 900),
        (len(with_exif), 1600, 600),
    ]
    assert b"".join(j for j, _, _ in found) == data
    assert trick.split_jpegs(b"") == []


def test_split_jpegs_een_herstartmarker_en_opvulling() -> None:
    # Met de hand: SOI, SOF0 (1600×900), SOS met data waarin FF00 en RST0 staan, EOI
    sof = b"\xff\xc0\x00\x11\x08" + (900).to_bytes(2) + (1600).to_bytes(2) + b"\x03" + b"\x00" * 9
    sos = b"\xff\xda\x00\x0c\x03" + b"\x00" * 7 + b"\x12\xff\x00\x34\xff\xd0\x56"
    one = b"\xff\xd8" + b"\xff\xff" + sof[1:] + sos + b"\xff\xd9"
    assert trick.split_jpegs(one + one) == [(one, 1600, 900), (one, 1600, 900)]


@pytest.mark.parametrize(
    "data",
    [
        b"rommel",
        b"\xff\xd8",
        b"\xff\xd8\xff\xd9",  # geen SOF
        b"\xff\xd8\xff\xd8\xff\xd9",
        b"\xff\xd8\xff\xc0\x00\x05\x08\x00",  # SOF te kort
        b"\xff\xd8\xff\xe0\x00\x01",  # lengte onder 2
        b"\xff\xd8\xff\xe0\xff\xff",  # buiten de grens
        b"\xff\xd8\x00",
    ],
)
def test_split_jpegs_weigert(data: bytes) -> None:
    with pytest.raises(trick.BadSprites):
        trick.split_jpegs(data)


def test_split_jpegs_rommel_na_een_beeld_en_te_veel() -> None:
    one = jpeg(160, 90)
    with pytest.raises(trick.BadSprites):
        trick.split_jpegs(one + b"\x00")
    with pytest.raises(trick.BadSprites):
        trick.split_jpegs(one[:-1])
    with pytest.raises(trick.BadSprites, match="tegels"):
        trick.split_jpegs(one * 3, max_count=2)


def test_split_jpegs_fuzz() -> None:
    """Vast zaad: bytes omgooien, afknippen, invoegen, stukken herhalen en willekeur. Alleen
    BadSprites mag; lukt het, dan zijn de stukken samen precies de invoer."""
    rng = random.Random(SEED)
    base = [jpeg(160, 90), jpeg(1600, 900, progressive=True), jpeg(320, 200)]
    for n in range(1500):
        data = bytearray(b"".join(rng.sample(base, rng.randint(1, 3))))
        for _ in range(rng.randint(1, 6)):
            what = rng.randrange(5)
            at = rng.randrange(len(data) + 1)
            if what == 0 and data:
                data[min(at, len(data) - 1)] = rng.randrange(256)
            elif what == 1:
                del data[at:]
            elif what == 2:
                data[at:at] = bytes(rng.randrange(256) for _ in range(rng.randint(1, 8)))
            elif what == 3:
                data[at:at] = rng.choice(
                    [b"\xff", b"\xff\xd8", b"\xff\xd9", b"\xff\xda", b"\xff\x00"]
                )
            else:
                data[at:at] = data[max(0, at - 40) : at]
        if n % 10 == 0:
            data = bytearray(rng.randbytes(rng.randint(0, 2000)))
        try:
            found = trick.split_jpegs(bytes(data))
        except trick.BadSprites:
            continue
        assert b"".join(j for j, _, _ in found) == bytes(data)


def test_tiles_of_en_de_maat() -> None:
    tiles, cell = trick.tiles_of(jpeg() + jpeg())
    assert len(tiles) == 2 and cell == 90
    # een staande video: 160 × 284 per beeld
    assert trick.tiles_of(jpeg(1600, 2840))[1] == 284
    for data in (
        b"",
        jpeg(1280, 720),  # niet 1600 breed
        jpeg() + jpeg(1600, 800),  # niet even hoog
        jpeg(1600, 905),  # geen 10 rijen
        jpeg(1600, 10),  # 1 pixel per beeld
        jpeg(1600, 4810),  # hoger dan 480 per beeld
    ):
        with pytest.raises(trick.BadSprites):
            trick.tiles_of(data)


def test_count_for() -> None:
    assert trick.count_for(2, 105, 1_055_000) == 105
    assert trick.count_for(1, 1, 6_008) == 1
    # wat de worker telde past niet bij de tegels: dan de duur, binnen de laatste tegel
    assert trick.count_for(2, 5, 1_055_000) == 106
    assert trick.count_for(2, None, None) == 101
    assert trick.count_for(2, True, 10**9) == 200
    assert trick.count_for(1, "7", 70_000) == 7


def test_make_vtt_past_bij_de_tegels() -> None:
    data = trick.make_vtt(105, 90, 1_055_000)
    found = cues(data)
    assert len(found) == 105
    assert found[0] == ("00:00:00.000 --> 00:00:10.000", "trick/0.jpg#xywh=0,0,160,90")
    assert found[11] == ("00:01:50.000 --> 00:02:00.000", "trick/0.jpg#xywh=160,90,160,90")
    assert found[99] == ("00:16:30.000 --> 00:16:40.000", "trick/0.jpg#xywh=1440,810,160,90")
    assert found[100] == ("00:16:40.000 --> 00:16:50.000", "trick/1.jpg#xywh=0,0,160,90")
    # de laatste loopt door tot het eind
    assert found[104] == ("00:17:20.000 --> 00:17:35.000", "trick/1.jpg#xywh=640,0,160,90")
    from app.media import subtitles

    assert len(subtitles.parse_vtt(data)) == 105  # ook een geldige VTT voor onze eigen controle


def test_info_alleen_als_alles_er_is(tmp_path) -> None:
    settings = Settings(data_dir=tmp_path, web_dist=tmp_path / "web", jwt_secret="t" * 32)
    p = recorded("h264_aac.mp4")
    p.blob_sha = "c" * 64
    assert trick.info(p, settings) is None
    found = {"interval": 10, "w": 160, "h": 90, "cols": 10, "rows": 10, "count": 1, "tiles": 1}
    p.trick_state, p.trick = "ready", found
    assert trick.info(p, settings) is None  # trick.vtt staat er (nog) niet
    trick._store(settings, p.blob_sha, [jpeg()], trick.make_vtt(1, 90, 10_000))
    assert trick.info(p, settings) == {k: v for k, v in found.items() if k != "tiles"}
    p.trick = {**found, "h": "x"}
    assert trick.info(p, settings) is None
    p.trick, p.trick_state = found, "failed"
    assert trick.info(p, settings) is None
    with pytest.raises(ValueError):
        trick.trick_dir(settings, "../x")


@pytest.mark.anyio
async def test_on_discard_gooit_de_sprites_weg(settings, monkeypatch):
    monkeypatch.setattr(runtime, "_settings", settings)
    sha = "d" * 64
    trick._store(settings, sha, [jpeg()], trick.make_vtt(1, 90, 10_000))
    assert trick.vtt_path(settings, sha).is_file()
    assert trick.discard in hooks._discard
    await hooks.on_discard(sha)
    assert not trick.trick_dir(settings, sha).exists()


# --- het commando van de worker -------------------------------------------------------------------


def test_argv_van_sprites() -> None:
    argv = ops_sprites.argv(SpritesArgs(demuxer="matroska", video_index=2))
    graph = argv[argv.index("-filter_complex") + 1]
    assert graph == "[0:2]fps=1/10,scale=160:-2,split=2[n][s];[s]tile=10x10[t]"
    assert argv[argv.index("-skip_frame") + 1] == "nokey"
    assert argv.index("-skip_frame") < argv.index("-i")
    # de lege uitvoer eerst: frame= telt de beelden
    assert argv.index("null") < argv.index("image2pipe")
    assert argv[-1] == "pipe:1" and argv[argv.index("-c:v") + 1] == "mjpeg"
    assert argv[argv.index("-protocol_whitelist") + 1] == "fd,pipe"


# --- make en de stap via de nep-worker ------------------------------------------------------------


class Sprites:
    def __init__(self, data: bytes, count: object = None):
        self.data = data
        self.count = count
        self.jobs: list[Job] = []

    async def __call__(self, job: Job) -> dict:
        assert isinstance(job.args, SpritesArgs)
        self.jobs.append(job)
        await ops_subs.send(job.outputs[0], self.data)
        ops_subs.close_fd(job.outputs[0])
        return {"count": self.count, "interval": 10, "width": 160, "cols": 10, "rows": 10}


@pytest.mark.anyio
async def test_make_via_de_worker(settings, fake, tmp_path):
    source = tmp_path / "bron.mkv"
    source.write_bytes(b"x")
    tiles = [jpeg(colour=(1, 2, 3)), jpeg(colour=(200, 100, 0))]
    handler = Sprites(b"".join(tiles), 105)
    fake.on(Op.SPRITES, handler)
    p = recorded("long_gop.mkv", duration_ms=1_055_000)
    sha = "e" * 64
    found = await trick.make(settings, sha, source, p)
    assert found == {
        "interval": 10,
        "w": 160,
        "h": 90,
        "cols": 10,
        "rows": 10,
        "count": 105,
        "tiles": 2,
    }
    assert fake.requests[-1].args == {"demuxer": "matroska", "video_index": 0}
    assert fake.requests[-1].fds == 2
    folder = trick.trick_dir(settings, sha)
    assert sorted(f.name for f in folder.iterdir()) == ["0.jpg", "1.jpg", "trick.vtt"]
    assert (folder / "1.jpg").read_bytes() == tiles[1]
    assert (folder / "trick.vtt").read_bytes() == trick.make_vtt(105, 90, 1_055_000)
    # opnieuw maken vervangt alles in één keer
    fake.on(Op.SPRITES, Sprites(jpeg(colour=(9, 9, 9)), 3))
    again = await trick.make(settings, sha, source, recorded("long_gop.mkv"))
    assert again["tiles"] == 1 and again["count"] == 3
    assert sorted(f.name for f in folder.iterdir()) == ["0.jpg", "trick.vtt"]
    assert not [f for f in folder.parent.iterdir() if f.name.startswith(".")]


@pytest.mark.anyio
async def test_make_weigert_vreemde_uitvoer(settings, fake, tmp_path, monkeypatch):
    source = tmp_path / "bron.mkv"
    source.write_bytes(b"x")
    p = recorded("long_gop.mkv")
    for data in (b"", b"rommel", jpeg(1280, 720), jpeg() + b"\x00"):
        fake.on(Op.SPRITES, Sprites(data, 1))
        with pytest.raises(trick.BadSprites):
            await trick.make(settings, "f" * 64, source, p)
    monkeypatch.setattr(trick, "MAX_BYTES", 1000)
    fake.on(Op.SPRITES, Sprites(jpeg() * 3, 1))
    with pytest.raises(trick.BadSprites, match="bytes"):
        await asyncio.wait_for(trick.make(settings, "f" * 64, source, p), 20)
    assert not trick.trick_dir(settings, "f" * 64).exists()
    count = len(fake.requests)
    with pytest.raises(trick.BadSprites):
        await trick.make(settings, "f" * 64, source, recorded("album__aac__01.m4a"))
    assert len(fake.requests) == count


@pytest.mark.anyio
async def test_de_stap_sprites(client, admin, settings, fake, tmp_path):
    headers, _ = admin
    node = await video(client, headers, settings, "stap.mkv", "long_gop.mkv")
    sha = node["sha"]
    source = tmp_path / "bron.mkv"
    source.write_bytes(b"x")
    step = next(s for s in analysis.steps() if s.name == "sprites")
    assert step.order == 30
    p = await analysis.load(maker(settings), sha)
    assert p is not None and p.trick_state == "none"
    assert (
        trick._applies(p, "movies") and trick._applies(p, "shows") and trick._applies(p, "videos")
    )
    assert not trick._applies(p, "music") and not trick._applies(p, None)
    assert not trick._applies(recorded("album__aac__01.m4a"), "movies")
    assert not trick._applies(recorded("hevc2160_hdr10.mkv"), "movies")  # 2 s: te kort
    seen: list[str] = []
    handler = Sprites(jpeg(), 6)

    async def watch(job: Job) -> dict:
        now = await analysis.load(maker(settings), sha)
        seen.append(now.trick_state if now else "?")
        return await handler(job)

    fake.on(Op.SPRITES, watch)
    await step.run(analysis.StepContext(settings, sha, source, None, "movies", p))
    assert seen == ["pending"]
    done = await analysis.load(maker(settings), sha)
    assert done is not None and done.trick_state == "ready"
    assert done.trick == {
        "interval": 10,
        "w": 160,
        "h": 90,
        "cols": 10,
        "rows": 10,
        "count": 6,
        "tiles": 1,
    }
    assert trick.info(done, settings) is not None
    # klaar: niet nog eens
    await step.run(analysis.StepContext(settings, sha, source, None, "movies", done))
    assert len(fake.requests) == 1
    # een bron die niet te lezen is: failed, en dan ook niet nog eens
    other = await video(client, headers, settings, "kapot.mkv", "long_gop.mkv")

    async def broken(job: Job) -> dict:
        raise OpError(Code.UNREADABLE, "kapot")

    fake.on(Op.SPRITES, broken)
    p = await analysis.load(maker(settings), other["sha"])
    with pytest.raises(WorkerFailed):
        await step.run(analysis.StepContext(settings, other["sha"], source, None, "movies", p))
    p = await analysis.load(maker(settings), other["sha"])
    assert p is not None and p.trick_state == "failed" and p.trick is None
    count = len(fake.requests)
    await step.run(analysis.StepContext(settings, other["sha"], source, None, "movies", p))
    assert len(fake.requests) == count
    # een worker van een andere versie (not_built): geen failed, de volgende keer opnieuw
    third = await video(client, headers, settings, "oud.mkv", "long_gop.mkv")
    fake._handlers.pop(Op.SPRITES)
    p = await analysis.load(maker(settings), third["sha"])
    with pytest.raises(WorkerFailed):
        await step.run(analysis.StepContext(settings, third["sha"], source, None, "movies", p))
    p = await analysis.load(maker(settings), third["sha"])
    assert p is not None and p.trick_state == "pending"


# --- de routes ------------------------------------------------------------------------------------


@pytest.mark.anyio
async def test_elk_adres_van_trick_vtt_via_de_sessie_en_403_na_intrekken(
    client, admin, settings, monkeypatch
):
    monkeypatch.setattr(runtime, "_settings", settings)
    headers, tokens = admin
    node = await video(client, headers, settings, "met-sprites.mp4")
    sha = node["sha"]
    p = recorded("h264_aac.mp4", duration_ms=1_055_000)
    p.trick_state = "ready"
    p.trick = {"interval": 10, "w": 160, "h": 90, "cols": 10, "rows": 10, "count": 105, "tiles": 2}
    await analysis.save_probe(maker(settings), sha, p)
    # save_probe zet de stappen terug (een nieuwe probe): de staat er weer in
    from sqlalchemy import update

    from app.models import MediaProbe

    async with maker(settings)() as db:
        await db.execute(
            update(MediaProbe)
            .where(MediaProbe.blob_sha == sha)
            .values(trick_state="ready", trick=p.trick)
        )
        await db.commit()
    tiles = [jpeg(colour=(1, 1, 1)), jpeg(colour=(2, 2, 2))]
    trick._store(settings, sha, tiles, trick.make_vtt(105, 90, 1_055_000))
    out = await play(client, headers, node["id"])
    assert out["trick"] == {"url": "trick.vtt", "interval_s": 10.0, "width": 160, "height": 90}
    sid = out["sid"]
    r = await client.get(f"{STREAM}/{sid}/trick.vtt")
    assert r.status_code == 200
    assert r.headers["content-type"] == "text/vtt; charset=utf-8"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["cache-control"] == "private, max-age=3600"
    found = cues(r.content)
    assert len(found) == 105
    urls = sorted({address.split("#")[0] for _, address in found})
    assert urls == ["trick/0.jpg", "trick/1.jpg"]
    for i, url in enumerate(urls):
        r = await client.get(f"{STREAM}/{sid}/{url}")
        assert r.status_code == 200
        assert r.headers["content-type"] == "image/jpeg"
        assert r.headers["x-content-type-options"] == "nosniff"
        assert r.content == tiles[i]
    for wrong in ("trick/2.jpg", "trick/99999.jpg"):
        failed(await client.get(f"{STREAM}/{sid}/{wrong}"), 404, "not_found")
    assert (await client.get(f"{STREAM}/{sid}/trick/-1.jpg")).status_code == 422
    assert (await client.get(f"{STREAM}/{sid}/trick/x.jpg")).status_code == 422
    # uitloggen trekt de sessie in: ook de sprites zijn dan meteen weg
    r = await client.post("/api/v1/auth/logout", json={"refresh_token": tokens["refresh_token"]})
    assert r.status_code == 204
    for rest in ("trick.vtt", "trick/0.jpg", "trick/1.jpg"):
        failed(await client.get(f"{STREAM}/{sid}/{rest}"), 403, "media_access_gone", "revoked")


@pytest.mark.anyio
async def test_zonder_sprites_404(client, admin, settings, monkeypatch):
    monkeypatch.setattr(runtime, "_settings", settings)
    headers, _ = admin
    node = await video(client, headers, settings, "zonder.mp4")
    out = await play(client, headers, node["id"])
    assert out["trick"] is None
    failed(await client.get(f"{STREAM}/{out['sid']}/trick.vtt"), 404, "not_found")
    failed(await client.get(f"{STREAM}/{out['sid']}/trick/0.jpg"), 404, "not_found")


# --- de echte worker (marker media) ---------------------------------------------------------------


@pytest.fixture(scope="module")
def anyio_backend():
    return "asyncio"


@pytest.fixture(scope="module")
def built(tmp_path_factory) -> dict[str, Path]:
    """De fixtures, en een video van 1055 s (64×36, 1 beeld per s): twee tegels."""
    from tests.media import fixtures as F

    out = {name: F.make(name) for name in ("vp9_opus.webm", "long_gop.mkv")}
    long = tmp_path_factory.mktemp("lang") / "lang.mkv"
    subprocess.run(
        [*F.FFMPEG, *F.video("64x36", 1, 1055), *F.X264, "-g", "10", str(long)],
        check=True,
        timeout=300,
        stdin=subprocess.DEVNULL,
    )
    out["lang.mkv"] = long
    return out


@pytest.fixture(scope="module")
async def worker(tmp_path_factory, built):
    from app.media import local

    base = tmp_path_factory.mktemp("trick-worker")
    config = Settings(data_dir=base, web_dist=base / "web", jwt_secret="t" * 32)
    w = await local.start(config, base / "run" / "worker.sock")
    client.bind(w.socket, w.key)
    try:
        yield config
    finally:
        await local.stop(w)
        client.bind(None)


async def real(config: Settings, path: Path, sha: str) -> tuple[dict, list[tuple[str, str]]]:
    found = await client.probe(path)
    out = await trick.make(config, sha, path, found)
    data = trick.vtt_path(config, sha).read_bytes()
    for i in range(out["tiles"]):
        with Image.open(trick.tile_path(config, sha, i)) as image:
            assert image.format == "JPEG"
            assert image.size == (1600, out["h"] * 10)
    return out, cues(data)


@pytest.mark.media
@pytest.mark.anyio
async def test_echt_vp9_opus_webm(worker, built):
    out, found = await real(worker, built["vp9_opus.webm"], "1" * 64)
    assert (out["count"], out["tiles"], out["h"]) == (1, 1, 90)  # 640×360 → 160×90
    assert found == [("00:00:00.000 --> 00:00:06.008", "trick/0.jpg#xywh=0,0,160,90")]


@pytest.mark.media
@pytest.mark.anyio
async def test_echt_long_gop_alleen_keyframes(worker, built):
    """Keyframes om de 25 s: -skip_frame nokey decodeert er maar 3, het filter fps vult aan."""
    out, found = await real(worker, built["long_gop.mkv"], "2" * 64)
    assert out["tiles"] == 1 and 5 <= out["count"] <= 6
    assert len(found) == out["count"]
    assert found[-1][0].endswith("--> 00:01:00.000")


@pytest.mark.media
@pytest.mark.anyio
async def test_echt_twee_tegels(worker, built):
    out, found = await real(worker, built["lang.mkv"], "3" * 64)
    assert (out["tiles"], out["h"]) == (2, 90)
    assert out["count"] == 105
    assert found[100] == ("00:16:40.000 --> 00:16:50.000", "trick/1.jpg#xywh=0,0,160,90")
    assert re.fullmatch(r"00:17:20\.000 --> 00:17:35\.\d{3}", found[-1][0])


@pytest.mark.anyio
async def test_de_routes_als_de_app_stopt(client, admin, settings, media, monkeypatch):
    monkeypatch.setattr(runtime, "_settings", settings)
    headers, _ = admin
    node = await video(client, headers, settings, "stopt.mp4")
    trick._store(settings, node["sha"], [jpeg()], trick.make_vtt(1, 90, 10_000))
    sid = (await play(client, headers, node["id"]))["sid"]
    assert (await client.get(f"{STREAM}/{sid}/trick.vtt")).status_code == 200
    media.stopping = True
    try:
        for rest in ("trick.vtt", "trick/0.jpg"):
            failed(await client.get(f"{STREAM}/{sid}/{rest}"), 503, "media_busy", "worker")
    finally:
        media.stopping = False
