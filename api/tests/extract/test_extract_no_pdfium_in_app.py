"""Op het pad van de tekst leest het app-proces zelf geen document (bouwontwerp fase 6, Z2;
Keuze 17, S6). Zoals test_media_no_ffmpeg_outside.py, in twee delen:

1. de AST van elke module aan de kant van de app (app/analysis.py, app/extract/step.py,
   batch.py, frame.py, result.py en __init__.py): geen import van pypdfium2, de kantoorlezer
   (app.office.reader), de lezers in app/extract of app.render, ook niet binnen een functie;
2. een eigen proces dat de hele keten draait (app.main geladen, een worker in dat proces, de
   pomp van de analyse, een PDF en een docx), met een import-haak die elke import van
   pypdfium2, de kantoorlezer en de lezers weigert: de tekst komt toch terug (het kind las hem),
   en geen van die modules staat daarna in sys.modules. Ook met media uit (de terugval: het
   renderproces van de app leest, Z2 stap 2).
"""

from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

import pytest

from tests.search import fixtures

API = Path(__file__).resolve().parents[2]
APP = API / "app"
APP_SIDE = [
    APP / "analysis.py",
    APP / "extract" / "__init__.py",
    APP / "extract" / "step.py",
    APP / "extract" / "batch.py",
    APP / "extract" / "frame.py",
    APP / "extract" / "result.py",
]
READERS = ("pdf", "office", "plain", "html", "rtf", "media", "read", "collect")
FORBIDDEN = (
    "pypdfium2",
    "app.office.reader",
    "app.render",
    *(f"app.extract.{name}" for name in READERS),
)


def _hits(full: str) -> bool:
    return any(full == name or full.startswith(name + ".") for name in FORBIDDEN)


def problems(tree: ast.AST) -> list[tuple[int, str]]:
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and not node.level:
            for full in [node.module, *(f"{node.module}.{a.name}" for a in node.names)]:
                if _hits(full):
                    found.append((node.lineno, f"import van {full}"))
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _hits(alias.name):
                    found.append((node.lineno, f"import van {alias.name}"))
        if isinstance(node, ast.Call):
            func = node.func
            if (isinstance(func, ast.Attribute) and func.attr == "import_module") or (
                isinstance(func, ast.Name) and func.id in ("__import__", "import_module")
            ):
                found.append((node.lineno, "import via een tekst"))
    return sorted(set(found))


@pytest.mark.parametrize("path", APP_SIDE, ids=lambda p: p.relative_to(APP).as_posix())
def test_de_kant_van_de_app_importeert_geen_lezer(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    assert problems(tree) == [], path


BAD = [
    "import pypdfium2",
    "import pypdfium2.raw as raw",
    "from app.office.reader import sax",
    "from app.office import reader",
    "from app.extract import pdf",
    "from app.extract.read import extract",
    "from app.extract import collect, frame",
    "def f():\n    from app import render\n",
    "importlib.import_module('app.extract.' + kind)",
    "__import__('pypdfium2')",
]
GOOD = [
    "from app.extract import LEGACY, MAX_BYTES, batch, kind_for",
    "from app.extract.frame import Found",
    "from app.office.convert import pdf_path",
    "from app.media.worker.args import TextArgs, TextItem",
]


@pytest.mark.parametrize("code", BAD)
def test_de_controle_vangt(code: str):
    assert problems(ast.parse(code)), code


@pytest.mark.parametrize("code", GOOD)
def test_de_controle_laat_staan(code: str):
    assert problems(ast.parse(code)) == [], code


# --- de hele keten in een eigen proces --------------------------------------------------------

SCRIPT = r"""
import asyncio, hashlib, json, sys
from pathlib import Path

FORBIDDEN = json.loads(sys.argv[2])
MEDIA = sys.argv[3] == "aan"


class Weiger:
    def find_spec(self, name, path=None, target=None):
        if any(name == f or name.startswith(f + ".") for f in FORBIDDEN):
            raise ImportError(f"{name} in het app-proces")
        return None


sys.meta_path.insert(0, Weiger())

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker

import app.main  # noqa: F401 - de app zoals hij draait, met de stap text aangemeld
from app import analysis
from app.config import Settings
from app.db import get_engine
from app.media import client
from app.media.worker.server import Config, Worker
from app.migrate import upgrade
from app.models import Blob, BlobText
from app.storage import BlobStore

KEY = "de-sleutel-van-de-keten-" * 3
CAPS = {"protocol": 1, "ffmpeg": "test", "ffprobe": "test", "mode": "local", "sandbox": "off"}
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


async def main(root: Path) -> dict:
    s = Settings(data_dir=root, web_dist=root / "web", jwt_secret="t" * 32, media_enabled=MEDIA)
    await upgrade(s)
    worker = Worker(Config(socket=root / "run" / "w.sock", max_procs=4, max_transcodes=1,
                           key=KEY), dict(CAPS))
    if MEDIA:
        await worker.start()
        client.bind(worker.config.socket, KEY)
    maker = async_sessionmaker(get_engine(s), expire_on_commit=False)
    shas = {}
    for name, mime in (("doc.pdf", "application/pdf"), ("doc.docx", DOCX)):
        data = (root / name).read_bytes()
        sha = hashlib.sha256(data).hexdigest()
        path = BlobStore(s.blob_dir).path(sha)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        async with maker() as db:
            db.add(Blob(sha256=sha, size=len(data), mime=mime))
            await db.flush()
            await analysis.queue(db, sha, [analysis.Step.TEXT], owner=None)
            await db.commit()
        shas[name] = sha
    try:
        count = await analysis.pump(s, "index")
    finally:
        if MEDIA:
            await worker.stop()
    async with maker() as db:
        bodies = {t.blob_sha: t.body for t in (await db.scalars(sa.select(BlobText))).all()}
    await get_engine(s).dispose()
    loaded = sorted(m for m in sys.modules
                    if any(m == f or m.startswith(f + ".") for f in FORBIDDEN))
    return {"count": count, "texts": {n: bodies.get(sha, "") for n, sha in shas.items()},
            "loaded": loaded}


print(json.dumps(asyncio.run(main(Path(sys.argv[1])))))
"""


@pytest.mark.parametrize("media", ["aan", "uit"])
def test_de_hele_keten_zonder_lezer_in_het_app_proces(tmp_path, media):
    (tmp_path / "doc.pdf").write_bytes(fixtures.pdf_tekst(1, tekst="pdfwoordx"))
    (tmp_path / "doc.docx").write_bytes(fixtures.docx("kantoorwoordx"))
    # app.render laadt de app al voor de previews (render.kind_of): die staat hier niet in de
    # lijst van de import-haak, wel in de AST-controle hierboven
    weiger = [name for name in FORBIDDEN if name != "app.render"]
    done = subprocess.run(
        [sys.executable, "-c", SCRIPT, str(tmp_path), json.dumps(weiger), media],
        cwd=API,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert done.returncode == 0, done.stderr[-2000:]
    out = json.loads(done.stdout.strip().splitlines()[-1])
    assert out["count"] == 2
    assert "pdfwoordx" in out["texts"]["doc.pdf"]
    assert "kantoorwoordx" in out["texts"]["doc.docx"]
    assert out["loaded"] == []
