"""Gezichten vinden: faces.plan en faces.scan met de nep-worker (fase 6, X2b; laag 1).

Plannen; om de beurt (≤ 4 per eigenaar), nieuwste eerst, de viewer vooraan; no_preview → wait,
dan na preview.large klaar; too_small → skip; alleen foto's; unreadable en bad_output zonder
nieuwe poging; timeout 3 keer; Defer zonder poging; busy → wait na een crash; "uit" tijdens een
job: niets geschreven.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import func, select

from app import jobs
from app.analyse import monitor, wire
from app.models import Face, FaceScan, PhotoPref
from app.people.faces import crypto, register, scan
from tests.analyse.fake_worker import FakeFail
from tests.factories import create_file, create_user, maker
from tests.people.faces.conftest import BASE, aan, draai, foto, write_large

pytestmark = pytest.mark.anyio

BOX = (0.1, 0.1, 0.2, 0.25)
BOX2 = (0.6, 0.2, 0.2, 0.25)


async def _eva(settings):
    async with maker(settings)() as session:
        eva = await create_user(session, "eva@example.nl")
        await session.commit()
        return eva.id


async def _scans(settings) -> dict:
    async with maker(settings)() as session:
        rows = await session.execute(select(FaceScan.node_id, FaceScan.state, FaceScan.code,
                                            FaceScan.tries, FaceScan.faces))  # fmt: skip
        return {r.node_id: (r.state, r.code, r.tries, r.faces) for r in rows}


async def _faces(settings, node=None) -> list:
    async with maker(settings)() as session:
        q = select(Face)
        if node is not None:
            q = q.where(Face.node_id == node)
        return list(await session.scalars(q))


async def test_plannen_zoeken_en_versleuteld_opslaan(settings, nep, fake):
    eva = await _eva(settings)
    een = await foto(settings, nep, eva, "een.jpg", [(BOX, 1), (BOX2, 2)])
    twee = await foto(settings, nep, eva, "twee.jpg", [(BOX, 1)])
    tekst = None
    async with maker(settings)() as session:
        from app.models import User

        tekst = (await create_file(session, await session.get(User, eva), name="a.txt")).id
        await session.commit()
    await aan(settings, eva)
    await draai(settings)
    scans = await _scans(settings)
    assert scans[een] == ("done", None, 0, 2)
    assert scans[twee] == ("done", None, 0, 1)
    assert tekst not in scans  # alleen foto's
    faces = await _faces(settings)
    assert len(faces) == 3
    key = crypto.load_key(settings, eva)
    assert key is not None
    for face in faces:
        assert len(face.vec) == crypto.VEC_BYTES == 540
        vec = crypto.open_vec(key, face.id, face.vec)
        assert vec is not None and len(vec) == wire.FACE_VEC_BYTES and vec not in face.vec
    # De nep kreeg de previews als fd (twee foto's in één opdracht), en groepeerde daarna
    assert nep.seen == 2 and nep.groups >= 1
    assert [s.previews for s in fake.requests if s.op is wire.Op.FACES] == [2]


async def test_zonder_1024_wacht_en_daarna_klaar(settings, nep):
    eva = await _eva(settings)
    wacht = await foto(settings, nep, eva, "wacht.jpg", [(BOX, 1)], large=False,
                       sizes=(256,))  # fmt: skip
    async with maker(settings)() as session:
        node = await session.get(__import__("app.models", fromlist=["Node"]).Node, wacht)
        sha = node.blob_id
        blob = await session.get(__import__("app.models", fromlist=["Blob"]).Blob, sha)
        blob.preview_meta = {"sizes": [256], "partial": True}
        await session.commit()
    klein = await foto(settings, nep, eva, "klein.jpg", [(BOX, 1)], large=False,
                       sizes=(256,))  # fmt: skip
    await aan(settings, eva)
    await draai(settings)
    scans = await _scans(settings)
    assert scans[wacht][:2] == ("wait", "no_preview")
    assert scans[klein][:2] == ("skip", "too_small")
    # De 1024 komt (preview.large): de haak zet faces.plan met scope sha, dan klaar
    data = __import__("tests.people.faces.conftest", fromlist=["webp"]).webp(99_001)
    nep.add(data, [(BOX, 1)])
    await write_large(settings, sha, data)
    await register.after_large(maker(settings), sha)
    await draai(settings)
    assert (await _scans(settings))[wacht] == ("done", None, 0, 1)


async def test_onleesbaar_en_te_groot_zonder_nieuwe_poging(settings, nep):
    eva = await _eva(settings)
    from tests.people.faces.conftest import webp

    stuk = await foto(settings, nep, eva, "stuk.jpg", [])
    groot = await foto(settings, nep, eva, "groot.jpg", [])
    async with maker(settings)() as session:
        from app.models import Node

        for node_id, code in ((stuk, "unreadable"), (groot, "too_big")):
            sha = (await session.get(Node, node_id)).blob_id
            data = webp(50_000 + len(code))
            nep.code(data, code)
            await write_large(settings, sha, data)
    await aan(settings, eva)
    await draai(settings)
    scans = await _scans(settings)
    assert scans[stuk][:3] == ("failed", "unreadable", 0)
    assert scans[groot][:3] == ("failed", "too_big", 0)


@pytest.mark.parametrize("lie", ["nan", "norm", "box", "short", "count"])
async def test_een_liegende_worker_is_bad_output(settings, nep, lie):
    eva = await _eva(settings)
    node = await foto(settings, nep, eva, "een.jpg", [(BOX, 1), (BOX2, 2)])
    nep.lie(lie)
    await aan(settings, eva)
    await draai(settings)
    assert (await _scans(settings))[node][:3] == ("failed", "bad_output", 0)
    assert await _faces(settings) == []


async def test_te_veel_bytes_in_uit_is_bad_output(settings, nep, fake):
    eva = await _eva(settings)
    node = await foto(settings, nep, eva, "een.jpg", [(BOX, 1)])

    async def te_veel(req):
        import os

        os.write(req.extra["uit"], b"\0" * (wire.FACES_MAX * wire.FACE_VEC_BYTES + 1))
        return wire.FacesData(photos=[wire.FacesPhoto(code="ok", w=1, h=1)])

    fake.on(wire.Op.FACES, te_veel)
    await aan(settings, eva)
    await draai(settings)
    assert (await _scans(settings))[node][:2] == ("failed", "bad_output")


async def test_timeout_drie_keer_dan_failed(settings, nep, fake):
    eva = await _eva(settings)
    node = await foto(settings, nep, eva, "een.jpg", [(BOX, 1)])
    fake.fail(wire.Op.FACES, wire.Code.TIMEOUT)
    await aan(settings, eva)
    await draai(settings)  # het plan, en de eerste poging
    for _ in range(3):
        await scan.scan_job(settings, {})
    state, code, tries, _n = (await _scans(settings))[node]
    assert (state, code, tries) == ("failed", "timeout", 3)


@pytest.mark.parametrize(
    ("make", "seconds"),
    [("down", monitor.DEFER_DOWN), ("busy", monitor.DEFER_BUSY),
     ("model", monitor.DEFER_MODEL)],
)  # fmt: skip
async def test_defer_zonder_poging(settings, nep, fake, make, seconds):
    eva = await _eva(settings)
    node = await foto(settings, nep, eva, "een.jpg", [(BOX, 1)])
    await aan(settings, eva)
    async with maker(settings)() as session:
        await scan.plan_nodes(session, eva)
        await session.commit()
    if make == "down":
        await fake.stop()
    elif make == "busy":
        fake.busy.add(wire.Child.WERK)
    else:
        fake.fail(wire.Op.FACES, wire.Code.MODEL_MISSING)
    with pytest.raises(jobs.Defer) as found:
        await scan.scan_job(settings, {})
    assert found.value.seconds == seconds
    assert (await _scans(settings))[node][:3] == ("wait", None, 0)
    assert (monitor.DEFER_DOWN, monitor.DEFER_BUSY, monitor.DEFER_MODEL) == (300, 30, 3600)


async def test_wijken_tijdens_de_opdracht(settings, nep, fake, monkeypatch):
    eva = await _eva(settings)
    node = await foto(settings, nep, eva, "een.jpg", [(BOX, 1)])
    await aan(settings, eva)
    async with maker(settings)() as session:
        await scan.plan_nodes(session, eva)
        await session.commit()
    gate = fake.hold(wire.Op.FACES)
    beurt = iter([False, True])
    monkeypatch.setattr(scan, "must_yield", lambda s: next(beurt, True))
    monkeypatch.setattr(scan, "POLL_S", 0.05)
    with pytest.raises(jobs.Defer) as found:
        await scan.scan_job(settings, {})
    assert found.value.seconds == scan.YIELD_S == 30
    assert gate.started.is_set()
    assert (await _scans(settings))[node][:3] == ("wait", None, 0)


async def test_om_de_beurt_hooguit_vier_per_eigenaar_nieuwste_eerst(settings, nep, fake):
    eva = await _eva(settings)
    async with maker(settings)() as session:
        bram = (await create_user(session, "bram@example.nl")).id
        await session.commit()
    evas = [await foto(settings, nep, eva, f"e{i}.jpg", [], taken=BASE + timedelta(days=i))
            for i in range(6)]  # fmt: skip
    brams = [await foto(settings, nep, bram, f"b{i}.jpg", []) for i in range(2)]
    await aan(settings, eva, bram)
    async with maker(settings)() as session:
        await scan.plan_nodes(session, eva)
        await scan.plan_nodes(session, bram)
        await session.commit()
    picked = await scan._pick(settings)
    by_owner = {}
    for item in picked:
        by_owner.setdefault(item.owner, []).append(item.node)
    assert len(by_owner[eva]) == 4 and set(by_owner[bram]) == set(brams)
    assert by_owner[eva] == list(reversed(evas))[:4]  # nieuwste eerst
    await scan._restore(settings, picked)
    # De viewer opent de oudste: die gaat vooraan
    async with maker(settings)() as session:
        await scan.to_front(session, eva, evas[0])
        await session.commit()
    picked = await scan._pick(settings)
    assert [i.node for i in picked if i.owner == eva][0] == evas[0]


async def test_busy_na_een_crash_terug_naar_wait(settings, nep):
    eva = await _eva(settings)
    node = await foto(settings, nep, eva, "een.jpg", [(BOX, 1)])
    await aan(settings, eva)
    async with maker(settings)() as session:
        await scan.plan_nodes(session, eva)
        await session.commit()
    picked = await scan._pick(settings)
    assert [i.node for i in picked] == [node]
    assert (await _scans(settings))[node][0] == "busy"
    await register.on_start(settings)
    assert (await _scans(settings))[node][:3] == ("wait", None, 0)
    await draai(settings)
    assert (await _scans(settings))[node][0] == "done"


async def test_uit_tijdens_een_job_schrijft_niets(settings, nep, fake):
    eva = await _eva(settings)
    node = await foto(settings, nep, eva, "een.jpg", [(BOX, 1)])
    await aan(settings, eva)
    async with maker(settings)() as session:
        await scan.plan_nodes(session, eva)
        await session.commit()

    async def en_dan_uit(req):
        async with maker(settings)() as session:
            row = await session.get(PhotoPref, eva)
            row.faces = "uit"
            await session.commit()
        return await nep.faces(req)

    fake.on(wire.Op.FACES, en_dan_uit)
    await scan.scan_job(settings, {})
    assert await _faces(settings) == []
    assert (await _scans(settings))[node][0] == "wait"
    async with maker(settings)() as session:
        assert await session.scalar(select(func.count()).select_from(Face)) == 0


async def test_een_fout_van_de_worker_logt_alleen_een_code(settings, nep, fake, caplog):
    eva = await _eva(settings)
    await foto(settings, nep, eva, "een.jpg", [(BOX, 1)])

    async def kapot(req):
        raise FakeFail(wire.Code.INTERNAL, "geheimwoord7")

    fake.on(wire.Op.FACES, kapot)
    await aan(settings, eva)
    with caplog.at_level("DEBUG"):
        await draai(settings)
    assert "geheimwoord7" not in caplog.text
