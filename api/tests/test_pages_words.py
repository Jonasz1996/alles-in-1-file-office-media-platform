"""De woorden per pagina (bouwontwerp fase 6, Z2; Keuze 18, §3.4) en de stand van de analyse.

GET /files/{id}/pages/{n}/words?token= geeft de woorden met hun plek (0..1) uit dezelfde render
als de pagina, in de cache naast de pagina's, met de limiter van de viewer. Het token is dat van
GET /document (met de versie die je bekijkt); de toegang wordt bij elk verzoek opnieuw
gecontroleerd; een publieke link heeft geen woorden. GET /files/{id}/analysis geeft de stand
van text, hash en ocr voor wie het bestand mag lezen.
"""

from __future__ import annotations

import asyncio
import io
import uuid
from datetime import UTC, datetime

import pytest
import sqlalchemy as sa
from PIL import Image
from sqlalchemy import update

from app import analysis, previews, render
from app.analysis import Step
from app.extract import step as extract_step
from app.models import Analysis, BlobText, User
from app.routers import pages_words, viewer
from app.search import hooks
from app.storage import BlobStore
from tests.factories import code_of, maker
from tests.search.fixtures import pdf_scan, pdf_tekst
from tests.test_files import mkdir, put, root
from tests.test_pages import document, sha_of, upload_pdf
from tests.test_shares import person, share_with

# zoek: ook in e2e/slim.sh op SQLite 3.46.1 (bouwontwerp §4.1)
pytestmark = [pytest.mark.anyio, pytest.mark.zoek]


@pytest.fixture(autouse=True)
def _schoon():
    pages_words.words_limiter.reset()
    analysis.reset()
    hooks.reset()
    extract_step.register()
    yield
    pages_words.words_limiter.reset()
    analysis.reset()
    hooks.reset()
    extract_step.register()


def token_of(doc: dict) -> str:
    return doc["page_url"].split("token=", 1)[1]


def words_url(node_id: str, n: int, token: str) -> str:
    return f"/api/v1/files/{node_id}/pages/{n}/words?token={token}"


async def words(client, doc: dict, node_id: str, n: int, expect: int = 200, **headers) -> dict:
    r = await client.get(words_url(node_id, n, token_of(doc)), headers=headers)
    assert r.status_code == expect, r.text
    return r


def cached_words(settings, sha: str) -> set[int]:
    folder = viewer.words_path(BlobStore(settings.blob_dir), sha, 1).parent
    return {int(p.stem) for p in folder.glob("*.json")} if folder.is_dir() else set()


class Processen:
    """Telt de renderprocessen (de forkserver of een eigen proces) en hoeveel er tegelijk
    liepen; elk proces duurt hier minstens hold seconden, zodat verzoeken elkaar overlappen."""

    def __init__(self, monkeypatch, hold: float = 0.0) -> None:
        self.running = self.most = self.started = 0
        self.requests: list[dict] = []
        self.hold = hold
        for name in ("_forked", "_spawn"):
            real = getattr(previews, name)
            monkeypatch.setattr(previews, name, self._wrap(real))

    def _wrap(self, real):
        async def counted(*args):
            request = next(a for a in args if isinstance(a, dict))
            self.requests.append(request)
            self.running += 1
            self.started += 1
            self.most = max(self.most, self.running)
            try:
                await asyncio.sleep(self.hold)
                return await real(*args)
            finally:
                self.running -= 1

        return counted


