"""own_live: de ene regel voor "eigen, levende bestanden" (bouwontwerp fase 6, Keuze 25a).

Drie dingen: de vorm ligt vast (de SQLAlchemy-vorm en de SQL-tekst zeggen hetzelfde), hij doet
wat hij zegt op een echte database met de triggers van 0010 (prullenbak in en uit, ook via een
map erboven; een kopie in Brams boom), en de wachter: elke fotofunctie gebruikt hem. De
fotofuncties komen in Z3 en Z6; de wachter kijkt naar elk bestand van die pakketten dat er is.

De wachter kijkt per functie (review Z0, veilig F4; eerst per bestand: één own_live ergens
dekte alles), naar node én de tabellen ernaast (photo_geo, photo_person, photo_ocr, blob_hash,
dup_pair), en neemt own_live_geo aan voor photo_geo zonder join.

Z0b ("Eigen en levend" van X2, en Keuze 80) zette gezichten en beeld erbij: de router van X2 en
alles onder app/beeld/ zijn fotofuncties, en de tabellen van 0011 en 0012 met een rij per foto of
per inhoud (face, face_person, face_scan, blob_embed) tellen als lezen van foto's.

Z3 (de plek, Keuze 25a en 27): de kaart en de plaatsen lezen photo_geo met own_live_geo. Op de
echte database toont own_live_geo na elke stap precies de foto's met een plek die own_live laat
staan, ook in de prullenbak via een map erboven, na terugzetten en voor Brams kopie. De route
/files/{id}/location (routers/geo.py) staat in de lijst. De schrijvers in app/photo_rows.py niet:
die schrijven per node voor de eigenaar en wissen bij "uit" ook de rijen in de prullenbak, dus
own_live zou daar juist rijen laten staan.
"""

from __future__ import annotations

import ast
import re
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from sqlalchemy.orm import aliased

from app.db import get_engine
from app.models import Node, PhotoGeo
from app.search.live import OWN_LIVE_GEO_SQL, OWN_LIVE_SQL, own_live, own_live_geo
from tests.search.migratie import NOW, SHA_JPG, fill_0009, migrate, settings_at

pytestmark = pytest.mark.anyio

APP = Path(__file__).resolve().parents[2] / "app"

# De fotofuncties van §2.0 en Keuze 25a: kaart, plaatsen, duplicaten, personen, herinneringen,
# de verversing van slimme albums en de foto-filters van zoeken
PHOTO_FUNCTIONS = (
    "memories.py",
    "albums_rule.py",
    "geo",
    "dupes",
    "people",
    "search/filters_photo.py",
    "routers/photos_map.py",
    "routers/geo.py",  # Z3: GET /files/{id}/location
    "routers/duplicates.py",
    "routers/people.py",
    "routers/memories.py",
    # Z0b: gezichten (X2; app/people/faces/ valt al onder people) en zoeken op beeld (X3)
    "routers/people_faces.py",
    "beeld",
    "routers/beeld.py",
)
# Wat geen node leest en dus geen own_live hoeft: de vaste typen van Z0
NOT_A_QUERY = {
    "geo/types.py",
    "geo/__init__.py",
    "dupes/__init__.py",
    "people/__init__.py",
    "people/types.py",
    "people/faces_types.py",
}

# Wat foto's leest (review Z0, veilig F4): node zelf, en de tabellen ernaast met een rij per foto
# of per inhoud. In SQL-tekst de tabelnaam na FROM of JOIN; in Python het model. De tabellen van
# gezichten en beeld (0011 en 0012, A0) staan er al in (Z0b); hun modellen heten zoals hier, en
# heet een model in A0 anders, dan komt die naam hier
TABLES = ("node", "photo_geo", "photo_person", "photo_ocr", "blob_hash", "dup_pair",
          "face", "face_person", "face_scan", "blob_embed")  # fmt: skip
MODELS = frozenset({"Node", "PhotoGeo", "PhotoPerson", "PhotoOcr", "BlobHash", "DupPair",
                    "Face", "FacePerson", "FaceScan", "BlobEmbed"})  # fmt: skip
READS_SQL = re.compile(r"\b(?:FROM|JOIN)\s+(?:" + "|".join(TABLES) + r")\b", re.I)
# Wat de regel toepast: own_live (op node) of own_live_geo (op photo_geo), of hun SQL-tekst
OWN_LIVE = frozenset({"own_live", "OWN_LIVE_SQL", "own_live_geo", "OWN_LIVE_GEO_SQL"})


