"""De parser van bestandsnamen (Keuze 7 en 8, M2): app/media/names.py.

- de testset tests/media/names.tsv, 100 % (≥ 500 regels, zelf geschreven, in groepen);
- names-open.tsv: wat we bewust niet aankunnen, elk met een reden;
- eigen fuzz (geen hypothesis): 10.000 namen uit een vaste seed, uit dezelfde bouwstenen. Nooit
  een uitzondering, het jaar altijd binnen bereik, nooit een lege titel als er letters zijn, een
  naam zonder afleveringstoken (wel met resolutie, codec, audio en jaar) geeft nooit een
  aflevering, en een titel uit de bouwstenen komt ongeschonden terug. 10.000 namen in minder dan
  0,5 s;
- absolute nummering geeft season leeg (Keuze 8);
- Parsed als Mapping (app.cli media-parse), normalize en sort_title.
"""

from __future__ import annotations

import datetime as dt
import json
import random
import time
from pathlib import Path

import pytest

from app.media import names

HERE = Path(__file__).parent / "media"
STRICT = ("year", "season", "episode", "episode_end", "absolute", "date", "part", "edition",
          "extra")  # fmt: skip
IDS = ("imdb", "tmdb", "tvdb")
INTS = {"year", "season", "episode", "episode_end", "absolute", "part", "show_index",
        "show_year", "disc", "track"}  # fmt: skip


