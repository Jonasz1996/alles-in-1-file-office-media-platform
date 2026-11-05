"""De typen van de web volgen de contracten van de API (bouwontwerp fase 6, §3.4): dezelfde velden
met dezelfde namen, dezelfde vaste woorden en dezelfde namen van gebeurtenissen.

Links staat de web (web/src/search/types.ts, photos/people/types.ts, photos/map/types.ts,
dupes/types.ts, admin/searchApi.ts en photos/faces/types.ts), rechts het model in app/search,
app/people, app/geo en app/prefs. Bij de samenvoeging van Z0 liepen ze op een paar plekken uiteen
(docs/fase-6/z0.md); deze test houdt ze gelijk tot Z1–Z7 de routes vullen. Z0b zette de vormen
van gezichten (X2) en zoeken op beeld (X3) erbij, aan beide kanten.

De TypeScript wordt met een eenvoudige lezer gelezen: `export interface X { ... }` (met of zonder
`extends Y`), velden gescheiden door `;`, een vorm `{ a: t; b: t }` als type van een veld, en
woorden als `"a" | "b"`, een eigen `export type` daarvan of `(typeof LIJST)[number]`.

Per veld ook het type (review Z0, haalbaar F5): string tegen str, UUID, date en datetime; number
tegen int en float; boolean tegen bool; `X[]` tegen list[X]; een tupel `[a: t, b: t]` tegen
tuple; een interface tegen zijn model (MODELLEN); `X["y"]` tegen het veld y van het model van X.
Null en `?` hangen af van de richting:

- antwoord (de API stuurt, de web leest): de API stuurt elk veld, dus geen `?`; `| null` precies
  waar het model None toelaat;
- vraag (de web stuurt, de API leest): `?` precies waar het model een standaard heeft; `| null`
  alleen waar het model None neemt (None als standaard staat voor weglaten, dat mag zonder);
- beide: allebei.

Een type dat de lezer niet kent, is een fout: hij slaat niets stil over.
"""

from __future__ import annotations

import datetime
import enum
import re
import types
import typing
import uuid
from pathlib import Path

import pytest
from pydantic import BaseModel

from app import prefs
from app.geo import types as G
from app.people import faces_types as F
from app.people import types as P
from app.routers.files import NodeOut, TagRef
from app.routers.photos import PhotoItem
from app.search import types as S

WEB = Path(__file__).resolve().parents[3] / "web" / "src"
BESTANDEN = [
    "search/types.ts",
    "photos/people/types.ts",
    "photos/map/types.ts",
    "dupes/types.ts",
    "admin/searchApi.ts",
    "photos/faces/types.ts",
    "api.ts",
]

COMMENT = re.compile(r"//[^\n]*|/\*.*?\*/", re.S)
INTERFACE = re.compile(r"export interface (\w+)(?: extends (\w+))? \{")
TYPE = re.compile(r"export type (\w+) = ([^;]+);")
LIJST = re.compile(r"export const (\w+) = \[([^\]]*)\] as const;", re.S)
NAAM = re.compile(r'export const (\w+) = "([^"]+)";')
VELD = re.compile(r"(\w+)(\??)\s*:\s*(.+)", re.S)
WOORD = re.compile(r'"([^"]*)"')
TYPEOF = re.compile(r"\(typeof (\w+)\)\[number\]")
INDEX = re.compile(r'(\w+)\["(\w+)"\]')
TUPEL = re.compile(r"(?:readonly )?\[(.*)\]")
WOORDNAAM = re.compile(r"\w+")

# string, number en boolean tegen de typen van Python
BASIS: dict[str, tuple[type, ...]] = {
    "string": (str, uuid.UUID, datetime.date),
    "number": (int, float),
    "boolean": (bool,),
}


class Veld(typing.NamedTuple):
    type: str
    # `x?: t`: het mag ontbreken
    optioneel: bool


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