def _woorden(node: ast.AST) -> Iterator[str]:
    """De namen en strings in deze stukken code, zonder geneste functies (die tellen apart),
    zonder imports, docstrings en type-annotaties (die lezen niets)."""
    todo = [node]
    while todo:
        cur = todo.pop()
        if isinstance(cur, ast.Name):
            yield cur.id
        elif isinstance(cur, ast.Attribute):
            yield cur.attr
        elif isinstance(cur, ast.Constant) and isinstance(cur.value, str):
            yield cur.value
        for field, value in ast.iter_fields(cur):
            if field in ("annotation", "returns", "decorator_list"):
                continue
            for child in value if isinstance(value, list) else [value]:
                if not isinstance(child, ast.AST) or isinstance(
                    child, (ast.Import, ast.ImportFrom)
                ):
                    continue
                if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                    continue
                if _is_docstring(cur, child):
                    continue
                todo.append(child)


def _is_docstring(parent: ast.AST, child: ast.AST) -> bool:
    body = getattr(parent, "body", None)
    return (
        isinstance(body, list)
        and bool(body)
        and body[0] is child
        and isinstance(child, ast.Expr)
        and isinstance(child.value, ast.Constant)
        and isinstance(child.value.value, str)
    )


def _units(tree: ast.Module) -> Iterator[tuple[str, list[ast.AST]]]:
    """Elke functie (ook een methode of een geneste) is een eenheid; daarbuiten (module en
    klasse) elke opdracht apart."""
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            yield node.name, [node]
    todo: list[ast.Module | ast.ClassDef] = [tree]
    while todo:
        cur = todo.pop()
        for stmt in cur.body:
            if isinstance(stmt, ast.ClassDef):
                todo.append(stmt)
            elif not isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef):
                if not _is_docstring(cur, stmt):
                    yield f"regel {stmt.lineno}", [stmt]


def offenders(source: str) -> list[str]:
    """De functies (of opdrachten buiten een functie) die foto's lezen zonder own_live of
    own_live_geo. Per functie, niet per bestand: één goede vraag in een bestand dekt een
    andere niet (review Z0, veilig F4)."""
    found = []
    for name, nodes in _units(ast.parse(source)):
        words = [w for n in nodes for w in _woorden(n)]
        reads = any(w in MODELS or READS_SQL.search(w) for w in words)
        if reads and not any(w in OWN_LIVE for w in words):
            found.append(name)
    return found


def needs_own_live(source: str) -> bool:
    """Leest dit bestand ergens foto's zonder own_live? (Dan is het een fout.)"""
    return bool(offenders(source))


def photo_files() -> list[Path]:
    found = []
    for name in PHOTO_FUNCTIONS:
        path = APP / name
        if path.is_dir():
            found += sorted(path.rglob("*.py"))
        elif path.exists():
            found.append(path)
    return [p for p in found if str(p.relative_to(APP)) not in NOT_A_QUERY]


def test_de_vorm_ligt_vast():
    assert OWN_LIVE_SQL == "{n}.owner_id = :me AND {n}.dead = 0 AND {n}.kind = 'file'"
    me = uuid.uuid4()
    compiled = own_live(Node, me).compile()
    assert str(compiled) == (
        "node.owner_id = :owner_id_1 AND node.dead = :dead_1 AND node.kind = :kind_1"
    )
    assert compiled.params == {"owner_id_1": me, "dead_1": 0, "kind_1": "file"}
    # Met een alias van het model en van de tabel: dezelfde drie voorwaarden op die alias
    n = aliased(Node, name="n")
    assert str(own_live(n, me).compile()).startswith("n.owner_id = ")
    t = Node.__table__.alias("t")
    assert str(own_live(t, me).compile()).startswith("t.owner_id = ")


async def _names(settings, me: uuid.UUID) -> tuple[set[str], set[str]]:
    """Wat own_live en OWN_LIVE_SQL voor deze gebruiker laten staan."""
    async with get_engine(settings).connect() as conn:
        orm = set((await conn.execute(sa.select(Node.name).where(own_live(Node, me)))).scalars())
        raw = set(
            (
                await conn.execute(
                    sa.text("SELECT n.name FROM node n WHERE " + OWN_LIVE_SQL.format(n="n")),
                    {"me": me.hex},
                )
            ).scalars()
        )
    return orm, raw


async def _plekken(settings, me: uuid.UUID) -> tuple[set[str], set[str]]:
    """Wat own_live_geo en OWN_LIVE_GEO_SQL voor deze gebruiker op de kaart laten staan (Z3). De
    kaart leest photo_geo zonder join; de join op node is hier alleen voor de naam."""
    async with get_engine(settings).connect() as conn:
        orm = set(
            (
                await conn.execute(
                    sa.select(Node.name)
                    .join(PhotoGeo, PhotoGeo.node_id == Node.id)
                    .where(own_live_geo(PhotoGeo, me))
                )
            ).scalars()
        )
        raw = set(
            (
                await conn.execute(
                    sa.text(
                        "SELECT n.name FROM photo_geo g JOIN node n ON n.id = g.node_id WHERE "
                        + OWN_LIVE_GEO_SQL.format(g="g")
                    ),
                    {"me": me.hex},
                )
            ).scalars()
        )
    return orm, raw


