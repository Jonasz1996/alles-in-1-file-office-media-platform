"""De opdracht nfo (Keuze 9, M2): app/media/worker/ops/nfo.py.

- de velden van een NFO van Kodi (film, serie, aflevering, seizoen, meerdere afleveringen);
- een NFO met alleen een link naar IMDb, TMDB of TheTVDB geeft het id;
- 2 MB wordt geweigerd (too_large, de grens van NfoArgs), door de echte worker in dit proces;
- kapotte XML, `DOCTYPE` en `ENTITY` (geweigerd vóór het parsen: geen XXE, geen billion laughs);
- de grenzen op diepte, aantal elementen en lengte;
- fuzz met een vaste seed: willekeur en verminkte NFO's geven altijd een antwoord met de vaste
  vorm, nooit een fout of een hang, en blijven onder de grens van een boodschap.
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import time
from types import SimpleNamespace

import pytest

from app.media import client, wire
from app.media.client import WorkerFailed
from app.media.wire import Op
from app.media.worker.args import NfoArgs
from app.media.worker.ops import nfo
from app.media.worker.server import Config, Worker

pytestmark = pytest.mark.anyio

KEY = "de-sleutel-van-de-nfo-test-" * 2
CAPS = {"protocol": 1, "ffmpeg": "test", "ffprobe": "test", "mode": "local", "sandbox": "off"}

MOVIE = """<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>
<!-- gemaakt door Kodi -->
<movie>
    <title>De Lift</title>
    <originaltitle>De Lift</originaltitle>
    <sorttitle>Lift, De</sorttitle>
    <year>1983</year>
    <plot>Een lift die mensen doodt.
    Een monteur zoekt uit waarom.</plot>
    <genre>Horror</genre>
    <genre>Thriller / Mystery</genre>
    <uniqueid type="imdb" default="true">tt0085833</uniqueid>
    <uniqueid type="tmdb">27420</uniqueid>
    <premiered>1983-03-10</premiered>
    <actor><name>Huub Stapel</name></actor>
</movie>
"""

EPISODE = """<episodedetails>
  <title>Pilot</title>
  <showtitle>Flikken</showtitle>
  <season>1</season>
  <episode>2</episode>
  <aired>1999-01-05</aired>
  <uniqueid type="tvdb">123456</uniqueid>