def _velden(blok: str) -> dict[str, Veld]:
    """Veld → type, gescheiden door `;` buiten geneste accolades."""
    uit, stuk, diepte = {}, "", 0
    for teken in blok + ";":
        diepte += teken == "{"
        diepte -= teken == "}"
        if teken == ";" and diepte == 0:
            if stuk.strip():
                m = VELD.fullmatch(stuk.strip())
                assert m, f"geen veld: {stuk.strip()!r}"
                uit[m.group(1)] = Veld(" ".join(m.group(3).split()), m.group(2) == "?")
            stuk = ""
        else:
            stuk += teken
    return uit


@pytest.fixture(scope="module")
def web() -> dict[str, Bron]:
    return {naam: Bron((WEB / naam).read_text()) for naam in BESTANDEN}


def _interface(web: dict[str, Bron], bestand: str, naam: str) -> dict[str, Veld]:
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


def _delen(ts: str) -> list[str]:
    """De delen van een union, alleen op het bovenste niveau (niet binnen {}, [], () of <>)."""
    uit, stuk, diepte = [], "", 0
    for teken in ts:
        diepte += teken in "{[(<"
        diepte -= teken in "}])>"
        if teken == "|" and diepte == 0:
            uit.append(stuk.strip())
            stuk = ""
        else:
            stuk += teken
    return [d for d in [*uit, stuk.strip()] if d]


def _ts_zonder_null(ts: str) -> tuple[str, bool]:
    """Het type zonder `| null`, en of null erbij stond."""
    delen = _delen(ts)
    rest = [d for d in delen if d != "null"]
    return " | ".join(rest), len(rest) < len(delen)


def _los(annotatie: object) -> object:
    """Zonder Annotated (de validators doen voor het contract niet mee)."""
    while typing.get_origin(annotatie) is typing.Annotated:
        annotatie = typing.get_args(annotatie)[0]
    return annotatie


def _zonder_none(annotatie: object) -> tuple[object, bool]:
    """Het type zonder None, en of None erbij stond."""
    annotatie = _los(annotatie)
    if typing.get_origin(annotatie) in (typing.Union, types.UnionType):
        args = typing.get_args(annotatie)
        rest = [a for a in args if a is not type(None)]
        if len(rest) < len(args):
            assert len(rest) == 1, f"een union van meer typen: {annotatie}"
            return _los(rest[0]), True
    return annotatie, False


def _python_woorden(annotatie: object) -> set[str] | None:
    if typing.get_origin(annotatie) is typing.Literal:
        return set(typing.get_args(annotatie))
    if isinstance(annotatie, type) and issubclass(annotatie, enum.Enum):
        return {e.value for e in annotatie}
    return None


def _gelijk_null(web, bestand: str, ts: str, annotatie: object, waar: str, richting: str):
    """Een type binnen een type (van een lijst of tupel): null aan beide kanten gelijk."""
    kern, ts_null = _ts_zonder_null(ts)
    py, py_null = _zonder_none(annotatie)
    assert ts_null == py_null, f"{waar}: web {ts} tegen {annotatie}"
    _type(web, bestand, kern, py, waar, richting)


def _type(web, bestand: str, ts: str, py: object, waar: str, richting: str) -> None:
    """Een type van de web zonder null tegen dat van de API zonder None."""
    tegen = f"{waar}: web {ts} tegen {py}"
    if not ts.endswith("[]") and (woorden := _woorden(web, bestand, ts)) is not None:
        assert woorden == _python_woorden(py), f"{waar}: web {sorted(woorden)} tegen {py}"
        return
    assert len(_delen(ts)) == 1, f"{waar}: de lezer kent de union {ts!r} niet"
    if ts.endswith("[]"):
        assert typing.get_origin(py) is list, tegen
        element = ts.removesuffix("[]")
        if element.startswith("(") and element.endswith(")"):
            element = element[1:-1]
        _gelijk_null(web, bestand, element, typing.get_args(py)[0], f"{waar}[]", richting)
    elif m := TUPEL.fullmatch(ts):
        assert typing.get_origin(py) is tuple, tegen
        leden = [lid.split(":")[-1].strip() for lid in m.group(1).split(",")]
        args = typing.get_args(py)
        assert len(leden) == len(args), tegen
        for i, (lid, arg) in enumerate(zip(leden, args, strict=True)):
            _gelijk_null(web, bestand, lid, arg, f"{waar}[{i}]", richting)
    elif ts.startswith("{"):
        assert isinstance(py, type) and issubclass(py, BaseModel), tegen
        _vergelijk(web, bestand, _velden(_blok(ts, 0)), py, waar, richting)
    elif m := INDEX.fullmatch(ts):
        assert m.group(1) in MODELLEN, f"{waar}: geen model voor {m.group(1)}"
        veld = MODELLEN[m.group(1)].model_fields[m.group(2)]
        assert _zonder_none(veld.annotation) == (py, False), tegen
    elif ts in BASIS:
        assert isinstance(py, type) and issubclass(py, BASIS[ts]), tegen
        assert ts == "boolean" or not issubclass(py, bool), tegen
    elif ts in MODELLEN:
        assert py is MODELLEN[ts], tegen
    elif WOORDNAAM.fullmatch(ts) and (alias := _alias(web, bestand, ts)) is not None:
        kern, null = _ts_zonder_null(alias)
        assert not null, f"{waar}: null in het type {ts}; de lezer kent dat niet"
        _type(web, bestand, kern, py, waar, richting)
    else:
        raise AssertionError(f"{waar}: de lezer kent het type {ts!r} niet")