@pytest.mark.parametrize("fts5", [True, False], ids=["fts5", "zonder-fts5"])
async def test_alleen_eigen_levende_bestanden_ook_na_prullenbak_en_terug(tmp_path, fts5):
    settings = settings_at(tmp_path, "db")
    await migrate(settings, command.upgrade, "0009")
    ids = await fill_0009(settings)
    await migrate(settings, command.upgrade, "head", fts5=fts5)
    eva, bram = ids["eva"], ids["bram"]

    # Geen mappen, geen album, niets uit de prullenbak (strand ligt in de map vakantie, los zelf)
    expected = {"verslag.x", "notities.x"}
    assert await _names(settings, eva) == (expected, expected)
    # werk en het album zijn met Bram gedeeld; toch is niets daarvan van hem
    assert await _names(settings, bram) == (set(), set())

    async def run(sql: str, **params) -> None:
        async with get_engine(settings).begin() as conn:
            await conn.execute(sa.text(sql), {k: v.hex if isinstance(v, uuid.UUID) else v
                                              for k, v in params.items()})  # fmt: skip

    async def plek(node: uuid.UUID, owner: uuid.UUID) -> None:
        """Een rij in photo_geo zoals photometa hem schrijft; dead en taken_at zet de trigger."""
        await run(
            "INSERT INTO photo_geo (node_id, owner_id, lat, lon, x16, y16, source) "
            "VALUES (:node, :owner, 52.37, 4.89, 33650, 21530, 'exif')",
            node=node, owner=owner,
        )  # fmt: skip

    # Z3: de twee foto's krijgen een plek terwijl ze in de prullenbak liggen (via vakantie). De
    # kaart (own_live_geo, zonder join) toont steeds precies de foto's met een plek die own_live
    # laat staan
    met_plek = {"strand.x", "schelp.x"}
    for naam in ("strand", "schelp"):
        await plek(ids[naam], eva)

    async def zelfde(me: uuid.UUID, verwacht: set[str]) -> None:
        assert await _names(settings, me) == (verwacht, verwacht)
        op_kaart = verwacht & met_plek
        assert await _plekken(settings, me) == (op_kaart, op_kaart)

    await zelfde(eva, expected)
    await zelfde(bram, set())

    # De map vakantie terug uit de prullenbak: strand en (in diep) schelp komen terug
    await run("UPDATE node SET deleted_at = NULL WHERE id = :id", id=ids["vakantie"])
    expected |= {"strand.x", "schelp.x"}
    await zelfde(eva, expected)
    # Z3: diep (in vakantie) naar de prullenbak en terug: schelp gaat van de kaart en komt terug
    await run("UPDATE node SET deleted_at = :t WHERE id = :id", id=ids["diep"], t=NOW)
    await zelfde(eva, expected - {"schelp.x"})
    await run("UPDATE node SET deleted_at = NULL WHERE id = :id", id=ids["diep"])
    await zelfde(eva, expected)
    # De map erboven (werk) naar de prullenbak: verslag is weg, ook al ligt het er zelf niet in
    await run("UPDATE node SET deleted_at = :t WHERE id = :id", id=ids["werk"], t=NOW)
    expected -= {"verslag.x"}
    await zelfde(eva, expected)

    # Bram kopieert Eva's foto naar zijn eigen boom (zelfde inhoud): zijn kopie is van hem
    copy = uuid.uuid4()
    await run(
        "INSERT INTO node (id, parent_id, owner_id, name, kind, mime, size, blob_id, created_at, "
        "modified_at, search_name) VALUES (:id, :parent, :owner, 'kopie.x', 'file', "
        "'image/jpeg', 1, :blob, :t, :t, 'kopie.x')",
        id=copy, parent=ids["bram_root"], owner=bram, blob=SHA_JPG, t=NOW,
    )  # fmt: skip
    assert await _names(settings, bram) == ({"kopie.x"}, {"kopie.x"})
    assert await _names(settings, eva) == (expected, expected)
    # Z3: zijn kopie krijgt een eigen plek, met hem als eigenaar: alleen op zijn kaart
    met_plek.add("kopie.x")
    await plek(copy, bram)
    await zelfde(bram, {"kopie.x"})
    await zelfde(eva, expected)