async def test_de_woorden_met_hun_plek(client, admin, settings):
    jonas, _ = admin
    node_id = await upload_pdf(client, jonas, "verslag.pdf", pdf_tekst(3))
    doc = await document(client, jonas, node_id)
    r = await words(client, doc, node_id, 1)  # zonder inloggen: het token is genoeg
    assert r.headers["content-type"] == "application/json"
    assert r.headers["cache-control"] == "private, max-age=600"
    body = r.json()
    assert body["page"] == 1 and body["source"] == "pdf"
    found = {w["t"]: w for w in body["words"]}
    assert {"paginawoord1", "Straße", "GROẞE", "Pagina", "Page"} <= set(found)
    assert "paginawoord2" not in found
    for w in body["words"]:
        assert set(w) == {"t", "x", "y", "w", "h"}
        assert 0 <= w["x"] and 0 <= w["y"] and w["w"] > 0 and w["h"] > 0
        assert w["x"] + w["w"] <= 1 and w["y"] + w["h"] <= 1
    # De plek: de linkerkolom begint op 50 pt van 595, de rechter op 310; de eerste regel staat
    # bovenaan (790 pt van onderen op 842)
    assert abs(found["Pagina"]["x"] - 50 / 595) < 0.01
    assert abs(found["Page"]["x"] - 310 / 595) < 0.01
    assert found["Pagina"]["y"] < 0.07 and found["Pagina"]["h"] < 0.03
    # Pagina 2 heeft zijn eigen woord
    second = (await words(client, doc, node_id, 2)).json()
    assert "paginawoord2" in {w["t"] for w in second["words"]}


async def test_uit_dezelfde_render_als_de_pagina(client, admin, settings, monkeypatch):
    jonas, _ = admin
    node_id = await upload_pdf(client, jonas, "verslag.pdf", pdf_tekst(5))
    meta = (await client.get(f"/api/v1/files/{node_id}", headers=jonas)).json()
    sha = sha_of(settings, meta)
    doc = await document(client, jonas, node_id)  # docinfo staat nu in de cache
    seen = Processen(monkeypatch)
    page = doc["page_url"].replace("{n}", "1").replace("{w}", "640")
    assert (await client.get(page)).status_code == 200
    # Eén render gaf de pagina's 1, 2 en 3 én hun woorden
    assert seen.started == 1 and seen.requests[0]["words"] is True
    assert cached_words(settings, sha) == {1, 2, 3}
    for n in (1, 2, 3):
        await words(client, doc, node_id, n)
    assert seen.started == 1  # geen proces per verzoek: uit de cache

    # Een pagina die vóór Z2 in de cache stond (zonder woorden): alleen de woorden renderen,
    # niet de pagina opnieuw
    for path in viewer.words_path(BlobStore(settings.blob_dir), sha, 1).parent.glob("*.json"):
        path.unlink()
    webp = viewer.page_path(BlobStore(settings.blob_dir), sha, 640, 1)
    before = webp.stat().st_mtime_ns
    await words(client, doc, node_id, 1)
    assert seen.started == 2
    assert seen.requests[1]["only_words"] is True and seen.requests[1]["pages"] == [1, 2, 3]
    assert webp.stat().st_mtime_ns == before
    assert cached_words(settings, sha) == {1, 2, 3}
    # En dan de woorden van pagina 4 zonder dat iemand de pagina bekeek
    await words(client, doc, node_id, 4)
    assert seen.requests[2]["pages"] == [4, 5]
    assert cached_words(settings, sha) == {1, 2, 3, 4, 5}


def rotated(data: bytes, rotation: int) -> bytes:
    """Dezelfde PDF met /Rotate op elke pagina."""
    import pypdfium2 as pdfium

    document = pdfium.PdfDocument(data)
    for page in document:
        page.set_rotation(rotation)
    out = io.BytesIO()
    document.save(out)
    document.close()
    return out.getvalue()


