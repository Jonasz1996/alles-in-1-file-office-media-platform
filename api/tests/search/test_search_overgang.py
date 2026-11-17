"""Na de overgang van de zoekvraag van 0.7.0 naar die van §3.4 (Z4; review Z0, haalbaar F4).

Tot Z4 nam GET /search ook de vraag van 0.7.0 aan (type=archive, limit tot 200) en stuurde die
altijd naar LikeFallback. Z4 zette de web op de vraag van §3.4 en haalde die tak weg. Nu krijgt
zo'n vraag in beide motoren een duidelijke 422 (invalid_input, met het veld), nooit stil een
ander antwoord; de rest gaat via de motor, met een gecontroleerde SearchParams (limit zonder
waarde: 30). type is in beide motoren de soort van §3.4 (node.ftype): een archief is "other".
"""

from __future__ import annotations

import io
import logging
import zipfile

import pytest
from sqlalchemy import create_engine, select

from app import mime
from app.models import Node
from app.routers import search as search_router
from app.search import engine
from app.search.like import of_category
from app.search.types import SearchPage, SearchParams, Suggestions
from tests.search.test_ftype import known_mimes
from tests.test_files import put, root


class Spion:
    """Staat voor FtsEngine van Z1: onthoudt elke vraag."""

    name = "fts"

    def __init__(self) -> None:
        self.vragen: list[SearchParams] = []

    async def search(self, session, user, params: SearchParams) -> SearchPage:
        self.vragen.append(params)
        return SearchPage(results=[], more=False, engine="fts")

    async def suggest(self, session, user, q: str) -> Suggestions:
        return Suggestions()


def namen(r) -> list[str]:
    return [h["node"]["name"] for h in r.json()["results"]]


@pytest.mark.anyio
@pytest.mark.parametrize("motor", ["like", "fts"])
async def test_de_vraag_van_070_is_een_422(client, admin, settings, monkeypatch, motor):
    if motor == "like":
        monkeypatch.setattr(settings, "search_index", "uit")
    spion = Spion()
    monkeypatch.setattr(engine, "FTS", spion)
    headers, _ = admin
    top = (await root(client, headers))["folder"]["id"]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("in.txt", "inhoud")
    for name, data in (("archief.zip", buf.getvalue()), ("archief.jpg", b"geen foto")):
        assert (await put(client, headers, top, name, data)).status_code == 201

    for params, veld in (
        ({"q": "archief", "limit": 200}, "query.limit"),
        ({"q": "archief", "limit": 51}, "query.limit"),
        ({"q": "archief", "limit": 0}, "query.limit"),
        ({"q": "archief", "type": "archive"}, "query.type.0"),
        ({"type": "archive", "limit": 200}, "query.type.0"),
        ({"q": "archief", "type": ["image", "archive"]}, "query.type.1"),
    ):
        r = await client.get("/api/v1/search", params=params, headers=headers)
        assert r.status_code == 422, (params, r.text)
        detail = r.json()["detail"]
        assert (detail["code"], detail["params"]) == ("invalid_input", {"field": veld}), params
        # De waarde komt niet terug (errors.invalid_input haalt input weg)
        assert all("input" not in e for e in detail["errors"]), params
    assert spion.vragen == []

    # Binnen de grenzen van §3.4: via de motor, met een gecontroleerde vraag
    params = {"q": "archief", "type": "other", "limit": 50}
    r = await client.get("/api/v1/search", params=params, headers=headers)
    assert r.status_code == 200, r.text
    if motor == "like":
        # Een archief is "other", net als in FtsEngine (tot Z4 niet: dat was de soort van
        # 0.7.0); "geen foto" is tekst (text/plain), dus een document en geen afbeelding
        assert r.json()["engine"] == "like"
        assert namen(r) == ["archief.zip"]
        for soort, verwacht in (("document", ["archief.jpg"]), ("image", [])):
            r = await client.get(
                "/api/v1/search", params={"q": "archief", "type": soort}, headers=headers
            )
            assert namen(r) == verwacht, soort
        assert spion.vragen == []
        return
    assert r.json()["engine"] == "fts"
    [vraag] = spion.vragen
    assert (vraag.q, vraag.types, vraag.limit) == ("archief", ["other"], 50)
    assert SearchParams.model_validate(vraag.model_dump()) == vraag
    # Zonder limit: 30 (§3.4), niet meer 50 zoals in 0.7.0
    r = await client.get("/api/v1/search", params={"q": "archief"}, headers=headers)
    assert r.status_code == 200, r.text
    assert (spion.vragen[-1].limit, spion.vragen[-1].offset) == (30, 0)


@pytest.mark.anyio
async def test_wat_searchparams_afwijst_is_422_zonder_de_vraag(client, admin, monkeypatch, caplog):
    """Kan niet meer (de grenzen staan in Query), maar mocht SearchParams toch iets afwijzen: een
    gewone 422 invalid_input, geen 500 met de vraag in de log of in het antwoord."""
    headers, _ = admin
    monkeypatch.setattr(search_router, "MAX_CHARS", 500)
    geheim = "geheimewoord" * 20  # 240 tekens: langer dan SearchParams.q toestaat
    with caplog.at_level(logging.DEBUG):
        r = await client.get("/api/v1/search", params={"q": geheim}, headers=headers)
    assert r.status_code == 422, r.text
    detail = r.json()["detail"]
    assert (detail["code"], detail["params"]) == ("invalid_input", {"field": "query.q"})
    assert "geheimewoord" not in r.text
    assert "geheimewoord" not in caplog.text


@pytest.mark.parametrize("kind", ["file", "dir", "album"])
def test_likefallback_kent_de_soorten_van_34(kind):
    """of_category (LikeFallback, ook op PostgreSQL zonder de kolom ftype) zegt voor elke mime
    die mime.py kent hetzelfde als mime.ftype() en node.ftype: een archief is "other", een
    rekenblad (ook CSV) of presentatie geen "document"."""
    mimes = sorted(known_mimes(), key=str)
    db = create_engine("sqlite://")
    with db.connect() as conn:
        conn.exec_driver_sql("CREATE TABLE node (kind TEXT, mime TEXT)")
        for m in mimes:
            conn.exec_driver_sql("INSERT INTO node (kind, mime) VALUES (?, ?)", (kind, m))
        for soort in mime.FTYPES:
            gevonden = sorted(
                conn.execute(select(Node.mime).where(of_category(soort))).scalars(), key=str
            )
            verwacht = [m for m in mimes if mime.ftype(m, kind) == soort]
            assert gevonden == verwacht, soort
