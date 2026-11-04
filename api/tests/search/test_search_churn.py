"""Churn (bouwontwerp fase 6, §4.3; Keuze 3, 12 en 25a): 2.000 willekeurige wijzigingen met een
vaste seed, langs de paden van de app: upload (ook in een gedeelde map, en dezelfde inhoud
nog eens), een map maken, kopie, hernoemen, verplaatsen, prullenbak in en uit, een nieuwe versie
en een versie terugzetten, tekst erbij en opnieuw geschreven (analysis.write_text), OCR in een
foto (photo_ocr, zoals X1 hem schrijft), een andere eigenaar, definitief wissen, en een gebruiker
wissen. Halverwege een herbouw in stukken, met wijzigingen tussen de stukken door.

Na elke 100 wijzigingen, en aan het eind:
- de boom klopt (num, pad, dead: migratie.check_tree);
- FTS5 zegt bij de integrity-check dat de index bij zijn inhoud past, en de index is term voor
  term gelijk aan een volledige herbouw (spike.herbouw_verschil);
- 42 vragen, voor elke gebruiker: FtsEngine geeft precies Referentie.zoek (eerst de hele woorden,
  dan de stukken van namen; niets dubbel, niets gedeeltelijk), en nergens search_access_mismatch.

Twee wijzigingen hebben geen pad in de app en gaan met de ORM of SQL: OCR in een foto (die
schrijft X1, golf 4) en een andere eigenaar. Die laatste verplaatst het bestand ook naar de
hoofdmap van de nieuwe eigenaar (een bestand van B in de boom van A bestaat in de app niet), en
geeft het een nieuwe naam: in twee UPDATEs. Gevonden met deze test: één UPDATE die owner_id én
search_name (of blob_id) verandert van een node met OCR van een foto, maakt de index kapot
("malformed"). node_au_owner wist dan photo_ocr (po_bd zegt 'delete' met de nieuwe naam) vóór
node_au_text de nieuwe rij schrijft, en die schrijft hem daarna nog eens. Geen pad in de app doet
dit; wie later eigendom laat overgaan, zet owner_id in een eigen UPDATE (0010 ligt vast).

De herbouw is die van spike S2 (test_spike_triggers.herbouw_in_stukken); de tweede stap van Z1
zet app.cli search-rebuild in de plaats. "churn" in de naam geeft de marker triggers: e2e/slim.sh
draait dit ook in python:3.12-slim (SQLite 3.46.1).
"""

from __future__ import annotations

import logging
import random
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

import anyio
import pytest
from sqlalchemy import text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app import analysis, jobs
from app.db import get_engine
from app.models import Node, User
from app.search import engine as engines
from app.search.types import SearchParams
from app.text import fold
from tests.factories import maker
from tests.search import referentie as R
from tests.search import spike
from tests.search.migratie import check_tree
from tests.search.test_spike_triggers import herbouw_in_stukken, pad
from tests.test_files import mkdir, put, root
from tests.test_shares import person, share_with
from tests.test_user_delete import block_and_delete

pytestmark = pytest.mark.anyio

SEED = 2025
STAPPEN = 2_000
ELKE = 100
HERBOUW = STAPPEN // 2
WEG = STAPPEN * 3 // 4  # dan wordt een gebruiker gewist
RUIM_MS = 60_000
ALLES = 10_000

# Woorden voor namen en tekst: met diakrieten, ß, een hoofdletter, korte woorden en cijfers
WOORDEN = [
    "factuur", "offerte", "jaarverslag", "notulen", "begroting", "café", "Straße", "groß",
    "strasse", "reis", "strand", "contract", "huur", "belasting", "recept", "kassabon",
    "rapport", "rapporteur", "plan", "planning", "budget", "overzicht", "één", "naïef",
    "Zoë", "de", "en", "op", "2025", "q3", "vergadering", "verslag", "foto", "zomer",
]  # fmt: skip
EXT = [".txt", ".pdf", ".jpg", ".docx", ".md", ""]
JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00"