def ink(image: Image.Image, word: dict) -> float:
    """Het deel van het vak van een woord dat inkt is."""
    width, height = image.size
    box = image.crop((
        int(word["x"] * width),
        int(word["y"] * height),
        int((word["x"] + word["w"]) * width) + 1,
        int((word["y"] + word["h"]) * height) + 1,
    ))  # fmt: skip
    pixels = list(box.get_flattened_data())
    return sum(p < 160 for p in pixels) / len(pixels)


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
async def test_de_plek_klopt_met_het_beeld_ook_gedraaid(client, admin, settings, rotation):
    """Elk vak ligt op de inkt van de pagina zoals de viewer hem toont (de draaiing van de
    pagina meegerekend); hetzelfde vak een halve pagina verder is bijna leeg."""
    jonas, _ = admin
    node_id = await upload_pdf(client, jonas, "gedraaid.pdf", rotated(pdf_tekst(1), rotation))
    doc = await document(client, jonas, node_id)
    r = await client.get(doc["page_url"].replace("{n}", "1").replace("{w}", "1280"))
    assert r.status_code == 200
    image = Image.open(io.BytesIO(r.content)).convert("L")
    assert (image.size[0] > image.size[1]) == (rotation in (90, 270))
    found = (await words(client, doc, node_id, 1)).json()["words"]
    assert len(found) > 400
    inside = [ink(image, w) for w in found]
    moved = [ink(image, {**w, "x": (w["x"] + 0.37) % 0.9, "y": (w["y"] + 0.41) % 0.9})
             for w in found]  # fmt: skip
    assert min(inside) > 0.1
    assert sum(inside) / len(inside) > 2 * sum(moved) / len(moved)


async def test_dezelfde_opruimregel_als_de_pagina_s(client, admin, settings):
    jonas, _ = admin
    settings.page_cache_mb = 0
    node_id = await upload_pdf(client, jonas, "verslag.pdf", pdf_tekst(4))
    meta = (await client.get(f"/api/v1/files/{node_id}", headers=jonas)).json()
    sha = sha_of(settings, meta)
    doc = await document(client, jonas, node_id)
    await words(client, doc, node_id, 1)
    # Alleen wat net gevraagd is, blijft staan
    assert cached_words(settings, sha) == {1}
    await words(client, doc, node_id, 4)
    assert cached_words(settings, sha) == {4}
    settings.page_cache_mb = 100
    await words(client, doc, node_id, 2)
    assert cached_words(settings, sha) == {2, 3, 4}


async def test_etag_en_304(client, admin, settings):
    jonas, _ = admin
    node_id = await upload_pdf(client, jonas, "verslag.pdf", pdf_tekst(2))
    meta = (await client.get(f"/api/v1/files/{node_id}", headers=jonas)).json()
    sha = sha_of(settings, meta)
    doc = await document(client, jonas, node_id)
    r = await words(client, doc, node_id, 2)
    etag = r.headers["etag"]
    assert etag == f'"w{render.WORDS_VERSION}-{sha}-2-p0"'
    again = await words(client, doc, node_id, 2, 304, **{"If-None-Match": etag})
    assert again.content == b"" and again.headers["etag"] == etag
    assert again.headers["cache-control"] == "private, max-age=600"
    # Een andere pagina: een andere ETag
    other = await words(client, doc, node_id, 1, **{"If-None-Match": etag})
    assert other.headers["etag"] != etag and other.json()["page"] == 1


async def test_het_token_hoort_bij_de_versie(client, admin, settings):
    jonas, _ = admin
    root_id = (await root(client, jonas))["folder"]["id"]
    node_id = (await put(client, jonas, root_id, "a.pdf", pdf_tekst(2, tekst="eerstewoord"))).json()
    node_id = node_id["id"]
    r = await put(client, jonas, root_id, "a.pdf", pdf_tekst(2, tekst="tweedewoord"),
                  conflict="replace")  # fmt: skip
    assert r.status_code == 201
    versions = (await client.get(f"/api/v1/files/{node_id}/versions", headers=jonas)).json()
    old = versions[0]["id"]
    now_doc = await document(client, jonas, node_id)
    old_doc = await document(client, jonas, node_id, version=old)
    current = await words(client, now_doc, node_id, 1)
    earlier = await words(client, old_doc, node_id, 1)
    assert "tweedewoord" in {w["t"] for w in current.json()["words"]}
    assert "eerstewoord" in {w["t"] for w in earlier.json()["words"]}
    assert "tweedewoord" not in {w["t"] for w in earlier.json()["words"]}
    assert current.headers["etag"] != earlier.headers["etag"]
    # Het token van de ene versie geeft nooit de woorden van de andere: met de ETag van nu
    # krijgt het oude token geen 304
    r = await words(client, old_doc, node_id, 1, **{"If-None-Match": current.headers["etag"]})
    assert r.status_code == 200
    # Het token van een ander bestand: 403
    other = await upload_pdf(client, jonas, "b.pdf", pdf_tekst(1))
    r = await client.get(words_url(other, 1, token_of(now_doc)))
    assert r.status_code == 403 and code_of(r) == "wrong_token"


