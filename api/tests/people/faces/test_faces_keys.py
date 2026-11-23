"""Een sleutel die weg is, en de sleutels nalopen (0.8.2, P4; app/people/faces/keycheck.py).

- Staat er een versleuteld gezicht van een eigenaar en is zijn sleutel weg (zonder "uit": een
  node zonder de sleutels van de andere), dan maakt ensure_key geen nieuwe (KeyLost), slaat de
  scan die eigenaar over (geen nieuwe sleutel, geen nieuwe vector), en zeggen /beheer
  (keys_missing) en analyse-status "sleutel ontbreekt" met het aantal eigenaren. Komt de sleutel
  terug, dan gaat alles weer verder.
- sleutels-check: exitcode 0 alleen als elke eigenaar die een sleutel nodig heeft er een heeft
  als gewoon bestand van 32 bytes, 0600, van gebruiker 10001, in een map die geen symlink is.
  --opruimen haalt alleen de sleutels weg van wie geen rijen meer heeft en gezichten niet aan
  heeft (of niet meer bestaat). Op het scherm alleen aantallen: geen sleutel, pad of eigenaar.
- backup-wipe-tables: backup.WIPE, één per regel.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app import backup
from app.analyse import status
from app.models import Face, FaceScan, PhotoPref
from app.people.faces import crypto, keycheck, scan
from tests.factories import create_user, maker
from tests.people.faces.conftest import aan, draai, foto

pytestmark = pytest.mark.anyio

A = (0.10, 0.10, 0.20, 0.25)
B = (0.60, 0.20, 0.20, 0.25)
ADMIN = "/api/v1/admin/search"
API_DIR = Path(__file__).resolve().parents[3]


async def _user(settings, email: str) -> uuid.UUID:
    async with maker(settings)() as session:
        user = await create_user(session, email)
        await session.commit()
    return user.id


async def _eva(settings, nep) -> uuid.UUID:
    """Eva met gezichten aan, twee gezichten op een foto, gescand (dus een sleutel)."""
    eva = await _user(settings, "eva@example.nl")
    await foto(settings, nep, eva, "0.jpg", [(A, 1), (B, 2)])
    await aan(settings, eva)
    await draai(settings)
    assert crypto.load_key(settings, eva) is not None
    return eva


async def _sealed(settings, owner: uuid.UUID) -> int:
    async with maker(settings)() as db:
        return int(
            await db.scalar(
                select(func.count()).where(Face.owner_id == owner, Face.vec.is_not(None))
            )
        )


async def test_een_sleutel_die_weg_is_wordt_niet_vervangen(client, settings, nep, admin):
    eva = await _eva(settings, nep)
    assert await _sealed(settings, eva) == 2
    path = crypto.key_path(settings, eva)
    bewaard = path.read_bytes()
    path.unlink()  # weg zonder "uit": zoals een node zonder de sleutels van de andere

    async with maker(settings)() as db:
        with pytest.raises(crypto.KeyLost):
            await crypto.ensure_key(settings, db, eva)
        assert await crypto.missing_owners(settings, db) == [eva]
    assert not path.exists()

    # Een nieuwe foto (met de planning van een upload): de scan laat Eva wachten, zonder nieuwe
    # sleutel of nieuwe vector
    nieuw = await foto(settings, nep, eva, "1.jpg", [(A, 3)])
    async with maker(settings)() as db:
        await scan.queue_plan(db, "owner", owner=eva)
        await db.commit()
    await draai(settings)
    assert not path.exists()
    assert await _sealed(settings, eva) == 2
    async with maker(settings)() as db:
        rij = await db.scalar(select(FaceScan).where(FaceScan.node_id == nieuw))
    assert rij is not None and rij.state == "wait", rij

    # /beheer en analyse-status zeggen het, met alleen het aantal
    r = await client.get(ADMIN, headers=admin[0])
    assert r.status_code == 200, r.text
    assert r.json()["faces"]["keys_missing"] == 1
    assert eva.hex not in r.text
    regels, _code = await status.collect(settings)
    assert status.keys_line(1) in regels
    assert not any(eva.hex in regel for regel in regels)

    # De sleutel terug: weer 0, en de scan gaat verder met dezelfde sleutel
    path.write_bytes(bewaard)
    os.chmod(path, 0o600)
    r = await client.get(ADMIN, headers=admin[0])
    assert r.json()["faces"]["keys_missing"] == 0
    regels, _code = await status.collect(settings)
    assert not any("sleutel ontbreekt" in regel for regel in regels)
    async with maker(settings)() as db:
        await scan.queue_scan(db)  # zoals de ronde van elk uur (faces.plan) of een nieuwe foto
        await db.commit()
    await draai(settings)
    assert crypto.load_key(settings, eva) == bewaard
    assert await _sealed(settings, eva) == 3


async def test_zonder_versleutelde_gezichten_wel_een_nieuwe_sleutel(settings):
    """De eerste sleutel (of na "uit" en het wissen): geen rijen, dus wel een sleutel."""
    owner = await _user(settings, "nieuw@example.nl")
    async with maker(settings)() as db:
        key = await crypto.ensure_key(settings, db, owner)
    assert len(key) == crypto.KEY_BYTES
    assert crypto.load_key(settings, owner) == key


# --- sleutels-check ----------------------------------------------------------------------------


@pytest.fixture
def mijn_uid(monkeypatch):
    """De sleutels in de test zijn van de gebruiker van de test, niet van 10001."""
    monkeypatch.setattr(keycheck, "KEY_UID", os.getuid())


async def _check(settings, cleanup: bool = False) -> keycheck.Report:
    return await keycheck.check(settings, cleanup)


async def test_sleutels_check_in_orde_en_wat_er_mis_kan_zijn(settings, nep, mijn_uid):
    eva = await _eva(settings, nep)
    found = await _check(settings)
    assert found.ok and found.needed == 1 and found.folder == "in orde", found
    assert found.problems == dict.fromkeys(keycheck.PROBLEMS, 0)
    path = crypto.key_path(settings, eva)
    bewaard = path.read_bytes()

    os.chmod(path, 0o644)
    found = await _check(settings)
    assert not found.ok and found.problems["mode"] == 1, found
    os.chmod(path, 0o600)

    elders = path.with_name("elders")
    path.rename(elders)
    path.symlink_to(elders)  # wijst naar een goede sleutel, maar is geen gewoon bestand
    found = await _check(settings)
    assert not found.ok and found.problems["not_file"] == 1, found
    path.unlink()
    elders.unlink()

    found = await _check(settings)
    assert not found.ok and found.problems["missing"] == 1, found

    path.write_bytes(bewaard[:16])
    os.chmod(path, 0o600)
    found = await _check(settings)
    assert not found.ok and found.problems["size"] == 1, found
    path.write_bytes(bewaard)
    assert (await _check(settings)).ok

    # Een sleutelmap die een symlink is: nooit in orde, ook als de sleutel klopt
    keys = crypto.keys_dir(settings)
    echt = keys.with_name("sleutels-echt")
    keys.rename(echt)
    keys.symlink_to(echt, target_is_directory=True)
    try:
        found = await _check(settings)
        assert not found.ok and found.folder == "is een symlink", found
    finally:
        keys.unlink()
        echt.rename(keys)
    assert (await _check(settings)).ok


async def test_sleutels_check_eigenaar_niet_10001(settings, nep):
    await _eva(settings, nep)
    assert os.getuid() != keycheck.KEY_UID
    found = await _check(settings)
    assert not found.ok and found.problems["uid"] == 1, found


async def test_opruimen_haalt_alleen_overbodige_sleutels_weg(settings, nep, mijn_uid):
    eva = await _eva(settings, nep)
    # Bram: gezichten uit en geen rijen (zijn "uit" liep op een andere node)
    bram = await _user(settings, "bram@example.nl")
    async with maker(settings)() as db:
        db.add(PhotoPref(user_id=bram, faces="uit"))
        await db.commit()
        await crypto.ensure_key(settings, db, bram)
    # Carla: gezichten uit, maar haar rijen staan er nog (de wisronde moet nog)
    carla = await _user(settings, "carla@example.nl")
    await foto(settings, nep, carla, "c.jpg", [(A, 7)])
    await aan(settings, carla)
    await draai(settings)
    async with maker(settings)() as db:
        (await db.get(PhotoPref, carla)).faces = "uit"
        await db.commit()
    # Dirk: gezichten aan, nog niets gescand (een sleutel zonder rijen: blijft)
    dirk = await _user(settings, "dirk@example.nl")
    await aan(settings, dirk)
    async with maker(settings)() as db:
        await crypto.ensure_key(settings, db, dirk)
    # Een sleutel van iemand die niet meer bestaat; Erik bestaat wel, zonder photo_pref
    weg = uuid.uuid4()
    erik = await _user(settings, "erik@example.nl")
    for owner in (weg, erik):
        p = crypto.key_path(settings, owner)
        p.write_bytes(os.urandom(crypto.KEY_BYTES))
        os.chmod(p, 0o600)
    (crypto.keys_dir(settings) / "iets-anders.txt").write_text("x")

    found = await _check(settings)
    assert found.ok and found.needed == 1 and found.spare == 5 and found.other == 1, found
    assert found.removed == 0
    found = await _check(settings, cleanup=True)
    assert found.ok and found.removed == 2, found
    gebleven = {o for o in (eva, bram, carla, dirk, weg, erik) if crypto.load_key(settings, o)}
    assert gebleven == {eva, carla, dirk, erik}
    assert (crypto.keys_dir(settings) / "iets-anders.txt").exists()
    # Nog een keer: niets meer weg te halen
    found = await _check(settings, cleanup=True)
    assert found.ok and found.removed == 0 and found.spare == 3, found


def _cli(settings, *args: str) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if not k.startswith("PLATFORM_")}
    env.update(
        PLATFORM_DATA_DIR=str(settings.data_dir),
        PLATFORM_FACES_KEYS=settings.faces_keys,
        PYTHONDONTWRITEBYTECODE="1",
    )
    if not settings.db_url.startswith("sqlite"):
        env["PLATFORM_DATABASE_URL"] = settings.database_url
    return subprocess.run(
        [sys.executable, "-m", "app.cli", *args],
        cwd=API_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


async def test_de_commandos_zeggen_alleen_aantallen(settings, nep):
    eva = await _eva(settings, nep)
    key = crypto.load_key(settings, eva)
    done = _cli(settings, "sleutels-check")
    # De sleutel is van de gebruiker van de test, niet van 10001: exitcode 1
    assert done.returncode == 1, done.stderr
    assert "eigenaar niet 10001: 1" in done.stdout
    assert "eigenaren met een sleutel nodig: 1" in done.stdout
    out = done.stdout + done.stderr
    assert eva.hex not in out and str(eva) not in out
    assert settings.faces_keys not in out and str(settings.data_dir) not in out
    assert key.hex() not in out
    # --opruimen haalt de sleutel van Eva niet weg (zij heeft gezichten)
    done = _cli(settings, "sleutels-check", "--opruimen")
    assert done.returncode == 1 and "weggehaald (--opruimen): 0" in done.stdout
    assert crypto.load_key(settings, eva) == key

    done = _cli(settings, "backup-wipe-tables")
    assert done.returncode == 0, done.stderr
    assert done.stdout.splitlines() == list(backup.WIPE)


async def test_sleutels_check_zonder_sleutelmap(settings, nep, mijn_uid):
    """Niet ingesteld en niemand heeft een sleutel nodig: in orde. Wel iemand: niet."""
    leeg = settings.model_copy(update={"faces_keys": ""})
    found = await _check(leeg)
    assert found.ok and found.folder.startswith("niet ingesteld"), found
    await _eva(settings, nep)
    found = await _check(leeg)
    assert not found.ok and found.problems["missing"] == 1, found
    weg = settings.model_copy(update={"faces_keys": str(settings.data_dir / "geen")})
    found = await _check(weg)
    assert not found.ok and found.folder == "ontbreekt", found
    shutil.rmtree(settings.data_dir / "geen", ignore_errors=True)