</episodedetails>"""


def answer(text: str | bytes) -> dict:
    return nfo.parse(text.encode() if isinstance(text, str) else text)


# --- velden --------------------------------------------------------------------------------------


def test_de_velden_van_een_film():
    got = answer(MOVIE)
    assert got["root"] == "movie" and got["error"] is None
    f = got["fields"]
    assert f["title"] == "De Lift" and f["originaltitle"] == "De Lift"
    assert f["sorttitle"] == "Lift, De" and f["year"] == 1983
    assert f["plot"] == "Een lift die mensen doodt.\nEen monteur zoekt uit waarom."
    assert f["genres"] == ["Horror", "Thriller", "Mystery"]
    assert f["ids"] == {"imdb": "tt0085833", "tmdb": "27420"}
    assert f["premiered"] == "1983-03-10"
    assert "actor" not in f


def test_een_aflevering_en_een_serie_en_een_seizoen():
    got = answer(EPISODE)
    assert got["root"] == "episodedetails"
    f = got["fields"]
    assert (f["title"], f["showtitle"], f["season"], f["episode"]) == ("Pilot", "Flikken", 1, 2)
    assert f["aired"] == "1999-01-05" and f["year"] == 1999  # het jaar uit de datum
    assert f["ids"] == {"tvdb": "123456"}
    show = answer("<tvshow><title>Flikken</title><year>1999</year><id>76543</id></tvshow>")
    assert show["root"] == "tvshow"
    assert show["fields"] == {"title": "Flikken", "year": 1999, "ids": {"tvdb": "76543"}}
    season = answer("<season><seasonnumber>2</seasonnumber><title>Twee</title></season>")
    assert season["root"] == "season" and season["fields"]["season"] == 2


def test_meerdere_afleveringen_in_een_nfo():
    text = EPISODE + EPISODE.replace("<episode>2</episode>", "<episode>3</episode>").replace(
        "Pilot", "Tweede"
    )
    got = answer(text)
    assert got["fields"]["episode"] == 2
    assert got["episodes"] == [
        {"season": 1, "episode": 2, "title": "Pilot"},
        {"season": 1, "episode": 3, "title": "Tweede"},
    ]


def test_oude_tags_en_ongeldige_waarden():
    got = answer(
        "<movie><title> Film </title><id>tt1234567</id><tmdbid>99</tmdbid>"
        "<year>abc</year><season>1</season><premiered>1999-02-30</premiered>"
        "<uniqueid type='imdb'>geen-id</uniqueid><uniqueid type='tvdb'>-5</uniqueid></movie>"
    )
    f = got["fields"]
    assert f == {"title": "Film", "ids": {"imdb": "tt1234567", "tmdb": "99"}}  # geen seizoen
    assert answer("<movie><id>603</id></movie>")["fields"]["ids"] == {"tmdb": "603"}
    assert answer("<movie><year>99999</year></movie>")["fields"] == {}


def test_uniqueid_gaat_voor_een_link():
    got = answer(
        "<movie><uniqueid type='imdb'>tt0000001</uniqueid></movie>\n"
        "https://www.imdb.com/title/tt0000002/\nhttps://www.themoviedb.org/movie/603-the-matrix"
    )
    assert got["fields"]["ids"] == {"imdb": "tt0000001", "tmdb": "603"}


def test_alleen_een_link_geeft_het_id():
    assert answer("https://www.imdb.com/title/tt0111161/\n") == {
        "root": None,
        "fields": {"ids": {"imdb": "tt0111161"}},
        "error": None,
    }
    assert answer("http://www.themoviedb.org/tv/1399")["fields"]["ids"] == {"tmdb": "1399"}
    tvdb = answer("https://thetvdb.com/?tab=series&id=81189")
    assert tvdb["fields"]["ids"] == {"tvdb": "81189"}
    assert answer("https://www.thetvdb.com/dereferrer/series/81189")["fields"]["ids"] == {
        "tvdb": "81189"
    }
    # een NFO van een release: ASCII-kunst met < en een link; de XML is kapot, de link telt
    scene = "  <<< RELEASE >>>  \n  imdb.com/title/tt0133093  \n  ██ ░░ \n"
    got = answer(scene)
    assert got["error"] == "xml" and got["fields"]["ids"] == {"imdb": "tt0133093"}
    assert answer("gewoon tekst")["fields"] == {}


def test_coderingen():
    latin = "<movie><title>Amélie</title></movie>".encode("cp1252")
    assert answer(latin)["fields"]["title"] == "Amélie"
    declared = '<?xml version="1.0" encoding="ISO-8859-1"?><movie><title>Café</title></movie>'
    assert answer(declared.encode("latin-1"))["fields"]["title"] == "Café"
    bom = b"\xef\xbb\xbf" + "<movie><title>Zoë</title></movie>".encode()
    assert answer(bom)["fields"]["title"] == "Zoë"
    utf16 = "<movie><title>Zoë</title></movie>".encode("utf-16")
    assert answer(utf16)["fields"]["title"] == "Zoë"
    # een codec die geen tekst geeft: dan CP1252
    rot = b'<?xml version="1.0" encoding="rot13"?><movie><title>\xe9</title></movie>'
    assert answer(rot)["fields"]["title"] == "é"
    assert answer(b"")["error"] == "empty"
    assert answer(b"  \n\t ")["error"] == "empty"


@pytest.mark.parametrize(
    "name",
    ["punycode", "raw_unicode_escape", "unicode_escape", "utf-7", "idna", "rot13", "zlib",
     "base64", "hex", "uu", "quopri", "bz2", "iso2022_jp", "hz", "undefined"],
)  # fmt: skip
def test_een_codering_buiten_de_lijst_wordt_cp1252(name):
    """Review golf 3, S1 en S3: punycode is kwadratisch (200 KB kostte 14 s, 1 MiB ± 6 minuten in
    de lus van de worker), en raw_unicode_escape maakte van \\ud800 een losse surrogaat waar expat
    met een UnicodeEncodeError op viel. Nu telt alleen een codering van DECLARED; de rest is
    CP1252, snel en altijd een antwoord."""
    head = f'<?xml version="1.0" encoding="{name}"?>'.encode()
    slow = head + b"\xff-" + b"a" * 200_000
    started = time.perf_counter()
    check_shape(answer(slow))
    assert time.perf_counter() - started < 1
    escaped = head + b"\xff<movie><title>\\ud800 \\udfff</title></movie>"
    got = answer(escaped)
    check_shape(got)
    assert got["fields"]["title"] == "\\ud800 \\udfff"


def test_een_losse_surrogaat_is_kapotte_xml_en_geen_fout():
    """Ook als er toch een losse surrogaat in de tekst komt: geen UnicodeEncodeError uit expat,
    maar kapotte XML (en in start gaat hij er al uit, zoals een stuurteken)."""
    reader = nfo.Reader("<movie><title>\ud800</title></movie>")
    with pytest.raises(nfo.NfoError):
        for part in reader.parts:
            reader.feed(part)
        reader.close()
    assert nfo._CONTROL.sub("", "a\ud800b\udfffc") == "abc"


def test_de_lijst_van_coderingen_is_snel_en_geeft_altijd_tekst():
    """Elke codering die een kop mag noemen: 1 MiB willekeurige bytes (vaste seed) in ruim
    minder dan een seconde, zonder losse surrogaat in de tekst."""
    rng = random.Random(1103)
    body = bytes(rng.randrange(256) for _ in range(1 << 20))
    for name in sorted(nfo.DECLARED):
        data = f'<?xml version="1.0" encoding="{name}"?>'.encode() + b"\xff" + body
        started = time.perf_counter()
        text = nfo.decode(data)
        assert time.perf_counter() - started < 0.5, name
        assert not any(0xD800 <= ord(c) <= 0xDFFF for c in text), name
        check_shape(answer(data[: 64 * 1024]))


def test_fuzz_elke_codering_die_python_kent():
    """Regel 10, met een vaste seed: elke codec van de standaardbibliotheek in de kop, met
    ongeldige UTF-8 en verminkte inhoud, geeft altijd een antwoord, en snel."""
    import encodings
    import pkgutil

    rng = random.Random(30)
    names = sorted(m.name for m in pkgutil.iter_modules(encodings.__path__))
    started = time.perf_counter()
    for name in names:
        for _ in range(3):
            text = list(rng.choice(SEEDS))
            for _ in range(rng.randint(1, 4)):
                text.insert(rng.randrange(len(text) + 1), rng.choice(PIECES + ["\\ud800"]))
            body = "".join(text).encode("utf-8", "surrogatepass")
            data = f'<?xml version="1.0" encoding="{name}"?>'.encode() + b"\xff" + body
            check_shape(answer(data + b"a" * rng.randint(0, 20_000)))
    assert time.perf_counter() - started < 10


# --- weigeren en grenzen -------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        '<!DOCTYPE movie [<!ENTITY x SYSTEM "file:///etc/passwd">]><movie><title>&x;</title>'
        "</movie>",
        '<?xml version="1.0"?>\n<!doctype movie><movie/>',
        '<movie><!ENTITY a "aaaa"><title>x</title></movie>',
        "<!  DOCTYPE lol [<!ENTITY lol 'lol'>]><movie>&lol;</movie>",
        "<movie><title>x</title></movie>\x00<!DOC\x00TYPE y>",  # stuurtekens eruit, dan zoeken
    ],
)
def test_doctype_en_entity_worden_geweigerd_voor_het_parsen(text):
    got = answer(text)
    assert got["error"] == "doctype" and got["root"] is None
    assert "passwd" not in json.dumps(got)


@pytest.mark.parametrize(
    "text",
    [
        "<movie><title>kapot</movie>",
        "<movie><title>&onbekend;</title></movie>",
        "<movie><title>a & b</title></movie>",
        "<movie>" + "<a>" * 70 + "</a>" * 70 + "</movie>",  # te diep
        '<movie><title>x</title></movie><?xml version="1.0"?>',
        "<movie></movie></movie>",
    ],
)
def test_kapotte_xml(text):
    got = answer(text)
    assert got["error"] == "xml" and got["root"] is None and got["fields"] == {}


def test_te_veel_elementen_en_lange_velden():
    many = "<movie>" + "<genre>g</genre>" * (nfo.MAX_ELEMENTS + 1) + "</movie>"
    assert answer(many)["error"] == "xml"
    long = (
        "<movie><title>"
        + "t" * 5000
        + "</title><plot>"
        + "é" * 10_000
        + "</plot>"
        + "".join(f"<genre>Genre {i} {'x' * 100}</genre>" for i in range(50))
        + "</movie>"
    )
    f = answer(long)["fields"]
    assert len(f["title"]) == nfo.MAX_TEXT and len(f["plot"]) == nfo.MAX_PLOT
    assert len(f["genres"]) == nfo.MAX_GENRES
    assert all(len(g) <= nfo.MAX_GENRE for g in f["genres"])
    many_eps = EPISODE * 30
    assert len(answer(many_eps)["episodes"]) == nfo.MAX_EPISODES


def test_het_grootste_antwoord_past_in_een_boodschap():
    """Elk veld op zijn grens, in tekens van vier bytes: ruim onder wire.MAX_MESSAGE."""
    wide = "\U0001f600"
    one = (
        "<episodedetails>"
        + "".join(
            f"<{tag}>{wide * 300}</{tag}>"
            for tag in ("title", "originaltitle", "sorttitle", "showtitle")
        )
        + f"<plot>{wide * 5000}</plot>"
        + "".join(f"<genre>{wide * 70}{i}</genre>" for i in range(12))
        + "<season>1</season><episode>1</episode></episodedetails>"
    )
    got = answer(one * 12)
    size = len(json.dumps({"ev": "result", "data": got}, ensure_ascii=False).encode())
    assert size < wire.MAX_MESSAGE // 2, size


# --- door de worker ------------------------------------------------------------------------------


@pytest.fixture
async def worker(tmp_path):
    config = Config(socket=tmp_path / "run" / "w.sock", max_procs=3, max_transcodes=1, key=KEY)
    w = Worker(config, dict(CAPS))
    await w.start()
    client.bind(config.socket, KEY)
    try:
        yield w
    finally:
        await w.stop()
        client.bind(None)


async def ask(path, max_bytes: int | None = None) -> dict:
    args = NfoArgs() if max_bytes is None else NfoArgs(max_bytes=max_bytes)
    job, reads = await client.call(Op.NFO, args, source=path)
    assert reads == []
    async with job:
        return await job.result()


async def test_door_de_worker(worker, tmp_path):
    path = tmp_path / "movie.nfo"
    path.write_text(MOVIE)
    got = await ask(path)
    assert got["root"] == "movie" and got["fields"]["ids"]["imdb"] == "tt0085833"


async def test_2_mb_wordt_geweigerd(worker, tmp_path):
    path = tmp_path / "groot.nfo"
    path.write_bytes(b"<movie><plot>" + b"x" * (2 * 1024 * 1024) + b"</plot></movie>")
    with pytest.raises(WorkerFailed) as failed:
        await ask(path)
    assert failed.value.code == "too_large"
    # precies op de grens mag wel
    small = tmp_path / "klein.nfo"
    small.write_bytes(b"<movie><title>x</title></movie>")
    assert (await ask(small, max_bytes=small.stat().st_size))["fields"] == {"title": "x"}
    with pytest.raises(WorkerFailed):
        await ask(small, max_bytes=small.stat().st_size - 1)


async def test_een_nfo_van_1_mb_geeft_de_lus_de_beurt(tmp_path, monkeypatch):
    """De worker heeft één lus voor alle opdrachten: de lezer wacht om de 16 stukken even."""
    actor = "<actor><name>Naam</name><role>Rol</role></actor>"
    head, tail = "<movie><title>Vol</title>", "</movie>"
    body = actor * ((1024 * 1024 - len(head) - len(tail)) // len(actor))
    path = tmp_path / "vol.nfo"
    path.write_text(head + body + tail)
    assert path.stat().st_size <= 1024 * 1024
    turns = []
    real = asyncio.sleep

    async def sleep(seconds):
        turns.append(seconds)
        await real(seconds)

    monkeypatch.setattr(nfo.asyncio, "sleep", sleep)
    fd = os.open(path, os.O_RDONLY)
    try:
        ctx = SimpleNamespace(args=NfoArgs(), inputs=[fd])
        started = time.perf_counter()
        got = await nfo.run(ctx)  # type: ignore[arg-type]
    finally:
        os.close(fd)
    assert got["root"] == "movie" and got["fields"] == {"title": "Vol"}
    assert len(turns) >= (1024 * 1024 // nfo.CHUNK) // nfo.YIELD_EVERY - 1
    assert time.perf_counter() - started < 5


@pytest.mark.media
async def test_door_de_lokale_worker_in_zijn_eigen_proces(tmp_path):
    """Met -m media: de worker als eigen proces (local.start, zoals in productie zonder
    container), dus ook de import van de opdracht en de grenzen van de sandbox."""
    from app.config import Settings
    from app.media import local

    settings = Settings(data_dir=tmp_path, web_dist=tmp_path / "web", jwt_secret="t" * 32)
    w = await local.start(settings, tmp_path / "run" / "worker.sock")
    client.bind(w.socket, w.key)
    try:
        path = tmp_path / "movie.nfo"
        path.write_text(MOVIE)
        got = await ask(path)
        assert got["fields"]["title"] == "De Lift" and got["fields"]["ids"]["tmdb"] == "27420"
        evil = tmp_path / "evil.nfo"
        evil.write_bytes(b'<!DOCTYPE x [<!ENTITY a "b">]><movie><title>&a;</title></movie>')
        assert (await ask(evil))["error"] == "doctype"
        big = tmp_path / "groot.nfo"
        big.write_bytes(b"<movie><plot>" + b"x" * (2 * 1024 * 1024) + b"</plot></movie>")
        with pytest.raises(WorkerFailed) as failed:
            await ask(big)
        assert failed.value.code == "too_large"
    finally:
        client.bind(None)
        await local.stop(w)


# --- fuzz ----------------------------------------------------------------------------------------

SEEDS = [MOVIE, EPISODE, EPISODE * 3, "https://www.imdb.com/title/tt0111161/"]
PIECES = ["<", ">", "</", "/>", "&", "&amp;", "&#x41;", "&#0;", "<!--", "-->", "<![CDATA[", "]]>",
          "<?xml", "?>", "<movie>", "</movie>", "<title>", "</title>", "<uniqueid type='imdb'>",
          "tt0000001", "<!DOCTYPE", "<!ENTITY", "\x00", "\xff", "é", "﻿", "<a>" * 20, "'", '"',
          "<season>-1</season>", "<year>1e9</year>",
          "<premiered>2024-13-01</premiered>"]  # fmt: skip


def check_shape(got: dict) -> None:
    assert set(got) <= {"root", "fields", "error", "episodes"}
    assert got["root"] in (None, *nfo.KINDS)
    assert got["error"] in (None, "doctype", "xml", "empty")
    assert isinstance(got["fields"], dict)
    for key, value in got["fields"].items():
        if key in ("title", "originaltitle", "sorttitle", "showtitle"):
            assert isinstance(value, str) and 0 < len(value) <= nfo.MAX_TEXT
        elif key == "plot":
            assert isinstance(value, str) and len(value) <= nfo.MAX_PLOT
        elif key in ("year", "season", "episode"):
            assert isinstance(value, int)
        elif key == "ids":
            assert set(value) <= {"imdb", "tmdb", "tvdb"}
    json.dumps(got)  # altijd te versturen


def test_fuzz_willekeurige_bytes():
    rng = random.Random(20261029)
    started = time.perf_counter()
    for _ in range(3000):
        data = bytes(rng.randrange(256) for _ in range(rng.randint(0, 300)))
        check_shape(answer(data))
    assert time.perf_counter() - started < 10


def test_fuzz_verminkte_nfos():
    rng = random.Random(9)
    started = time.perf_counter()
    for _ in range(3000):
        text = list(rng.choice(SEEDS))
        for _ in range(rng.randint(1, 6)):
            at = rng.randrange(len(text) + 1)
            action = rng.random()
            if action < 0.4:
                text.insert(at, rng.choice(PIECES))
            elif action < 0.7 and text:
                del text[at : at + rng.randint(1, 20)]
            else:
                text.insert(at, chr(rng.randrange(1, 0x2FFF)))
        data = "".join(text).encode("utf-8", "surrogatepass")
        check_shape(answer(data))
    assert time.perf_counter() - started < 10
