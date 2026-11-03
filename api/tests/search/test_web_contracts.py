"""De typen van de web volgen de contracten van de API (bouwontwerp fase 6, §3.4): dezelfde velden
met dezelfde namen, dezelfde vaste woorden en dezelfde namen van gebeurtenissen.

Links staat de web (web/src/search/types.ts, photos/people/types.ts, photos/map/types.ts,
dupes/types.ts en admin/searchApi.ts), rechts het model in app/search, app/people, app/geo en
app/prefs. Bij de samenvoeging van Z0 liepen ze op een paar plekken uiteen (docs/fase-6/z0.md);
deze test houdt ze gelijk tot Z1–Z7 de routes vullen.

De TypeScript wordt met een eenvoudige lezer gelezen: `export interface X { ... }` (met of zonder
`extends Y`), velden gescheiden door `;`, een vorm `{ a: t; b: t }` als type van een veld, en
woorden als `"a" | "b"`, een eigen `export type` daarvan of `(typeof LIJST)[number]`.
"""

from __future__ import annotations

import enum
import re
import types
import typing
from pathlib import Path

import pytest
from pydantic import BaseModel

from app import prefs
from app.geo import types as G
from app.people import types as P
from app.search import types as S

WEB = Path(__file__).resolve().parents[3] / "web" / "src"
BESTANDEN = [
    "search/types.ts",
    "photos/people/types.ts",
    "photos/map/types.ts",
    "dupes/types.ts",
    "admin/searchApi.ts",
    "api.ts",
]

COMMENT = re.compile(r"//[^\n]*|/\*.*?\*/", re.S)
INTERFACE = re.compile(r"export interface (\w+)(?: extends (\w+))? \{")
TYPE = re.compile(r"export type (\w+) = ([^;]+);")
LIJST = re.compile(r"export const (\w+) = \[([^\]]*)\] as const;", re.S)
NAAM = re.compile(r'export const (\w+) = "([^"]+)";')
VELD = re.compile(r"(\w+)\??\s*:\s*(.+)", re.S)
WOORD = re.compile(r'"([^"]*)"')
TYPEOF = re.compile(r"\(typeof (\w+)\)\[number\]")


class Bron:
    """Wat één bestand van de web uitvoert: interfaces, types, lijsten en namen."""

    def __init__(self, tekst: str):
        tekst = COMMENT.sub("", tekst)
        self.interfaces: dict[str, tuple[str | None, str]] = {}
        for m in INTERFACE.finditer(tekst):
            self.interfaces[m.group(1)] = (m.group(2), _blok(tekst, m.end() - 1))
        self.types = {m.group(1): " ".join(m.group(2).split()) for m in TYPE.finditer(tekst)}
        self.lijsten = {m.group(1): set(WOORD.findall(m.group(2))) for m in LIJST.finditer(tekst)}
        self.namen = {m.group(1): m.group(2) for m in NAAM.finditer(tekst)}


def _blok(tekst: str, start: int) -> str:
    """De inhoud tussen de accolade op start en de accolade die hem sluit."""
    diepte = 0
    for i in range(start, len(tekst)):
        if tekst[i] == "{":
            diepte += 1
        elif tekst[i] == "}":
            diepte -= 1
            if diepte == 0:
                return tekst[start + 1 : i]
    raise AssertionError("accolade niet gesloten")


def _velden(blok: str) -> dict[str, str]:
    """Veld → type, gescheiden door `;` buiten geneste accolades."""
    uit, stuk, diepte = {}, "", 0
    for teken in blok + ";":
        diepte += teken == "{"
        diepte -= teken == "}"
        if teken == ";" and diepte == 0:
            if stuk.strip():
                m = VELD.fullmatch(stuk.strip())
                assert m, f"geen veld: {stuk.strip()!r}"
                uit[m.group(1)] = " ".join(m.group(2).split())
            stuk = ""
        else:
            stuk += teken
    return uit


@pytest.fixture(scope="module")
def web() -> dict[str, Bron]:
    return {naam: Bron((WEB / naam).read_text()) for naam in BESTANDEN}


def _interface(web: dict[str, Bron], bestand: str, naam: str) -> dict[str, str]:
    """De velden van een interface, met die van `extends` erbij (eerst in hetzelfde bestand)."""
    for bron in [web[bestand], *web.values()]:
        if naam in bron.interfaces:
            ouder, blok = bron.interfaces[naam]
            velden = _interface(web, bestand, ouder) if ouder else {}
            return {**velden, **_velden(blok)}
    raise AssertionError(f"interface {naam} niet gevonden")