def vragen() -> list[str]:
    """42 vragen: hele woorden, voorvoegsels, stukken van namen, korte woorden, twee woorden,
    frases, en operators die gewone tekst moeten blijven."""
    rng = random.Random(f"{SEED}:vragen")
    lang = [w for w in WOORDEN if len(w) >= 5]
    vast = ["café", "cafe", "straße", "strasse", "groß", "GROSS", "een", "naief", "zoe", "2025"]
    vast += ["de", "en", "op", "q3", "factuur AND", "plan NEAR budget", "rap*", "name:strand"]
    soorten = [
        lambda: rng.choice(lang),  # heel woord
        lambda: rng.choice(lang)[:3],  # voorvoegsel
        lambda: rng.choice(lang)[:4],
        lambda: rng.choice(lang)[1:5],  # stuk van een naam
        lambda: " ".join(rng.sample(WOORDEN, 2)),  # twee woorden
        lambda: '"{} {}"'.format(*rng.sample(lang, 2)),  # frase
    ]
    uit = dict.fromkeys(vast)
    while len(uit) < 42:
        uit[soorten[len(uit) % len(soorten)]()] = None
    return list(uit)


@dataclass
class Wereld:
    client: object
    settings: object
    rng: random.Random
    koppen: dict[uuid.UUID, dict[str, str]] = field(default_factory=dict)
    wortels: dict[uuid.UUID, str] = field(default_factory=dict)
    teller: int = 0
    inhoud: list[bytes] = field(default_factory=list)
    log: list[str] = field(default_factory=list)
    tellingen: dict[str, int] = field(default_factory=dict)

    # -- de database lezen

    async def rijen(self, sql: str, **args) -> list:
        async with get_engine(self.settings).connect() as conn:
            return (await conn.execute(text(sql), args)).all()

    async def ids(self, sql: str, **args) -> list[str]:
        return [str(uuid.UUID(hex=r[0])) for r in await self.rijen(sql, **args)]

    def kies(self, lijst):
        return self.rng.choice(lijst) if lijst else None

    def iemand(self) -> uuid.UUID:
        """Een gebruiker, op de volgorde van aanmaken (niet op id: die is willekeurig)."""
        return self.rng.choice(list(self.koppen))

    async def mappen(self, u: uuid.UUID) -> list[str]:
        """De levende mappen waar u in mag schrijven: de eigen, en die onder een deling om te
        bewerken."""
        return await self.ids(
            "SELECT n.id FROM node n WHERE n.kind = 'dir' AND n.dead = 0 AND (n.owner_id = :u "
            "OR EXISTS (SELECT 1 FROM share s JOIN node r ON r.id = s.node_id "
            "WHERE s.grantee_id = :u AND s.permission = 'write' AND r.dead = 0 "
            "AND n.mpath >= r.mpath AND n.mpath < substr(r.mpath, 1, length(r.mpath) - 1) || '0'"
            ")) ORDER BY n.num",
            u=u.hex,
        )

    async def eigen(self, u: uuid.UUID, kind: str | None = None, *, weg: bool = False) -> list:
        """De eigen nodes van u (zonder de hoofdmap); weg: die in de prullenbak staan."""
        soort = "AND kind = :k" if kind else ""
        staat = "deleted_at IS NOT NULL" if weg else "dead = 0"
        return await self.ids(
            f"SELECT id FROM node WHERE owner_id = :u AND parent_id IS NOT NULL {soort} "
            f"AND {staat} AND kind <> 'album' ORDER BY num",
            u=u.hex,
            k=kind,
        )

    # -- wat erin komt

    def naam(self) -> str:
        self.teller += 1
        a, b = self.rng.choice(WOORDEN), self.rng.choice(WOORDEN)
        return f"{a} {b} {self.teller}{self.rng.choice(EXT)}"

    def zin(self) -> str:
        return " ".join(self.rng.choice(WOORDEN) for _ in range(self.rng.randint(2, 12)))

    def data(self) -> bytes:
        """Meestal nieuwe inhoud; soms dezelfde als een eerdere (één blob, meer nodes)."""
        if self.inhoud and self.rng.random() < 0.2:
            return self.rng.choice(self.inhoud)
        self.teller += 1
        nieuw = f"inhoud {self.teller} {self.rng.random()}".encode()
        self.inhoud.append(nieuw)
        return nieuw

    laatste: str = ""

    def kijk(self, wat: str, r) -> None:
        """Een antwoord van de app: goed, of een weigering die bij een willekeurige keuze kan
        horen (bestaat niet meer, naam bezet, in zichzelf); nooit een fout van de server."""
        self.laatste = f"{wat} {r.request.method} {r.request.url.path} {r.status_code}"
        assert r.status_code < 500, (wat, r.status_code, r.text)
        assert r.status_code in (200, 201, 202, 204, 404, 409, 422), (wat, r.text)
        sleutel = f"{wat} {'ok' if r.status_code < 300 else r.status_code}"
        self.tellingen[sleutel] = self.tellingen.get(sleutel, 0) + 1


