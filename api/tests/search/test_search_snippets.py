"""De snippets (bouwontwerp fase 6, Keuze 8 en 13; app/search/snippets.py).

Wat hier vastligt:
- "strasse" vindt en markeert "Straße", "grosse" ook "GROẞE", "resume" ook "résumé" met een
  los accent; het laatste woord als voorvoegsel (gemarkeerd: het stuk dat de vraag heeft), een
  woord met een spatie erachter als heel woord;
- gewone tekst, nooit HTML: "<b>" komt er letterlijk in, stuurtekens, tekens die de richting
  omdraaien en regeleinden worden een spatie; alleen de velden t en m;
- een treffer na teken 20.000 geeft snippet null met match "text" (de SQL leest alleen het
  venster), een treffer die net binnen het venster eindigt een snippet;
- de klok (met een nepklok): hooguit 40 ms per verzoek en 100 ms per seconde voor de hele app;
  een naam-treffer kost niets en blijft "name";
- de kolommen in de volgorde van bm25 (title, text, ocr, meta), de OCR van een foto alleen
  voor de eigenaar;
- het werk draait in een thread met een limiter van 1;
- Server-Timing alleen met search_timing.
"""

from __future__ import annotations

import gc
import itertools
import random
import threading
import uuid

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app import analysis
from app.db import get_engine
from app.models import User
from app.search import limits, snippets
from app.search.query import parse
from app.search.snippets import WINDOW, Snip, Source, build, pattern, snippet
from app.text import fold
from tests.search import wereld as W
from tests.search.test_search_access import kop

pytestmark = pytest.mark.anyio


@pytest.fixture(autouse=True)
def _lege_klok():
    """De klok van de hele app is van het proces: elke test begint en eindigt met een lege."""
    snippets.APP.clear()
    yield
    snippets.APP.clear()


@pytest.fixture(autouse=True)
def _zonder_volle_gc(request):
    """Een test via de API draait zonder volle ronde van de garbage collector, zoals measuring() in
    tests/test_search.py. In een volledige run heeft dit proces honderdduizenden objecten van
    eerdere tests; een volle ronde kost dan 0,4 tot 1 s (gemeten bij de samenvoeging van golf 3).
    Viel die in de stap snippets van een verzoek, dan was het budget van 40 ms op en kwam de
    treffer zonder snippet, zoals het hoort (Keuze 8); de test wil de snippet zien. Dat is de
    test, niet het zoeken."""
    if "client" not in request.fixturenames:
        yield
        return
    gc.collect()
    enabled = gc.isenabled()
    gc.disable()
    try:
        yield
    finally:
        if enabled:
            gc.enable()


@pytest.fixture(autouse=True)
def _zonder_snelheidsgrens(monkeypatch):
    monkeypatch.setattr(limits, "RATE", {"search": 100_000, "suggest": 100_000})


@pytest.fixture(autouse=True)
def _ruime_grens(settings, monkeypatch):
    """Het gaat om de snippets, niet om de tijdsgrens (zie test_search_access.py)."""
    monkeypatch.setattr(settings, "search_deadline_ms", 10_000)


def _stukken(text: str, q: str) -> list[tuple[str, bool]] | None:
    parsed = parse(q)
    assert parsed is not None
    parts = snippet(text, pattern(parsed))
    return None if parts is None else [(p.t, p.m) for p in parts]


def _gemarkeerd(text: str, q: str) -> list[str]:
    return [t for t, m in _stukken(text, q) or [] if m]


# --- De tekst ---------------------------------------------------------------------------------


def test_ss_en_accenten_worden_gevonden_en_gemarkeerd():
    assert _gemarkeerd("Die Straße in Berlin", "strasse") == ["Straße"]
    assert _gemarkeerd("Die Strasse in Berlin", "Straße") == ["Strasse"]
    assert _gemarkeerd("EINE GROẞE STADT", "grosse") == ["GROẞE"]
    # Een accent als los teken hoort bij de treffer
    assert _gemarkeerd("Mijn résumé staat hier", "resume") == ["résumé"]
    assert _gemarkeerd("Een café in Utrecht", "cafe utrecht") == ["café", "Utrecht"]
    # De tekst ervoor en erna blijft zoals hij was
    assert _stukken("Die Straße in Berlin", "strasse") == [
        ("Die ", False),
        ("Straße", True),
        (" in Berlin", False),
    ]


