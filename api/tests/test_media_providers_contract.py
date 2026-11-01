"""De clients van TMDB, MusicBrainz en de Cover Art Archive tegen de nep-providers (M8, §4.4).

- TMDB: zoeken (film en serie, met jaar en taal), details met `external_ids`, een seizoen,
  `find` op een IMDb-id; de terugval naar en-US voor een lege samenvatting; 404, 401, 429
  (kort: de client wacht zelf; lang: ProviderRate), 500, kapotte JSON, een verbroken verbinding
  en traag; de plaatjes zonder sleutel.
- MusicBrainz: zoeken en opzoeken op MBID, `fmt=json`, de User-Agent, en 1 verzoek per seconde,
  gemeten met de nepklok (de nep telt met dezelfde klok en weigert boven 1 per seconde).
- CAA: de voorkant via de omleiding naar de tweede host; zonder voorkant NotFound.
- Een fuzztest met een vaste seed op alles wat JSON of bytes van buiten leest (regel 10).
- De vorm: met `docs/fase-5/provider-shapes.json` (Jonas neemt hem op met
  `app.cli media-provider-check --opnemen`) moet de nep dezelfde sleutels en soorten geven;
  zonder dat bestand wordt die test overgeslagen.
- `providers.check` (de cli) tegen de nep, ook `--opnemen` naar een tijdelijk bestand.
"""

from __future__ import annotations

import copy
import json
import random
import uuid

import httpx
import pytest

from app.media import providers
from app.media.providers import art, caa, http, musicbrainz, secrets, tmdb
from app.media.providers.base import ProviderAuth, ProviderRate, ProviderUnavailable
from tests.media.fake_providers import FakeConfig, create_app, kit, load

pytestmark = pytest.mark.anyio

KEY = kit.KEY
SEED = 20260508
ZEE = str(uuid.uuid5(uuid.NAMESPACE_URL, "https://nep.invalid/release/zeegezichten"))
COMPILATIE = str(uuid.uuid5(uuid.NAMESPACE_URL, "https://nep.invalid/release/nepcompilatie"))


@pytest.fixture(autouse=True)
def _schoon():
    secrets.reset()
    previous = http.use(None)
    yield
    http.use(previous)
    secrets.reset()


@pytest.fixture
def clock():
    return kit.Clock()


@pytest.fixture
def fake(clock):
    # De nep telt het tempo met dezelfde nepklok als de client: wachten kost geen echte tijd
    return create_app(FakeConfig(tmdb_key=KEY, data=load(), clock=clock))


@pytest.fixture
def log(fake):
    return fake.state.fake.log


@pytest.fixture
async def out(settings, fake, clock):
    o = kit.outbound(settings, fake, clock=clock)
    http.use(o)
    yield o
    await o.aclose()


@pytest.fixture
def db(settings, out):
    return tmdb.Tmdb(settings, KEY)


@pytest.fixture
def mb(settings, out):
    return musicbrainz.MusicBrainz(settings)


# --- TMDB ----------------------------------------------------------------------------------------


async def test_zoeken_naar_een_film_in_het_nederlands(db, log):
    found = await db.search("movie", "De Nephaven", 2004, "nl-NL")
    first = found[0]
    assert (first.provider, first.id, first.kind) == ("tmdb", "movie:900001", "movie")
    assert (first.title, first.original_title, first.year) == ("De Nephaven", "De Nephaven", 2004)
    assert first.poster == "/p900001.jpg" and first.popularity == 12.5
    [entry] = log
    assert entry["inner"] == "/3/search/movie" and entry["status"] == 200
    assert "primary_release_year=2004" in entry["query"] and "language=nl-NL" in entry["query"]
    assert "api_key" not in entry["query"] and entry["auth"] == "bearer" and entry["auth_ok"]


