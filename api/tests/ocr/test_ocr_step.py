"""De stap ocr aan de kant van de app (bouwontwerp fase 6, X1; Keuzes 20, 23, 24).

De keten is die van de app: de pomp van de rij ocr (analysis.pump), de stap in app/ocr/step.py,
de worker via de client (hier de nep van tests/media/fake_worker.py, met de opdracht text uit
tests/ocr/nep.py), en de triggers van 0010 die blob_text en photo_ocr in de index zetten.

- een PDF: eerst welke pagina's scans zijn (minder dan 20 tekens), dan alleen die door OCR; de
  woorden per pagina in ocr_page, de tekst in blob_text.ocr; al genoeg tekst: ocr_has_text;
  hooguit ocr_max_pages; na het budget geeft een PDF zijn beurt weg en gaat later verder; een
  pagina die mislukt, leest een nieuw verzoek opnieuw;
- een foto alleen op verzoek of met "Tekst in foto's herkennen" aan, en de tekst alleen bij de
  node die het wilde;
- zonder tesseract: none met ocr_missing, en terug op wait zodra caps OCR meldt (Keuze 24);
- /beheer: aan of uit, wat er wacht, en alles opnieuw.
"""

from __future__ import annotations

import uuid

import pytest
import sqlalchemy as sa

from app import analysis, jobs
from app.config import Settings
from app.media.wire import Op
from app.models import Job, OcrPage, PhotoOcr
from app.ocr import step as ocr_step
from app.ocr import store
from app.search import hooks
from app.search.types import AdminOcr
from app.security import download_token
from tests.media import fake_worker
from tests.ocr import nep
from tests.search.fixtures import pdf_scan
from tests.test_files import put, root

pytestmark = [pytest.mark.anyio, pytest.mark.usefixtures("stappen")]

WORD = "kassawoord"


async def upload(client, headers, name: str, data: bytes) -> dict:
    root_id = (await root(client, headers))["folder"]["id"]
    r = await put(client, headers, root_id, name, data)
    assert r.status_code == 201, r.text
    return r.json()


async def backfill(settings) -> None:
    """De backfill van de analyse: elke aangemelde stap een rij (wait of skip)."""
    await analysis.backfill_job(settings, {"after": ""})


async def jobs_of(settings, kind: str) -> list[dict]:
    async with nep.maker(settings)() as db:
        rows = await db.scalars(sa.select(Job.payload).where(Job.type == kind))
        return list(rows.all())


def test_ocr_staat_standaard_aan(tmp_path):
    """Jonas koos ja (Vraag 1 en 15): PLATFORM_OCR_ENABLED staat aan; de rij ocr draait alleen
    met media aan (jobs.active_lanes)."""
    s = Settings(data_dir=tmp_path, jwt_secret="t" * 32)
    assert s.ocr_enabled is True
    assert (s.ocr_max_pages, s.ocr_per_hour, s.ocr_pages_per_day) == (200, 20, 200)
    assert "ocr" not in jobs.active_lanes(s)
    assert "ocr" in jobs.active_lanes(s.model_copy(update={"media_enabled": True}))
    assert ocr_step.runs(s.model_copy(update={"media_enabled": True}))
    uit = s.model_copy(update={"media_enabled": True, "ocr_enabled": False})
    assert not ocr_step.runs(uit) and "ocr" not in jobs.active_lanes(uit)


