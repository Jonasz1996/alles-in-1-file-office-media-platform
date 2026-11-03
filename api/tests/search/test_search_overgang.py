"""De overgang van de zoekvraag van 0.7.0 naar die van §3.4 (review Z0, haalbaar F4).

GET /search neemt nog de vraag van 0.7.0 aan: type=archive en limit tot 200, zoals de web van
nu (web/src/search/searchQuery.ts, MAX_LIMIT = 200) en de tests van 0.7.0 hem sturen. De frozen
SearchParams kent geen archive en gaat tot limit 50. Zo'n vraag gaat daarom altijd naar
LikeFallback, ook als Z1 FtsEngine aanzet; de rest gaat via select_engine, met een
SearchParams die gecontroleerd is. Z4 zet de web op de vraag van §3.4 en haalt deze tak weg.
"""

from __future__ import annotations

import io
import zipfile

import pytest

from app.search import engine
from app.search.types import SearchPage, SearchParams, Suggestions
from tests.test_files import put, root

pytestmark = pytest.mark.anyio


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


async def test_de_vraag_van_070_gaat_altijd_naar_like(client, admin, monkeypatch):
    spion = Spion()
    monkeypatch.setattr(engine, "FTS", spion)
    headers, _ = admin
    top = (await root(client, headers))["folder"]["id"]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("in.txt", "inhoud")
    for name, data in (("archief.zip", buf.getvalue()), ("archief.jpg", b"geen foto")):
        assert (await put(client, headers, top, name, data)).status_code == 201

    for params, gevonden in (
        ({"q": "archief", "limit": 200}, {"archief.zip", "archief.jpg"}),
        ({"q": "archief", "limit": 51}, {"archief.zip", "archief.jpg"}),
        ({"q": "archief", "type": "archive"}, {"archief.zip"}),
        ({"type": "archive", "limit": 200}, {"archief.zip"}),
    ):
        r = await client.get("/api/v1/search", params=params, headers=headers)
        assert r.status_code == 200, (params, r.text)
        assert r.json()["engine"] == "like", params
        assert {h["node"]["name"] for h in r.json()["results"]} == gevonden, params
    assert spion.vragen == []

    # Binnen de grenzen van SearchParams: via select_engine, met een gecontroleerde vraag
    r = await client.get(
        "/api/v1/search", params={"q": "archief", "type": "image", "limit": 50}, headers=headers
    )
    assert r.status_code == 200, r.text
    assert r.json()["engine"] == "fts"
    [vraag] = spion.vragen
    assert (vraag.q, vraag.types, vraag.limit) == ("archief", ["image"], 50)
    assert SearchParams.model_validate(vraag.model_dump()) == vraag