def _alias(web: dict[str, Bron], bestand: str, naam: str) -> str | None:
    for bron in [web[bestand], *web.values()]:
        if naam in bron.types:
            return bron.types[naam]
    return None


def _vergelijk(
    web, bestand: str, ts: dict[str, Veld], model: type[BaseModel], waar: str, richting: str
):
    py = {(f.alias or naam): f for naam, f in model.model_fields.items()}
    assert set(ts) == set(py), f"{waar}: web {sorted(ts)} tegen {model.__name__} {sorted(py)}"
    for naam, veld in ts.items():
        plek = f"{waar}.{naam}"
        kern, ts_null = _ts_zonder_null(veld.type)
        annotatie, py_null = _zonder_none(py[naam].annotation)
        standaard = not py[naam].is_required()
        if richting in ("vraag", "beide"):
            # Weglaten mag waar de API een standaard heeft; null sturen waar hij None neemt
            assert veld.optioneel == standaard, (
                f"{plek}: web ?={veld.optioneel}, standaard={standaard}"
            )
            assert py_null or not ts_null, f"{plek}: de web stuurt null, de API neemt het niet"
        if richting in ("antwoord", "beide"):
            # De API stuurt elk veld, met null precies waar het model None toelaat
            assert not veld.optioneel, f"{plek}: de API stuurt het altijd, de web zegt `?`"
            assert ts_null == py_null, f"{plek}: web {veld.type} tegen {py[naam].annotation}"
        _type(web, bestand, kern, annotatie, plek, richting)


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
    ("photos/people/types.ts", "AlbumRuleInfo", P.AlbumRuleInfo),
    ("photos/people/types.ts", "AlbumItemRule", P.AlbumItemRule),
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
    # Z0b: zoeken op beeld (X3) en gezichten (X2)
    ("search/types.ts", "BeeldStatus", S.BeeldStatus),
    ("admin/searchApi.ts", "CodeCount", S.CodeCount),
    ("admin/searchApi.ts", "AnalyseModel", S.AnalyseModel),
    ("admin/searchApi.ts", "AdminFaces", S.AdminFaces),
    ("admin/searchApi.ts", "AdminBeeld", S.AdminBeeld),
    ("photos/people/types.ts", "PhotoSettingsPatch", prefs.PhotoSettingsPatch),
    ("photos/faces/types.ts", "FaceSuggest", F.FaceSuggest),
    ("photos/faces/types.ts", "FaceOut", F.FaceOut),
    ("photos/faces/types.ts", "FacesPhotos", F.FacesPhotos),
    ("photos/faces/types.ts", "FacesCount", F.FacesCount),
    ("photos/faces/types.ts", "FacesStatus", F.FacesStatus),
    ("photos/faces/types.ts", "PersonSuggestions", F.PersonSuggestions),
    ("photos/faces/types.ts", "SuggestionsByPerson", F.SuggestionsByPerson),
    ("photos/faces/types.ts", "FacePage", F.FacePage),
    ("photos/faces/types.ts", "FaceGroup", F.FaceGroup),
    ("photos/faces/types.ts", "FaceGroups", F.FaceGroups),
    ("photos/faces/types.ts", "FacesReject", F.FacesRejectIn),
    ("photos/faces/types.ts", "FacesQueued", F.FacesQueued),
]


