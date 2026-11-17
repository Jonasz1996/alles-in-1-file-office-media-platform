"""Een plek komt nooit via een publieke link naar buiten (bouwontwerp fase 6, Keuzes 25a en 27;
§4.1). Een foto met GPS staat met plek en plaats (Amsterdam) bij de eigenaar; daarna gaat elke
route onder /api/v1/public langs: met een leeslink op de map, op het bestand en op een album, en
met een uploadlink waarmee een tweede foto met GPS binnenkomt. In geen antwoord (de JSON helemaal
doorlopen, en de kopteksten) staat een sleutel als lat, lon, place of geo, of de coördinaten, de
plaatsnaam of het id van de plaats; ook niet in preview_meta en niet in de previews zelf.

De download is het origineel, met zijn eigen EXIF (V9; daarom de waarschuwing bij delen, Keuze
30): daar kijken we alleen naar de kopteksten. Een nieuwe route onder /api/v1/public laat
test_elke_publieke_route_komt_langs falen tot hij hier ook langs gaat."""

import io
import json
import re
import uuid
from typing import Any

import pytest
from fastapi.routing import iter_route_contexts
from PIL import Image
from sqlalchemy import select

from app import jobs, photometa
from app.main import app
from app.models import Blob, PhotoGeo
from app.routers import public
from tests.factories import code_of, maker
from tests.media.fake_stream import H264_PROFILE
from tests.test_previews import upload
from tests.test_public import TOKEN_PATH, bearer, meta, new_link
from tests.test_render_meta import GPS_IFD, camera_jpeg, no_gps
from tests.test_uploads import PATCH, TUS

pytestmark = pytest.mark.anyio

PUBLIEK = "/api/v1/public"
# Sleutels die een plek zouden zijn (of een stukje ervan: een tegel, een land, een provincie)
VERBODEN = {
    "lat", "lon", "latitude", "longitude", "alt", "gps", "geo", "location", "place", "places",
    "place_id", "country", "cc", "admin1", "x16", "y16", "cell", "cells",
}  # fmt: skip
# camera_jpeg(): 52°22'1" N en 4°53'2" O, dus 52.3669… en 4.8838…; als losse getallen (niet een
# stukje van een sleutel of handtekening, die geen punt hebben of er letters omheen)
COORDINATEN = re.compile(r"(?<![\w.-])(52\.36|4\.88)\d*(?![\w-])")
PLAATS = ("amsterdam", "north holland", "noord-holland")


def _sleutels(waarde: Any, pad: str = "$"):
    if isinstance(waarde, dict):
        for k, v in waarde.items():
            yield f"{pad}.{k}", str(k)
            yield from _sleutels(v, f"{pad}.{k}")
    elif isinstance(waarde, list):
        for i, v in enumerate(waarde):
            yield from _sleutels(v, f"{pad}[{i}]")


def geen_plek(tekst: str, waar: str, place_id: int) -> None:
    laag = tekst.lower()
    for woord in PLAATS:
        assert woord not in laag, (waar, woord)
    gevonden = COORDINATEN.search(tekst)
    assert gevonden is None, (waar, gevonden and gevonden.group(0))
    assert re.search(rf"(?<!\d){place_id}(?!\d)", tekst) is None, (waar, "place_id")


class Publiek:
    """Roept routes onder /api/v1/public aan en kijkt elk antwoord na. Houdt bij welke routes
    (methode en sjabloon) langs zijn gekomen."""

    def __init__(self, client, place_id: int) -> None:
        self.client = client
        self.place_id = place_id
        self.gedaan: set[tuple[str, str]] = set()
        self.previews: set[str] = set()

    async def __call__(self, methode: str, sjabloon: str, *, origineel=False, **kw):
        pad = PUBLIEK + sjabloon.format(**kw.pop("vul", {}))
        r = await self.client.request(methode, pad, **kw)
        self.gedaan.add((methode, PUBLIEK + sjabloon))
        self.kijk(r, f"{methode} {sjabloon}", origineel)
        return r

    def kijk(self, r, waar: str, origineel: bool = False) -> None:
        for naam, waarde in r.headers.items():
            assert not any(w in naam.lower() for w in ("gps", "geo", "place")), (waar, naam)
            geen_plek(waarde, f"{waar} kop {naam}", self.place_id)
        if origineel or r.headers.get("content-type", "").startswith("image/"):
            return
        geen_plek(r.text, waar, self.place_id)
        if not r.headers.get("content-type", "").startswith("application/json"):
            return
        body = r.json()
        for pad, sleutel in _sleutels(body):
            assert sleutel.lower() not in VERBODEN, (waar, pad)
        tekst = json.dumps(body)
        for url in re.findall(r'"(/api/v1/previews/[^"]+)"', tekst):
            self.previews.add(url)


