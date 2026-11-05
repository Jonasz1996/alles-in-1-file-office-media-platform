"""Elk filter van Keuze 43 en elke combinatie van twee, tegen de referentie (bouwontwerp fase 6,
Z6; de Klaar-als-regel "Filters: type, datum, persoon, locatie, tag, bibliotheek").

Eén register, drie plekken (Keuze 43): GET /search (in beide motoren: FtsEngine en, met
PLATFORM_SEARCH_INDEX=uit, LikeFallback), GET /photos met /photos/months (de tijdlijn: eigen,
levende foto's en video's met een opnamedag) en de regel van een slim album
(albums_rule.check en matches; q in de motor van die test). Wat er moet komen, rekent gewoon
Python uit: wat je ziet uit tests/search/referentie.py (Keuze 4; met q Referentie.zoek voor
FtsEngine en op_naam voor LikeFallback), en per filter de regel uit de tabel van Keuze 43:

- type: mime.ftype() van de node; meer keer is OF;
- from en to: de opnamedag voor een foto of video die er een heeft, anders de dag van
  modified_at in settings.timezone (een verslag van 23:30 UTC op oudjaar is nieuwjaarsdag);
- person: eigen, levende bestanden met elk van die eigen personen erop (EN); een persoon van een
  ander geeft niets;
- tag: elk van die eigen tags erop (EN);
- library en in: onder die map (de keten omhoog), niet de map zelf.

place, country en camera meldt Z3 aan; tot dan zijn ze 501 not_built, ook op /photos. Z3 voegt
ze hier toe (een regel in past() en waarden in WAARDEN), dan doen ze vanzelf mee in de paren.
"""

from __future__ import annotations

import itertools
import uuid
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from fastapi import HTTPException
from sqlalchemy import update

from app import albums_rule, analysis, mime
from app.db import get_engine
from app.models import MediaLibrary, Node, NodeTag, Person, PhotoPerson, Share, Tag, User
from app.people import search as people_search
from app.search import limits
from app.search.filters import DATED
from app.search.types import SearchRule
from app.text import fold
from tests.factories import code_of, create_album, create_file, create_folder, create_user, maker
from tests.search import referentie as R
from tests.test_photos import login

pytestmark = pytest.mark.anyio

OFFICE = "application/vnd.openxmlformats-officedocument."
STANDAARD_DAG = datetime(2024, 6, 1, 12, tzinfo=UTC)
# De vragen naast een filter: een heel woord (ook in de tekst van het verslag), een voorvoegsel
# en een stuk van een naam
VRAGEN = ("feest", "fee", "ee")


@pytest.fixture(params=["fts", "like"])
def motor(request, settings, monkeypatch) -> str:
    if request.param == "like":
        monkeypatch.setattr(settings, "search_index", "uit")
    return request.param


@pytest.fixture(autouse=True)
def _ruim(settings, monkeypatch):
    """Het filter person staat in het register (een andere test kan filters.reset() gedaan
    hebben); geen grens van de snelheid per gebruiker of van ronde A: het gaat hier om wat er
    gevonden wordt."""
    people_search.register()
    monkeypatch.setattr(limits, "RATE", {"search": 100_000, "suggest": 100_000})
    monkeypatch.setattr(settings, "search_deadline_ms", 60_000)


# --- De wereld ------------------------------------------------------------------------------


@dataclass
class Wereld:
    ids: dict[str, uuid.UUID] = field(default_factory=dict)
    ref: R.Referentie | None = None
    soort: dict[uuid.UUID, str] = field(default_factory=dict)
    dag: dict[uuid.UUID, int | None] = field(default_factory=dict)
    gewijzigd: dict[uuid.UUID, datetime] = field(default_factory=dict)
    tags: dict[uuid.UUID, set[uuid.UUID]] = field(default_factory=dict)
    tag_van: dict[uuid.UUID, uuid.UUID] = field(default_factory=dict)
    op_foto: dict[uuid.UUID, set[uuid.UUID]] = field(default_factory=dict)
    persoon_van: dict[uuid.UUID, uuid.UUID] = field(default_factory=dict)
    bieb: dict[uuid.UUID, uuid.UUID] = field(default_factory=dict)
    namen: dict[uuid.UUID, str] = field(default_factory=dict)

    def naam(self, ids) -> list[str]:
        return sorted(self.namen.get(i, str(i)) for i in ids)


def _foto(dag: tuple[int, ...]) -> datetime:
    return datetime(*dag, tzinfo=UTC)


