"""Gezichten (X2b) en zoeken op beeld (X3b) samen in de rij analyse en het kind werk (de
samenvoeging van golf 6).

Allebei werken in de rij analyse: faces.scan en faces.group van gezichten, de pomp van de analyse
(analysis.analyse) met de stap embed van beeld. Hun opdrachten (faces, group, embed_image) gaan
naar hetzelfde kind werk van de container. Hier één nep-worker met de nep van gezichten en die van
beeld, één eigenaar met allebei aan, en de jobs één voor één zoals de Runner van de rij ze pakt
(jobs.claim: de vroegste run_after eerst):

- om de beurt: zolang allebei werk hebben, volgt op een batch van gezichten een batch van beeld en
  omgekeerd; de groepsronde (suggest na elke batch, full aan het eind) komt tussen de batches van
  embed_image, en een full die klaarstaat gaat vóór de volgende batch van beeld;
- werk bezig (busy): elke job wijkt (jobs.Defer, DEFER_BUSY) en staat weer in de rij zonder dat
  de poging telt, zonder fout en zonder failed; de foto's staan weer op wait zonder poging; als
  werk vrij is, komt alles klaar.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import func, select

from app import analysis, jobs
from app.analyse import monitor
from app.analyse.wire import Child, Code, Op
from app.analysis import Step
from app.beeld import cache as bcache
from app.beeld import jobs as bjobs
from app.beeld import limits as blimits
from app.models import Analysis, BlobEmbed, Face, FaceScan, Job, Node, PhotoPref
from app.people.faces import group, scan
from tests.beeld import nep as beeld_nep
from tests.factories import create_user, maker
from tests.people.faces.conftest import aan, foto

pytestmark = pytest.mark.anyio

BOX = (0.1, 0.1, 0.2, 0.25)
LANE = "analyse"
PUMP = analysis.pump_type(LANE)
FOTOS = 12


async def _fotos(s, nep, eva, namen) -> None:
    """Foto's van Eva met één gezicht, een 1024 (gezichten) en een 256 (beeld)."""
    for naam in namen:
        node = await foto(s, nep, eva, naam, [(BOX, 1)])
        async with maker(s)() as session:
            sha = (await session.get(Node, node)).blob_id
        beeld_nep.preview(s, sha, beeld_nep.KLEUREN["rood"])


async def _plannen(s, eva) -> None:
    """faces.plan (Eva) en de backfill van de analyse (embed), zoals na aanzetten of een upload;
    daarna de rijen default en index leeg."""
    await aan(s, eva)
    await beeld_nep.backfill(s)
    await jobs.run_pending(s, lane="default")
    await jobs.run_pending(s, lane="index")


@pytest.fixture
async def samen(settings, nep, fake):
    """(settings, eva): de nep van beeld naast die van gezichten op dezelfde worker, beeld
    aangemeld, en Eva met twaalf foto's en gezichten en beeld aan; de backfill van de index is
    klaar en faces.plan heeft gedraaid. Batches van beeld van 2 (gezichten: PER_OWNER, 4), zodat
    beeld na gezichten nog werk heeft."""
    beeld_nep.Nep(fake).install()
    s = settings.model_copy(update={"beeld_batch": 2})
    monitor._status = await monitor.check(s)
    assert monitor._status.state == "ok" and bjobs.ready(), monitor._status
    assert bjobs.enable(s)
    try:
        async with maker(s)() as session:
            eva = (await create_user(session, "eva@example.nl")).id
            session.add(PhotoPref(user_id=eva, beeld="aan"))
            await session.commit()
        await _fotos(s, nep, eva, [f"foto-{i:02d}.jpg" for i in range(FOTOS)])
        await _plannen(s, eva)
        yield s, eva
    finally:
        bjobs.disable()
        bcache.forget()
        blimits.reset()


