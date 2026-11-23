"""0.8.2, P4: nooit een nieuwe sleutel naast versleutelde gezichten.

In het cluster staan de sleutels per node en komen ze elke minuut van de andere nodes. Neemt een
node het VIP over voor de sync er was, dan mag faces.scan geen nieuwe sleutel maken: al de oude
gezichten van die eigenaar waren dan onleesbaar. De scan wacht (Defer); sleutels-check zegt wie
het zijn en wist ze bewust met --wissen.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app import jobs
from app.models import Face, FaceScan
from app.people.faces import crypto, keycheck, scan
from tests.factories import create_user, maker
from tests.people.faces.conftest import aan, draai, foto

pytestmark = pytest.mark.anyio

BOX = (0.1, 0.1, 0.2, 0.25)


async def _user(settings, email):
    async with maker(settings)() as session:
        user = await create_user(session, email)
        await session.commit()
        return user.id


async def _vecs(settings, owner) -> int:
    async with maker(settings)() as session:
        return await session.scalar(
            select(func.count()).where(Face.owner_id == owner, Face.vec.is_not(None))
        )


async def test_geen_nieuwe_sleutel_naast_oude_gezichten(settings, nep):
    eva = await _user(settings, "eva@example.nl")
    await foto(settings, nep, eva, "een.jpg", [(BOX, 1)])
    await aan(settings, eva)
    await draai(settings)
    assert await _vecs(settings, eva) == 1
    path = crypto.key_path(settings, eva)
    bewaard = path.read_bytes()
    found = await keycheck.check(settings)
    assert (found.owners, found.lost) == (1, {})
    assert keycheck.report(found)[0] == 0

    # De sleutel is (nog) niet op deze node, en er komt een nieuwe foto
    path.unlink()
    assert await scan.keys_lost(settings, {eva}) == {eva}
    nieuw = await foto(settings, nep, eva, "twee.jpg", [(BOX, 2)])
    await aan(settings, eva)
    await draai(settings)
    assert not path.exists()  # geen nieuwe sleutel
    async with maker(settings)() as session:
        state = await session.scalar(select(FaceScan.state).where(FaceScan.node_id == nieuw))
        deferred = await session.scalar(
            select(func.count()).where(jobs.Job.type == scan.SCAN, jobs.Job.error.is_(None))
        )
    assert state == "wait" and deferred >= 1
    assert await _vecs(settings, eva) == 1  # het oude gezicht is er nog en blijft leesbaar
    found = await keycheck.check(settings)
    assert found.lost == {eva: 1}
    code, lines = keycheck.report(found)
    assert code == 1 and str(eva) in "\n".join(lines)
    assert bewaard.hex() not in "\n".join(lines)

    # De sync zet de sleutel terug: de scan gaat verder met dezelfde sleutel
    path.write_bytes(bewaard)
    path.chmod(0o600)
    await draai(settings, scan.state.now() + scan.timedelta(seconds=scan.KEY_WAIT_S + 1))
    assert path.read_bytes() == bewaard
    assert await _vecs(settings, eva) == 2


async def test_sleutels_check_wissen(settings, nep):
    eva = await _user(settings, "eva@example.nl")
    await foto(settings, nep, eva, "een.jpg", [(BOX, 1)])
    await aan(settings, eva)
    await draai(settings)
    crypto.key_path(settings, eva).unlink()
    found = await keycheck.check(settings, wipe=True)
    assert found.lost == {eva: 1} and found.queued == 1
    assert keycheck.report(found)[0] == 0
    await draai(settings)
    assert await _vecs(settings, eva) == 0
    assert (await keycheck.check(settings)).lost == {}


async def test_een_eigenaar_zonder_gezichten_krijgt_gewoon_een_sleutel(settings, nep):
    eva = await _user(settings, "eva@example.nl")
    assert await scan.keys_lost(settings, {eva}) == set()
    await foto(settings, nep, eva, "een.jpg", [(BOX, 1)])
    await aan(settings, eva)
    await draai(settings)
    assert crypto.load_key(settings, eva) is not None