# --- De wijzigingen ---------------------------------------------------------------------------


async def upload(w: Wereld) -> None:
    u = w.iemand()
    doel = w.kies(await w.mappen(u))
    naam = w.naam()
    data = w.data()
    if naam.endswith(".jpg"):
        data = JPEG + data  # een foto (sniff kijkt naar de inhoud): dan kan er OCR bij
    w.kijk("upload", await put(w.client, w.koppen[u], doel, naam, data))


async def map_maken(w: Wereld) -> None:
    u = w.iemand()
    doel = w.kies(await w.mappen(u))
    r = await w.client.post(f"/api/v1/files/{doel}/folders", json={"name": w.naam()},
                            headers=w.koppen[u])  # fmt: skip
    w.kijk("map", r)


async def kopie(w: Wereld) -> None:
    u = w.iemand()
    # Een bestand, of een kleine map (hooguit 30 nodes eronder)
    bron = w.kies(
        await w.ids(
            "SELECT n.id FROM node n WHERE n.owner_id = :u AND n.parent_id IS NOT NULL "
            "AND n.dead = 0 AND (n.kind = 'file' OR (SELECT count(*) FROM node m WHERE "
            "m.mpath >= n.mpath AND m.mpath < substr(n.mpath, 1, length(n.mpath) - 1) || '0')"
            " <= 30) ORDER BY n.num",
            u=u.hex,
        )
    )
    doel = w.kies(await w.mappen(u))
    if bron is None:
        return
    r = await w.client.post(f"/api/v1/files/{bron}/copy", json={"parent_id": doel},
                            headers=w.koppen[u])  # fmt: skip
    w.kijk("kopie", r)


async def hernoemen(w: Wereld) -> None:
    u = w.iemand()
    node = w.kies(await w.eigen(u))
    if node is None:
        return
    r = await w.client.patch(f"/api/v1/files/{node}", json={"name": w.naam()}, headers=w.koppen[u])
    w.kijk("hernoemen", r)


async def verplaatsen(w: Wereld) -> None:
    u = w.iemand()
    node = w.kies(await w.eigen(u))
    doel = w.kies(await w.eigen(u, "dir") + [w.wortels[u]])
    if node is None:
        return
    r = await w.client.patch(f"/api/v1/files/{node}", json={"parent_id": doel},
                             headers=w.koppen[u])  # fmt: skip
    w.kijk("verplaatsen", r)


async def prullenbak(w: Wereld) -> None:
    u = w.iemand()
    node = w.kies(await w.eigen(u))
    if node is None:
        return
    w.kijk("prullenbak", await w.client.delete(f"/api/v1/files/{node}", headers=w.koppen[u]))


async def terugzetten(w: Wereld) -> None:
    u = w.iemand()
    node = w.kies(await w.eigen(u, weg=True))
    if node is None:
        return
    r = await w.client.post(f"/api/v1/files/{node}/restore", headers=w.koppen[u])
    w.kijk("terugzetten", r)