async def _rij(s) -> list[tuple[str, str]]:
    async with maker(s)() as session:
        rows = await session.execute(
            select(Job.type, Job.status).where(Job.type.in_((scan.SCAN, group.GROUP, PUMP)))
        )
        return sorted((r.type, r.status) for r in rows)


async def _stap(s, at=None) -> str | None:
    """Eén job uit de rij analyse, zoals de Runner hem pakt; geeft zijn soort."""
    job = await jobs.claim(s, at, LANE)
    if job is None:
        return None
    await jobs.execute(s, job)
    return job.type


async def _alles(s, at=None) -> list[str]:
    soorten: list[str] = []
    for _ in range(60):
        soort = await _stap(s, at)
        if soort is None:
            return soorten
        soorten.append(soort)
    raise AssertionError(f"de rij analyse wordt niet leeg: {soorten}")


async def _klaar(s) -> tuple[int, int, int]:
    """(foto's met gezichten gescand, inhouden met een vector, gezichten)."""
    async with maker(s)() as session:
        scanned = await session.scalar(
            select(func.count()).select_from(FaceScan).where(FaceScan.state == "done")
        )
        embedded = await session.scalar(select(func.count()).select_from(BlobEmbed))
        faces = await session.scalar(select(func.count()).select_from(Face))
    return int(scanned or 0), int(embedded or 0), int(faces or 0)


async def _fouten(s) -> list:
    async with maker(s)() as session:
        return list(await session.execute(select(Job.type, Job.status, Job.error).where(
            Job.error.is_not(None) | (Job.status == "failed"))))  # fmt: skip


def _om_de_beurt(reeks: list, a, b) -> None:
    """Van het begin tot de laatste a: a en b wisselen elkaar af (nooit twee keer dezelfde)."""
    alleen = [x for x in reeks if x in (a, b)]
    tot = len(alleen) - 1 - alleen[::-1].index(a)
    for i in range(tot):
        assert alleen[i] != alleen[i + 1], (i, reeks)


async def test_om_de_beurt_en_niemand_verhongert(samen, fake):
    s, _eva = samen
    assert await _rij(s) == [(PUMP, "queued"), (scan.SCAN, "queued")]
    fake.requests.clear()
    soorten = await _alles(s)
    # Drie batches van gezichten (4 per eigenaar) en zes van beeld (2): om de beurt zolang
    # allebei werk hebben; de full van gezichten (klaargezet bij de laatste batch, de rij van Eva
    # leeg) wacht hooguit op de ene batch van beeld die al klaarstond
    assert soorten.count(scan.SCAN) == 3 and soorten.count(PUMP) == 6, soorten
    assert soorten.count(group.GROUP) == 1, soorten
    _om_de_beurt(soorten, scan.SCAN, PUMP)
    laatste_scan = len(soorten) - 1 - soorten[::-1].index(scan.SCAN)
    assert soorten[laatste_scan + 1 : soorten.index(group.GROUP)] in ([], [PUMP]), soorten
    assert soorten[-1] == PUMP, soorten  # beeld had nog werk: de full hield hem niet tegen
    # In het kind werk hetzelfde: faces en embed_image om de beurt, na elke faces meteen group
    # (de suggest van die batch, vóór de volgende embed_image), en de full van group (de vierde)
    # vóór de laatste embed_image
    werk = [r for r in fake.requests if r.op in (Op.FACES, Op.GROUP, Op.EMBED_IMAGE)]
    assert {r.code for r in werk} == {Code.OK}, werk
    ops = [r.op for r in werk]
    assert ops.count(Op.FACES) == 3 and ops.count(Op.EMBED_IMAGE) == 6, ops
    assert ops.count(Op.GROUP) == 4, ops
    _om_de_beurt(ops, Op.FACES, Op.EMBED_IMAGE)
    for i, op in enumerate(ops):
        if op is Op.FACES:
            assert ops[i + 1] is Op.GROUP, ops
    full = len(ops) - 1 - ops[::-1].index(Op.GROUP)
    assert Op.EMBED_IMAGE in ops[full + 1 :], ops
    # Alles klaar, zonder een job met een fout
    assert await _klaar(s) == (FOTOS, FOTOS, FOTOS)
    assert await _fouten(s) == []