async def test_alleen_de_gescande_pagina_s_van_een_pdf(client, admin, aan, tmp_path):
    headers, _ = admin
    data = pdf_scan(WORD)
    node = await upload(client, headers, "scan.pdf", data)
    sha = nep.sha(data)
    async with nep.worker(tmp_path) as w:
        # Pagina 1 heeft een tekstlaag, 2 en 3 zijn scans
        w.pdf(data, [2, 3], "Pagina met tekst")
        w.page(data, 2, "Kassabon", WORD)
        w.page(data, 3, "Totaal", "twaalf")
        await backfill(aan)
        assert await nep.row(aan, sha) == ("wait", None, 0)
        assert await nep.pump(aan) == 1
        assert [(c["kind"], c["page"]) for c in w.ocr_calls()] == [("pdf", 2), ("pdf", 3)]
        assert w.surveys == 1
    assert await nep.row(aan, sha) == ("done", None, 0)
    found = await nep.pages(aan, sha)
    assert sorted(found) == [2, 3]
    # De plek als deel van de pagina (0..1), zoals de viewer hem toont
    t, x, y, width, height = found[2][1]
    assert t == WORD
    assert 0 < x < 1 and 0 < y < 1 and 0 < width < 1 and 0 < height < 1
    assert await nep.pdf_ocr(aan, sha) == f"Kassabon {WORD}\nTotaal twaalf"
    # In de index (kolom ocr) en via /search met match ocr
    assert await nep.fts(aan, "ocr", WORD) == {node["id"]}
    hits = await nep.search(client, headers, WORD)
    assert hits[node["id"]]["match"] == "ocr"
    # De stand voor de API en de woorden met source ocr (de pagina zonder tekstlaag)
    r = await client.get(f"/api/v1/files/{node['id']}/analysis", headers=headers)
    assert r.json()["ocr"] == "done"
    me = (await client.get("/api/v1/auth/me", headers=headers)).json()["id"]
    token = download_token(aan, uuid.UUID(me), uuid.UUID(node["id"]))
    r = await client.get(f"/api/v1/files/{node['id']}/pages/2/words", params={"token": token})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["source"] == "ocr" and [w["t"] for w in body["words"]] == ["Kassabon", WORD]
    etag = r.headers["etag"]
    again = await client.get(
        f"/api/v1/files/{node['id']}/pages/2/words",
        params={"token": token},
        headers={"If-None-Match": etag},
    )
    assert again.status_code == 304


async def test_een_pdf_met_genoeg_tekst_krijgt_geen_ocr(client, admin, aan, tmp_path):
    """≥ 200.000 tekens tekst (de lezer kapte af): skip met ocr_has_text, en geen OCR (C11)."""
    headers, _ = admin
    data = pdf_scan("veeltekst")
    await upload(client, headers, "dik.pdf", data)
    async with nep.worker(tmp_path) as w:
        w.pdf(data, [7], "veel tekst " * 10, truncated=True)
        await backfill(aan)
        assert await nep.pump(aan) == 1
        assert w.ocr_calls() == []
    assert await nep.row(aan, nep.sha(data)) == ("skip", "ocr_has_text", 0)
    assert await nep.pages(aan, nep.sha(data)) == {}


async def test_hooguit_ocr_max_pages_per_document(client, admin, aan, tmp_path):
    headers, _ = admin
    data = pdf_scan("lang")
    await upload(client, headers, "lang.pdf", data)
    s = aan.model_copy(update={"ocr_max_pages": 2})
    async with nep.worker(tmp_path) as w:
        w.pdf(data, [1, 2, 3, 4])
        for n in (1, 2, 3, 4):
            w.page(data, n, f"pagina{n}")
        await backfill(s)
        assert await nep.pump(s) == 1
        assert [c["page"] for c in w.ocr_calls()] == [1, 2]
    assert await nep.row(s, nep.sha(data)) == ("done", "pages_limit", 0)
    assert await nep.pdf_ocr(s, nep.sha(data)) == "pagina1\npagina2"


async def test_na_het_budget_geeft_een_pdf_zijn_beurt_weg(
    client, admin, aan, tmp_path, monkeypatch
):
    """Na RUN_BUDGET_S (hier 0: na elke pagina) terug op wait zonder poging; de volgende beurt
    gaat verder waar hij was, en leest geen pagina twee keer."""
    headers, _ = admin
    data = pdf_scan("budget")
    sha = nep.sha(data)
    await upload(client, headers, "budget.pdf", data)
    monkeypatch.setattr(ocr_step, "RUN_BUDGET_S", 0.0)
    async with nep.worker(tmp_path) as w:
        w.pdf(data, [1, 2, 3])
        for n in (1, 2, 3):
            w.page(data, n, f"stuk{n}")
        await backfill(aan)
        assert await nep.pump(aan) == 1
        assert await nep.row(aan, sha) == ("wait", None, 0)
        assert sorted(await nep.pages(aan, sha)) == [1]
        assert await nep.pdf_ocr(aan, sha) is None  # de tekst pas als alles klaar is
        assert await nep.pump(aan) == 1
        assert sorted(await nep.pages(aan, sha)) == [1, 2]
        assert await nep.pump(aan) == 1
        assert [c["page"] for c in w.ocr_calls()] == [1, 2, 3]
        assert w.surveys == 1  # welke pagina's scans zijn, onthoudt de stap
    assert await nep.row(aan, sha) == ("done", None, 0)
    assert await nep.pdf_ocr(aan, sha) == "stuk1\nstuk2\nstuk3"