def _woorden(web: dict[str, Bron], bestand: str, ts: str) -> set[str] | None:
    """De vaste woorden van een type in TypeScript, of None als het geen woorden zijn."""
    delen = [d.strip() for d in ts.removesuffix("[]").split("|") if d.strip() != "null"]
    if delen and all(WOORD.fullmatch(d) for d in delen):
        return {d[1:-1] for d in delen}
    if len(delen) == 1:
        if m := TYPEOF.fullmatch(delen[0]):
            for bron in [web[bestand], *web.values()]:
                if m.group(1) in bron.lijsten:
                    return bron.lijsten[m.group(1)]
        for bron in [web[bestand], *web.values()]:
            if delen[0] in bron.types:
                return _woorden(web, bestand, bron.types[delen[0]])
    return None


def _kern(annotatie: object) -> object:
    """Zonder None en zonder list: het type waar het om gaat."""
    while True:
        oorsprong = typing.get_origin(annotatie)
        if oorsprong in (typing.Union, types.UnionType):
            rest = [a for a in typing.get_args(annotatie) if a is not type(None)]
            if len(rest) != 1:
                return annotatie
            annotatie = rest[0]
        elif oorsprong is list:
            annotatie = typing.get_args(annotatie)[0]
        else:
            return annotatie


def _python_woorden(annotatie: object) -> set[str] | None:
    kern = _kern(annotatie)
    if typing.get_origin(kern) is typing.Literal:
        return set(typing.get_args(kern))
    if isinstance(kern, type) and issubclass(kern, enum.Enum):
        return {e.value for e in kern}
    return None


def _vergelijk(web, bestand: str, ts: dict[str, str], model: type[BaseModel], waar: str):
    py = {(f.alias or naam): f for naam, f in model.model_fields.items()}
    assert set(ts) == set(py), f"{waar}: web {sorted(ts)} tegen {model.__name__} {sorted(py)}"
    for veld, ts_type in ts.items():
        annotatie = py[veld].annotation
        if ts_type.startswith("{"):
            kern = _kern(annotatie)
            assert isinstance(kern, type) and issubclass(kern, BaseModel), f"{waar}.{veld}"
            blok = ts_type[: ts_type.rindex("}") + 1]
            _vergelijk(web, bestand, _velden(_blok(blok, 0)), kern, f"{waar}.{veld}")
            continue
        woorden = _woorden(web, bestand, ts_type)
        if woorden is not None:
            assert woorden == _python_woorden(annotatie), (
                f"{waar}.{veld}: web {sorted(woorden)} tegen {annotatie}"
            )