def test_de_wachter_ziet_een_fotofunctie_zonder_own_live():
    zonder = "def f(me):\n    q = select(Node.id).where(Node.owner_id == me)\n"
    met = zonder + "    q = q.where(own_live(Node, me))\n"
    assert needs_own_live(zonder)
    assert not needs_own_live(met)
    assert needs_own_live("SQL = 'SELECT id FROM node WHERE owner_id = :me'")
    assert not needs_own_live("SQL = 'SELECT n.id FROM node n WHERE ' + OWN_LIVE_SQL")
    assert not needs_own_live("def place(lat, lon): ...")  # leest geen nodes
    # photo_geo zonder join, met own_live_geo of zijn SQL-tekst: goed
    geo = "SELECT g.lat FROM photo_geo g WHERE "
    assert not needs_own_live(f"SQL = {geo!r} + OWN_LIVE_GEO_SQL.format(g='g')")
    assert not needs_own_live(
        "def f(me):\n    return select(PhotoGeo).where(own_live_geo(PhotoGeo, me))"
    )
    assert needs_own_live("def f(me):\n    return select(PhotoGeo).where(PhotoGeo.owner_id == me)")
    # Wat niets leest: een import, een docstring, een type-annotatie
    assert not needs_own_live('"""Leest FROM node niet."""\nfrom app.models import Node, PhotoGeo')
    assert not needs_own_live('def f(n: Node) -> Node:\n    """SELECT * FROM node"""\n    return n')
    # Een geneste functie telt apart: own_live in de buitenste dekt hem niet
    nest = "def f(me):\n    own_live\n    def g():\n        return select(Node)\n    return g\n"
    assert offenders(nest) == ["g"]
    assert OWN_LIVE_GEO_SQL.startswith("{g}.owner_id")


def test_de_wachter_kijkt_naar_de_kaart_en_de_plek():
    """Z3: de kaart, de plaatsen, /location en de haken onder app/geo/ staan in de lijst."""
    namen = {str(p.relative_to(APP)) for p in photo_files()}
    z3 = {"routers/photos_map.py", "routers/geo.py", "geo/places.py", "geo/wire.py"}
    assert z3 <= namen, z3 - namen
    assert "geo/types.py" not in namen


def test_elke_fotofunctie_gebruikt_own_live():
    fout = {
        str(p.relative_to(APP)): offenders(p.read_text())
        for p in photo_files()
        if offenders(p.read_text())
    }
    assert not fout, f"Fotofuncties zonder own_live of own_live_geo (Keuze 25a): {fout}"


# De drie proeven van de review (Z0, veilig F4): elk leest foto's zonder own_live, en de wachter
# moet ze zien
PROEF_GEO = """
from app.search.live import own_live

async def kaart(session, me):
    return await session.execute(text("SELECT lat, lon FROM photo_geo WHERE owner_id = :me"))
"""
PROEF_TWEE = """
from app.models import Node
from app.search.live import own_live

async def goed(session, me):
    return await session.scalars(select(Node.id).where(own_live(Node, me)))

async def fout(session, me):
    return await session.scalars(select(Node).where(Node.owner_id == me))
"""
PROEF_PERSOON = """
SQL = "SELECT person_id FROM photo_person WHERE node_id = :id"
"""
# Z0b: gezichten en beeld zonder own_live (X2 "Eigen en levend", X3)
PROEF_GEZICHT = """
async def voorstellen(session, me):
    return await session.scalars(select(Face).where(Face.owner_id == me))
"""
PROEF_BEELD = """
SQL = "SELECT e.vec FROM blob_embed e JOIN beeld_owner o ON o.user_id = :me"
"""


@pytest.mark.parametrize(
    "bron",
    [PROEF_GEO, PROEF_TWEE, PROEF_PERSOON, PROEF_GEZICHT, PROEF_BEELD],
    ids=["photo_geo", "twee-functies", "persoon", "gezicht", "beeld"],
)
def test_de_wachter_ziet_de_proeven_van_de_review(bron):
    assert needs_own_live(bron)


def test_de_wachter_kijkt_naar_gezichten_en_beeld():
    """Z0b: de router van X2, app/beeld/ en routers/beeld.py staan in de lijst; faces_types.py
    leest niets. Een vraag op face met own_live is goed."""
    namen = {str(p.relative_to(APP)) for p in photo_files()}
    assert {"routers/people_faces.py", "routers/beeld.py"} <= namen
    assert "people/faces_types.py" not in namen
    assert (APP / "people" / "faces_types.py").exists()
    assert not needs_own_live(
        "def f(me):\n    return select(Face).join(Node).where(own_live(Node, me))"
    )
    assert needs_own_live("SQL = 'SELECT f.id FROM face_scan f WHERE f.node_id = :id'")