async def test_een_mislukte_pagina_leest_een_nieuw_verzoek_opnieuw(client, admin, aan, tmp_path):
    headers, _ = admin
    data = pdf_scan("traag")
    sha = nep.sha(data)
    node = await upload(client, headers, "traag.pdf", data)
    slow = {"pages": {2}}

    async with nep.worker(tmp_path) as w:
        fake = w.fake

        async def ocr(job: fake_worker.Job) -> dict:
            if job.args.page in slow["pages"]:
                return fake_worker.real_ocr._summary("failed", "too_slow")
            return await fake._ocr_page(job)

        fake.on(Op.OCR, ocr)
        w.pdf(data, [1, 2])
        w.page(data, 1, "eerste")
        w.page(data, 2, "tweede")
        await backfill(aan)
        assert await nep.pump(aan) == 1
        assert await nep.row(aan, sha) == ("done", "too_slow", 0)
        found = await nep.pages(aan, sha)
        assert [w[0] for w in found[1]] == ["eerste"] and found[2] == []
        assert await nep.pdf_ocr(aan, sha) == "eerste"
        # Een nieuw verzoek: pagina 2 opnieuw, pagina 1 niet
        slow["pages"] = set()
        r = await client.post(f"/api/v1/files/{node['id']}/ocr", headers=headers)
        assert r.status_code == 202, r.text
        assert await nep.pump(aan) == 1
        assert [c["page"] for c in w.ocr_calls()] == [1, 2, 2]
    assert await nep.row(aan, sha) == ("done", None, 0)
    assert await nep.pdf_ocr(aan, sha) == "eerste\ntweede"


async def test_zonder_tesseract_ocr_missing_en_terug_als_caps_het_meldt(
    client, admin, aan, tmp_path
):
    headers, _ = admin
    data = pdf_scan("zonder")
    sha = nep.sha(data)
    node = await upload(client, headers, "zonder.pdf", data)
    async with nep.worker(tmp_path, ocr=False) as w:
        w.pdf(data, [1])
        w.page(data, 1, "later")
        await backfill(aan)
        assert await nep.pump(aan) == 1
        assert await nep.row(aan, sha) == ("none", "ocr_missing", 0)
        assert w.ocr_calls() == [] and w.surveys == 0
        assert ocr_step.installed() is False
        # De job ocr.recheck kijkt later opnieuw; POST zegt intussen ocr_off
        assert await jobs_of(aan, ocr_step.RECHECK) == [{}]
        r = await client.post(f"/api/v1/files/{node['id']}/ocr", headers=headers)
        assert r.status_code == 503 and r.json()["detail"]["code"] == "ocr_off"
        r = await client.get(f"/api/v1/files/{node['id']}/analysis", headers=headers)
        assert r.json()["ocr"] == "none" and r.json()["code"] == "ocr_missing"
        # Nog steeds geen tesseract: de job zet zichzelf weer klaar
        await ocr_step.recheck_job(aan, {})
        assert await nep.row(aan, sha) == ("none", "ocr_missing", 0)
        # Een nieuw image: caps meldt OCR, de rijen gaan terug op wait en de pomp leest ze
        w.fake.ocr = True
        await ocr_step.recheck_job(aan, {})
        assert await nep.row(aan, sha) == ("wait", None, 0)
        assert ocr_step.installed() is True
        assert await nep.pump(aan) == 1
    assert await nep.row(aan, sha) == ("done", None, 0)
    assert await nep.pdf_ocr(aan, sha) == "later"


