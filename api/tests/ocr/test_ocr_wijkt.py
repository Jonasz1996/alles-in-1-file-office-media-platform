"""De pomp van ocr wijkt, ook midden in een PDF (review golf 4 van fase 6, uitrol F1 en F3).

- **De index gaat vóór** (F1): Op.TEXT (de stap text, de index) en Op.OCR delen de ene plaats
  background van de worker (Keuze 12, Keuze 22). Leest OCR een PDF van veel pagina's, dan krijgt
  de index de plaats na de pagina die OCR nu leest; niet pas als OCR klaar is, of met busy
  WORKER_DEFER_S later opnieuw (een grote achterstand van OCR hield de index zo uren op).
- **Pauze, wijken en uit tussen twee pagina's** (F3): de pomp keek alleen bij het begin van een
  batch; een PDF van 200 pagina's las daarna door tot RUN_BUDGET_S. Nu kijkt hij na elke
  pagina: de rij op pauze (/beheer), de app die wijkt voor omzettingen of OnlyOffice
  (analysis.busy_elsewhere), of OCR uit op /beheer. Dan gaat de PDF terug op wait (zonder
  poging) en leest de volgende beurt verder waar hij was.

De keten is die van de app (analysis.pump, app/ocr/step.py, app/extract/step.py, de client) met
de nep-worker (tests/media/fake_worker.py, die de plaatsen telt zoals de echte).
"""

from __future__ import annotations

import asyncio

import pytest
import sqlalchemy as sa

from app import analysis, jobs
from app.analysis import Step
from app.extract import step as extract_step
from app.media.wire import Op
from app.models import Analysis
from app.ocr import step as ocr_step
from app.ocr import store
from tests.ocr import nep
from tests.ocr.test_ocr_step import backfill, upload
from tests.search.fixtures import pdf_scan

pytestmark = [pytest.mark.anyio, pytest.mark.usefixtures("stappen")]

PAGE_S = 1.0  # zo lang leest de nep-worker één pagina (slow_start, met de plaats bezet)
PAGES = (1, 2, 3, 4, 5, 6)


async def _until(check, limit: float = 10.0) -> None:
    end = asyncio.get_running_loop().time() + limit
    while not check():
        assert asyncio.get_running_loop().time() < end, "het wachten duurde te lang"
        await asyncio.sleep(0.01)


async def _text_state(settings, blob_sha: str) -> str | None:
    async with nep.maker(settings)() as db:
        return await db.scalar(
            sa.select(Analysis.state).where(
                Analysis.blob_sha == blob_sha, Analysis.step == Step.TEXT.value
            )
        )


async def test_de_index_wacht_hooguit_een_pagina_op_ocr(client, admin, aan, tmp_path):
    headers, _ = admin
    scan = pdf_scan("veelpaginas")
    scan_sha = nep.sha(scan)
    await upload(client, headers, "dik.pdf", scan)
    note = b"Een briefje voor de index.\n"
    await upload(client, headers, "briefje.txt", note)
    async with nep.worker(tmp_path) as w:
        w.pdf(scan, list(PAGES))
        for n in PAGES:
            w.page(scan, n, f"blad{n}")
        w.pdf(note, [], "Een briefje voor de index.")
        await backfill(aan)
        assert await _text_state(aan, nep.sha(note)) == "wait"
        w.fake.fault("slow_start", op=Op.OCR, times=len(PAGES), seconds=PAGE_S)
        loop = asyncio.get_running_loop()
        ocr = asyncio.create_task(nep.pump(aan))
        # OCR leest nu pagina 1 (de plaats background is bezet)
        await _until(lambda: len(w.ocr_calls()) == 1)
        start = loop.time()
        assert await analysis.pump(aan, "index") >= 1
        took = loop.time() - start
        read_then = len(w.ocr_calls())
        assert await _text_state(aan, nep.sha(note)) == "done"
        # Hooguit de pagina die OCR al las; OCR begon geen nieuwe zolang de index wachtte
        assert read_then == 1, read_then
        assert took < 3 * PAGE_S, took
        # OCR gaf zijn beurt weg na die pagina, en gaat later verder waar hij was
        assert await ocr == 1
        assert sorted(await nep.pages(aan, scan_sha)) == [1]
        assert await nep.row(aan, scan_sha) == ("wait", None, 0)
        w.fake.clear_faults()
        assert await nep.pump(aan) == 1
        assert [c["page"] for c in w.ocr_calls()] == list(PAGES)
    assert await nep.row(aan, scan_sha) == ("done", None, 0)
    assert await nep.pdf_ocr(aan, scan_sha) == "\n".join(f"blad{n}" for n in PAGES)