async def test_werk_bezig_wijkt_zonder_fout(samen, fake, nep):
    """Met werk bezig wijkt elke job van de rij analyse (scan, group en de pomp van beeld):
    Defer(DEFER_BUSY), terug in de rij zonder poging en zonder fout; daarna komt alles klaar."""
    s, eva = samen
    assert await _alles(s)
    assert await _klaar(s) == (FOTOS, FOTOS, FOTOS)
    # Vier nieuwe foto's (nieuw werk voor scan en de pomp) en een full van group, die nu
    # gezichten heeft om te groeperen
    await _fotos(s, nep, eva, [f"nieuw-{i}.jpg" for i in range(4)])
    await _plannen(s, eva)
    async with maker(s)() as session:
        await group.queue_group(session, eva, "full")
        await session.commit()
    rij = await _rij(s)
    assert [r for r in rij if r[1] == "queued"] == [
        (PUMP, "queued"), (group.GROUP, "queued"), (scan.SCAN, "queued")
    ]  # fmt: skip
    fake.requests.clear()
    fake.busy.add(Child.WERK)
    voor = jobs.now()
    # Elke job één keer: allemaal busy, allemaal Defer(DEFER_BUSY)
    assert sorted(await _alles(s)) == sorted([PUMP, group.GROUP, scan.SCAN])
    gezien = sorted((r.op.value, r.code.value) for r in fake.requests if r.op is not Op.CAPS)
    assert gezien == sorted(
        [(Op.FACES.value, "busy"), (Op.GROUP.value, "busy"), (Op.EMBED_IMAGE.value, "busy")]
    )
    async with maker(s)() as session:
        rows = list(
            await session.execute(
                select(Job.type, Job.attempts, Job.error, Job.run_after).where(
                    Job.type.in_((scan.SCAN, group.GROUP, PUMP)), Job.status == "queued"
                )
            )
        )
        scans = list(await session.execute(
            select(FaceScan.state, FaceScan.tries).where(FaceScan.state != "done")))  # fmt: skip
        embeds = list(
            await session.execute(
                select(Analysis.state, Analysis.tries).where(
                    Analysis.step == Step.EMBED.value, Analysis.state != "done"
                )
            )
        )
    assert sorted(r.type for r in rows) == sorted([PUMP, group.GROUP, scan.SCAN])
    for row in rows:
        assert (row.attempts, row.error) == (0, None), row
        after = row.run_after if row.run_after.tzinfo else row.run_after.replace(tzinfo=voor.tzinfo)
        wacht = (after - voor).total_seconds()
        assert monitor.DEFER_BUSY - 5 <= wacht <= monitor.DEFER_BUSY + 5, (row.type, wacht)
    assert scans == [("wait", 0)] * 4
    assert embeds == [("wait", 0)] * 4
    assert await _klaar(s) == (FOTOS, FOTOS, FOTOS)
    assert await _fouten(s) == []
    # Werk weer vrij: na de wachttijd komt alles klaar, zonder fout
    fake.busy.discard(Child.WERK)
    vrij = len(fake.requests)
    assert await _alles(s, jobs.now() + timedelta(seconds=monitor.DEFER_BUSY + 10))
    assert await _klaar(s) == (FOTOS + 4, FOTOS + 4, FOTOS + 4)
    assert await _fouten(s) == []
    na = [(r.op, r.code) for r in fake.requests[vrij:] if r.op is not Op.CAPS]
    assert {op for op, _c in na} == {Op.FACES, Op.GROUP, Op.EMBED_IMAGE}, na
    assert {c for _op, c in na} == {Code.OK}, na