async def test_zonder_tesseract_zegt_ook_een_foto_ocr_missing(client, mensen, aan, tmp_path):
    """Review golf 4, contract F2: zonder tesseract zegt een foto ook none met ocr_missing (de web
    toont dan "niet geïnstalleerd" zonder knop, net als bij een PDF), niet skip met een knop die
    503 ocr_off geeft. Zolang de app het niet weet (caps nog niet gevraagd): skip. Tekst die er
    al is, blijft done."""
    (eva, _), _bram = mensen
    data = nep.photo("zonderfoto")
    node = await upload(client, eva, "bon.jpg", data)
    other = await upload(client, eva, "kassa.jpg", nep.photo("kassa"))

    async def ocr_of(n: dict) -> tuple[str, str | None]:
        r = await client.get(f"/api/v1/files/{n['id']}/analysis", headers=eva)
        assert r.status_code == 200, r.text
        return r.json()["ocr"], r.json()["code"]

    assert ocr_step.installed() is None
    assert await ocr_of(node) == ("skip", None)
    async with nep.worker(tmp_path) as w:
        w.page(data, 1, "Bakkerij", WORD)
        await backfill(aan)
        r = await client.post(f"/api/v1/files/{node['id']}/ocr", headers=eva)
        assert r.status_code == 202, r.text
        assert await nep.pump(aan) == 1
    assert await ocr_of(node) == ("done", None)
    assert await ocr_of(other) == ("skip", None)
    ocr_step._set_installed(False)
    assert await ocr_of(other) == ("none", "ocr_missing")
    assert await ocr_of(node) == ("done", None)  # de tekst die er is, blijft
    r = await client.post(f"/api/v1/files/{other['id']}/ocr", headers=eva)
    assert r.status_code == 503 and r.json()["detail"]["code"] == "ocr_off"
    ocr_step._set_installed(True)
    assert await ocr_of(other) == ("skip", None)


async def test_een_foto_alleen_op_verzoek(client, mensen, aan, tmp_path):
    """Zonder verzoek en zonder "Tekst in foto's herkennen" leest de rij geen foto (skip). Na
    "Tekst herkennen" (POST): de tekst in photo_ocr bij die node, in de index (pocr), en via
    /search met match ocr."""
    (eva, _), _bram = mensen
    data = nep.photo("bon")
    sha = nep.sha(data)
    node = await upload(client, eva, "bon.jpg", data)
    async with nep.worker(tmp_path) as w:
        w.page(data, 1, "Bakkerij", WORD)
        await backfill(aan)
        assert await nep.row(aan, sha) == ("skip", None, 0)
        assert await nep.pump(aan) == 0
        r = await client.get(f"/api/v1/files/{node['id']}/analysis", headers=eva)
        assert r.json()["ocr"] == "skip"
        r = await client.post(f"/api/v1/files/{node['id']}/ocr", headers=eva)
        assert r.status_code == 202, r.text
        assert await nep.row(aan, sha) == ("wait", None, 0)
        # Het verzoek staat bij de node (words leeg), de stand is queued
        assert await nep.photo_text(aan, node["id"]) == ("", store.PENDING)
        r = await client.get(f"/api/v1/files/{node['id']}/analysis", headers=eva)
        assert r.json()["ocr"] == "queued"
        assert await nep.pump(aan) == 1
        assert [(c["kind"], c["page"]) for c in w.ocr_calls()] == [("image", 1)]
    assert await nep.row(aan, sha) == ("done", None, 0)
    text, words = await nep.photo_text(aan, node["id"])
    assert text == f"Bakkerij {WORD}"
    assert [w[0] for w in store.unpack(words)] == ["Bakkerij", WORD]
    # Niet in blob_text: de tekst van een foto is van de node, niet van de inhoud
    assert await nep.pdf_ocr(aan, sha) is None
    assert await nep.fts(aan, "pocr", WORD) == {node["id"]}
    assert await nep.fts(aan, "ocr", WORD) == set()
    assert (await nep.search(client, eva, WORD))[node["id"]]["match"] == "ocr"
    r = await client.get(f"/api/v1/files/{node['id']}/analysis", headers=eva)
    assert r.json()["ocr"] == "done"