async def _plek_van(settings, node_id: str) -> PhotoGeo:
    async with maker(settings)() as db:
        row = await db.scalar(select(PhotoGeo).where(PhotoGeo.node_id == uuid.UUID(node_id)))
    assert row is not None and row.place_id is not None, "geen plek: dan zegt de test niets"
    return row


async def _verwerk(settings) -> None:
    await jobs.run_pending(settings)  # de previews; die zetten photometa klaar (na DELAY)
    await jobs.run_pending(settings, at=jobs.now() + photometa.DELAY * 2)


@pytest.fixture(autouse=True)
def verse_limieten():
    for limiter in (public.open_limiter, public.upload_limiter, public.password_limiter):
        limiter.reset()
    yield
    for limiter in (public.open_limiter, public.upload_limiter, public.password_limiter):
        limiter.reset()


def _publieke_routes() -> set[tuple[str, str]]:
    """Ook die in een ingesloten router (FastAPI houdt een router met include_router als één
    element in app.routes)."""
    uit = {
        (methode, c.path)
        for c in iter_route_contexts(app.routes)
        if (c.path or "").startswith(PUBLIEK)
        for methode in c.methods or ()
    }
    assert len(uit) >= 17, "de routes onder /api/v1/public zijn niet gevonden"
    return uit


async def test_elke_publieke_route_zonder_plek(client, admin, settings, plaatsen):
    headers, _tokens = admin
    root = (await client.get("/api/v1/files/root", headers=headers)).json()["folder"]["id"]
    r = await client.post(f"/api/v1/files/{root}/folders", json={"name": "Reis"}, headers=headers)
    assert r.status_code == 201, r.text
    reis = r.json()["id"]
    foto = await upload(client, headers, "reis.jpg", camera_jpeg(size=(1600, 1200)), parent=reis)
    await _verwerk(settings)
    plek = await _plek_van(settings, foto["id"])
    # De eigenaar ziet zijn plek wel (anders bewijst de rest niets)
    r = await client.get(f"/api/v1/files/{foto['id']}/location", headers=headers)
    assert r.status_code == 200 and r.json()["place"]["name"] == "Amsterdam", r.text

    r = await client.post("/api/v1/albums", json={"name": "Vakantie"}, headers=headers)
    album = r.json()["id"]
    r = await client.post(
        f"/api/v1/albums/{album}/items", json={"node_ids": [foto["id"]]}, headers=headers
    )
    assert r.status_code == 200 and r.json()["added"] == 1, r.text

    p = Publiek(client, plek.place_id)
    sleutels = {}
    for naam, node in (("map", reis), ("bestand", foto["id"]), ("album", album)):
        link = await new_link(client, headers, node, permission="read")
        token = link["link"]["url"][len(TOKEN_PATH) :]
        r = await p("POST", "/open", json={"token": token})
        assert r.status_code == 200, r.text
        sleutels[naam] = r.json()["key"]
        # De publieke pagina zelf (de web-app; zonder build een 404): ook daar niets
        p.kijk(await client.get(f"{TOKEN_PATH}{token}"), f"pagina {naam}")

    f = {"vul": {"node_id": foto["id"]}}
    for naam, sleutel in sleutels.items():
        h = bearer(sleutel)
        r = await p("GET", "/children", headers=h)
        assert r.status_code == (200 if naam == "map" else 422), (naam, r.text)
        r = await p("GET", "/listing", headers=h)
        assert r.status_code == (200 if naam == "map" else 422), (naam, r.text)
        r = await p("POST", "/previews", headers=h, json={"node_ids": [foto["id"]]})
        assert r.status_code == 200 and r.json()[foto["id"]]["state"] == "ready", (naam, r.text)
        r = await p("GET", "/preview/{node_id}", headers=h, **f)
        assert r.status_code == 200, (naam, r.text)
        # Een foto heeft geen pagina's (de route is wel bereikt: geen 401 of 404)
        r = await p("GET", "/document/{node_id}", headers=h, **f)
        assert (r.status_code, code_of(r)) == (422, "not_a_document"), (naam, r.text)
        r = await p("GET", "/pages/{node_id}/{n}.webp", headers=h, vul={**f["vul"], "n": 1})
        assert (r.status_code, code_of(r)) == (422, "not_a_document"), (naam, r.text)
        r = await p("GET", "/album/items", headers=h)
        if naam == "album":
            assert [i["id"] for i in r.json()["items"]] == [foto["id"]], r.text
        r = await p("GET", "/download/{node_id}", headers=h, origineel=True, **f)
        assert r.status_code == 200, (naam, r.text)
        r = await p("HEAD", "/download/{node_id}", headers=h, origineel=True, **f)
        assert r.status_code == 200, naam
        # Zonder media (de gewone testrun): de route antwoordt wel, met media_off
        r = await p("POST", "/media/{node_id}/play", headers=h, json={"profile": H264_PROFILE}, **f)
        assert (r.status_code, code_of(r)) == (503, "media_off"), (naam, r.text)

    # De previews zelf: geen EXIF met een GPS-IFD, geen XMP
    assert p.previews, "geen previewadres gezien"
    for url in sorted(p.previews):
        r = await client.get(url)
        assert r.status_code == 200, url
        with Image.open(io.BytesIO(r.content)) as img:
            assert not img.getexif().get_ifd(GPS_IFD), url
            assert "xmp" not in img.info, url
        assert b"GPS" not in r.content, url

    # Een uploadlink: een tweede foto met GPS komt binnen, de uploader ziet niets terug
    link = await new_link(client, headers, reis, permission="upload")
    r = await p("POST", "/open", json={"token": link["link"]["url"][len(TOKEN_PATH) :]})
    h = bearer(r.json()["key"])
    data = camera_jpeg(color="green")
    r = await p("OPTIONS", "/tus")
    assert r.status_code == 204
    r = await p(
        "POST",
        "/tus",
        headers={
            **h, **TUS, "Upload-Length": str(len(data)), "Upload-Metadata": meta(filename="b.jpg")
        },
    )  # fmt: skip
    assert r.status_code == 201, r.text
    upload_id = r.headers["location"].rsplit("/", 1)[1]
    u = {"vul": {"upload_id": upload_id}}
    r = await p("OPTIONS", "/tus/{upload_id}", **u)
    assert r.status_code == 204
    r = await p("HEAD", "/tus/{upload_id}", headers={**h, **TUS}, **u)
    assert r.headers["upload-offset"] == "0"
    r = await p(
        "PATCH", "/tus/{upload_id}", headers={**h, **PATCH, "Upload-Offset": "0"}, content=data, **u
    )
    assert r.status_code == 204, r.text
    # Een tweede upload die de uploader afbreekt
    r = await p("POST", "/tus", headers={**h, **TUS, "Upload-Length": "3",
                                         "Upload-Metadata": meta(filename="c.jpg")})  # fmt: skip
    weg = {"vul": {"upload_id": r.headers["location"].rsplit("/", 1)[1]}}
    r = await p("DELETE", "/tus/{upload_id}", headers={**h, **TUS}, **weg)
    assert r.status_code == 204, r.text
    await _verwerk(settings)
    kinderen = (await client.get(f"/api/v1/files/{reis}/children", headers=headers)).json()
    nieuw = next(c for c in kinderen["children"] if c["name"] == "b.jpg")
    await _plek_van(settings, nieuw["id"])  # de eigenaar heeft er wel een plek bij

    # preview_meta van beide foto's: zonder plek
    async with maker(settings)() as db:
        blobs = (await db.scalars(select(Blob).where(Blob.mime == "image/jpeg"))).all()
    assert len(blobs) == 2
    for blob in blobs:
        assert blob.preview == "ready"
        no_gps(blob.preview_meta)

    # Alles onder /api/v1/public is langs geweest (een nieuwe route: hier toevoegen)
    assert p.gedaan == _publieke_routes()