async def wissen(w: Wereld) -> None:
    u = w.iemand()
    node = w.kies(await w.eigen(u, weg=True))
    if node is None:
        return
    w.kijk("wissen", await w.client.delete(f"/api/v1/trash/{node}", headers=w.koppen[u]))


async def versie(w: Wereld) -> None:
    """Nieuwe inhoud (zoals de editor opslaat), of een oude versie terugzetten."""
    u = w.iemand()
    node = w.kies(await w.eigen(u, "file"))
    if node is None:
        return
    kop = w.koppen[u]
    if w.rng.random() < 0.3:
        r = await w.client.get(f"/api/v1/files/{node}/versions", headers=kop)
        w.kijk("versies", r)
        oud = w.kies([v["id"] for v in r.json()] if r.status_code == 200 else [])
        if oud is not None:
            r = await w.client.post(f"/api/v1/files/{node}/versions/{oud}/restore", headers=kop)
            w.kijk("versie terug", r)
            return
    r = await w.client.put(f"/api/v1/files/{node}/content", content=w.data(), headers=kop)
    w.kijk("versie", r)


async def tekst(w: Wereld) -> None:
    """De tekst van een inhoud (zoals Z2 hem schrijft): erbij, of opnieuw geschreven."""
    sql = "SELECT DISTINCT blob_id FROM node WHERE blob_id IS NOT NULL ORDER BY blob_id"
    blobs = [r[0] for r in await w.rijen(sql)]
    sha = w.kies(blobs)
    if sha is None:
        return
    velden = {"body": w.zin()}
    if w.rng.random() < 0.3:
        velden["meta"] = w.zin()
    if w.rng.random() < 0.2:
        velden["ocr"] = w.zin()
    async with maker(w.settings)() as session:
        await analysis.write_text(session, sha, **velden)
    w.tellingen["tekst"] = w.tellingen.get("tekst", 0) + 1


async def ocr(w: Wereld) -> None:
    """OCR in een foto (photo_ocr): erbij, opnieuw, of weg."""
    fotos = await w.rijen(
        "SELECT id, owner_id FROM node WHERE kind = 'file' AND mime LIKE 'image/%' ORDER BY num"
    )
    foto = w.kies(fotos)
    if foto is None:
        return
    er = await w.rijen("SELECT 1 FROM photo_ocr WHERE node_id = :n", n=foto[0])
    async with get_engine(w.settings).begin() as conn:
        if not er:
            await conn.execute(
                text(
                    "INSERT INTO photo_ocr (node_id, owner_id, text, words, angle, updated_at) "
                    "VALUES (:n, :o, :t, x'00', 0, :u)"
                ),
                {"n": foto[0], "o": foto[1], "t": w.zin(), "u": spike.NU.isoformat()},
            )
        elif w.rng.random() < 0.7:
            await conn.execute(text("UPDATE photo_ocr SET text = :t WHERE node_id = :n"),
                               {"t": w.zin(), "n": foto[0]})  # fmt: skip
        else:
            await conn.execute(text("DELETE FROM photo_ocr WHERE node_id = :n"), {"n": foto[0]})
    w.tellingen["ocr"] = w.tellingen.get("ocr", 0) + 1


async def eigenaar(w: Wereld) -> None:
    """Een andere eigenaar (geen pad in de app): het bestand gaat naar de hoofdmap van de nieuwe
    eigenaar, in één UPDATE."""
    u = w.iemand()
    node = w.kies(await w.eigen(u, "file"))
    ander = w.kies([v for v in w.koppen if v != u])
    if node is None or ander is None:
        return
    naam = f"overgedragen {w.naam()}"
    waar = Node.id == uuid.UUID(node)
    async with maker(w.settings)() as session:
        # Eerst eigenaar en map, dan de naam (die mag botsen in de nieuwe map): twee UPDATEs.
        # In één UPDATE met owner_id en search_name maken de triggers van 0010 de index kapot
        # als de node OCR van een foto heeft (zie de docstring bovenaan).
        await session.execute(
            update(Node).where(waar).values(owner_id=ander, parent_id=uuid.UUID(w.wortels[ander]))
        )
        # Een UPDATE van Core: het event op Node.name (dat search_name zet) gaat dan niet af
        await session.execute(update(Node).where(waar).values(name=naam, search_name=fold(naam)))
        await session.commit()
    w.tellingen["eigenaar"] = w.tellingen.get("eigenaar", 0) + 1