async def test_lezen_nodig_en_bij_elk_verzoek_opnieuw(client, admin, settings):
    jonas, _ = admin
    node_id = await upload_pdf(client, jonas, "verslag.pdf", pdf_tekst(2))
    anna, anna_id = await person(client, jonas, "anna@example.nl")
    # Zonder deling: geen document en dus geen token; ook de analyse niet
    await document(client, anna, node_id, expect=404)
    r = await client.get(f"/api/v1/files/{node_id}/analysis", headers=anna)
    assert r.status_code == 404
    share = await share_with(client, jonas, node_id, user_id=anna_id, permission="read")
    doc = await document(client, anna, node_id)
    await words(client, doc, node_id, 1)
    r = await client.get(f"/api/v1/files/{node_id}/analysis", headers=anna)
    assert r.status_code == 200
    # Ingetrokken: hetzelfde token stopt meteen, ook voor woorden in de cache
    r = await client.delete(f"/api/v1/shares/{share['id']}", headers=jonas)
    assert r.status_code == 204
    r = await words(client, doc, node_id, 1, 404)
    assert code_of(r) == "not_found"
    r = await client.get(f"/api/v1/files/{node_id}/analysis", headers=anna)
    assert r.status_code == 404
    # Geblokkeerd: het token stopt meteen
    await share_with(client, jonas, node_id, user_id=anna_id, permission="read")
    doc = await document(client, anna, node_id)
    await words(client, doc, node_id, 1)
    async with maker(settings)() as session:
        await session.execute(
            update(User).where(User.id == uuid.UUID(anna_id)).values(disabled_at=datetime.now(UTC))
        )
        await session.commit()
    r = await words(client, doc, node_id, 1, 404)
    assert code_of(r) == "not_found"
    # Zonder token of met rommel
    r = await client.get(f"/api/v1/files/{node_id}/pages/1/words")
    assert r.status_code == 422
    r = await client.get(words_url(node_id, 1, "rommel"))
    assert r.status_code == 401


async def test_niet_via_een_publieke_link(client, admin, settings):
    jonas, _ = admin
    root_id = (await root(client, jonas))["folder"]["id"]
    folder = await mkdir(client, jonas, root_id, "Boeken")
    inside = (await put(client, jonas, folder, "boek.pdf", pdf_tekst(2))).json()["id"]
    r = await client.post(f"/api/v1/files/{folder}/links", json={"permission": "read"},
                          headers=jonas)  # fmt: skip
    assert r.status_code == 201, r.text
    token = r.json()["link"]["url"].removeprefix("/s/")
    opened = (await client.post("/api/v1/public/open", json={"token": token})).json()
    key = {"Authorization": f"Bearer {opened['key']}"}
    doc = (await client.get(f"/api/v1/public/document/{inside}", headers=key)).json()
    public_key = doc["page_url"].split("&key=", 1)[1]
    # De pagina's wel, de woorden nergens
    page = doc["page_url"].replace("{n}", "1").replace("{w}", "640")
    assert (await client.get(page)).status_code == 200
    for url in (
        f"/api/v1/public/pages/{inside}/1/words?key={public_key}",
        f"/api/v1/public/files/{inside}/pages/1/words?key={public_key}",
        f"/api/v1/public/words/{inside}/1",
    ):
        r = await client.get(url, headers=key)
        assert r.status_code == 404, url
    # De sleutel van de link is geen download-token
    r = await client.get(words_url(inside, 1, public_key))
    assert r.status_code == 401