async def test_zoeken_naar_een_serie_en_een_vreemde_taal(db, log):
    found = await db.search("show", "Havenlichten", None, "nl-NL")
    assert [(c.id, c.kind, c.title) for c in found][:1] == [("tv:900102", "show", "Havenlichten")]
    # Een taal in een verkeerde vorm wordt en-US (hij gaat niet zo naar buiten)
    await db.search("show", "Harbour Lights", None, "nl; drop")
    assert "language=en-US" in log[-1]["query"]
    # Leeg zoeken vraagt niets
    assert await db.search("movie", "   ", None, "nl-NL") == []
    assert len(log) == 2


async def test_details_met_terugval_naar_engels(db, log):
    details = await db.details("movie", "movie:900016", "nl-NL")
    assert (details.title, details.year) == ("Dakzomer", 2021)
    # De Nederlandse samenvatting is leeg: die van en-US
    assert details.overview == "Four friends spend an invented summer on a roof."
    assert details.ids == {"tmdb": "900016", "imdb": "tt9000016"}
    assert [(i.kind, i.path) for i in details.images] == [
        ("poster", "/p900016.jpg"),
        ("backdrop", "/b900016.jpg"),
    ]
    assert [e["inner"] for e in log] == ["/3/movie/900016", "/3/movie/900016"]
    assert "append_to_response=external_ids" in log[0]["query"]
    assert "language=en-US" in log[1]["query"]


async def test_een_serie_en_een_seizoen(db, log):
    show = await db.details("show", "tv:900101", "nl-NL")
    assert show.kind == "show" and show.seasons == 2 and show.ids["tmdb"] == "900101"
    episodes = await db.season("tv:900101", 1, "nl-NL")
    assert [e.episode for e in episodes] == [1, 2, 3]
    assert all(e.overview for e in episodes)  # aflevering 2 uit en-US
    assert all(e.show_id == "tv:900101" and e.season == 1 for e in episodes)
    seasons = [e["inner"] for e in log if "/season/" in e["inner"]]
    assert seasons == ["/3/tv/900101/season/1", "/3/tv/900101/season/1"]
    with pytest.raises(ValueError):
        await db.season("movie:900001", 1, "nl-NL")


async def test_find_op_imdb(db, log):
    [found] = await db.find("tt9000011", "movie", "nl-NL")
    assert (found.id, found.title, found.year) == ("movie:900011", "The Long Way Home", 2019)
    assert "external_source=imdb_id" in log[0]["query"]
    # Een id in een verkeerde vorm vraagt niets
    assert await db.find("tt12", "movie", "nl-NL") == []
    assert await db.find("../../configuration", "movie", "nl-NL") == []
    assert len(log) == 1


async def test_een_onbekend_id_is_niet_gevonden(db):
    with pytest.raises(http.NotFound):
        await db.details("movie", "movie:123", "en-US")
    for wrong in ("movie:0", "tv:abc", "movie:1/../2", "person:5", ""):
        with pytest.raises(ValueError):
            await db.details("movie", wrong, "en-US")


async def test_een_foute_sleutel_is_provider_auth(settings, out, log):
    with pytest.raises(ProviderAuth):
        await tmdb.Tmdb(settings, "een-foute-sleutel-" + "q" * 20).search("movie", "x", None, "")
    assert log[0]["status"] == 401 and log[0]["auth_ok"] is False


async def test_429_kort_wacht_de_client_zelf(db, fake, clock):
    fake.state.fake.fault("429", "/3/search/movie", host="tmdb", retry_after=1)
    found = await db.search("movie", "Copper Sky", None, "en-US")
    assert found[0].id == "movie:900012"
    assert 1.0 in clock.slept


async def test_429_lang_is_provider_rate(db, fake, log):
    fake.state.fake.fault("429", "/3/search/movie", host="tmdb", retry_after=30)
    with pytest.raises(ProviderRate) as caught:
        await db.search("movie", "Copper Sky", None, "en-US")
    assert caught.value.retry_after == 30
    # Tot dan gaat er niets naar TMDB: meteen weer ProviderRate, zonder verzoek
    with pytest.raises(ProviderRate):
        await db.details("movie", "movie:900012", "en-US")
    assert len(log) == 1