def test_heel_woord_of_voorvoegsel_zoals_parse():
    tekst = "Kunstwerk art artikel partner"
    # "art" aan het eind van de vraag is een voorvoegsel: ook het begin van artikel (het stuk dat
    # de vraag heeft, gemarkeerd), nooit midden in een woord (partner)
    assert _stukken(tekst, "art") == [
        ("Kunstwerk ", False),
        ("art", True),
        (" ", False),
        ("art", True),
        ("ikel partner", False),
    ]
    # Met een spatie erachter een heel woord
    assert _gemarkeerd(tekst, "art ") == ["art"]
    # Een kort woord (onder de 3 tekens) is nooit een voorvoegsel
    assert _gemarkeerd("ab abc", "ab") == ["ab"]
    # Een frase: de woorden apart gemarkeerd
    assert _gemarkeerd("het verslag van het kwartaal", '"verslag kwartaal"') == [
        "verslag",
        "kwartaal",
    ]
    assert _stukken("niets hier", "zebra") is None


def test_alleen_tekst_nooit_html():
    tekst = 'Klik <b onclick="x">hier</b> &amp; zebra‮ omgedraaid\x00 en\nverder⁦ klaar'
    parts = _stukken(tekst, "zebra")
    assert parts == [
        ('Klik <b onclick="x">hier</b> &amp; ', False),
        ("zebra", True),
        (" omgedraaid en verder klaar", False),
    ]
    # Een treffer met een stuurteken erin blijft één stuk, het teken wordt een spatie
    assert _stukken("<script>zebra</script>", "zebra") == [
        ("<script>", False),
        ("zebra", True),
        ("</script>", False),
    ]
    # Alleen de velden t en m
    found = build([Source(uuid.uuid4(), "x", body="een zebra")], parse("zebra"))
    (snip,) = found.values()
    assert [p.model_dump() for p in snip.snippet] == [
        {"t": "een ", "m": False},
        {"t": "zebra", "m": True},
    ]


def test_het_stuk_rond_de_eerste_treffer():
    woorden = [f"woord{i}" for i in range(400)]
    woorden[200] = "zebra"
    woorden[205] = "zebra"
    woorden[300] = "zebra"  # buiten het stuk
    parts = _stukken(" ".join(woorden), "zebra")
    assert parts[0][0].startswith("…") and parts[-1][0].endswith("…")
    assert [t for t, m in parts if m] == ["zebra", "zebra"]
    tekst = "".join(t for t, _ in parts)
    assert len(tekst) <= snippets.SPAN + 2 * len(snippets.ELLIPSIS)
    # Niet midden in een woord: na "…" en vóór "…" hele woorden
    assert tekst[1:].split(" ")[0] in woorden
    assert tekst[:-1].split(" ")[-1] in woorden
    # Bovenaan geen "…"
    assert _stukken("zebra staat vooraan", "zebra")[0] == ("zebra", True)


# --- De kolommen ------------------------------------------------------------------------------


def test_de_kolommen_in_de_volgorde_van_bm25():
    parsed = parse("zebra")
    i = [uuid.uuid4() for _ in range(7)]
    found = build(
        [
            Source(i[0], "zebra.txt", title="zebra", body="zebra"),
            Source(i[1], "x", title="zebra", body="zebra"),
            Source(i[2], "x", title="iets", body="een zebra", ocr="zebra"),
            Source(i[3], "x", body="iets", ocr="de zebra"),
            Source(i[4], "x", body="iets", pocr="foto zebra", meta="zebra"),
            Source(i[5], "x", meta="zebra auteur"),
            Source(i[6], "x", body="ander woord"),
        ],
        parsed,
    )
    assert [found[n].match for n in i] == ["name", "title", "text", "ocr", "ocr", "meta", "text"]
    assert found[i[0]].snippet is None  # de naam staat er al
    assert found[i[6]].snippet is None  # gevonden, maar niet in het venster
    assert [p.t for p in found[i[4]].snippet if p.m] == ["zebra"]


# --- De klok ----------------------------------------------------------------------------------


class Klok:
    def __init__(self) -> None:
        self.t = 5_000.0

    def __call__(self) -> float:
        return self.t