async def test_geen_woorden_is_404(client, admin, settings):
    jonas, _ = admin
    root_id = (await root(client, jonas))["folder"]["id"]
    # Een scan zonder tekstlaag (de woorden daarvan komen in X1 uit ocr_page)
    scan = await upload_pdf(client, jonas, "scan.pdf", pdf_scan())
    doc = await document(client, jonas, scan)
    r = await words(client, doc, scan, 1, 404)
    assert code_of(r) == "not_found"
    # Een pagina die er niet is
    tekst = await upload_pdf(client, jonas, "verslag.pdf", pdf_tekst(2))
    doc = await document(client, jonas, tekst)
    r = await words(client, doc, tekst, 3, 404)
    assert code_of(r) == "not_found"
    r = await words(client, doc, tekst, 0, 422)
    # Geen PDF: een tekstbestand heeft geen pagina's
    note = (await put(client, jonas, root_id, "notitie.txt", b"gewone tekst")).json()["id"]
    from app.security import download_token

    me = (await client.get("/api/v1/auth/me", headers=jonas)).json()["id"]
    token = download_token(settings, uuid.UUID(me), uuid.UUID(note))
    r = await client.get(words_url(note, 1, token))
    assert r.status_code == 404 and code_of(r) == "not_found"


async def test_vijftig_verzoeken_tegelijk(client, admin, settings, monkeypatch):
    """50 verzoeken tegelijk (drie lezers, 20 pagina's) starten nooit meer dan
    page_render_workers renderprocessen tegelijk; elk antwoord is goed."""
    jonas, _ = admin
    settings.page_render_workers = 2
    node_id = await upload_pdf(client, jonas, "dik.pdf", pdf_tekst(20))
    readers = [jonas]
    for name in ("anna", "bram"):
        headers, user_id = await person(client, jonas, f"{name}@example.nl")
        await share_with(client, jonas, node_id, user_id=user_id, permission="read")
        readers.append(headers)
    docs = [await document(client, headers, node_id) for headers in readers]
    seen = Processen(monkeypatch, hold=0.2)
    asks = [(docs[i % 3], i % 20 + 1) for i in range(50)]
    results = await asyncio.gather(*(words(client, doc, node_id, n) for doc, n in asks))
    for (_, n), r in zip(asks, results, strict=True):
        body = r.json()
        assert body["page"] == n and f"paginawoord{n}" in {w["t"] for w in body["words"]}
    assert seen.most == settings.page_render_workers
    assert all(r.get("only_words") for r in seen.requests)
    # Niet één proces per verzoek: elk proces deed tot drie pagina's, en de rest kwam uit de
    # cache
    assert seen.started <= 20


async def test_hooguit_twintig_per_seconde_per_gebruiker(client, admin, settings):
    jonas, _ = admin
    node_id = await upload_pdf(client, jonas, "verslag.pdf", pdf_tekst(1))
    doc = await document(client, jonas, node_id)
    for _ in range(pages_words.WORDS_PER_SECOND):
        await words(client, doc, node_id, 1)
    r = await words(client, doc, node_id, 1, 429)
    assert code_of(r) == "rate_limited" and int(r.headers["retry-after"]) >= 1
    # Een andere gebruiker heeft zijn eigen teller
    anna, anna_id = await person(client, jonas, "anna@example.nl")
    await share_with(client, jonas, node_id, user_id=anna_id, permission="read")
    await words(client, await document(client, anna, node_id), node_id, 1)
    # Na een seconde mag het weer
    await asyncio.sleep(1.05)
    await words(client, doc, node_id, 1)