# Een interface als type van een veld: het model dat erbij hoort. Ook de typen van 0.7.0 die de
# contracten gebruiken (hun eigen velden vergelijkt deze test niet), en AlbumRule (de Omit van
# SearchParams, zie test_de_filters_zijn_de_regel_van_een_slim_album)
MODELLEN: dict[str, type[BaseModel]] = {
    **{naam: model for _, naam, model in PAREN},
    "FileNode": NodeOut,
    "TagRef": TagRef,
    "PhotoItem": PhotoItem,
    "SearchHit": S.Hit,
    "AlbumRule": S.SearchRule,
}

# Wat de web stuurt (vraag), of stuurt en terugkrijgt (beide); de rest stuurt de API (antwoord)
RICHTING = {
    "SearchParams": "vraag",
    "AdminSearchPatch": "vraag",
    "PersonPatch": "vraag",
    "AlbumRuleBody": "vraag",
    "MemoryHide": "beide",
    "PhotoSettingsPatch": "vraag",
    "FacesReject": "vraag",
}


def _controleer(web, bestand: str, naam: str) -> None:
    ts = _interface(web, bestand, naam)
    _vergelijk(web, bestand, ts, MODELLEN[naam], naam, RICHTING.get(naam, "antwoord"))


@pytest.mark.parametrize(("bestand", "naam", "model"), PAREN, ids=[p[1] for p in PAREN])
def test_de_web_heeft_dezelfde_velden_als_de_api(web, bestand, naam, model):
    _controleer(web, bestand, naam)


# Een veld van de web anders dan de API: elk moet de vergelijking zien (review Z0, haalbaar F5)
FOUTEN = [
    ("search/types.ts", "GroupItem", "  n: number;\n}", "  n: string | null;\n}"),
    ("search/types.ts", "GroupItem", "  n: number;\n}", "  n: string;\n}"),
    ("search/types.ts", "GroupItem", "  n: number;\n}", "  n: number | null;\n}"),
    ("search/types.ts", "GroupItem", "  n: number;\n}", "  n?: number;\n}"),
    ("search/types.ts", "Hit", "snippet: SnippetPart[] | null;", "snippet: SnippetPart | null;"),
    ("search/types.ts", "Suggestions", "places: PlaceRef[];", "places: GroupItem[];"),
    ("search/types.ts", "SearchParams", "  limit?: number;", "  limit?: string;"),
    ("search/types.ts", "SearchParams", "  q?: string;", "  q?: string | null;"),
    (
        "photos/people/types.ts",
        "AlbumRuleBody",
        "  rule: AlbumRule;\n  share_new?: boolean;",
        "  rule?: AlbumRule;\n  share_new?: boolean;",
    ),
    (
        "photos/people/types.ts",
        "AlbumRuleInfo",
        "  refreshed_at: string;",
        "  refreshed_at?: string;",
    ),
    ("photos/people/types.ts", "AlbumRuleInfo", "  code: string | null;", "  code: string;"),
    ("photos/people/types.ts", "AlbumItemRule", "  shared: boolean;", "  shared: number;"),
    ("admin/searchApi.ts", "AdminSearch", "    text_done: number;", "    text_done: boolean;"),
    (
        "photos/people/types.ts",
        "FilePerson",
        "Box = readonly [x: number, y: number, w: number, h: number];",
        "Box = readonly [x: number, y: number, w: number];",
    ),
    ("dupes/types.ts", "DupeItem", "taken_at: string | null;", "taken_at: number | null;"),
    # Z0b
    (
        "search/types.ts",
        "SearchPage",
        "  fallback: BeeldFallback | null;",
        "  fallback: BeeldFallback;",
    ),
    ("search/types.ts", "SearchParams", "  mode?: SearchMode;", "  mode: SearchMode;"),
    ("admin/searchApi.ts", "AdminFaces", "  users_on: number;", "  users_on: string;"),
    ("admin/searchApi.ts", "AdminSearch", "  beeld: AdminBeeld | null;", "  beeld: AdminBeeld;"),
    (
        "photos/faces/types.ts",
        "FaceOut",
        "  suggest: FaceSuggest | null;",
        "  suggest: FaceSuggest;",
    ),
    ("photos/people/types.ts", "PhotoSettings", "  beeld: OnOff;\n}", "  beeld: boolean;\n}"),
]