@pytest.fixture
def klok(monkeypatch):
    """Een nepklok: elk document dat een snippet krijgt, kost klok.per seconden."""
    k = Klok()
    k.per = 0.015
    echt = snippets._one

    def duur(src, rx):
        k.t += k.per
        return echt(src, rx)

    monkeypatch.setattr(snippets, "CLOCK", k)
    monkeypatch.setattr(snippets, "_one", duur)
    return k


def _documenten(n: int, naam: str = "doc") -> list[Source]:
    return [Source(uuid.uuid4(), f"{naam}{i}", body=f"tekst {i} met zebra") for i in range(n)]


def test_hooguit_40_ms_per_verzoek(klok):
    """15 ms per document. De klok kijkt vóór elk document, en een document begint alleen als
    het nog past (geschat op het duurste tot nu toe): na twee (30 ms) zou een derde op 45 ms
    uitkomen (boven 40 - RETURN_MS), dus de rest krijgt snippet null met match text. Een
    document wordt nooit halverwege gestopt."""
    docs = _documenten(5)
    found = build(docs, parse("zebra"))
    met = [d.id for d in docs if found[d.id].snippet is not None]
    assert met == [d.id for d in docs[:2]]
    assert [found[d.id] for d in docs[2:]] == [Snip("text", None)] * 3
    assert klok.t == pytest.approx(5_000.030)
    # Een nieuw verzoek heeft weer 40 ms (de app heeft nog 70 over)
    found = build(docs, parse("zebra"))
    assert sum(found[d.id].snippet is not None for d in docs) == 2


def test_wat_de_stap_al_kostte_telt_mee_in_de_schatting(klok):
    """De SQL kostte al 20 ms: één document van 15 ms past (35), een tweede niet meer (50)."""
    docs = _documenten(3)
    found = build(docs, parse("zebra"), 20.0)
    assert [found[d.id].snippet is not None for d in docs] == [True, False, False]


def test_een_naam_treffer_kost_geen_tijd(klok):
    docs = [Source(uuid.uuid4(), f"zebra{i}", body="zebra") for i in range(10)]
    found = build(docs, parse("zebra"))
    assert {s.match for s in found.values()} == {"name"}
    assert klok.t == 5_000.0
    # Ook als het budget op is, blijft een naam-treffer "name"
    klok.per = 0.050
    gemengd = _documenten(2) + docs[:2]
    found = build(gemengd, parse("zebra"))
    assert [found[d.id].match for d in gemengd] == ["text", "text", "name", "name"]
    assert found[gemengd[0].id].snippet is not None and found[gemengd[1].id].snippet is None


def test_hooguit_100_ms_per_seconde_voor_de_app(klok):
    """30 ms per document: elk verzoek één (een tweede zou op 60 ms uitkomen). De app staat na
    drie verzoeken op 90 ms; met 10 over past een document van 30 niet meer, ook niet als
    eerste van een nieuw verzoek (de app schat op het duurste in zijn venster). Na een seconde
    weer wel."""
    klok.per = 0.030
    docs = _documenten(4)
    parsed = parse("zebra")

    def met_snippet() -> int:
        return sum(s.snippet is not None for s in build(docs, parsed).values())

    assert [met_snippet() for _ in range(3)] == [1, 1, 1]
    found = build(docs, parsed)
    assert list(found.values()) == [Snip("text", None)] * 4
    klok.t += 1.0
    assert met_snippet() == 1


# --- Met de app -------------------------------------------------------------------------------


def _sessions(settings):
    return async_sessionmaker(get_engine(settings), expire_on_commit=False)


async def _zoek(client, headers, q: str) -> dict[uuid.UUID, dict]:
    r = await client.get("/api/v1/search", params={"q": q}, headers=headers)
    assert r.status_code == 200, r.text
    page = r.json()
    assert page["engine"] == "fts", page
    return {uuid.UUID(h["node"]["id"]): h for h in page["results"]}