WIJZIGINGEN: list[tuple[Callable[[Wereld], Awaitable[None]], int]] = [
    (upload, 22),
    (map_maken, 7),
    (kopie, 6),
    (hernoemen, 9),
    (verplaatsen, 8),
    (prullenbak, 8),
    (terugzetten, 6),
    (wissen, 4),
    (versie, 8),
    (tekst, 14),
    (ocr, 6),
    (eigenaar, 2),
]


async def een_wijziging(w: Wereld) -> None:
    doe = w.rng.choices([f for f, _ in WIJZIGINGEN], [g for _, g in WIJZIGINGEN])[0]
    w.log.append(doe.__name__)
    await doe(w)


# --- De controles -----------------------------------------------------------------------------


async def de_index_klopt(w: Wereld, waar: str) -> None:
    async with get_engine(w.settings).connect() as conn:
        await conn.run_sync(check_tree)
    verschil = await anyio.to_thread.run_sync(lambda: spike.herbouw_verschil(pad(w.settings)))
    assert verschil == {}, (waar, verschil, w.log[-20:])


async def de_referentie_klopt(w: Wereld, waar: str) -> int:
    """Elke gebruiker, elke vraag: FtsEngine geeft precies wat de referentie zegt."""
    treffers = 0
    async with AsyncSession(get_engine(w.settings), expire_on_commit=False) as db:
        ref = await (await db.connection()).run_sync(R.Referentie.lees)
        for u in w.koppen:
            user = await db.get(User, u)
            for q in VRAGEN:
                verwacht = ref.zoek(u, q)
                p = SearchParams.model_construct(q=q, limit=ALLES, offset=0)
                page = await engines.FtsEngine().search(db, user, p)
                ids = [h.node.id for h in page.results]
                plek = (waar, user.email, q, w.log[-5:])
                assert page.engine == "fts" and not page.partial and not page.more, plek
                assert len(ids) == len(set(ids)), plek
                woorden = set(ids[: len(verwacht.woorden)])
                if set(ids) != verwacht.alles or woorden != verwacht.woorden:
                    anders = (set(ids) ^ verwacht.alles) | (woorden ^ verwacht.woorden)
                    raise AssertionError((plek, await _uitleg(db, anders, verwacht)))
                treffers += len(ids)
    return treffers


async def _uitleg(db: AsyncSession, ids: set[uuid.UUID], verwacht: R.Uitkomst) -> list:
    """Wat er van deze nodes in de database en in de index staat (bij een verschil)."""
    uit = []
    for node_id in sorted(ids):
        rij = (await db.execute(text(
            "SELECT n.name, n.kind, n.owner_id, n.dead, n.num, n.search_name, t.body, t.ocr, "
            "t.meta, (SELECT text FROM photo_ocr WHERE node_id = n.id), "
            "(SELECT name || ' | ' || body || ' | ' || ocr || ' | ' || meta || ' | ' || pocr "
            "FROM fts_node WHERE rowid = n.num) "
            "FROM node n LEFT JOIN blob_text t ON t.blob_sha = n.blob_id WHERE n.id = :id"
        ), {"id": node_id.hex})).first()  # fmt: skip
        soort = "woord" if node_id in verwacht.woorden else "naam" if node_id in verwacht.namen \
            else "niet"  # fmt: skip
        uit.append((str(node_id), soort, tuple(rij) if rij else None))
    return uit


VRAGEN = vragen()