# --- de stand van de analyse -----------------------------------------------------------------


async def analysis_of(client, headers, node_id, expect=200) -> dict:
    r = await client.get(f"/api/v1/files/{node_id}/analysis", headers=headers)
    assert r.status_code == expect, r.text
    return r.json()


async def set_row(settings, sha: str, step: Step, state: str, code: str | None = None) -> None:
    async with maker(settings)() as db:
        await db.execute(sa.delete(Analysis).where(Analysis.blob_sha == sha))
        db.add(Analysis(blob_sha=sha, step=step.value, version=1, state=state, code=code))
        await db.commit()


async def test_de_stand_van_de_analyse(client, admin, settings):
    jonas, _ = admin
    root_id = (await root(client, jonas))["folder"]["id"]
    note = (await put(client, jonas, root_id, "notitie.txt", b"het zoekwoord staat hier")).json()
    sha = note["etag"].strip('"')
    # Nog geen rij (de backfill of de pomp komt nog): queued; hash (Z3) is er alleen voor een
    # afbeelding en ocr (X1) alleen voor een PDF of foto: skip
    assert await analysis_of(client, jonas, note["id"]) == {
        "text": "queued", "hash": "skip", "ocr": "skip", "truncated": False, "code": None,
    }  # fmt: skip
    # De echte keten: de stap text met de terugval zonder media
    async with maker(settings)() as db:
        await analysis.queue(db, sha, [Step.TEXT], owner=None)
        await db.commit()
    assert (await analysis_of(client, jonas, note["id"]))["text"] == "queued"
    assert await analysis.pump(settings, "index") == 1
    assert await analysis_of(client, jonas, note["id"]) == {
        "text": "done", "hash": "skip", "ocr": "skip", "truncated": False, "code": None,
    }  # fmt: skip
    # busy is voor de API ook queued; failed met zijn code
    await set_row(settings, sha, Step.TEXT, "busy")
    assert (await analysis_of(client, jonas, note["id"]))["text"] == "queued"
    await set_row(settings, sha, Step.TEXT, "failed", "too_slow")
    found = await analysis_of(client, jonas, note["id"])
    assert found["text"] == "failed" and found["code"] == "too_slow"
    # Een code die de API niet kent, gaat niet naar buiten
    await set_row(settings, sha, Step.TEXT, "failed", "iets_anders")
    assert (await analysis_of(client, jonas, note["id"]))["code"] is None
    # Een stap die er (later) wel is, met zijn eigen rij
    await set_row(settings, sha, Step.OCR, "none", "ocr_has_text")
    found = await analysis_of(client, jonas, note["id"])
    assert found["ocr"] == "none" and found["code"] == "ocr_has_text"
    assert found["text"] == "queued"  # de rij van text is weg (set_row), en text geldt hier
    # Afgekapt
    async with maker(settings)() as db:
        await db.execute(sa.update(BlobText).where(BlobText.blob_sha == sha).values(truncated=1))
        await db.commit()
    assert (await analysis_of(client, jonas, note["id"]))["truncated"] is True


async def test_de_analyse_alleen_voor_een_bestand(client, admin, settings):
    jonas, _ = admin
    root_id = (await root(client, jonas))["folder"]["id"]
    folder = await mkdir(client, jonas, root_id, "Map")
    r = await client.get(f"/api/v1/files/{folder}/analysis", headers=jonas)
    assert r.status_code == 422 and code_of(r) == "not_a_file"
    # Een foto doet niet mee met de stap text: skip
    image = await put(client, jonas, root_id, "foto.png", b"\x89PNG\r\n\x1a\nrommel")
    found = await analysis_of(client, jonas, image.json()["id"])
    assert found["text"] == "skip"
    r = await client.get(f"/api/v1/files/{uuid.uuid4()}/analysis", headers=jonas)
    assert r.status_code == 404
    r = await client.get(f"/api/v1/files/{folder}/analysis")
    assert r.status_code == 401
