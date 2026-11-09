"""Duplicaten: de klieken, GET /duplicates en POST /duplicates/ignore (bouwontwerp fase 6, Keuzes
34–36 en 4a; Z3).

- Klieken (Keuze 36): een inhoud komt alleen bij een groep als hij met elk lid een paar is; nooit
  "verbonden via een ander"; elke inhoud in hooguit één groep; het niveau is dat van het zwakste
  paar, dist de grootste afstand.
- Precies gelijk: alleen eigen, levende bestanden (niet die in de prullenbak, niet die van een
  ander, ook niet als die gedeeld zijn); de grootste eerst. Nooit iets vooraf gekozen: een item is
  alleen node, pad, grootte en opnamedatum.
- Bijna gelijk door de echte keten: upload, preview, photometa (pHash, tweede controle,
  dup_pair) en de groep "zeker"; met andere tekst erbij (OCR na de pHash) wordt hij "lijkt".
- "Niet gelijk" (dup_ignore), per gebruiker: de groep komt niet terug. Bij bijna gelijk wel als er
  een inhoud bij komt of af gaat (een andere sleutel); bij precies gelijk is de sleutel die van de
  inhoud, dus een derde kopie houdt hem weg.
- Bladeren met de cursor; een cursor die niet klopt, geeft 422; een gast 403.
- De stand van hash in GET /files/{id}/analysis komt uit blob_hash.
"""

from __future__ import annotations

import io

import pytest
from PIL import Image
from sqlalchemy import select

from app import analysis, photo_hash
from app.dupes import groups, phash
from app.geo import wire
from app.geo.types import DupeGroup, DupeItem
from app.models import BlobHash, DupPair
from tests.factories import maker
from tests.geo.hulp import map_maken, verwerk
from tests.search import fixtures as fx
from tests.test_previews import upload
from tests.test_shares import person, share_with
from tests.test_trash import trash_it

pytestmark = [pytest.mark.anyio, pytest.mark.zoek]


@pytest.fixture(autouse=True)
def _aangemeld():
    """Een test eerder in de run kan de haken van previews of zoeken leeggemaakt hebben; dan
    ontbreekt de keten van photometa na preview.ready (zoals tests/geo/conftest.py)."""
    wire.register()


def _sha(n: int) -> str:
    return f"{n:064x}"


# --- de klieken ---------------------------------------------------------------------------------


def test_nooit_verbonden_via_een_ander():
    a, b, c = _sha(1), _sha(2), _sha(3)
    # a lijkt op b en b op c, maar a niet op c: geen groep van drie
    found = groups.cliques([(a, b, 2, "zeker"), (b, c, 3, "zeker")])
    assert [g.shas for g in found] == [(a, b)]
    # Een keten van 150 (zoals de brieven bij "verbonden via een ander": één groep van 149)
    keten = [(_sha(i), _sha(i + 1), 4, "lijkt") for i in range(150)]
    found = groups.cliques(keten)
    assert max(len(g.shas) for g in found) == 2
    gezien = [s for g in found for s in g.shas]
    assert len(gezien) == len(set(gezien)), "een inhoud in hooguit één groep"


def test_een_kliek_en_zijn_niveau():
    a, b, c, d = _sha(1), _sha(2), _sha(3), _sha(4)
    paren = [(a, b, 1, "zeker"), (a, c, 3, "zeker"), (b, c, 5, "lijkt"), (c, d, 0, "zeker")]
    found = groups.cliques(paren)
    # c-d (afstand 0) eerst; dan a-b; a-c en b-c: c zit al in een groep, a en b ook: niets
    assert [(g.shas, g.level, g.dist) for g in found] == [((c, d), "zeker", 0),
                                                          ((a, b), "zeker", 1)]  # fmt: skip
    found = groups.cliques([(a, b, 1, "zeker"), (a, c, 3, "zeker"), (b, c, 5, "lijkt")])
    (g,) = found
    # Het zwakste paar beslist, dist is de grootste afstand
    assert (g.shas, g.level, g.dist) == ((a, b, c), "lijkt", 5)
    assert g.key == phash.group_key([c, b, a]) and groups.is_key(g.key)
    # Bij gelijke afstand "zeker" eerst
    found = groups.cliques([(a, c, 2, "lijkt"), (a, b, 2, "zeker")])
    assert [g.shas for g in found] == [(a, b)]