def _velden(schema: Any, onderdelen: dict, gezien: set[str]) -> set[str]:
    uit: set[str] = set()
    if isinstance(schema, dict):
        ref = schema.get("$ref")
        if isinstance(ref, str):
            naam = ref.rsplit("/", 1)[1]
            if naam not in gezien:
                gezien.add(naam)
                uit |= _velden(onderdelen[naam], onderdelen, gezien)
        for k, v in schema.items():
            if k == "properties" and isinstance(v, dict):
                uit |= set(v)
            uit |= _velden(v, onderdelen, gezien)
    elif isinstance(schema, list):
        for v in schema:
            uit |= _velden(v, onderdelen, gezien)
    return uit


async def test_het_contract_van_de_publieke_routes():
    """In de OpenAPI: geen publieke route met een plek in zijn naam of in zijn antwoord; en
    coördinaten staan alleen in de antwoorden van deze twee routes (alleen voor de eigenaar)."""
    spec = app.openapi()
    onderdelen = spec["components"]["schemas"]
    met_coordinaten = set()
    for pad, methodes in spec["paths"].items():
        for methode, op in methodes.items():
            velden = {v.lower() for v in _velden(op.get("responses", {}), onderdelen, set())}
            if pad.startswith(PUBLIEK):
                for woord in ("geo", "location", "map", "place", "gps"):
                    assert woord not in pad.lower(), pad
                assert not velden & VERBODEN, (methode, pad, velden & VERBODEN)
            if velden & {"lat", "lon", "latitude", "longitude"}:
                met_coordinaten.add((methode.upper(), pad))
    assert met_coordinaten == {
        ("GET", "/api/v1/files/{node_id}/location"),
        ("GET", "/api/v1/photos/map"),
    }
