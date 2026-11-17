"""Het geheugen van migratie 0010 met temp_store=MEMORY (review A0, uitrol U1).

app/db.py zet op elke verbinding PRAGMA temp_store=2 (Keuze 64, K1-03): een sortering zonder
index en een tijdelijke tabel blijven in het geheugen, niet in een bestand buiten de database.
Het vullen van 0010 (de recursieve CTE achter UPDATE … FROM, die mpath en dead zet) houdt zo al
zijn rijen in het geheugen van het proces. Gemeten op 100.000 nodes (docs/fase-6/a0.md, De
review): +116 MiB op SQLite 3.46.1 en +119 MiB op 3.53.1; met temp_store=FILE +14–16 MiB. Op
300.000 nodes +765 MiB. Eenmalig: alleen de update van 0.7.1 draait 0010, bij de start, vóór de
app verzoeken aanneemt. De grens hieronder laat een toename zien voordat iemand hem in productie
merkt; temp_store blijft MEMORY."""

import asyncio
import json
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from alembic import command

from app.db import get_engine
from tests.search.migratie import NOW, fill_0009, migrate, settings_at

N = 100_000
GRENS_MIB = 200  # gemeten +116–119 MiB (zie boven)
API = Path(__file__).resolve().parents[2]

# In een eigen proces: de piek van pytest zelf zegt niets
KIND = """
import asyncio, json, sys
from pathlib import Path
from alembic import command
from app.config import Settings
from app.db import get_engine
from tests.search.migratie import migrate

def piek():
    # VmHWM: de piek van dit proces sinds exec; ru_maxrss neemt ook die van pytest mee (fork)
    for line in Path("/proc/self/status").read_text().splitlines():
        if line.startswith("VmHWM:"):
            return int(line.split()[1])
    raise SystemExit("geen VmHWM")

async def main(path):
    settings = Settings(data_dir=Path(path), jwt_secret="test" * 8, timezone="Europe/Amsterdam")
    async with get_engine(settings).connect() as conn:
        store = (await conn.exec_driver_sql("PRAGMA temp_store")).scalar()
    voor = piek()
    await migrate(settings, command.upgrade, "0010")
    na = piek()
    await get_engine(settings).dispose()
    print(json.dumps({"temp_store": store, "groei_kib": na - voor}))

asyncio.run(main(sys.argv[1]))
"""


@pytest.mark.anyio
@pytest.mark.zoek_perf
async def test_migratie_0010_op_100000_nodes_blijft_onder_de_grens_van_het_geheugen(tmp_path):
    settings = settings_at(tmp_path, "db")
    await migrate(settings, command.upgrade, "0009")
    ids = await fill_0009(settings)

    def bulk(conn) -> None:
        dirs = [ids["root"].hex]
        rows = []
        for i in range(N):
            node_id = uuid.uuid4().hex
            is_dir = i % 17 == 0
            parent = dirs[(i * 7) % len(dirs)]
            rows.append((node_id, parent, ids["eva"].hex, f"bestand {i}", "dir" if is_dir
                         else "file", NOW, NOW, f"bestand {i}"))  # fmt: skip
            if is_dir:
                dirs.append(node_id)
        conn.exec_driver_sql(
            "INSERT INTO node (id, parent_id, owner_id, name, kind, size, created_at, "
            "modified_at, search_name) VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?)",
            rows,
        )

    async with get_engine(settings).begin() as conn:
        await conn.run_sync(bulk)
    await get_engine(settings).dispose()

    done = await asyncio.to_thread(
        subprocess.run,
        [sys.executable, "-c", KIND, str(settings.data_dir)],
        cwd=API,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert done.returncode == 0, done.stderr[-2000:]
    found = json.loads(done.stdout.strip().splitlines()[-1])
    assert found["temp_store"] == 2  # de instelling van app/db.py, niet die van de bouw
    groei = found["groei_kib"] / 1024
    print(f"migratie 0010 op {N} nodes: +{groei:.0f} MiB (grens {GRENS_MIB} MiB)")
    assert groei < GRENS_MIB, groei