def test_nooit_iets_vooraf_gekozen():
    """Een groep en een item hebben geen veld om iets te kiezen of te bewaren: opruimen gaat
    alleen via de prullenbak, en dan kiest de gebruiker zelf (Keuze 36)."""
    assert set(DupeGroup.model_fields) == {"key", "kind", "level", "dist", "items"}
    assert set(DupeItem.model_fields) == {"node", "path", "size", "taken_at"}


# --- de routes ----------------------------------------------------------------------------------


async def lijst(client, headers, kind: str = "exact", cursor: str | None = None) -> dict:
    params = {"kind": kind} | ({"cursor": cursor} if cursor else {})
    r = await client.get("/api/v1/duplicates", params=params, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


async def negeer(client, headers, key: str, expect: int = 204) -> None:
    r = await client.post("/api/v1/duplicates/ignore", json={"key": key}, headers=headers)
    assert r.status_code == expect, r.text
    if expect == 400:
        assert r.json()["detail"]["code"] == "dupes_bad_key", r.text


def _namen(page: dict) -> list[list[str]]:
    return [[i["node"]["name"] for i in g["items"]] for g in page["groups"]]


async def test_precies_gelijk_alleen_eigen_en_levend(client, admin):
    eva, _ = admin
    inhoud = b"dezelfde inhoud " * 100
    groot = b"x" * 5000
    a = await upload(client, eva, "a.txt", inhoud)
    map_ = await map_maken(client, eva, "Kopieen")
    await upload(client, eva, "b.txt", inhoud, parent=map_)
    weg = await upload(client, eva, "weg.txt", inhoud)
    await trash_it(client, eva, weg["id"])
    # Drie keer de grote: die groep eerst (meer bestanden)
    for naam in ("g1.bin", "g2.bin", "g3.bin"):
        await upload(client, eva, naam, groot)
    await upload(client, eva, "alleen.txt", b"maar een keer")
    # Bram heeft dezelfde inhoud, en ziet Eva's map via een deling
    bram, bram_id = await person(client, eva, "bram@example.com")
    await upload(client, bram, "van-bram.txt", inhoud)
    await share_with(client, eva, map_, user_id=bram_id, permission="read")

    page = await lijst(client, eva)
    assert page["cursor"] is None
    assert [sorted(n) for n in _namen(page)] == [["g1.bin", "g2.bin", "g3.bin"],
                                                 ["a.txt", "b.txt"]]  # fmt: skip
    groep = page["groups"][1]
    assert groep["kind"] == "exact" and groep["level"] is None and groep["dist"] is None
    assert groep["key"] == phash.group_key([a["etag"].strip('"')])
    item = next(i for i in groep["items"] if i["node"]["name"] == "b.txt")
    assert item["size"] == len(inhoud) and item["taken_at"] is None
    assert [p["name"] for p in item["path"]][-1] == "Kopieen"
    assert item["node"]["permission"] == "owner"
    # Bram: zijn eigen bestand is alleen; Eva's gedeelde kopie telt niet
    assert (await lijst(client, bram))["groups"] == []
    # In de prullenbak: een groep van één is geen groep meer
    await trash_it(client, eva, a["id"])
    assert [sorted(n) for n in _namen(await lijst(client, eva))] == [["g1.bin", "g2.bin",
                                                                      "g3.bin"]]  # fmt: skip


async def test_niet_gelijk_blijft_weg_en_is_per_gebruiker(client, admin):
    eva, _ = admin
    inhoud = b"twee keer " * 50
    await upload(client, eva, "a.txt", inhoud)
    await upload(client, eva, "b.txt", inhoud)
    (groep,) = (await lijst(client, eva))["groups"]
    bram, _ = await person(client, eva, "bram@example.com")
    # Niet van Bram, geen sleutel, of geen groep: 400
    await negeer(client, bram, groep["key"], 400)
    for fout in ("x", groep["key"].upper(), "0" * 64, groep["key"] + "0"):
        await negeer(client, eva, fout, 400)
    await negeer(client, eva, groep["key"])
    assert (await lijst(client, eva))["groups"] == []
    await negeer(client, eva, groep["key"])  # een tweede keer: ook 204
    # Bij precies gelijk is de sleutel die van de inhoud: een derde kopie verandert hem niet, de
    # groep blijft weg. Een andere inhoud is een andere groep
    await upload(client, eva, "c.txt", inhoud)
    assert (await lijst(client, eva))["groups"] == []
    await upload(client, eva, "d.txt", b"nog een " * 50)
    await upload(client, eva, "e.txt", b"nog een " * 50)
    assert [sorted(n) for n in _namen(await lijst(client, eva))] == [["d.txt", "e.txt"]]
    # Bram heeft zijn eigen lijst "niet gelijk"
    await upload(client, bram, "a.txt", inhoud)
    await upload(client, bram, "b.txt", inhoud)
    assert [g["key"] for g in (await lijst(client, bram))["groups"]] == [groep["key"]]


async def test_bladeren_met_de_cursor(client, admin, monkeypatch):
    eva, _ = admin
    monkeypatch.setattr(groups, "PAGE", 2)
    for n, inhoud in enumerate((b"een " * 40, b"twee " * 40, b"drie " * 40)):
        for k in range(n + 2):  # 2, 3 en 4 kopieen: de grootste groep eerst
            await upload(client, eva, f"{n}-{k}.txt", inhoud)
    eerste = await lijst(client, eva)
    assert [len(g["items"]) for g in eerste["groups"]] == [4, 3] and eerste["cursor"]
    tweede = await lijst(client, eva, cursor=eerste["cursor"])
    assert [len(g["items"]) for g in tweede["groups"]] == [2] and tweede["cursor"] is None
    # Een cursor die niet klopt (kapot, van de andere soort, geen sleutel): 422
    near = groups.encode_cursor(groups.Group("a" * 64, "near", "zeker", 1, (), (0, 1, "a" * 64)))
    for fout in ("!!", "W10", near, "x" * 200):
        r = await client.get("/api/v1/duplicates", params={"cursor": fout}, headers=eva)
        assert r.status_code == 422, (fout, r.text)
        detail = r.json()["detail"]
        assert detail["code"] == "invalid_input", r.text
        assert detail["params"] == {"field": "query.cursor"}, r.text
    r = await client.get("/api/v1/duplicates", params={"cursor": "x" * 201}, headers=eva)
    assert r.status_code == 422, r.text
    r = await client.get("/api/v1/duplicates", params={"kind": "bijna"}, headers=eva)
    assert r.status_code == 422, r.text


async def test_een_gast_heeft_geen_eigen_bestanden(client, admin):
    eva, _ = admin
    gast, _ = await person(client, eva, "gast@example.com", role="guest")
    vragen = (
        ("GET", "/api/v1/duplicates", None),
        ("POST", "/api/v1/duplicates/ignore", {"key": "a" * 64}),
    )
    for methode, pad, body in vragen:
        r = await client.request(methode, pad, json=body, headers=gast)
        assert r.status_code == 403 and r.json()["detail"]["code"] == "guest", r.text


def _jpeg(img: Image.Image, quality: int = 90) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=quality)
    return buf.getvalue()