@pytest.mark.parametrize("kind", ["500", "garbage", "drop", "wrongtype"])
async def test_fouten_van_tmdb_zijn_provider_unavailable(db, fake, kind):
    fake.state.fake.fault(kind, "/3/movie/*", host="tmdb")
    with pytest.raises(ProviderUnavailable):
        await db.details("movie", "movie:900012", "en-US")
    # Daarna weer goed
    assert (await db.details("movie", "movie:900012", "en-US")).title == "Copper Sky"


async def test_traag_is_provider_unavailable(settings, clock):
    async def nap(seconds):
        import asyncio

        await asyncio.sleep(seconds)

    app = create_app(FakeConfig(tmdb_key=KEY, data=load(), clock=clock, sleep=nap))
    app.state.fake.fault("slow", "/3/search/movie", host="tmdb", seconds=5)
    o = http.Outbound(
        settings, resolver=kit.Resolver(), transport=kit.transport(app), total_timeout=0.3
    )
    http.use(o)
    try:
        with pytest.raises(ProviderUnavailable, match="time-out"):
            await tmdb.Tmdb(settings, KEY).search("movie", "Copper Sky", None, "en-US")
    finally:
        await o.aclose()


async def test_plaatjes_zonder_sleutel(settings, db, log):
    url = tmdb.image_url(settings, "poster", "/p900001.jpg")
    assert url == "https://image.tmdb.org/t/p/w500/p900001.jpg"
    assert tmdb.image_url(settings, "backdrop", "/b900001.jpg").endswith("/w1280/b900001.jpg")
    for wrong in ("p900001.jpg", "/../x.jpg", "/p900001.gif", "/a/b.jpg", "//evil/x.jpg"):
        assert tmdb.image_url(settings, "poster", wrong) is None
    fetched = await http.get_image("tmdb", url)
    assert fetched.content_type == "image/jpeg" and art.sniff(fetched.body) == "image/jpeg"
    [entry] = log
    assert entry["service"] == "image" and entry["auth"] is None


# --- MusicBrainz en de CAA ---------------------------------------------------------------------


async def test_musicbrainz_zoeken_en_opzoeken(mb, log):
    found = await mb.search_album("Zeegezichten", "Nep Orkest")
    first = found[0]
    assert (first.id, first.title, first.artist, first.track_count) == (
        ZEE,
        "Zeegezichten",
        "Nep Orkest",
        10,
    )
    # Een zoekresultaat zegt niets over de voorkant (zoals bij de echte); de release wel
    assert first.cover is False
    release = await mb.release(ZEE)
    assert release.group_id and release.year == 2001 and release.cover is True
    for entry in log:
        assert entry["status"] == 200 and "fmt=json" in entry["query"]
        assert entry["headers"]["user-agent"].startswith("alles-in-1-platform/")
        assert "@" not in entry["headers"]["user-agent"]
    assert "release%3A%22Zeegezichten%22" in log[0]["query"]
    details = await mb.details("album", f"release:{ZEE}", "")
    assert details.ids["musicbrainz"] == ZEE and details.images[0].provider == "caa"


async def test_musicbrainz_lucene_ontsnapt(mb, log):
    """Aanhalingstekens en een backslash in een titel breken de query niet open."""
    assert await mb.search_album('Zee" OR artist:"x', None) == []
    assert log[0]["status"] == 200
    assert musicbrainz.phrase('a"b\\c') == '"a\\"b\\\\c"'


async def test_musicbrainz_een_verzoek_per_seconde(mb, log, clock):
    """Vijf verzoeken achter elkaar: de token bucket spreidt ze over ruim 4 s (nepklok), en de nep
    (die met dezelfde klok telt en boven 1 per seconde 503 geeft) weigert er geen."""
    start = clock.now
    for title in ("Zeegezichten", "Lanterns", "Static", "Tweede Plaat", "Nepcompilatie"):
        await mb.search_album(title, None)
    assert clock.now - start >= 4.0
    assert [e["status"] for e in log] == [200] * 5
    times = [e for e in clock.slept if e > 0]
    assert len(times) >= 4