async def test_de_snippet_in_het_antwoord(client, settings):
    w = await W.bouw_async(settings)
    eva = await kop(client, "eva")
    hits = await _zoek(client, eva, W.TEKST_WOORD)
    doc = hits[w.ids["doc"]]
    assert doc["match"] == "text"
    assert [p["t"] for p in doc["snippet"] if p["m"]] == [W.TEKST_WOORD]
    tekst = "".join(p["t"] for p in doc["snippet"])
    assert tekst == f"Verslag Het verslag noemt {W.TEKST_WOORD} en de kwartaalcijfers."
    # Op naam: geen snippet
    hits = await _zoek(client, eva, "verslag kwartaal")
    assert hits[w.ids["doc"]]["match"] == "name"
    assert hits[w.ids["doc"]]["snippet"] is None


async def test_een_treffer_na_teken_20000(client, settings):
    """De tekst van Plan.txt: 19.988 tekens vulling, dan binnenwoord (eindigt op teken 19.999),
    een spatie, dan buitenwoord (begint op teken 20.001). FTS vindt beide (de hele tekst staat
    in de index); de snippet alleen binnenwoord."""
    w = await W.bouw_async(settings)
    vulling = "vul " * 4997
    body = vulling + "binnenwoord " + "buitenwoord en verder"
    assert body.index("buitenwoord") == WINDOW
    async with _sessions(settings)() as db:
        await analysis.write_text(db, w.shas["plan"], body=body)
    eva = await kop(client, "eva")
    binnen = (await _zoek(client, eva, "binnenwoord"))[w.ids["plan"]]
    assert binnen["match"] == "text"
    assert [p["t"] for p in binnen["snippet"] if p["m"]] == ["binnenwoord"]
    buiten = (await _zoek(client, eva, "buitenwoord"))[w.ids["plan"]]
    assert buiten["match"] == "text" and buiten["snippet"] is None


async def test_ss_in_de_tekst_via_de_api(client, settings):
    w = await W.bouw_async(settings)
    async with _sessions(settings)() as db:
        await analysis.write_text(db, w.shas["plan"], body="Wir wohnen in der Großen Straße 5")
    eva = await kop(client, "eva")
    for q in ("strasse", "STRASSE", "straße", "grossen strasse"):
        hit = (await _zoek(client, eva, q))[w.ids["plan"]]
        gemarkeerd = [p["t"] for p in hit["snippet"] if p["m"]]
        assert "Straße" in gemarkeerd, q


async def test_de_ocr_van_een_foto_alleen_voor_de_eigenaar(client, settings):
    """Eva vindt haar foto op de OCR-tekst, met de snippet uit die tekst. De SQL leest die
    tekst niet voor Bram, ook niet als hij de foto op naam vindt."""
    w = await W.bouw_async(settings)
    eva = await kop(client, "eva")
    foto = (await _zoek(client, eva, W.FOTO_OCR_WOORD))[w.ids["foto"]]
    assert foto["match"] == "ocr"
    assert [p["t"] for p in foto["snippet"] if p["m"]] == [W.FOTO_OCR_WOORD]
    bram = await kop(client, "bram")
    assert (await _zoek(client, bram, "IMG_utrecht"))[w.ids["foto"]]["match"] == "name"
    async with _sessions(settings)() as db:
        voor_eva = await snippets.sources(db, await db.get(User, w.users["eva"]), [w.ids["foto"]])
        bram_user = await db.get(User, w.users["bram"])
        voor_bram = await snippets.sources(db, bram_user, [w.ids["foto"]])
    assert voor_eva[0].pocr == W.FOTO_OCR_WOORD
    assert voor_bram[0].pocr is None
    # Brams snippet op de naam komt nooit uit de OCR-tekst
    found = build(voor_bram, parse(f"img {W.FOTO_OCR_WOORD}"))
    assert found[w.ids["foto"]] == Snip("text", None)


def test_de_sql_telt_mee_in_de_40_ms():
    """De stap snip (Server-Timing) is SQL plus build plus de weg terug: wat de SQL al kostte,
    gaat van het budget af, en RETURN_MS blijft over (§4.4: snip p95 ≤ 40 ms, ook in "groot")."""
    docs = [Source(uuid.uuid4(), "x", body="een zebra") for _ in range(3)]
    assert all(s.snippet for s in build(docs, parse("zebra"), 0.0).values())
    op = build(docs, parse("zebra"), snippets.REQUEST_MS)
    assert {(s.match, s.snippet) for s in op.values()} == {("text", None)}
    # De weg terug uit de thread hoort ook bij de stap: RETURN_MS blijft over
    op = build(docs, parse("zebra"), snippets.REQUEST_MS - snippets.RETURN_MS)
    assert {(s.match, s.snippet) for s in op.values()} == {("text", None)}