PAREN = [
    ("search/types.ts", "SearchParams", S.SearchParams),
    ("search/types.ts", "SnippetPart", S.SnippetPart),
    ("search/types.ts", "Hit", S.Hit),
    ("search/types.ts", "PersonRef", S.PersonRef),
    ("search/types.ts", "PlaceRef", S.PlaceRef),
    ("search/types.ts", "GroupItem", S.GroupItem),
    ("search/types.ts", "Groups", S.Groups),
    ("search/types.ts", "IndexProgress", S.IndexProgress),
    ("search/types.ts", "SearchPage", S.SearchPage),
    ("search/types.ts", "NameRef", S.NameRef),
    ("search/types.ts", "AlbumRef", S.AlbumRef),
    ("search/types.ts", "Suggestions", S.Suggestions),
    ("search/types.ts", "PageWord", S.PageWord),
    ("search/types.ts", "PageWords", S.PageWords),
    ("search/types.ts", "Analysis", S.AnalysisOut),
    ("search/types.ts", "SearchReadyData", S.SearchReadyEvent),
    ("search/types.ts", "SearchProgressData", S.SearchProgressEvent),
    ("admin/searchApi.ts", "Rebuild", S.Rebuild),
    ("admin/searchApi.ts", "AdminSearch", S.AdminSearch),
    ("admin/searchApi.ts", "AdminSearchPatch", S.AdminSearchPatch),
    ("admin/searchApi.ts", "FailedItem", S.FailedItem),
    ("admin/searchApi.ts", "FailedPage", S.FailedPage),
    ("photos/people/types.ts", "Person", P.Person),
    ("photos/people/types.ts", "People", P.People),
    ("photos/people/types.ts", "PersonPatch", P.PersonPatch),
    ("photos/people/types.ts", "FilePerson", P.FilePerson),
    ("photos/people/types.ts", "FilePeople", P.FilePeople),
    ("photos/people/types.ts", "PersonPhotos", P.PersonPhotos),
    ("photos/people/types.ts", "MemoryYear", P.MemoryYear),
    ("photos/people/types.ts", "Memories", P.Memories),
    ("photos/people/types.ts", "MemoryHide", P.MemoryHide),
    ("photos/people/types.ts", "MemoryHides", P.MemoryHides),
    ("photos/people/types.ts", "PhotoSettings", prefs.PhotoSettings),
    ("photos/people/types.ts", "AlbumRuleBody", P.AlbumRuleIn),
    ("photos/people/types.ts", "AlbumRuleSaved", P.AlbumRuleSaved),
    ("photos/map/types.ts", "PlaceName", G.PlaceName),
    ("photos/map/types.ts", "FileLocation", G.FileLocation),
    ("photos/map/types.ts", "MapCell", G.MapCell),
    ("photos/map/types.ts", "MapCells", G.MapCells),
    ("photos/map/types.ts", "MapItems", G.MapItems),
    ("photos/map/types.ts", "Place", G.PlaceCount),
    ("photos/map/types.ts", "Places", G.Places),
    ("photos/map/types.ts", "ShareCheck", G.ShareCheck),
    ("photos/map/types.ts", "GeoAttribution", G.GeoAttribution),
    ("dupes/types.ts", "DupeItem", G.DupeItem),
    ("dupes/types.ts", "DupeGroup", G.DupeGroup),
    ("dupes/types.ts", "DupePage", G.DupePage),
]


@pytest.mark.parametrize(("bestand", "naam", "model"), PAREN, ids=[p[1] for p in PAREN])
def test_de_web_heeft_dezelfde_velden_als_de_api(web, bestand, naam, model):
    _vergelijk(web, bestand, _interface(web, bestand, naam), model, naam)


def test_de_filters_zijn_de_regel_van_een_slim_album(web):
    """Filters met q is SearchRule; een AlbumRule is SearchParams zonder de pagina."""
    filters = _interface(web, "search/types.ts", "Filters")
    _vergelijk(web, "search/types.ts", {"q": "string", **filters}, S.SearchRule, "Filters")
    rule = web["photos/people/types.ts"].types["AlbumRule"]
    assert rule == 'Omit<SearchParams, "offset" | "limit" | "after">'
    assert set(S.SearchParams.model_fields) - set(S.SearchRule.model_fields) == {
        "offset",
        "limit",
        "after",
    }


def test_een_persoon_op_een_foto_zetten(web):
    """AddPerson: een bestaande persoon of een nieuwe naam, met of zonder vak (AddPersonIn)."""
    ts = web["photos/people/types.ts"].types["AddPerson"]
    assert set(re.findall(r"(\w+)\??:", ts)) == set(P.AddPersonIn.model_fields)


def test_de_gebeurtenissen_heten_hetzelfde(web):
    namen = {
        waarde
        for bron in web.values()
        for naam, waarde in bron.namen.items()
        if naam in {"SEARCH_READY", "SEARCH_PROGRESS", "PEOPLE_CHANGED"}
    }
    assert namen == set(S.EVENTS)
    assert web["photos/people/types.ts"].types["PeopleChangedData"] == "Record<string, never>"
    assert not S.EVENTS["people.changed"].model_fields


def test_de_lezer_ziet_de_vormen_van_de_web(web):
    """Een lezer die niets vindt, zegt niets: deze vormen moet hij lezen."""
    admin = _interface(web, "admin/searchApi.ts", "AdminSearch")
    assert _velden(_blok(admin["index"], 0)) == {
        "names_done": "boolean",
        "text_done": "number",
        "text_total": "number",
        "failed": "number",
    }
    assert _woorden(web, "search/types.ts", "SearchType[]") == set(typing.get_args(S.FileType))
    assert _woorden(web, "search/types.ts", "AnalysisCode | null") == set(
        typing.get_args(S.AnalysisCode)
    )
    assert _woorden(web, "search/types.ts", "FileNode") is None