async def _bouw(settings) -> Wereld:
    """Eva met foto's, video's, documenten, een bibliotheek Muziek, de map Gedeeld (met Bram),
    een map in de prullenbak, een foto in de prullenbak, een album; Bram met eigen foto's, de
    map Voor eva (met Eva gedeeld, een bibliotheek) en de map Prive (een bibliotheek die Eva niet
    ziet). Personen en tags van allebei."""
    w = Wereld()
    ids = w.ids
    async with maker(settings)() as s:
        eva = await create_user(s, "eva@example.nl")
        bram = await create_user(s, "bram@example.nl")
        ids |= {"eva": eva.id, "bram": bram.id}

        async def map_(key, owner, parent=None, *, deleted=False, name=None):
            node = await create_folder(s, owner, parent, name or key, deleted=deleted)
            ids[key] = node.id
            return node.id

        async def bestand(key, owner, parent, soort, *, taken=None, modified=None, deleted=False):
            node = await create_file(
                s, owner, parent, key, data=f"{owner.id}:{key}".encode(), mime=soort,
                taken_at=_foto(taken) if taken else None, deleted=deleted,
            )  # fmt: skip
            ids[key] = node.id
            if modified is not None:
                node.modified_at = _foto(modified)
            return node

        fotos = await map_("Fotos", eva)
        j2023 = await map_("2023", eva, fotos)
        j2024 = await map_("2024", eva, fotos)
        weg = await map_("Weg", eva, fotos, deleted=True)
        docs = await map_("Documenten", eva)
        muziek = await map_("Muziek", eva)
        album1 = await map_("Album 1", eva, muziek)
        gedeeld = await map_("Gedeeld", eva)
        await bestand("strand-1.jpg", eva, j2023, "image/jpeg", taken=(2023, 7, 1, 10))
        await bestand("strand-2.jpg", eva, j2023, "image/jpeg", taken=(2023, 7, 2, 10))
        await bestand("zee.mp4", eva, j2023, "video/mp4", taken=(2023, 7, 2, 18))
        await bestand("feest.jpg", eva, j2024, "image/jpeg", taken=(2024, 3, 14, 20))
        await bestand("feest-2.png", eva, j2024, "image/png", taken=(2024, 3, 15, 0, 30))
        await bestand("feest-avond.jpg", eva, j2024, "image/jpeg", taken=(2024, 3, 13, 23, 30))
        # Zonder opnamedag: niet op de tijdlijn; from en to op modified_at (00:30 in Amsterdam)
        await bestand("scan.jpg", eva, j2024, "image/jpeg", modified=(2024, 3, 13, 23, 30))
        await bestand("weg-feest.jpg", eva, weg, "image/jpeg", taken=(2024, 3, 14, 9))
        await bestand(
            "verwijderd.jpg", eva, j2024, "image/jpeg", taken=(2024, 3, 14, 9), deleted=True
        )
        await bestand("feest-factuur.pdf", eva, docs, "application/pdf", modified=(2024, 3, 14, 10))
        verslag = await bestand(
            "verslag.docx", eva, docs, OFFICE + "wordprocessingml.document",
            modified=(2023, 12, 31, 23, 30),
        )  # fmt: skip
        await bestand("begroting.xlsx", eva, docs, OFFICE + "spreadsheetml.sheet")
        await bestand("cijfers.csv", eva, docs, "text/csv", modified=(2024, 3, 14, 8))
        await bestand("presentatie.pptx", eva, docs, OFFICE + "presentationml.presentation")
        await bestand("notitie.txt", eva, docs, "text/plain", modified=(2024, 3, 15, 8))
        await bestand("archief.zip", eva, docs, "application/zip")
        await bestand("ruw.bin", eva, docs, None)
        await bestand("liedje.mp3", eva, muziek, "audio/mpeg")
        await bestand("hoes.jpg", eva, muziek, "image/jpeg", taken=(2022, 5, 1, 12))
        await bestand("nummer.flac", eva, album1, "audio/flac")
        await bestand("samen.jpg", eva, gedeeld, "image/jpeg", taken=(2024, 3, 14, 12))
        await bestand("samen-feest.mp4", eva, gedeeld, "video/mp4", taken=(2024, 3, 14, 13))
        album = await create_album(s, eva, "Vakantie")
        ids["Vakantie"] = album.id

        voor_eva = await map_("Voor eva", bram)
        prive = await map_("Prive", bram)
        await bestand("bram-feest.jpg", bram, None, "image/jpeg", taken=(2024, 3, 14, 15))
        await bestand("film.mp4", bram, voor_eva, "video/mp4", taken=(2024, 3, 14, 16))
        await bestand("feest-bram.pdf", bram, voor_eva, "application/pdf",
                      modified=(2024, 3, 14, 11))  # fmt: skip
        await bestand("geheim.jpg", bram, prive, "image/jpeg", taken=(2024, 3, 14, 17))

        # Alles een vaste modified_at, tenzij hierboven gezet
        await s.flush()
        await s.execute(
            update(Node)
            .where(Node.modified_at > datetime(2025, 1, 1, tzinfo=UTC))
            .values(modified_at=STANDAARD_DAG)
            .execution_options(synchronize_session=False)
        )
        for key, owner, root in (("Muziek-bieb", eva, muziek), ("Films van Bram", bram, voor_eva),
                                 ("Prive-bieb", bram, prive)):  # fmt: skip
            ids[key] = uuid.uuid4()
            s.add(MediaLibrary(id=ids[key], owner_id=owner.id, root_id=root, kind="videos",
                               name=key))  # fmt: skip
        s.add(Share(node_id=gedeeld, owner_id=eva.id, grantee_id=bram.id, permission="read"))
        s.add(Share(node_id=voor_eva, owner_id=bram.id, grantee_id=eva.id, permission="read"))
        for key, owner in (("anna", eva), ("bert", eva), ("carla", bram)):
            person = Person(owner_id=owner.id, name=key.capitalize(), search_name=fold(key))
            s.add(person)
            await s.flush()
            ids[key] = person.id
        for key, owner in (("vakantie", eva), ("familie", eva), ("bram-tag", bram)):
            tag = Tag(owner_id=owner.id, name=key)
            s.add(tag)
            await s.flush()
            ids[key] = tag.id
        await s.flush()
        personen = {
            "feest.jpg": ("anna", "bert"),
            "feest-2.png": ("anna",),
            "zee.mp4": ("bert",),
            "scan.jpg": ("anna",),
            "weg-feest.jpg": ("anna",),
            "verwijderd.jpg": ("anna", "bert"),
            "hoes.jpg": ("anna",),
            "samen.jpg": ("anna",),
            "bram-feest.jpg": ("carla",),
            "geheim.jpg": ("carla",),
        }
        for node, wie in personen.items():
            for p in wie:
                owner = bram.id if p == "carla" else eva.id
                s.add(PhotoPerson(person_id=ids[p], node_id=ids[node], owner_id=owner,
                                  source="hand", created_at=STANDAARD_DAG))  # fmt: skip
        tags = {
            "strand-2.jpg": ("vakantie",),
            "feest.jpg": ("vakantie", "familie"),
            "feest-2.png": ("familie",),
            "feest-avond.jpg": ("vakantie",),
            "weg-feest.jpg": ("vakantie", "familie"),
            "verwijderd.jpg": ("vakantie",),
            "feest-factuur.pdf": ("vakantie",),
            "begroting.xlsx": ("familie",),
            "samen.jpg": ("familie", "bram-tag"),
            "film.mp4": ("vakantie", "familie"),
            "bram-feest.jpg": ("bram-tag",),
        }
        for node, welke in tags.items():
            for t in welke:
                s.add(NodeTag(node_id=ids[node], tag_id=ids[t]))
        await s.commit()
        sha = verslag.blob_id
    async with maker(settings)() as s:
        await analysis.write_text(s, sha, body="verslag van het feest in maart")
    async with get_engine(settings).connect() as conn:
        await conn.run_sync(lambda c: _lees(c, w))
    return w