async def _hash_staat(client, headers, node_id: str) -> str:
    r = await client.get(f"/api/v1/files/{node_id}/analysis", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()["hash"]


async def test_bijna_gelijk_door_de_keten(client, admin, settings):
    """Upload, preview en photometa: blob_hash, dup_pair en de groep "zeker"; vlak geeft geen
    hash; de stand van hash in /analysis; tekst die na de pHash komt, maakt hem "lijkt"."""
    eva, _ = admin
    foto = fx.beeld("dubbel", 1200, 900)
    a = await upload(client, eva, "a.jpg", _jpeg(foto))
    half = await upload(client, eva, "a-half.jpg", _jpeg(foto.resize((600, 450))))
    await upload(client, eva, "ander.jpg", _jpeg(fx.beeld("ander", 1200, 900)))
    vlak = await upload(client, eva, "vlak.jpg", _jpeg(Image.new("RGB", (800, 600), (9, 9, 9))))
    tekst = await upload(client, eva, "notitie.txt", b"geen afbeelding")
    # Nog niet gerekend: queued voor een afbeelding, skip voor de rest
    assert await _hash_staat(client, eva, a["id"]) == "queued"
    assert await _hash_staat(client, eva, tekst["id"]) == "skip"
    await verwerk(settings)
    staat = {n["name"]: await _hash_staat(client, eva, n["id"]) for n in (a, half, vlak, tekst)}
    assert staat == {"a.jpg": "done", "a-half.jpg": "done", "vlak.jpg": "none",
                     "notitie.txt": "skip"}, staat  # fmt: skip
    sha = {n["name"]: n["etag"].strip('"') for n in (a, half, vlak)}
    async with maker(settings)() as db:
        hashes = dict((await db.execute(select(BlobHash.blob_sha, BlobHash.state))).all())
        paren = (await db.execute(select(DupPair.a, DupPair.b, DupPair.level))).all()
    assert hashes[sha["vlak.jpg"]] == "flat" and hashes[sha["a.jpg"]] == "ok"
    paar = tuple(sorted((sha["a.jpg"], sha["a-half.jpg"])))
    assert [(x, y, lv) for x, y, lv in paren] == [(*paar, "zeker")], paren
    (groep,) = (await lijst(client, eva, "near"))["groups"]
    assert groep["kind"] == "near" and groep["level"] == "zeker" and groep["dist"] <= phash.SURE
    assert groep["key"] == phash.group_key(paar)
    # De grootste eerst; niets gekozen
    assert [i["node"]["name"] for i in groep["items"]] == ["a.jpg", "a-half.jpg"]
    # Precies gelijk ziet ze niet: twee inhouden
    assert (await lijst(client, eva))["groups"] == []
    # OCR (of andere tekst) na de pHash: beide met tekst, en die is anders: "lijkt"
    async with maker(settings)() as db:
        await analysis.write_text(db, sha["a.jpg"], ocr="Factuur 2024")
    async with maker(settings)() as db:
        await analysis.write_text(db, sha["a-half.jpg"], ocr="Factuur 2025")
    (groep,) = (await lijst(client, eva, "near"))["groups"]
    assert groep["level"] == "lijkt"
    # Dezelfde tekst: weer "zeker"
    async with maker(settings)() as db:
        await analysis.write_text(db, sha["a-half.jpg"], ocr="factuur  2024")
    (groep,) = (await lijst(client, eva, "near"))["groups"]
    assert groep["level"] == "zeker"
    # "Niet gelijk", en terug als er een lid bij komt (een andere sleutel)
    await negeer(client, eva, groep["key"])
    assert (await lijst(client, eva, "near"))["groups"] == []
    await upload(client, eva, "a-q60.jpg", _jpeg(foto, quality=60))
    await verwerk(settings)
    (groep,) = (await lijst(client, eva, "near"))["groups"]
    assert len(groep["items"]) == 3 and groep["key"] != phash.group_key(paar)
    # Een ander ziet niets van Eva's paren, ook niet als hij dezelfde inhoud heeft
    bram, _ = await person(client, eva, "bram@example.com")
    await upload(client, bram, "a.jpg", _jpeg(foto))
    assert (await lijst(client, bram, "near"))["groups"] == []
    # Na de prullenbak geen groep meer van drie
    await trash_it(client, eva, half["id"])
    (groep,) = (await lijst(client, eva, "near"))["groups"]
    assert sorted(i["node"]["name"] for i in groep["items"]) == ["a-q60.jpg", "a.jpg"]
    assert photo_hash.VERSION == 1