async def test_zonder_index_leest_ocr_door(client, admin, aan, tmp_path):
    """De tegenproef: zonder werk voor de index leest één beurt de hele PDF (binnen het budget)."""
    headers, _ = admin
    scan = pdf_scan("doorlezen")
    scan_sha = nep.sha(scan)
    await upload(client, headers, "door.pdf", scan)
    async with nep.worker(tmp_path) as w:
        w.pdf(scan, [1, 2, 3])
        for n in (1, 2, 3):
            w.page(scan, n, f"stuk{n}")
        await backfill(aan)
        assert await nep.pump(aan) == 1
        assert [c["page"] for c in w.ocr_calls()] == [1, 2, 3]
    assert await nep.row(aan, scan_sha) == ("done", None, 0)


async def test_een_index_die_loopt_houdt_ocr_tegen(client, admin, aan, tmp_path):
    """Heeft de index een batch met de worker, dan begint OCR niets (ook geen overzicht van de
    pagina's): jobs.Defer, de rij blijft wait, de worker kreeg geen enkele vraag van OCR."""
    headers, _ = admin
    scan = pdf_scan("tegen")
    await upload(client, headers, "tegen.pdf", scan)
    async with nep.worker(tmp_path) as w:
        w.pdf(scan, [1])
        w.page(scan, 1, "tegenwoord")
        await backfill(aan)
        with extract_step.batch.index_first(), pytest.raises(jobs.Defer):
            await nep.pump(aan)
        assert w.ocr_calls() == [] and w.surveys == 0
        assert await nep.row(aan, nep.sha(scan)) == ("wait", None, 0)
        assert await nep.pump(aan) == 1
    assert await nep.row(aan, nep.sha(scan)) == ("done", None, 0)


async def test_een_index_die_loopt_houdt_ook_een_foto_tegen(client, admin, aan, tmp_path):
    """Nacontrole golf 4: een foto heeft geen overzicht van pagina's; de wacht voor de index zit
    dan alleen in _ocr. Heeft de index een batch met de worker, dan vraagt OCR geen foto:
    jobs.Defer, de rij blijft wait, de worker kreeg geen vraag van OCR."""
    headers, _ = admin
    data = nep.photo("wachtfoto")
    sha = nep.sha(data)
    node = await upload(client, headers, "wacht.jpg", data)
    async with nep.worker(tmp_path) as w:
        w.page(data, 1, "fotowoord")
        await backfill(aan)
        r = await client.post(f"/api/v1/files/{node['id']}/ocr", headers=headers)
        assert r.status_code == 202, r.text
        assert await nep.row(aan, sha) == ("wait", None, 0)
        with extract_step.batch.index_first(), pytest.raises(jobs.Defer):
            await nep.pump(aan)
        assert w.ocr_calls() == []
        assert await nep.row(aan, sha) == ("wait", None, 0)
        assert await nep.pump(aan) == 1
        assert [(c["kind"], c["page"]) for c in w.ocr_calls()] == [("image", 1)]
    assert await nep.row(aan, sha) == ("done", None, 0)
    assert (await nep.photo_text(aan, node["id"]))[0] == "fotowoord"