@pytest.mark.parametrize("seed", range(20))
def test_de_weg_terug_zoals_per_teken(seed):
    """Folded.back() (alleen de tekens die niet precies één teken geven, met bisect) geeft voor
    elke plek in de gevouwen tekst dezelfde plek in de tekst als een tabel per teken; ook als
    de tekst in stukken gevouwen wordt."""
    rnd = random.Random(seed)
    stukjes = ["a", "Z", " ", "7", "ß", "ẞ", "e\u0301", "\u0301", "ﬁ", "é", "½", "\u200b"]
    tekst = "".join(rnd.choice(stukjes) for _ in range(rnd.randint(1, 400)))
    per_teken = [i for i, c in enumerate(tekst) for _ in fold(c)]
    f = snippets.Folded(tekst, 0)
    grenzen = sorted(rnd.sample(range(1, len(tekst) + 1), min(3, len(tekst))))
    for n in [*grenzen, len(tekst)]:
        f.extend(n)
        assert f.out == "".join(fold(c) for c in tekst[:n])
        verwacht = [i for i in per_teken if i < n]
        assert [f.back(i) for i in range(len(f.out))] == verwacht
        assert f.back(len(f.out)) == n


@pytest.mark.parametrize("plek", [0, 1_990, 1_998, 7_990, 12_000, 19_990])
def test_in_stappen_gevouwen_zoals_in_een_keer(plek):
    """Vouwen in stappen (2.000, 8.000, het hele venster) geeft hetzelfde als alles in één keer,
    ook als het woord over de rand van een stap loopt, of als er een langer woord op de rand
    staat dat met het woord begint; ook met ß ervoor (gevouwen "ss": de plekken in de gevouwen
    tekst en in de tekst verschillen dan)."""
    rx = snippets.pattern(parse("zebrapad"))
    for vul, voor in itertools.product(("x", "ß"), ("", "zebrapadden ")):
        tekst = (vul * max(0, plek - len(voor) - 1) + " " + voor + "zebrapad en verder")[:20_000]
        stappen = snippets.snippet(tekst, rx)
        heel = snippets.STEPS
        try:
            snippets.STEPS = ()
            in_een_keer = snippets.snippet(tekst, rx)
        finally:
            snippets.STEPS = heel
        assert stappen == in_een_keer
        # "zebrapadden" begint met het woord: het laatste woord is een voorvoegsel (parse)
        verwacht = ["zebrapad"] * (2 if voor else 1)
        assert stappen is not None and [p.t for p in stappen if p.m] == verwacht


async def test_in_een_thread_met_een_limiter_van_1(client, settings, monkeypatch):
    w = await W.bouw_async(settings)
    eva = await kop(client, "eva")
    loop = threading.get_ident()
    threads: list[int] = []
    echt = snippets.build

    def build_met_thread(sources, parsed, *rest):
        threads.append(threading.get_ident())
        return echt(sources, parsed, *rest)

    monkeypatch.setattr(snippets, "build", build_met_thread)
    assert (await _zoek(client, eva, W.TEKST_WOORD))[w.ids["doc"]]["snippet"]
    assert threads and loop not in threads
    assert snippets._thread().total_tokens == 1
    assert snippets._thread() is snippets._thread()


async def test_server_timing_alleen_met_search_timing(client, settings, monkeypatch):
    await W.bouw_async(settings)
    eva = await kop(client, "eva")
    r = await client.get("/api/v1/search", params={"q": W.TEKST_WOORD}, headers=eva)
    assert r.status_code == 200 and "server-timing" not in r.headers
    monkeypatch.setattr(settings, "search_timing", True)
    r = await client.get("/api/v1/search", params={"q": W.TEKST_WOORD}, headers=eva)
    assert r.status_code == 200
    stappen = [s.split(";")[0] for s in r.headers["server-timing"].split(", ")]
    assert stappen[:2] == ["roots", "fts"] and {"check", "snip"} <= set(stappen)
    for stap in r.headers["server-timing"].split(", "):
        assert float(stap.split(";dur=")[1]) >= 0
    # De kop zegt nooit iets over de vraag
    assert W.TEKST_WOORD not in r.headers["server-timing"]