async def test_tekst_in_foto_s_herkennen(client, mensen, aan, tmp_path):
    """photo_pref.ocr_photos aan: de job ocr.photos zet de foto's van die eigenaar klaar, en een
    nieuwe foto ook. Uit: de rij leest geen foto meer die niemand vroeg; de tekst die er is,
    blijft bij de eigen node."""
    (eva, eva_id), (bram, _) = mensen
    oud = nep.photo("oud")
    eerst = await upload(client, eva, "oud.jpg", oud)
    van_bram = await upload(client, bram, "bram.jpg", nep.photo("van bram"))
    async with nep.worker(tmp_path) as w:
        w.page(oud, 1, "oudewoord")
        await backfill(aan)
        r = await client.patch("/api/v1/me/photo-settings", json={"ocr_photos": True}, headers=eva)
        assert r.status_code == 200, r.text
        assert await jobs_of(aan, ocr_step.PHOTOS) == [{"user": eva_id, "after": ""}]
        await ocr_step.photos_job(aan, {"user": eva_id, "after": ""})
        assert await nep.row(aan, nep.sha(oud)) == ("wait", None, 0)
        assert await nep.pump(aan) == 1
        assert (await nep.photo_text(aan, eerst["id"]))[0] == "oudewoord"
        # Bram heeft het niet aan: zijn foto blijft skip
        assert await nep.row(aan, nep.sha(nep.photo("van bram"))) == ("skip", None, 0)
        assert await nep.photo_text(aan, van_bram["id"]) is None
        # Een nieuwe foto van Eva (files.add_ref → search.hooks.blob_added → _new_photo)
        nieuw = nep.photo("nieuw")
        tweede = await upload(client, eva, "nieuw.jpg", nieuw)
        w.page(nieuw, 1, "nieuwewoord")
        await ocr_step._new_photo(aan, nep.sha(nieuw), uuid.UUID(eva_id))
        assert await nep.row(aan, nep.sha(nieuw)) == ("wait", None, 0)
        # Uit, terwijl hij nog wacht: de rij leest hem niet meer (skip), de oude tekst blijft
        r = await client.patch("/api/v1/me/photo-settings", json={"ocr_photos": False}, headers=eva)
        assert r.status_code == 200, r.text
        assert await nep.pump(aan) == 1
        assert await nep.row(aan, nep.sha(nieuw)) == ("skip", None, 0)
        assert await nep.photo_text(aan, tweede["id"]) is None
        assert (await nep.photo_text(aan, eerst["id"]))[0] == "oudewoord"
        assert len(w.ocr_calls()) == 1
        # Uit: een nieuwe foto gaat niet meer in de rij
        derde = nep.photo("derde")
        await upload(client, eva, "derde.jpg", derde)
        await ocr_step._new_photo(aan, nep.sha(derde), uuid.UUID(eva_id))
        assert await nep.row(aan, nep.sha(derde)) is None


async def test_een_kopie_door_dezelfde_eigenaar_houdt_de_tekst(client, mensen, aan, tmp_path):
    (eva, _), _bram = mensen
    data = nep.photo("kopie")
    node = await upload(client, eva, "bon.jpg", data)
    async with nep.worker(tmp_path) as w:
        w.page(data, 1, WORD)
        await backfill(aan)
        await client.post(f"/api/v1/files/{node['id']}/ocr", headers=eva)
        assert await nep.pump(aan) == 1
    eva_root = (await root(client, eva))["folder"]["id"]
    r = await client.post(
        f"/api/v1/files/{node['id']}/copy", json={"parent_id": eva_root}, headers=eva
    )
    assert r.status_code == 201, r.text
    copy = r.json()
    assert await nep.photo_text(aan, copy["id"]) == await nep.photo_text(aan, node["id"])
    assert await nep.fts(aan, "pocr", WORD) == {node["id"], copy["id"]}


