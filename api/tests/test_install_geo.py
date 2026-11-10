"""De bronnen voor plaatsnamen in install.sh (Z7, Keuze 29) zijn die van de app.

install.sh haalt ze naar DATA_ROOT/geo met een vaste grootte en sha256; app/geo/load.py leest ze
alleen met precies die sha256. Staan er twee verschillende waarden, dan haalt install.sh elke run
een bestand dat de app weigert.
"""

from __future__ import annotations

from pathlib import Path

from app.config import Settings
from app.geo import load

INSTALL_SH = Path(__file__).resolve().parents[2] / "infra" / "single" / "install.sh"


def _bronnen() -> list[tuple[str, ...]]:
    text = INSTALL_SH.read_text(encoding="utf-8")
    start = text.index("geo_bronnen() {\n  cat <<'BRONNEN'\n") + len(
        "geo_bronnen() {\n  cat <<'BRONNEN'\n"
    )
    block = text[start : text.index("\nBRONNEN\n", start)]
    return [tuple(line.split(" ")) for line in block.splitlines()]


def test_de_bronnen_in_install_sh_zijn_die_van_de_app(tmp_path):
    assert _bronnen() == [
        (b.naam, str(b.grootte), b.sha256, b.url) for b in (load.LANDEN, load.PLAATSEN)
    ]
    # De bestanden komen waar de app ze zoekt: geo_src is standaard data_dir/geo (sinds de review
    # van golf 4, veilig F2, eerst in een eigen map tmp van root buiten DATA_ROOT)
    assert "  local dir=$DATA_ROOT/geo name size sha url tmp failed=0 new=0\n" in (
        INSTALL_SH.read_text(encoding="utf-8")
    )
    assert Settings(data_dir=tmp_path).geo_src == tmp_path / "geo"