def _lees(conn, w: Wereld) -> None:
    """De referentie (wat je ziet, en q) en wat de filters nodig hebben, rechtstreeks uit de
    tabellen."""
    sql = conn.exec_driver_sql
    w.ref = R.Referentie.lees(conn)
    u = R._uuid
    for node_id, kind, soort, dag, gewijzigd, naam in sql(
        "SELECT id, kind, mime, taken_day, modified_at, name FROM node"
    ):
        n = u(node_id)
        w.soort[n] = mime.ftype(soort, kind)
        w.dag[n] = dag
        w.gewijzigd[n] = R._tijd(gewijzigd)
        w.namen[n] = naam or "(hoofdmap)"
    for node_id, tag_id in sql("SELECT node_id, tag_id FROM node_tag"):
        w.tags.setdefault(u(node_id), set()).add(u(tag_id))
    w.tag_van = {u(t): u(o) for t, o in sql("SELECT id, owner_id FROM tag")}
    for person_id, node_id in sql("SELECT person_id, node_id FROM photo_person"):
        w.op_foto.setdefault(u(node_id), set()).add(u(person_id))
    w.persoon_van = {u(p): u(o) for p, o in sql("SELECT id, owner_id FROM person")}
    w.bieb = {u(i): u(r) for i, r in sql("SELECT id, root_id FROM media_library")}