async def test_de_index_wacht_ook_op_het_overzicht_van_ocr(client, admin, aan, tmp_path):
    """Nacontrole golf 4: ook het overzicht van de pagina's (Op.TEXT voor OCR, _survey) bezet de
    plaats background als opdracht van OCR (batch.ocr_call). De index die dan komt, wacht tot
    het overzicht klaar is, in plaats van busy en WORKER_DEFER_S later opnieuw; daarna begint OCR
    geen pagina zolang de index loopt."""
    headers, _ = admin
    scan = pdf_scan("overzicht")
    scan_sha = nep.sha(scan)
    await upload(client, headers, "overzicht.pdf", scan)
    note = b"Nog een briefje voor de index.\n"
    await upload(client, headers, "briefje2.txt", note)
    async with nep.worker(tmp_path) as w:
        w.pdf(scan, [1])
        w.page(scan, 1, "overzichtwoord")
        w.pdf(note, [], "Nog een briefje voor de index.")
        await backfill(aan)
        assert await _text_state(aan, nep.sha(note)) == "wait"
        # Het overzicht van OCR en daarna de batch van de index duren elk PAGE_S, met de plaats
        # background bezet
        w.fake.fault("slow_start", op=Op.TEXT, times=2, seconds=PAGE_S)
        loop = asyncio.get_running_loop()
        ocr = asyncio.create_task(nep.pump(aan))
        await _until(lambda: [r.op for r in w.fake.requests].count(Op.TEXT.value) == 1)
        start = loop.time()
        assert await analysis.pump(aan, "index") >= 1
        took = loop.time() - start
        assert await _text_state(aan, nep.sha(note)) == "done"
        assert took < 3 * PAGE_S, took
        # Na het overzicht vroeg OCR geen pagina: de index liep
        with pytest.raises(jobs.Defer):
            await ocr
        assert w.ocr_calls() == []
        assert await nep.row(aan, scan_sha) == ("wait", None, 0)
        w.fake.clear_faults()
        assert await nep.pump(aan) == 1
        assert [c["page"] for c in w.ocr_calls()] == [1]
    assert await nep.row(aan, scan_sha) == ("done", None, 0)
    assert await nep.pdf_ocr(aan, scan_sha) == "overzichtwoord"


@pytest.mark.parametrize("why", ["pauze", "elders", "uit"])
async def test_tussen_twee_pagina_s_wijkt_de_pomp(client, admin, aan, tmp_path, monkeypatch, why):
    headers, _ = admin
    scan = pdf_scan(f"wijk{why}")
    scan_sha = nep.sha(scan)
    await upload(client, headers, "wijk.pdf", scan)
    elders = False
    monkeypatch.setattr(analysis, "busy_elsewhere", lambda settings: elders)
    put_page = store.put_page

    async def after_page(db, *args, **kwargs):
        nonlocal elders
        await put_page(db, *args, **kwargs)
        # Na pagina 1: wat /beheer of de app nu doet
        if why == "pauze":
            analysis.PAUSED.add(ocr_step.LANE)
        elif why == "elders":
            elders = True
        else:
            await ocr_step.admin_switch(db, False)
            await db.commit()

    monkeypatch.setattr(store, "put_page", after_page)
    try:
        async with nep.worker(tmp_path) as w:
            w.pdf(scan, [1, 2, 3])
            for n in (1, 2, 3):
                w.page(scan, n, f"deel{n}")
            await backfill(aan)
            assert await nep.pump(aan) == 1
            # Alleen pagina 1: de rest wacht, zonder poging
            assert [c["page"] for c in w.ocr_calls()] == [1]
            assert sorted(await nep.pages(aan, scan_sha)) == [1]
            assert await nep.row(aan, scan_sha) == ("wait", None, 0)
            # Weer verder (geen pauze, niet wijken, aan): pagina 2 en 3, niet 1 opnieuw
            monkeypatch.setattr(store, "put_page", put_page)
            analysis.PAUSED.discard(ocr_step.LANE)
            elders = False
            async with nep.maker(aan)() as db:
                await ocr_step.admin_switch(db, True)
                await db.commit()
            assert await nep.pump(aan) == 1
            assert [c["page"] for c in w.ocr_calls()] == [1, 2, 3]
    finally:
        analysis.PAUSED.discard(ocr_step.LANE)
    assert await nep.row(aan, scan_sha) == ("done", None, 0)
    assert await nep.pdf_ocr(aan, scan_sha) == "deel1\ndeel2\ndeel3"