async def test_musicbrainz_onbekend_en_verkeerd(mb):
    with pytest.raises(http.NotFound):
        await mb.release(str(uuid.uuid4()))
    for wrong in ("nep", "release:x", "../x"):
        with pytest.raises(ValueError):
            await mb.release(wrong)


async def test_de_voorkant_via_de_omleiding(settings, out, log):
    cover = caa.CoverArt(settings)
    fetched = await cover.front(ZEE)
    assert fetched.content_type == "image/jpeg" and art.sniff(fetched.body) == "image/jpeg"
    hosts = [e["service"] for e in log]
    assert hosts == ["caa", "archive"]
    with pytest.raises(http.NotFound):
        await cover.front(COMPILATIE)  # geen voorkant
    with pytest.raises(ValueError):
        await cover.front("geen-mbid")
    assert cover.front_url("../x") is None


# --- de fuzztest (regel 10) ----------------------------------------------------------------------


def _junk(rng: random.Random, depth: int = 0):
    kinds = ["int", "float", "str", "none", "bool", "list", "dict", "big", "nan", "neg"]
    kind = rng.choice(kinds if depth < 3 else kinds[:5])
    if kind == "int":
        return rng.randint(-5, 10**6)
    if kind == "float":
        return rng.uniform(-1e3, 1e3)
    if kind == "str":
        alphabet = "ab é/\\.:-\x00\n\"'<>{}0123456789"
        return "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 40)))
    if kind == "none":
        return None
    if kind == "bool":
        return rng.random() < 0.5
    if kind == "list":
        return [_junk(rng, depth + 1) for _ in range(rng.randint(0, 4))]
    if kind == "dict":
        return {rng.choice(["id", "title", "name", "x"]): _junk(rng, depth + 1) for _ in range(3)}
    if kind == "big":
        return 10**30
    if kind == "nan":
        return float("nan")
    return -1


def _mutate(rng: random.Random, value, rate: float = 0.25):
    """Een echt antwoord met hier en daar iets anders: een ander soort, weg, of rommel."""
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            r = rng.random()
            if r < rate / 3:
                continue
            out[k] = _junk(rng) if r < rate else _mutate(rng, v, rate)
        return out
    if isinstance(value, list):
        return [_mutate(rng, v, rate) for v in value if rng.random() > rate / 4]
    return _junk(rng) if rng.random() < rate else value


async def _real_answers(db, mb) -> dict[str, list]:
    """Echte antwoorden van de nep, om te verknoeien."""
    found = await http.get_json(
        "tmdb",
        db.base + "/search/movie",
        params={"query": "the", "language": "en-US"},
        headers={"Authorization": f"Bearer {KEY}"},
    )
    movie = await db._get("/movie/900001", {"append_to_response": "external_ids"})
    show = await db._get("/tv/900101", {"append_to_response": "external_ids"})
    season = await db._get("/tv/900101/season/1")
    release = await mb._get(f"/release/{ZEE}", {"inc": "release-groups+artist-credits+media"})
    return {
        "results": found["results"],
        "movie": [movie],
        "show": [show],
        "season": [season],
        "release": [release],
    }


async def test_fuzz_de_lezers_met_een_vaste_seed(db, mb):
    answers = await _real_answers(db, mb)
    rng = random.Random(SEED)
    for _ in range(400):
        for result in answers["results"][:3]:
            for kind in ("movie", "tv"):
                c = tmdb.candidate(_mutate(rng, result), kind)
                if c is not None:
                    assert c.id.startswith(f"{kind}:") and c.title
                    assert c.poster is None or tmdb.IMAGE_PATH.match(c.poster)
            tmdb.candidate(_junk(rng), "movie")  # mag niet vallen
        for raw, kind in ((answers["movie"][0], "movie"), (answers["show"][0], "tv")):
            try:
                d = tmdb.details_of(_mutate(rng, copy.deepcopy(raw)), kind)
            except ProviderUnavailable:
                continue
            assert d.title and all(tmdb.IMAGE_PATH.match(i.path) for i in d.images)
            assert all(isinstance(v, str) for v in d.ids.values())
        episodes = tmdb.episodes_of(_mutate(rng, answers["season"][0]), "tv:900101", 1)
        assert len({e.episode for e in episodes}) == len(episodes)
        r = musicbrainz.release_of(_mutate(rng, answers["release"][0]))
        if r is not None:
            assert musicbrainz.mbid(r.id) == r.id
            assert r.track_count is None or 0 < r.track_count <= 10_000 * 100
        musicbrainz.release_of(_junk(rng))  # mag niet vallen
    # En helemaal geen object
    for value in (None, [], "x", 5, [{"id": 1}]):
        assert tmdb.candidate(value, "movie") is None
        assert musicbrainz.release_of(value) is None
        assert tmdb.episodes_of({"episodes": value}, "tv:1", 1) == []


