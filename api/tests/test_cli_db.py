"""python -m app.cli db-downgrade en office-checkpoint (§3.5, §3.8): db-downgrade 0006 vraagt
om bevestiging en doet daarna precies dat; een onbekende of latere revisie doet niets."""

import os
import sys
from pathlib import Path

import anyio
import pytest
import sqlalchemy as sa

from app import cli
from app.db import get_engine
from app.migrate import upgrade

API_DIR = Path(__file__).resolve().parents[1]


async def run_cli(settings, *args: str, stdin: bytes | None = None):
    env = {
        **os.environ,
        "PLATFORM_DATA_DIR": str(settings.data_dir),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    return await anyio.run_process(
        [sys.executable, "-m", "app.cli", *args],
        cwd=API_DIR,
        env=env,
        check=False,
        input=stdin if stdin is not None else b"",
    )


async def revision(settings) -> str:
    async with get_engine(settings).connect() as conn:
        return await conn.scalar(sa.text("SELECT version_num FROM alembic_version"))


async def has_table(settings, name: str) -> bool:
    async with get_engine(settings).connect() as conn:
        found = await conn.scalar(
            sa.text("SELECT count(*) FROM sqlite_master WHERE type='table' AND name=:n"),
            {"n": name},
        )
    return bool(found)


def test_check_downgrade():
    cli.check_downgrade("0007", "0006")
    cli.check_downgrade("0007", "0001")
    for current, target in (
        ("0007", "0007"),  # gelijk: niets te doen
        ("0006", "0007"),  # later: dat is geen downgrade
        ("0007", "9999"),  # bestaat niet
        ("0007", "kapot"),
        (None, "0006"),  # een lege database
    ):
        with pytest.raises(cli.UnknownRevision):
            cli.check_downgrade(current, target)


@pytest.mark.anyio
async def test_downgrade_vraagt_om_bevestiging(settings):
    assert await revision(settings) == "0007"

    result = await run_cli(settings, "db-downgrade", "0006", stdin=b"nee\n")
    assert result.returncode == 1
    out = result.stdout.decode()
    assert "van revisie 0007 terug naar 0006" in out
    assert 'Typ "ja" om door te gaan' in out
    assert "Niets gedaan." in result.stderr.decode()
    assert await revision(settings) == "0007"

    # Zonder invoer (EOF) ook niet
    result = await run_cli(settings, "db-downgrade", "0006")
    assert result.returncode == 1
    assert await revision(settings) == "0007"

    result = await run_cli(settings, "db-downgrade", "0006", stdin=b"ja\n")
    assert result.returncode == 0, result.stderr.decode()
    assert "De database staat nu op revisie 0006." in result.stdout.decode()
    assert await revision(settings) == "0006"
    assert not await has_table(settings, "office_session")

    # Een latere of onbekende revisie: niets
    for target in ("0007", "9999"):
        result = await run_cli(settings, "db-downgrade", target, "--ja")
        assert result.returncode == 1
        assert "bestaat niet of ligt niet vóór de huidige (0006)" in result.stderr.decode()
    assert await revision(settings) == "0006"

    # De app zet hem bij het starten weer op head
    await upgrade(settings)
    assert await revision(settings) == "0007"
    assert await has_table(settings, "office_session")


@pytest.mark.anyio
async def test_downgrade_met_ja_vraagt_niets(settings):
    result = await run_cli(settings, "db-downgrade", "0005", "--ja")
    assert result.returncode == 0, result.stderr.decode()
    assert "Typ" not in result.stdout.decode()
    assert await revision(settings) == "0005"


@pytest.mark.anyio
async def test_office_checkpoint_zonder_office(settings):
    """Zonder office is er niets open: 0 documenten, en geen fout."""
    result = await run_cli(settings, "office-checkpoint", "--wait", "1")
    assert result.returncode == 0, result.stderr.decode()
    assert "Tussentijds opgeslagen: 0 document(en)." in result.stdout.decode()