def rows(name: str) -> list[tuple[int, str, str, dict[str, str]]]:
    out = []
    for n, line in enumerate((HERE / name).read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip() or line.startswith("#"):
            continue
        kind, path, *cells = line.split("\t")
        exp = dict(cell.split("=", 1) for cell in cells)
        out.append((n, kind, path, exp))
    return out


def differences(kind: str, path: str, exp: dict[str, str]) -> list[str]:
    got = names.parse(path, kind)
    wrong = []

    def check(key: str, value) -> None:
        want = exp.get(key)
        if key in INTS and want is not None:
            want = int(want)
        if value != want:
            wrong.append(f"{key}: {value!r} != {want!r}")

    check("title", got.title)
    for key in STRICT:
        value = getattr(got, key)
        check(key, value.isoformat() if isinstance(value, dt.date) else value)
    for key in IDS:
        check(key, got.ids.get(key))
    for key in ("show", "show_index", "show_year", "episode_title", "artist", "album", "disc",
                "track"):  # fmt: skip
        if key in exp:
            check(key, getattr(got, key))
    for key in ("show_tvdb", "show_tmdb"):
        if key in exp:
            check(key, got.show_ids.get(key.removeprefix("show_")))
    return wrong


def test_de_testset_is_100_procent():
    found = rows("names.tsv")
    assert len(found) >= 500
    failures = []
    for n, kind, path, exp in found:
        wrong = differences(kind, path, exp)
        if wrong:
            failures.append(f"regel {n}: {kind} {path!r}: " + "; ".join(wrong))
    assert not failures, f"{len(failures)} van {len(found)} fout:\n" + "\n".join(failures)


def test_de_testset_heeft_de_groepen_van_het_ontwerp():
    text = (HERE / "names.tsv").read_text(encoding="utf-8")
    groups = {line.removeprefix("# groep: ") for line in text.splitlines()
              if line.startswith("# groep: ")}  # fmt: skip
    for group in ("films", "scene-tokens", "ids", "edities", "delen", "extras", "sxxeyy",
                  "meerdere", "specials", "datums", "absoluut", "nederlands", "jaartallen",
                  "unicode", "leestekens", "muziek", "rommel", "lijkt-op-aflevering"):  # fmt: skip
        assert group in groups, group
    pairs = [(kind, path) for _, kind, path, _ in rows("names.tsv")]
    assert len(pairs) == len(set(pairs)), "een regel staat er twee keer in"


def test_names_open_heeft_elk_een_reden_en_faalt_niet():
    found = rows("names-open.tsv")
    assert found
    known = {(kind, path) for _, kind, path, _ in rows("names.tsv")}
    for n, kind, path, exp in found:
        assert exp.get("reden"), f"regel {n} zonder reden"
        assert (kind, path) not in known, f"regel {n} staat ook in names.tsv"
        names.parse(path, kind)  # nooit een uitzondering


def test_absolute_nummering_verzint_geen_seizoen():
    """Keuze 8: [Groep] Titel - 012 geeft season leeg en absolute 12, ook in een map van de
    serie; nooit seizoen 1."""
    for path in ("[Groep] Titel - 012 [1080p].mkv", "Titel/[Groep] Titel - 012 [1080p].mkv",
                 "Titel/Titel - 012.mkv"):  # fmt: skip
        got = names.parse(path, "shows")
        assert got.season is None and got.episode is None, path
        assert got.absolute == 12 and got.is_episode, path


def test_een_naam_als_string_of_als_lijst():
    assert names.parse("Films/The Matrix (1999).mkv") == names.parse(
        ["Films", "The Matrix (1999).mkv"]
    )
    assert names.parse("Films\\The Matrix (1999).mkv").title == "The Matrix"
    assert names.parse("").title == ""
    assert names.parse([]).title == ""


def test_parsed_als_mapping_voor_de_cli():
    got = names.parse("Show/Season 1/Show 2024-03-01.mkv", "shows")
    data = dict(got)
    assert json.loads(json.dumps(data)) == data
    assert data["date"] == "2024-03-01"
    assert "season" not in data  # leeg: niet in de mapping
    with pytest.raises(KeyError):
        got["season"]
    assert len(got) == len(data)
    movie = dict(names.parse("Films/The Matrix (1999).mkv"))
    assert movie["title"] == "The Matrix" and movie["year"] == 1999


def test_weinig_zeker_onder_een_half():
    """Op /beheer staat per bibliotheek een lijst "Weinig zeker" (zekerheid < 0,5)."""
    for path in ("video.mkv", "1.mp4", "DSC_0001.MOV", "movie.mkv"):
        assert names.parse(path).confidence < 0.5, path
    for path in ("The Matrix (1999).mkv", "Inception (2010)/video.mkv",
                 "The.Matrix.1999.1080p.BluRay.x264-GROUP.mkv"):  # fmt: skip
        assert names.parse(path).confidence >= 0.5, path
    assert names.parse("Show/Season 1/Show S01E02.mkv", "shows").confidence >= 0.5
    assert names.parse("The Matrix (1999) [imdbid-tt0133093].mkv").confidence > 0.9


def test_normalize_en_sorteren():
    assert names.normalize("Amélie: Ça Va!") == "amelie ca va"
    assert names.sort_title("The Matrix") == "matrix"
    assert names.sort_title("De Lift") == "lift"
    assert names.sort_title("Het Meisje") == "meisje"
    assert names.sort_title("A") == "a"  # alleen een lidwoord: dan dat
    assert names.sort_title("Up") == "up"


def test_een_heel_lange_naam_en_rare_tekens():
    long = "A" * 5000 + ".mkv"
    assert len(names.parse(long).title) <= 255
    assert names.parse("\x00\x01\x02.mkv").title is not None
    assert names.parse("((((((((((.mkv").title is not None
    assert names.parse("....mkv").title is not None
    assert names.parse("-.mkv", "shows") is not None


# --- fuzz -----------------------------------------------------------------------------------------

# fmt: off
TITLE_WORDS = [
    "Matrix", "Dune", "Inception", "Night", "Of", "The", "Blue", "Red", "Zwart", "Boek", "Amélie",
    "Café", "Ocean's", "Spider-Man", "X-Men", "Mad", "Max", "Fury", "Road", "Lost", "City",
    "Liefde", "Oranje", "Soldaat", "Van", "Het", "Meisje", "Borís", "Крёстный", "千尋", "Up",
    "Her", "It", "Heat", "Alien", "Coco", "Joker", "Moon", "Sun", "Star", "Wars", "Apollo", "13",
    "Seven", "Eleven", "Twelve", "Rock", "Roll", "&", "Stad", "Kind", "Dag", "Nacht", "Zomer",
]
# fmt: on
FIRST_ONLY = ["2001", "1917", "1984", "2012"]  # een jaartal kan een titel beginnen
RESOLUTION = ["480p", "720p", "1080p", "2160p", "1920x1080", "1280x720", "4K", "UHD"]
SOURCE = ["BluRay", "WEB-DL", "WEBRip", "HDTV", "DVDRip", "BDRip", "REMUX", "AMZN"]
CODEC = ["x264", "x265", "H.264", "H.265", "HEVC", "AVC", "XviD", "10bit", "AV1"]
AUDIO = ["5.1", "7.1", "2.0", "DDP5.1", "AAC2.0", "DTS-HD.MA.5.1", "TrueHD.Atmos.7.1", "AC3"]
RELEASE = ["PROPER", "REPACK", "GRP", "-SPARKS", "-YIFY", "[ABCD1234]", "-NTb"]
EXT = ["mkv", "mp4", "avi", "m4v", "ts", "webm"]
SEPS = [".", " ", "_"]
EPISODE = ["S01E02", "s1e2", "1x02", "S03E04E05", "Season 1 Episode 2"]


def build(rng: random.Random, episode: bool) -> tuple[str, str, int | None]:
    """(naam, de titel die terug moet komen, het jaar of None)."""
    sep = rng.choice(SEPS)
    words = []
    if rng.random() < 0.1:
        words.append(rng.choice(FIRST_ONLY))
    words += [rng.choice(TITLE_WORDS) for _ in range(rng.randint(1, 4))]
    if words[-1] == "&":
        words.append("Roll")
    title_text = sep.join(words)
    pieces = [title_text]
    year = None
    if rng.random() < 0.7:
        year = rng.randint(1920, dt.date.today().year)
        pieces.append(f"({year})" if sep == " " and rng.random() < 0.5 else str(year))
    if episode:
        pieces.append(rng.choice(EPISODE))
    noise = False
    for pool, p in ((RESOLUTION, 0.7), (SOURCE, 0.5), (CODEC, 0.6), (AUDIO, 0.5)):
        if rng.random() < p:
            pieces.append(rng.choice(pool))
            noise = True
    name = sep.join(pieces)
    # Een groep (-SPARKS) alleen na ruis: "Stad-SPARKS" zonder ruis is een titel met een streep
    if noise and rng.random() < 0.3:
        release = rng.choice(RELEASE)
        name += release if release.startswith(("-", "[")) else sep + release
    expected = " ".join(words)
    return f"{name}.{rng.choice(EXT)}", expected, year


def fuzz_names(seed: int, n: int) -> list[tuple[str, str, int | None, bool]]:
    rng = random.Random(seed)
    out = []
    for _ in range(n):
        episode = rng.random() < 0.3
        name, title, year = build(rng, episode)
        if rng.random() < 0.2:
            name = f"{title}/{name}" if "/" not in title else name
        out.append((name, title, year, episode))
    return out


def test_fuzz_10000_namen_met_vaste_seed():
    cases = fuzz_names(20261029, 10_000)
    top = dt.date.today().year + 1
    # De beste van drie keer, elke keer zonder wat de parser onthoudt: een gedeelde machine
    # (CI, een tweede agent) kan één keer haperen; de parser zelf wordt zo niet sneller
    elapsed = 9.0
    for _ in range(3):
        names._movie_core.cache_clear()
        started = time.perf_counter()
        results = [
            names.parse(name, "shows" if episode else "movies") for name, *_, episode in cases
        ]
        elapsed = min(elapsed, time.perf_counter() - started)
    assert elapsed < 0.5, f"10.000 namen in {elapsed:.3f} s"
    for (name, title, year, episode), got in zip(cases, results, strict=True):
        assert got.year is None or names.YEAR_MIN <= got.year <= top, name
        if any(c.isalpha() for c in name.rsplit(".", 1)[0]):
            assert got.title, name
        if not episode:
            assert got.episode is None and got.season is None, name
            assert got.absolute is None and got.episode_end is None, name
        assert got.title == title, (name, got.title, title)
        assert got.year == year, (name, got.year, year)
        if episode:
            assert got.is_episode, name


def test_zonder_afleveringstoken_nooit_een_aflevering():
    """De eigenschap over de uitkomst, ook in een bibliotheek met series: een naam die zonder
    afleveringstoken is gebouwd (wel met resolutie, codec, audio en jaar) is nooit een
    aflevering."""
    rng = random.Random(5)
    for _ in range(3000):
        name, _, _ = build(rng, episode=False)
        got = names.parse(name, "shows")
        assert not got.is_episode, (name, dict(got))
        assert got.season is None and got.episode is None and got.absolute is None, name


def test_fuzz_willekeurige_tekens_geeft_nooit_een_fout():
    rng = random.Random(77)
    alphabet = "abcXYZ019 ._-[](){}xSEe/\\éß千\x00\t'&!?,"
    started = time.perf_counter()
    for _ in range(5000):
        text = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 80)))
        for kind in ("movies", "shows", "music", "videos"):
            got = names.parse(text, kind)
            assert isinstance(got.title, str)
            assert got.year is None or names.YEAR_MIN <= got.year
    assert time.perf_counter() - started < 10
