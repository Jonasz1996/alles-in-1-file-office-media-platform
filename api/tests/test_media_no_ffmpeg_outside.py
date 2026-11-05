"""Regel 4 van fase 5 (Keuze 11, M1): buiten app/media/worker/ start niets ffmpeg of ffprobe.

De test leest de AST van elke module onder app/ buiten app/media/worker/ en zoekt:
1. een tekst die precies een van de programma's noemt: "ffmpeg", "ffprobe", of een pad dat
   ermee eindigt ("/usr/bin/ffmpeg"), ook als stuk van een f-string. Waar de naam als tekst
   staat, kan hij in een argv of in shutil.which terechtkomen; een melding als "ffmpeg
   ontbreekt" is geen programmanaam en mag;
2. een aanroep die een proces start (subprocess, os.exec*, os.spawn*, os.system, os.popen,
   asyncio.create_subprocess_*, pty.spawn) met een tekst waarvan het eerste woord zo'n
   programma is ("ffmpeg -i x" met shell=True);
3. een import van wat in de worker processen start (app.media.worker.run en de opdrachten in
   app.media.worker.ops): die horen alleen in de worker.

De namen staan op één plek: guard.BINARIES. render.py gebruikt die voor shutil.which (media uit:
staat ffmpeg op deze machine?), en start zelf niets: het vraagt de worker.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

API = Path(__file__).resolve().parents[1]
APP = API / "app"
WORKER = APP / "media" / "worker"

PROGRAM = re.compile(r"(?:.*/)?ff(?:mpeg|probe)(?:\.exe)?", re.IGNORECASE)
SPAWNERS = {
    ("subprocess", name)
    for name in ("run", "call", "check_call", "check_output", "Popen", "getoutput")
} | {
    ("os", name)
    for name in (
        "system", "popen", "execl", "execle", "execlp", "execlpe", "execv", "execve",
        "execvp", "execvpe", "spawnl", "spawnle", "spawnlp", "spawnlpe", "spawnv", "spawnve",
        "spawnvp", "spawnvpe", "posix_spawn", "posix_spawnp",
    )
} | {
    ("asyncio", "create_subprocess_exec"),
    ("asyncio", "create_subprocess_shell"),
    ("pty", "spawn"),
}  # fmt: skip
FORBIDDEN_IMPORTS = ("app.media.worker.run", "app.media.worker.ops")


def _strings(node: ast.AST):
    """Alle teksten in node (ook de vaste delen van een f-string)."""
    for sub in ast.walk(node):
        if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
            yield sub, sub.value


def _dotted(func: ast.expr) -> tuple[str, str] | None:
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        return func.value.id, func.attr
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Attribute):
        return func.value.attr, func.attr  # bijv. asyncio.subprocess.create_subprocess_exec
    return None


def problems(tree: ast.AST) -> list[tuple[int, str]]:
    found: list[tuple[int, str]] = []
    for node, text in _strings(tree):
        if PROGRAM.fullmatch(text.strip()):
            found.append((node.lineno, f"programmanaam als tekst: {text!r}"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = _dotted(node.func)
            if name in SPAWNERS:
                for sub, text in _strings(node):
                    first = (text.strip().split() or [""])[0]
                    if PROGRAM.fullmatch(first):
                        found.append((sub.lineno, f"{'.'.join(name)} start {first}"))
        if isinstance(node, ast.ImportFrom) and node.module:
            module = node.module
            names = [f"{module}.{alias.name}" for alias in node.names]
            for full in [module, *names]:
                if full.startswith(FORBIDDEN_IMPORTS):
                    found.append((node.lineno, f"import van {full}"))
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith(FORBIDDEN_IMPORTS):
                    found.append((node.lineno, f"import van {alias.name}"))
    return sorted(set(found))


def modules() -> list[Path]:
    return sorted(p for p in APP.rglob("*.py") if WORKER not in p.parents)


def test_er_zijn_modules_om_te_lezen():
    names = {p.relative_to(APP).as_posix() for p in modules()}
    assert {"render.py", "render_meta.py", "previews.py", "media/local.py"} <= names
    assert not any(n.startswith("media/worker/") for n in names)


@pytest.mark.parametrize("path", modules(), ids=lambda p: p.relative_to(APP).as_posix())
def test_geen_ffmpeg_buiten_de_worker(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    assert problems(tree) == [], path


# --- de controle zelf vangt wat hij moet vangen -----------------------------------------------

BAD = [
    'subprocess.run(["ffmpeg", "-i", x])',
    "subprocess.Popen(['ffprobe', '-v', 'error'])",
    'shutil.which("ffmpeg")',
    'os.execvp("/usr/bin/ffprobe", argv)',
    'await asyncio.create_subprocess_exec("ffmpeg", *args)',
    'subprocess.run("ffmpeg -i x out.png", shell=True)',
    'os.system("ffprobe -show_format x")',
    'BIN = "ffmpeg"',
    'cmd = [f"/opt/bin/ffmpeg"]',
    "from app.media.worker.run import spawn",
    "from app.media.worker.ops import frame",
    "import app.media.worker.run",
    'asyncio.subprocess.create_subprocess_exec("ffmpeg")',
]
GOOD = [
    'log.warning("ffmpeg ontbreekt")',
    'missing = ", ".join(caps.missing) or "ffmpeg en ffprobe"',
    'state.set_worker("ffmpeg_missing", reason)',
    "from app.media.worker.guard import BINARIES",
    "from app.media.worker.args import FrameArgs",
    "all(shutil.which(name) for name in BINARIES)",
    'subprocess.run(["sleep", "1"])',
]


@pytest.mark.parametrize("code", BAD)
def test_de_controle_vangt(code: str):
    assert problems(ast.parse(code)), code


@pytest.mark.parametrize("code", GOOD)
def test_de_controle_laat_staan(code: str):
    assert problems(ast.parse(code)) == [], code