# --- De referentie van de filters -------------------------------------------------------------


def _de_dag(w: Wereld, n: uuid.UUID, zone: str) -> date:
    dag = w.dag[n]
    if w.soort[n] in DATED and dag is not None:
        return date(dag // 10_000, dag // 100 % 100, dag % 100)
    return w.gewijzigd[n].astimezone(ZoneInfo(zone)).date()


def past(w: Wereld, user: uuid.UUID, n: uuid.UUID, f: dict[str, Any], zone: str) -> bool:
    """Past node n op de filters f (de namen van de query string), voor user?"""
    ref = w.ref
    assert ref is not None
    node = ref.nodes[n]
    if "type" in f and w.soort[n] not in f["type"]:
        return False
    if "from" in f and _de_dag(w, n, zone) < date.fromisoformat(f["from"]):
        return False
    if "to" in f and _de_dag(w, n, zone) > date.fromisoformat(f["to"]):
        return False
    if "person" in f:
        if node.owner != user or node.kind != "file" or not ref.levend(n):
            return False
        for p in f["person"]:
            if p not in w.op_foto.get(n, ()) or w.persoon_van.get(p) != user:
                return False
    for t in f.get("tag", ()):
        if t not in w.tags.get(n, ()) or w.tag_van.get(t) != user:
            return False
    for key in ("library", "in"):
        if key in f:
            top = w.bieb[f[key]] if key == "library" else f[key]
            if n == top or top not in ref.keten(n):
                return False
    return True


def gezocht(w: Wereld, user: uuid.UUID, q: str, motor: str) -> set[uuid.UUID]:
    """Wat /search zonder filters geeft: alles wat je ziet, of met q de treffers van die motor."""
    assert w.ref is not None
    if not q:
        return w.ref.zichtbaar(user)
    return w.ref.zoek(user, q).alles if motor == "fts" else w.ref.op_naam(user, q)


def tijdlijn(w: Wereld, user: uuid.UUID) -> set[uuid.UUID]:
    """De tijdlijn: eigen bestanden met een opnamedag, levend, onder de eigen hoofdmap."""
    ref = w.ref
    assert ref is not None
    root = ref.wortels[user]
    return {
        n
        for n, node in ref.nodes.items()
        if node.owner == user
        and node.kind == "file"
        and w.dag[n] is not None
        and ref.levend(n)
        and root in ref.keten(n)
    }


def regel(w: Wereld, user: uuid.UUID, q: str, motor: str) -> set[uuid.UUID]:
    """Waar de regel van een slim album uit kiest: eigen, levende foto's en video's met een
    opnamedag; met q de treffers van de motor."""
    ref = w.ref
    assert ref is not None
    eigen = {
        n
        for n, node in ref.nodes.items()
        if node.owner == user
        and node.kind == "file"
        and ref.levend(n)
        and w.soort[n] in DATED
        and w.dag[n] is not None
    }
    if not q:
        return eigen
    return eigen & (ref.zoek(user, q).alles if motor == "fts" else ref.op_naam(user, q))


def regel_geldig(w: Wereld, user: uuid.UUID, f: dict[str, Any]) -> bool:
    """albums_rule.check: in een eigen map, alleen eigen tags en personen (de bibliotheek ziet
    user hier altijd)."""
    assert w.ref is not None
    if "in" in f and w.ref.nodes[f["in"]].owner != user:
        return False
    if any(w.persoon_van.get(p) != user for p in f.get("person", ())):
        return False
    return all(w.tag_van.get(t) == user for t in f.get("tag", ()))


# --- De waarden en de combinaties -----------------------------------------------------------


def waarden(w: Wereld, wie: str) -> dict[str, list[dict[str, Any]]]:
    """Per filter de waarden die user mag vragen (geen 404): Eva alles, Bram een deel."""
    i = w.ids
    if wie == "eva":
        return {
            "type": [
                {"type": ["image"]},
                {"type": ["video", "pdf", "spreadsheet", "audio"]},
                {"type": ["folder", "document", "other"]},
                {"type": ["presentation"]},
            ],
            "dates": [
                {"from": "2024-03-14"},
                {"to": "2023-12-31"},
                {"from": "2024-03-14", "to": "2024-03-14"},
            ],
            "person": [
                {"person": [i["anna"]]},
                {"person": [i["anna"], i["bert"]]},
                {"person": [i["carla"]]},
            ],
            "tag": [{"tag": [i["vakantie"]]}, {"tag": [i["vakantie"], i["familie"]]}],
            "library": [{"library": i["Muziek-bieb"]}, {"library": i["Films van Bram"]}],
            "in": [{"in": i["Fotos"]}, {"in": i["Gedeeld"]}, {"in": i["Voor eva"]}],
        }
    return {
        "type": [{"type": ["image", "pdf"]}],
        "dates": [{"from": "2024-03-14", "to": "2024-03-14"}],
        "person": [{"person": [i["carla"]]}, {"person": [i["anna"]]}],
        "tag": [{"tag": [i["bram-tag"]]}],
        "library": [{"library": i["Films van Bram"]}],
        "in": [{"in": i["Voor eva"]}, {"in": i["Gedeeld"]}],
    }


def combinaties(per: dict[str, list[dict[str, Any]]]) -> Iterator[dict[str, Any]]:
    """Elk filter alleen, en elke combinatie van twee filters (met elk paar waarden)."""
    for lijst in per.values():
        yield from lijst
    for a, b in itertools.combinations(per, 2):
        for x, y in itertools.product(per[a], per[b]):
            yield {**x, **y}


def query(f: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in f.items():
        out[key] = [str(v) for v in value] if isinstance(value, list) else str(value)
    return out


# --- De vergelijking ------------------------------------------------------------------------


async def zoek(client, headers, motor: str, params: dict) -> set[uuid.UUID]:
    r = await client.get("/api/v1/search", params={**params, "limit": 50}, headers=headers)
    assert r.status_code == 200, (params, r.text)
    page = r.json()
    assert (page["engine"], page["partial"], page["more"]) == (motor, False, False), params
    found = [uuid.UUID(h["node"]["id"]) for h in page["results"]]
    assert len(found) == len(set(found)), params
    return set(found)


async def foto_s(client, headers, params: dict) -> tuple[set[uuid.UUID], list[dict]]:
    r = await client.get("/api/v1/photos", params=params, headers=headers)
    assert r.status_code == 200, (params, r.text)
    page = r.json()
    assert page["next"] is None, params
    r = await client.get("/api/v1/photos/months", params=params, headers=headers)
    assert r.status_code == 200, (params, r.text)
    return {uuid.UUID(item["id"]) for item in page["items"]}, r.json()["months"]


def maanden(w: Wereld, ids: set[uuid.UUID]) -> list[dict]:
    per = Counter(w.dag[n] // 100 for n in ids)  # type: ignore[operator]
    return [{"month": f"{m // 100:04d}-{m % 100:02d}", "count": per[m]} for m in sorted(per)[::-1]]


async def vergelijk(
    client, settings, w: Wereld, wie: str, motor: str, vragen
) -> tuple[list[str], int]:
    """Elke vraag (q en filters) op /search, /photos en de regel: de verschillen met de
    referentie (leesbaar), en hoeveel treffers de referentie op /search verwachtte."""
    user_id = w.ids[wie]
    headers = await login(client, f"{wie}@example.nl")
    zone = settings.timezone
    fout: list[str] = []
    treffers = 0
    album_id = uuid.uuid4()
    async with maker(settings)() as db:
        owner = await db.get(User, user_id)
        assert owner is not None
        for q, f in vragen:
            waar = f"{wie} q={q!r} {sorted(f)} {query(f)}"
            params = query(f) | ({"q": q} if q else {})
            # /search
            verwacht = {n for n in gezocht(w, user_id, q, motor) if past(w, user_id, n, f, zone)}
            treffers += len(verwacht)
            kreeg = await zoek(client, headers, motor, params)
            if kreeg != verwacht:
                fout.append(f"/search {waar}: te veel {w.naam(kreeg - verwacht)}, "
                            f"te weinig {w.naam(verwacht - kreeg)}")  # fmt: skip
            # De regel van een slim album
            rule = SearchRule.model_validate({"q": q, **f})
            if regel_geldig(w, user_id, f):
                await albums_rule.check(db, owner, rule)
                verwacht = {n for n in regel(w, user_id, q, motor) if past(w, user_id, n, f, zone)}
                kreeg = set(await albums_rule.matches(db, settings, owner, album_id, rule))
                if kreeg != verwacht:
                    fout.append(f"regel {waar}: te veel {w.naam(kreeg - verwacht)}, "
                                f"te weinig {w.naam(verwacht - kreeg)}")  # fmt: skip
            else:
                with pytest.raises(HTTPException) as e:
                    await albums_rule.check(db, owner, rule)
                assert e.value.status_code == 400, waar
            if q:
                continue
            # /photos en /photos/months
            verwacht = {n for n in tijdlijn(w, user_id) if past(w, user_id, n, f, zone)}
            kreeg, per_maand = await foto_s(client, headers, params)
            if kreeg != verwacht or per_maand != maanden(w, verwacht):
                fout.append(
                    f"/photos {waar}: te veel {w.naam(kreeg - verwacht)}, "
                    f"te weinig {w.naam(verwacht - kreeg)}, maanden {per_maand}"
                )
    return fout, treffers


async def test_elk_filter_en_elk_paar_tegen_de_referentie(client, settings, motor):
    w = await _bouw(settings)
    vragen = [("", f) for f in combinaties(waarden(w, "eva"))]
    # Met q: elk filter alleen
    vragen += [(q, f) for q in VRAGEN for lijst in waarden(w, "eva").values() for f in lijst]
    fout, treffers = await vergelijk(client, settings, w, "eva", motor, vragen)
    assert fout == [], "\n".join(fout)
    # De vragen vinden echt iets: anders bewijst de vergelijking weinig
    assert treffers > 250, treffers


async def test_een_ander_ziet_alleen_het_zijne(client, settings, motor):
    """Bram: zijn eigen personen en tags, de map die Eva met hem deelt; Eva's personen geven
    hem niets, haar tags kent hij niet."""
    w = await _bouw(settings)
    vragen = [("", f) for f in combinaties(waarden(w, "bram"))]
    vragen += [("feest", f) for lijst in waarden(w, "bram").values() for f in lijst]
    fout, treffers = await vergelijk(client, settings, w, "bram", motor, vragen)
    assert fout == [], "\n".join(fout)
    assert treffers > 30, treffers


async def test_de_filters_vinden_echt_iets(client, settings):
    """Geen lege vergelijking: elk filter alleen geeft op /search en /photos iets, en een
    persoon of tag van een ander niets."""
    w = await _bouw(settings)
    eva = await login(client, "eva@example.nl")
    for naam, lijst in waarden(w, "eva").items():
        for f in lijst:
            gevonden = await zoek(client, eva, "fts", query(f))
            if f.get("person") == [w.ids["carla"]]:
                assert gevonden == set(), f
            else:
                assert gevonden, (naam, f)
    feest = await zoek(client, eva, "fts", {"q": "feest", "type": "document"})
    assert w.naam(feest) == ["verslag.docx"]


async def test_wat_je_niet_ziet_is_404_en_z3_is_501(client, settings, motor):
    w = await _bouw(settings)
    eva = await login(client, "eva@example.nl")
    i = w.ids
    fouten = [
        ({"tag": i["bram-tag"]}, 404, "tag_not_found"),
        ({"library": i["Prive-bieb"]}, 404, None),
        ({"library": uuid.uuid4()}, 404, None),
        ({"in": i["Prive"]}, 404, "not_found"),
        ({"in": i["Weg"]}, 404, "folder_not_found"),
        ({"place": 2759794}, 501, "not_built"),
        ({"country": "NL"}, 501, "not_built"),
        ({"camera": "canon eos"}, 501, "not_built"),
    ]
    for f, status, code in fouten:
        for pad in ("/api/v1/search", "/api/v1/photos", "/api/v1/photos/months"):
            r = await client.get(pad, params=query(f), headers=eva)
            assert r.status_code == status, (pad, f, r.text)
            if code is not None:
                assert code_of(r) == code, (pad, f, r.text)
    # Een onbekende soort: 422, zoals bij zoeken
    for pad in ("/api/v1/photos", "/api/v1/photos/months"):
        r = await client.get(pad, params={"type": "archief"}, headers=eva)
        assert r.status_code == 422, r.text