@pytest.mark.parametrize(
    ("bestand", "naam", "oud", "nieuw"),
    FOUTEN,
    ids=[f"{f[1]}-{f[3].split(';')[0].strip()}" for f in FOUTEN],
)
def test_de_vergelijking_ziet_een_ander_type(web, bestand, naam, oud, nieuw):
    tekst = (WEB / bestand).read_text()
    assert tekst.count(oud) == 1, oud
    anders = {**web, bestand: Bron(tekst.replace(oud, nieuw))}
    with pytest.raises(AssertionError):
        _controleer(anders, bestand, naam)


def test_de_filters_zijn_de_regel_van_een_slim_album(web):
    """Filters met q is SearchRule; een AlbumRule is SearchParams zonder de pagina."""
    filters = _interface(web, "search/types.ts", "Filters")
    ts = {"q": Veld("string", optioneel=True), **filters}
    _vergelijk(web, "search/types.ts", ts, S.SearchRule, "Filters", "vraag")
    rule = web["photos/people/types.ts"].types["AlbumRule"]
    # Zonder de pagina, en zonder mode en like: een regel zoekt nooit op beeld (Z0b, Keuze 80)
    assert rule == 'Omit<SearchParams, "offset" | "limit" | "after" | "mode" | "like">'
    assert set(S.SearchParams.model_fields) - set(S.SearchRule.model_fields) == {
        "offset",
        "limit",
        "after",
        "mode",
        "like",
    }


def test_gezichten_bevestigen_en_de_grenzen(web):
    """FacesConfirm: de gezichten met een bestaande persoon of een nieuwe naam (FacesConfirmIn);
    de grenzen van X2 aan beide kanten gelijk (Z0b)."""
    faces = web["photos/faces/types.ts"]
    assert set(re.findall(r"(\w+)\??:", faces.types["FacesConfirm"])) == set(
        F.FacesConfirmIn.model_fields
    )
    tekst = COMMENT.sub("", (WEB / "photos/faces/types.ts").read_text())
    assert re.search(rf"export const MAX_FACES = {F.MAX_FACES};", tekst)
    assert re.search(rf"export const FACES_PAGE = {F.FACES_PAGE};", tekst)
    sizes = re.search(r"export const CROP_SIZES = \[([^\]]*)\] as const;", tekst)
    assert sizes and {int(n) for n in sizes.group(1).split(",")} == set(F.CropSize.__args__)
    assert faces.lijsten["FACES_ERROR_CODES"] == {
        "faces_forbidden", "faces_off", "faces_unavailable", "faces_too_many", "faces_gone"
    }  # fmt: skip
    assert web["search/types.ts"].lijsten["BEELD_ERROR_CODES"] == {
        "beeld_off", "beeld_no_vector", "beeld_not_installed"
    }  # fmt: skip


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
    assert _velden(_blok(admin["index"].type, 0)) == {
        "names_done": Veld("boolean", optioneel=False),
        "text_done": Veld("number", optioneel=False),
        "text_total": Veld("number", optioneel=False),
        "failed": Veld("number", optioneel=False),
    }
    assert _interface(web, "search/types.ts", "SearchParams")["limit"] == Veld("number", True)
    assert _delen('{ a: "x" | "y" } | null') == ['{ a: "x" | "y" }', "null"]
    assert _delen('Omit<SearchParams, "a" | "b">') == ['Omit<SearchParams, "a" | "b">']
    assert TUPEL.fullmatch(web["photos/people/types.ts"].types["Box"])
    assert _woorden(web, "search/types.ts", "SearchType[]") == set(typing.get_args(S.FileType))
    assert _woorden(web, "search/types.ts", "AnalysisCode | null") == set(
        typing.get_args(S.AnalysisCode)
    )
    assert _woorden(web, "search/types.ts", "FileNode") is None