async def test_uit_op_beheer_en_alles_opnieuw(client, admin, aan, tmp_path, monkeypatch):
    """Het deel ocr van /admin/search (Z7 toont het): installed, on, pages_queued; uit laat de
    pomp wijken (de rijen blijven wait) en POST zegt ocr_off; rebuild leest alles opnieuw."""
    headers, _ = admin
    data = pdf_scan("beheer")
    sha = nep.sha(data)
    node = await upload(client, headers, "beheer.pdf", data)
    async with nep.worker(tmp_path) as w:
        w.pdf(data, [1])
        w.page(data, 1, "beheerwoord")
        await backfill(aan)
        await ocr_step.on_start(aan)
        async with nep.maker(aan)() as db:
            assert await ocr_step.admin_block(db) == AdminOcr(
                installed=True, on=True, pages_queued=1
            )
            await ocr_step.admin_switch(db, False)
            await db.commit()
        with pytest.raises(jobs.Defer):
            await nep.pump(aan)
        assert await nep.row(aan, sha) == ("wait", None, 0)
        r = await client.post(f"/api/v1/files/{node['id']}/ocr", headers=headers)
        assert r.status_code == 503 and r.json()["detail"]["code"] == "ocr_off"
        async with nep.maker(aan)() as db:
            assert (await ocr_step.admin_block(db)).on is False
            await ocr_step.admin_switch(db, True)
            await db.commit()
        # Aan wekt de pomp
        assert await jobs_of(aan, analysis.pump_type("ocr"))
        assert await nep.pump(aan) == 1
        assert await nep.row(aan, sha) == ("done", None, 0)
        async with nep.maker(aan)() as db:
            assert await ocr_step.admin_block(db) == AdminOcr(
                installed=True, on=True, pages_queued=0
            )
            await ocr_step.admin_rebuild(db)
            await db.commit()
            assert await db.scalar(sa.select(sa.func.count()).select_from(OcrPage)) == 0
        assert await nep.row(aan, sha) == ("wait", None, 0)
        # De tekst blijft tot de nieuwe er is
        assert await nep.pdf_ocr(aan, sha) == "beheerwoord"
        assert await nep.pump(aan) == 1
        assert [c["page"] for c in w.ocr_calls()] == [1, 1]
    # Z7 meldt het deel ocr aan in search.hooks (ADMIN_PARTS); dan doet register() het ook
    monkeypatch.setitem(hooks.ADMIN_PARTS, "ocr", "X1")
    ocr_step._register_admin()
    assert hooks._admin["ocr"].block is ocr_step.admin_block
    monkeypatch.delitem(hooks._admin, "ocr")


async def test_een_foto_van_een_ander_krijgt_niets_van_de_pomp(client, mensen, aan, tmp_path):
    """Dezelfde inhoud bij Eva en Bram (elk een eigen upload): Eva vraagt OCR; alleen haar node
    krijgt de tekst (photo_targets), Brams node niets."""
    (eva, _), (bram, _) = mensen
    data = nep.photo("samen")
    van_eva = await upload(client, eva, "samen.jpg", data)
    van_bram = await upload(client, bram, "samen.jpg", data)
    async with nep.worker(tmp_path) as w:
        w.page(data, 1, WORD)
        await backfill(aan)
        await client.post(f"/api/v1/files/{van_eva['id']}/ocr", headers=eva)
        assert await nep.pump(aan) == 1
    assert (await nep.photo_text(aan, van_eva["id"]))[0] == WORD
    assert await nep.photo_text(aan, van_bram["id"]) is None
    async with nep.maker(aan)() as db:
        owners = (await db.scalars(sa.select(PhotoOcr.owner_id))).all()
    assert len(owners) == 1
    assert await nep.fts(aan, "pocr", WORD) == {van_eva["id"]}
    assert await nep.search(client, bram, WORD) == {}
    r = await client.get(f"/api/v1/files/{van_bram['id']}/analysis", headers=bram)
    assert r.json()["ocr"] == "skip"


async def test_zonder_media_wacht_er_niets(client, admin, settings):
    """Draait de rij ocr niet (media uit), dan zegt de stand skip in plaats van queued: er komt
    niets. POST zegt ocr_off."""
    headers, _ = admin
    data = pdf_scan("geenmedia")
    node = await upload(client, headers, "scan.pdf", data)
    await backfill(settings)
    assert await nep.row(settings, nep.sha(data)) == ("wait", None, 0)
    r = await client.get(f"/api/v1/files/{node['id']}/analysis", headers=headers)
    assert r.json()["ocr"] == "skip"
    r = await client.post(f"/api/v1/files/{node['id']}/ocr", headers=headers)
    assert r.status_code == 503 and r.json()["detail"]["code"] == "ocr_off"
