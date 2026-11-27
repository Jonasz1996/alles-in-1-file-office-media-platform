"""De naad van Keuze 3 (§2.0 regel 4, §3.8): buiten app/office/oo/ importeert alleen
app/office/engine.py de adapter voor OnlyOffice. De rest vraagt engine.current()."""

import ast
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"
ADAPTER = APP / "office" / "oo"
ALLOWED = {APP / "office" / "engine.py"}


def module_of(path: Path) -> str:
    parts = path.relative_to(APP.parent).with_suffix("").parts
    return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)


def imported(path: Path, module: str | None = None) -> set[str]:
    """Elke module die dit bestand importeert, ook binnen een functie, voluit geschreven.
    module: de naam van het bestand als module (standaard volgens zijn plek onder app/)."""
    tree = ast.parse(path.read_text(), filename=str(path))
    package = module or module_of(path)
    if path.name != "__init__.py":
        package = package.rpartition(".")[0]
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                anchor = package.split(".")
                anchor = anchor[: len(anchor) - (node.level - 1)]
                base = ".".join([*anchor, base] if base else anchor)
            found.add(base)
            found.update(f"{base}.{alias.name}" for alias in node.names)
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "import_module"
            and node.args
            and isinstance(node.args[0], ast.Constant)
        ):
            found.add(str(node.args[0].value))
    return found


def uses_adapter(path: Path, module: str | None = None) -> bool:
    found = imported(path, module)
    return any(m == "app.office.oo" or m.startswith("app.office.oo.") for m in found)


def test_alleen_engine_importeert_de_adapter():
    offenders = [
        str(path.relative_to(APP.parent))
        for path in sorted(APP.rglob("*.py"))
        if ADAPTER not in path.parents and path not in ALLOWED and uses_adapter(path)
    ]
    assert offenders == []


def test_engine_importeert_hem_wel():
    """Anders zegt de eerste test niets: de controle vindt de import echt."""
    assert uses_adapter(APP / "office" / "engine.py")


def test_de_controle_ziet_elke_vorm(tmp_path):
    fake = tmp_path / "proef.py"
    name = "app.office.proef"  # alsof het bestand in app/office/ staat
    forms = [
        "import app.office.oo.tokens",
        "from app.office.oo import tokens",
        "from app.office import oo",
        "from .oo import client",
        "from . import oo",
        "def f():\n    from app.office.oo.client import get",
        "import importlib\nimportlib.import_module('app.office.oo.admin')",
    ]
    for source in forms:
        fake.write_text(source + "\n")
        assert uses_adapter(fake, name), source
    fake.write_text("from app.office import engine, oops\nimport app.office.ooo\nfrom . import x\n")
    assert not uses_adapter(fake, name)