def test_fuzz_de_bytes_van_een_plaatje():
    rng = random.Random(SEED)
    heads = [b"\xff\xd8\xff", b"\x89PNG\r\n\x1a\n", b"RIFF\0\0\0\0WEBP", b"GIF89a", b"<svg", b""]
    for _ in range(2000):
        body = rng.choice(heads) + rng.randbytes(rng.randint(0, 64))
        if rng.random() < 0.3:
            body = body[: rng.randint(0, len(body))]
        kind = art.sniff(body)
        assert kind in (None, "image/jpeg", "image/png", "image/webp")
        try:
            assert art.check(body) == kind
        except art.Refused:
            assert kind is None


def test_fuzz_json_van_buiten():
    """parse_json: alleen een object; al het andere is een nette fout, ook heel diep."""
    rng = random.Random(SEED)
    samples = [b'{"a": 1}', b"[1, 2]", b"", b"\xff\xfe", b"{" * 5000, b'{"a": ' + b"[" * 5000]
    for _ in range(500):
        samples.append(rng.randbytes(rng.randint(0, 50)))
        samples.append(json.dumps(_junk(rng)).encode())
    for body in samples:
        try:
            assert isinstance(http.parse_json(body), dict)
        except http._Failure:
            pass


# --- de vorm van de echte antwoorden -------------------------------------------------------------


def differences(fake, real, where: str = "") -> list[str]:
    """Waar de vorm van de nep afwijkt van de opgenomen vorm: een sleutel die de echte niet heeft,
    of een ander soort (null mag altijd: TMDB geeft vaak null)."""
    out: list[str] = []
    if isinstance(fake, dict) and isinstance(real, dict):
        for k, v in fake.items():
            if k not in real:
                out.append(f"{where}/{k}: niet bij de echte")
            else:
                out += differences(v, real[k], f"{where}/{k}")
        return out
    if isinstance(fake, list) and isinstance(real, list):
        if fake and real:
            out += differences(fake[0], real[0], where + "[]")
        return out
    if "null" in (fake, real) or fake == real:
        return out
    if {fake, real} <= {"int", "float"}:
        return out
    return [f"{where}: {fake} bij de nep, {real} bij de echte"]


def test_differences_vindt_wat_anders_is():
    real = {"a": "int", "b": [{"c": "str"}], "d": "null"}
    assert differences({"a": "float", "b": [{"c": "str"}], "d": "str"}, real) == []
    assert differences({"x": "int"}, real) == ["/x: niet bij de echte"]
    assert differences({"b": [{"c": "int"}]}, real) == ["/b[]/c: int bij de nep, str bij de echte"]


async def test_de_nep_heeft_de_vorm_van_de_echte(db, mb):
    if not providers.SHAPES.is_file():
        pytest.skip("geen provider-shapes.json: Jonas neemt hem op met media-provider-check")
    recorded = json.loads(providers.SHAPES.read_text())
    ours = {
        "tmdb": await providers._record_tmdb(db),
        "musicbrainz": await providers._record_musicbrainz(mb),
    }
    problems = []
    for service, endpoints in ours.items():
        for name, shape in endpoints.items():
            real = recorded.get(service, {}).get(name)
            if real is not None:
                problems += [f"{service} {name}{p}" for p in differences(shape, real)]
    assert problems == []