async def test_churn(client, admin, settings, caplog, monkeypatch):
    caplog.set_level(logging.WARNING, logger="platform.search")
    # Een ruime grens: het gaat hier om wat er gevonden wordt (zie test_search_reference). In
    # de test zelf gezet: een ContextVar uit een fixture zonder async komt niet in de test.
    monkeypatch.setattr(settings, "search_deadline_ms", RUIM_MS)
    token = engines.SETTINGS.set(settings)
    try:
        await _churn(client, admin, settings, caplog)
    finally:
        engines.SETTINGS.reset(token)


async def _churn(client, admin, settings, caplog) -> None:
    jonas_kop, _ = admin
    w = Wereld(client, settings, random.Random(SEED))
    jonas = uuid.UUID((await client.get("/api/v1/auth/me", headers=jonas_kop)).json()["id"])
    w.koppen[jonas] = jonas_kop
    nieuw = []
    for naam in ("anna", "bram", "cas"):
        kop, uid = await person(client, jonas_kop, f"{naam}@example.nl")
        nieuw.append(uuid.UUID(uid))
        w.koppen[nieuw[-1]] = kop
    anna, bram, cas = nieuw
    w.wortels = {u: (await root(client, kop))["folder"]["id"] for u, kop in w.koppen.items()}
    # Een beginboom, en delingen: om te bewerken (dan komen er ook nodes van een ander in een
    # map die je ziet) en om te bekijken
    for u in w.koppen:
        for _ in range(3):
            await mkdir(client, w.koppen[u], w.wortels[u], w.naam())
    gedeeld = await mkdir(client, w.koppen[anna], w.wortels[anna], "Gedeeld werk")
    await share_with(client, w.koppen[anna], gedeeld, user_id=str(bram), permission="write")
    await share_with(client, w.koppen[anna], gedeeld, user_id=str(jonas), permission="read")
    van_cas = await mkdir(client, w.koppen[cas], w.wortels[cas], "Voor iedereen")
    for u in (anna, bram, jonas):
        await share_with(client, w.koppen[cas], van_cas, user_id=str(u), permission="write")

    treffers = []
    tijd = {"wijzigingen": 0.0, "index": 0.0, "referentie": 0.0}
    for stap in range(1, STAPPEN + 1):
        begin = time.perf_counter()
        await een_wijziging(w)
        tijd["wijzigingen"] += time.perf_counter() - begin
        if stap == HERBOUW:
            # Een herbouw in stukken van 50 nums, met wijzigingen tussen de stukken door

            async def tussendoor(_hi: int) -> None:
                for _ in range(3):
                    await een_wijziging(w)

            await herbouw_in_stukken(get_engine(settings), stuk=50, na_elk_stuk=tussendoor)
        if stap == WEG:
            # Een gebruiker wissen: cas, met zijn map die met anderen gedeeld is
            await block_and_delete(client, jonas_kop, str(cas))
            await jobs.run_pending(settings)
            del w.koppen[cas]
            w.log.append("gebruiker wissen")
        if stap % ELKE == 0:
            begin = time.perf_counter()
            await de_index_klopt(w, f"na {stap}")
            tijd["index"] += time.perf_counter() - begin
            begin = time.perf_counter()
            treffers.append(await de_referentie_klopt(w, f"na {stap}"))
            tijd["referentie"] += time.perf_counter() - begin
    print("churn:", {k: round(v, 1) for k, v in tijd.items()}, w.tellingen)
    # Alles is langsgekomen, en de vragen vinden echt iets
    assert {k.split()[0] for k in w.tellingen if k.endswith("ok")} >= {
        "upload", "map", "kopie", "hernoemen", "verplaatsen", "prullenbak", "terugzetten",
        "wissen", "versie",
    }, w.tellingen  # fmt: skip
    assert w.tellingen.get("tekst", 0) > 100 and w.tellingen.get("ocr", 0) > 20, w.tellingen
    assert w.tellingen.get("eigenaar", 0) > 5, w.tellingen
    assert min(treffers) > 100, treffers
    assert await w.rijen("SELECT 1 FROM node WHERE owner_id = :c", c=cas.hex) == []
    assert "search_access_mismatch" not in caplog.text