# --- de cli: providers.check ---------------------------------------------------------------------


async def _media_config(settings, **values):
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app import db as app_db
    from app.media.state import MEDIA_KEY
    from app.models import Setting

    async with async_sessionmaker(app_db.get_engine(settings))() as session:
        session.add(Setting(key=MEDIA_KEY, value=values))
        await session.commit()


async def _save_key(settings):
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app import db as app_db

    async with async_sessionmaker(app_db.get_engine(settings), expire_on_commit=False)() as s:
        await secrets.save_tmdb(s, settings, KEY)


async def test_check_zonder_sleutel_vraagt_niets(settings, out, log):
    assert await providers.check(settings, outbound=out) == {
        "tmdb": "off",
        "musicbrainz": "off",
        "caa": "off",
    }
    assert log == []


async def test_check_met_sleutel_en_musicbrainz(settings, out, log, fake):
    await _save_key(settings)
    await _media_config(settings, musicbrainz=True)
    log.clear()
    assert await providers.check(settings, outbound=out) == {
        "tmdb": "ok",
        "musicbrainz": "ok",
        "caa": "ok",
    }
    fake.state.fake.config.tmdb_key = "een-andere-sleutel-" + "z" * 20
    found = await providers.check(settings, outbound=out)
    assert found["tmdb"] == "auth"


async def test_check_onbereikbaar(settings, out, fake):
    await _save_key(settings)
    fake.state.fake.fault("500", "/3/configuration", host="tmdb", times=None)
    assert (await providers.check(settings, outbound=out))["tmdb"] == "unavailable"


async def test_opnemen_schrijft_alleen_de_vorm(settings, out, tmp_path, log):
    await _save_key(settings)
    await _media_config(settings, musicbrainz=True)
    target = tmp_path / "vorm" / "provider-shapes.json"
    await providers.check(settings, record=True, path=target, outbound=out)
    text = target.read_text()
    document = json.loads(text)
    assert set(document) >= {"_opgenomen", "_versie", "tmdb", "musicbrainz"}
    assert set(document["tmdb"]) == {
        "configuration",
        "search/movie",
        "movie/{id}",
        "find/{imdb_id}",
        "search/tv",
        "tv/{id}",
        "tv/{id}/season/{n}",
    }
    assert document["tmdb"]["movie/{id}"]["title"] == "str"
    assert document["tmdb"]["movie/{id}"]["external_ids"]["imdb_id"] == "str"
    # Geen waarde: geen sleutel, geen titel, geen id van de nep
    assert KEY not in text
    for value in ("Nephaven", "900001", "p900001", "tt90000", "Zeegezichten", ZEE):
        assert value not in text
    # En zonder --opnemen wordt er niets geschreven
    other = tmp_path / "niet.json"
    await providers.check(settings, path=other, outbound=out)
    assert not other.exists()


async def test_opnemen_met_een_fout_schrijft_niets(settings, out, tmp_path, fake):
    await _save_key(settings)
    fake.state.fake.config.tmdb_key = None
    target = tmp_path / "provider-shapes.json"
    assert (await providers.check(settings, record=True, path=target, outbound=out))[
        "tmdb"
    ] == "auth"
    assert not target.exists()


def test_shape_kent_geen_waarden():
    value = {"b": [1, 2], "a": {"x": "geheim", "y": None, "z": 1.5, "t": True}, "e": []}
    assert providers.shape(value) == {
        "a": {"t": "bool", "x": "str", "y": "null", "z": "float"},
        "b": ["int"],
        "e": [],
    }
    deep: dict = {}
    cur = deep
    for _ in range(30):
        cur["n"] = {}
        cur = cur["n"]
    assert "..." in json.dumps(providers.shape(deep))


async def test_transport_van_de_kit_is_de_nep(fake):
    """Een vreemde host gaat ook naar de nep (die hem met 404 weigert): nooit naar buiten."""
    async with httpx.AsyncClient(transport=kit.transport(fake)) as client:
        response = await client.get("https://evil.example/x")
    assert response.status_code == 404
