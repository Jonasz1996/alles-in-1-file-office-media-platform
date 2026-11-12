"""De vaste wereld van de toegangsmatrix (bouwontwerp fase 6, §4.2): Eva, Bram, Cas, Gijs,
Dirk, Ada, Fien, Hans en Iris, met hun mappen, delingen, albums en bestanden. Gedeeld door
test_search_access.py, tests/geo/test_geo_access.py, tests/people/test_people_privacy.py,
test_own_live.py en de e2e test_zoek_toegang.py (via e2e/search_harness.py).

    from tests.search import wereld
    w = await wereld.bouw_async(settings)          # API-tests: de database van de test
    w = wereld.bouw(conn, blob_dir)                # e2e: een synchrone verbinding (seed.connect)
    w.ids["foto"], w.users["bram"], w.email("bram"), wereld.WACHTWOORD

Wat erin staat (de woorden zijn uniek, zodat een test precies weet wat hij hoort te vinden):

- **Eva** (eigenaar, locatie "mij"). `Werk` is gedeeld met Bram (lezen), met groep `Team`
  (Cas) en met gast Gijs; ook met Fien (verlopen) en Hans (nog geldig: intrekken() trekt hem
  in, op het moment dat de test kiest). `Werk/Inbox` is apart gedeeld met Bram, met schrijven
  (het dubbele pad). `Werk/Foto's` heeft een publieke link.
- In `Werk`: `Verslag kwartaal.docx` met TEKST_WOORD in de tekst; `Bon scan.pdf`, een scan
  met OCR_WOORD alleen in het beeld (geen tekst: X1 moet hem lezen); `Oud verslag.txt` in de
  prullenbak (met TEKST_WOORD en PRULLENBAK_WOORD); de map `Verhuisd` met `Plan.txt`
  (VERHUIS_WOORD), die verhuis() naar Eva's eigen map zet.
- In `Werk/Foto's`: `IMG_utrecht.jpg` met GPS (Utrecht), persoon "Anna" en OCR-tekst
  FOTO_OCR_WOORD; een kopie (dezelfde inhoud) en een verkleinde kopie (bijna gelijk, in
  dup_pair); `Dom.jpg` zonder GPS.
- In `Werk/Inbox`: `Brief.txt` (INBOX_WOORD), `Bonnetje.jpg` (een foto: OCR aanzetten mag
  Bram daar niet) en `Factuur scan.pdf` (een PDF: dat mag wel, Keuze 23).
- Buiten `Werk`, alleen van Eva: `Privé/Belasting 2025.txt` (TEKST_WOORD en PRIVE_WOORD).
- Album `Utrecht` van Eva, met de regel place = Utrecht en share_new 0: `Dom.jpg` met de hand
  (gedeeld) en `IMG_utrecht.jpg` door de regel (niet gedeeld). Gedeeld met Bram en met een
  publieke link.
- **Bram** heeft een kopie van de foto in zijn eigen map (`Kopie Utrecht.jpg`), met zijn eigen
  plek (zijn locatie staat op "mij") en zonder Anna of OCR-tekst.
- **Cas** (lid van Team), **Gijs** (gast), **Dirk** (niets gedeeld, wel een eigen notitie met
  TEKST_WOORD), **Ada** (beheerder, niets gedeeld), **Fien** (deling verlopen), **Hans**
  (deling, in te trekken), **Iris** (een foto met plek en persoon "Joop"; de test wist haar).

De afgeleide rijen staan er al, zoals de jobs van Z2, Z3 en Z6 ze zouden schrijven: blob_text
(tekst van de documenten) met de rijen van de stappen text en ocr in analysis, blob_meta, photo_geo,
photo_ocr, person en photo_person, dup_pair. Geen blob_hash en geen ocr_page: die maken de jobs
zelf. De bestanden zelf staan in blob_dir (gemaakt met tests/search/fixtures.py), dus downloaden
en de jobs werken op echte inhoud.

Schrijft met een ORM-sessie op de verbinding van de aanroeper: niets commit zelf, en de
triggers van 0010 zetten num, mpath, dead en de index. blob_text komt met een gewone INSERT
(testdata; de app schrijft hem alleen via analysis.write_text).
"""

from __future__ import annotations

import hashlib
import json
import uuid
import zlib
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import delete, update
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import (
    Album,
    AlbumItem,
    AlbumRule,
    Blob,
    BlobMeta,
    BlobText,
    DupPair,
    Group,
    GroupMember,
    Node,
    Person,
    PhotoGeo,
    PhotoOcr,
    PhotoPerson,
    PhotoPref,
    Share,
    User,
)
from app.photodate import day_of
from app.search import bench
from app.search.bench import tegel16
from app.security import hash_password
from app.text import fold
from tests.search import fixtures

# Een vaste dag, niet nu (de regel van de e2e): de wereld is elke keer gelijk
DAG = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)
GENOMEN = datetime(2024, 6, 1, 14, 30)  # zwevende tijd, zoals de EXIF van fixtures.exif()
DOMEIN = "wereld.voorbeeld.nl"
WACHTWOORD = "wereld-wachtwoord-lang"

TEKST_WOORD = "zebrakwarts"
OCR_WOORD = fixtures.OCR_WOORD  # alleen in het beeld van de scan (X1)
FOTO_OCR_WOORD = "bonwoordy"  # OCR-tekst in Eva's foto: alleen voor Eva
PRULLENBAK_WOORD = "prullenwoordq"
VERHUIS_WOORD = "verhuiswoordz"
INBOX_WOORD = "inboxwoordv"
PRIVE_WOORD = "privewoordp"
DIRK_WOORD = "dirkwoordd"
INBOX_SCAN_WOORD = "inboxscanw"
PERSOON = "Anna"
IRIS_PERSOON = "Joop"
PLAATS = "Utrecht"
# Het id van de stad Utrecht in places.sqlite. rg_cities1000.csv heeft geen GeoNames-id, dus
# geo-load geeft elke plaats een vast id uit land, provincie, naam en plek (app.geo.load.place_id;
# Z3). tests/geo/test_places.py controleert hem tegen de echte bronnen.
PLAATS_ID = 150904566956734

# naam -> (rol, weergavenaam)
GEBRUIKERS = {
    "eva": ("user", "Eva"),
    "bram": ("user", "Bram"),
    "cas": ("user", "Cas"),
    "gijs": ("guest", "Gijs"),
    "dirk": ("user", "Dirk"),
    "ada": ("admin", "Ada"),
    "fien": ("user", "Fien"),
    "hans": ("user", "Hans"),
    "iris": ("user", "Iris"),
}
GROEP = "Team"
# Vragen op naam (zoals in 0.7.0) met voor elke persoon een ander antwoord: de API-test en de
# e2e leggen de app hiermee naast Referentie.op_naam
NAAMVRAGEN = ("verslag", "utrecht", "img", "brief", "notities", "plan", "werk", "jpg", "kopie",
              "scan", "a", "IMG_utrecht klein", "privé")  # fmt: skip
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_NS = uuid.UUID("6e0c2f5e-7a3b-4d58-9a51-2b7f4d1c0006")


def _uuid(sleutel: str) -> uuid.UUID:
    return uuid.uuid5(_NS, sleutel)


def email(naam: str) -> str:
    return f"{naam}@{DOMEIN}"


@dataclass
class Wereld:
    """Wat bouw() maakte: ids per sleutel (zie bovenaan), en per gebruiker id en hoofdmap."""

    users: dict[str, uuid.UUID] = field(default_factory=dict)
    roots: dict[str, uuid.UUID] = field(default_factory=dict)
    ids: dict[str, uuid.UUID] = field(default_factory=dict)
    shas: dict[str, str] = field(default_factory=dict)
    shares: dict[str, uuid.UUID] = field(default_factory=dict)
    tokens: dict[str, str] = field(default_factory=dict)
    personen: dict[str, uuid.UUID] = field(default_factory=dict)
    groep: uuid.UUID | None = None

    @staticmethod
    def email(naam: str) -> str:
        return email(naam)


_hash: dict[str, str] = {}


def _wachtwoord() -> str:
    if WACHTWOORD not in _hash:  # argon2 kost ±100 ms: één keer
        _hash[WACHTWOORD] = hash_password(WACHTWOORD)
    return _hash[WACHTWOORD]


class _Bouwer:
    def __init__(self, s: Session, blob_dir: Path | None):
        self.s = s
        self.blob_dir = blob_dir
        self.w = Wereld()
        self.refs: dict[str, int] = {}

    def gebruikers(self) -> None:
        for naam, (rol, weergave) in GEBRUIKERS.items():
            user = User(
                id=_uuid(f"user:{naam}"),
                email=email(naam),
                display_name=weergave,
                password_hash=_wachtwoord(),
                role=rol,
                quota_bytes=0,
                created_at=DAG,
            )
            self.s.add(user)
            self.w.users[naam] = user.id
        self.s.flush()
        for naam, user_id in self.w.users.items():
            root = Node(id=_uuid(f"root:{naam}"), owner_id=user_id, name="", kind="dir")
            root.created_at = root.modified_at = DAG
            self.s.add(root)
            self.w.roots[naam] = root.id
        self.s.flush()
        for naam, user_id in self.w.users.items():
            self.s.execute(
                update(User).where(User.id == user_id).values(root_id=self.w.roots[naam])
            )

    def map(self, sleutel: str, eigenaar: str, ouder: uuid.UUID, naam: str) -> uuid.UUID:
        node = Node(id=_uuid(f"node:{sleutel}"), parent_id=ouder, owner_id=self.w.users[eigenaar],
                    name=naam, kind="dir", created_at=DAG, modified_at=DAG)  # fmt: skip
        self.s.add(node)
        self.s.flush()
        self.w.ids[sleutel] = node.id
        return node.id

    def blob(self, sleutel: str, data: bytes, mime: str, *, foto: bool = False) -> str:
        sha = hashlib.sha256(data).hexdigest()
        self.w.shas[sleutel] = sha
        if sha in self.refs:
            return sha
        self.refs[sha] = 0
        self.s.add(
            Blob(sha256=sha, size=len(data), ref_count=0, created_at=DAG, mime=mime,
                 taken_at=GENOMEN if foto else None)
        )  # fmt: skip
        self.s.flush()
        if self.blob_dir is not None:
            pad = self.blob_dir / sha[:2] / sha[2:4] / sha
            pad.parent.mkdir(parents=True, exist_ok=True)
            pad.write_bytes(data)
        return sha

    def bestand(
        self,
        sleutel: str,
        eigenaar: str,
        ouder: uuid.UUID,
        naam: str,
        data: bytes,
        mime: str,
        *,
        foto: bool = False,
        weg: bool = False,
        blob_sleutel: str | None = None,
    ) -> uuid.UUID:
        sha = self.blob(blob_sleutel or sleutel, data, mime, foto=foto)
        self.refs[sha] += 1
        self.w.shas[sleutel] = sha
        node = Node(
            id=_uuid(f"node:{sleutel}"),
            parent_id=ouder,
            owner_id=self.w.users[eigenaar],
            name=naam,
            kind="file",
            mime=mime,
            size=len(data),
            blob_id=sha,
            created_at=DAG,
            modified_at=DAG,
            deleted_at=DAG if weg else None,
            taken_at=GENOMEN if foto else None,
            taken_day=day_of(GENOMEN) if foto else None,
        )
        self.s.add(node)
        self.s.flush()
        self.w.ids[sleutel] = node.id
        return node.id

    def tekst(self, sleutel: str, body: str = "", meta: str = "") -> None:
        self.s.add(BlobText(blob_sha=self.w.shas[sleutel], body=body, ocr="", meta=meta,
                            truncated=0, updated_at=DAG))  # fmt: skip

    def deel(
        self,
        sleutel: str,
        node: uuid.UUID,
        eigenaar: str,
        *,
        naar: str | None = None,
        groep: uuid.UUID | None = None,
        token: str | None = None,
        recht: str = "read",
        verloopt: datetime | None = None,
    ) -> None:
        share = Share(
            id=_uuid(f"share:{sleutel}"),
            node_id=node,
            owner_id=self.w.users[eigenaar],
            grantee_id=self.w.users[naar] if naar else None,
            group_id=groep,
            token=token,
            permission=recht,
            expires_at=verloopt,
            created_at=DAG,
            label="wereld" if token else None,
        )
        self.s.add(share)
        self.w.shares[sleutel] = share.id
        if token:
            self.w.tokens[sleutel] = token

    def plek(self, sleutel: str, eigenaar: str, lat: float, lon: float) -> None:
        x, y = tegel16(lat, lon)
        self.s.add(
            PhotoGeo(node_id=self.w.ids[sleutel], owner_id=self.w.users[eigenaar], lat=lat,
                     lon=lon, x16=x, y16=y, place_id=PLAATS_ID, country="NL", admin1="Utrecht",
                     source="exif")
        )  # fmt: skip

    def persoon(self, sleutel: str, eigenaar: str, naam: str, op: list[str]) -> None:
        pid = _uuid(f"persoon:{sleutel}")
        self.s.add(Person(id=pid, owner_id=self.w.users[eigenaar], name=naam,
                          search_name=fold(naam), created_at=DAG, updated_at=DAG))  # fmt: skip
        self.s.flush()
        for foto in op:
            self.s.add(PhotoPerson(person_id=pid, node_id=self.w.ids[foto],
                                   owner_id=self.w.users[eigenaar], source="hand",
                                   created_at=DAG))  # fmt: skip
        self.w.personen[sleutel] = pid

    def ref_counts(self) -> None:
        self.s.flush()
        for sha, n in self.refs.items():
            self.s.execute(update(Blob).where(Blob.sha256 == sha).values(ref_count=n))

    def analyse(self) -> None:
        """De rijen van de stappen text en ocr zoals de backfill van Z2 en de pompen ze
        achterlaten (bench.analyse_rijen): dan leest een server met de wereld bij het starten
        de documenten niet opnieuw, en verandert de index niet onder een test door. Een test
        die OCR een scan van de wereld wil laten lezen, haalt eerst de rij ocr ervan weg."""
        self.s.flush()
        conn = self.s.connection()
        rijen = bench.analyse_rijen(conn.exec_driver_sql(bench.ANALYSE_BLOBS).all())
        conn.exec_driver_sql(bench.ANALYSE_SQL, rijen)


def bouw(conn: Connection, blob_dir: Path | None = None) -> Wereld:
    """Bouwt de wereld in de database van conn (al op head); bestanden in blob_dir (de
    BlobStore van de app, settings.blob_dir). Commit niet."""
    with Session(bind=conn, autoflush=False) as s:
        b = _Bouwer(s, blob_dir)
        w = b.w
        b.gebruikers()
        eva = w.roots["eva"]

        # Eva's mappen
        werk = b.map("werk", "eva", eva, "Werk")
        fotos = b.map("fotos", "eva", werk, "Foto's")
        inbox = b.map("inbox", "eva", werk, "Inbox")
        verhuisd = b.map("verhuisd", "eva", werk, "Verhuisd")
        prive = b.map("prive", "eva", eva, "Privé")

        # Documenten in Werk
        verslag = f"Het verslag noemt {TEKST_WOORD} en de kwartaalcijfers."
        b.bestand("doc", "eva", werk, "Verslag kwartaal.docx", fixtures.docx(verslag), DOCX)
        b.tekst("doc", f"Verslag\n{verslag}", meta=f"{fixtures.KANTOOR['titel']} Eva Jansen")
        b.bestand("scan", "eva", werk, "Bon scan.pdf", fixtures.pdf_scan(OCR_WOORD),
                  "application/pdf")  # fmt: skip
        oud = f"Oud: {TEKST_WOORD} en {PRULLENBAK_WOORD}"
        b.bestand("prullenbak", "eva", werk, "Oud verslag.txt", oud.encode(), "text/plain",
                  weg=True)  # fmt: skip
        b.tekst("prullenbak", oud)
        plan = f"Het plan voor de verhuizing: {VERHUIS_WOORD}"
        b.bestand("plan", "eva", verhuisd, "Plan.txt", plan.encode(), "text/plain")
        b.tekst("plan", plan)
        belasting = f"Aangifte met {TEKST_WOORD} en {PRIVE_WOORD}"
        b.bestand("belasting", "eva", prive, "Belasting 2025.txt", belasting.encode(),
                  "text/plain")  # fmt: skip
        b.tekst("belasting", belasting)

        # De foto met plek, persoon en OCR-tekst, met een kopie en een verkleinde kopie
        foto = fixtures.foto_gps(*fixtures.UTRECHT, zaad="wereld")
        klein = fixtures.foto_klein(foto)
        b.bestand("foto", "eva", fotos, "IMG_utrecht.jpg", foto, "image/jpeg", foto=True)
        b.bestand("foto_kopie", "eva", fotos, "IMG_utrecht kopie.jpg", foto, "image/jpeg",
                  foto=True, blob_sleutel="foto")  # fmt: skip
        b.bestand("foto_klein", "eva", fotos, "IMG_utrecht klein.jpg", klein, "image/jpeg",
                  foto=True)  # fmt: skip
        b.bestand("dom", "eva", fotos, "Dom.jpg", fixtures.foto_zonder_gps("dom"), "image/jpeg",
                  foto=True)  # fmt: skip

        # Inbox: schrijven voor Bram
        brief = f"Een brief in de inbox: {INBOX_WOORD}"
        b.bestand("brief", "eva", inbox, "Brief.txt", brief.encode(), "text/plain")
        b.tekst("brief", brief)
        b.bestand("bonnetje", "eva", inbox, "Bonnetje.jpg", fixtures.foto_zonder_gps("bon"),
                  "image/jpeg", foto=True)  # fmt: skip
        b.bestand("factuur_scan", "eva", inbox, "Factuur scan.pdf",
                  fixtures.pdf_scan(INBOX_SCAN_WOORD), "application/pdf")  # fmt: skip

        # Bram: een kopie van Eva's foto in zijn eigen map (dezelfde inhoud, V9)
        b.bestand("bram_kopie", "bram", w.roots["bram"], "Kopie Utrecht.jpg", foto,
                  "image/jpeg", foto=True, blob_sleutel="foto")  # fmt: skip
        # Dirk: een eigen notitie met hetzelfde woord (Bram mag hem nooit zien)
        dirk = f"Dirk schrijft over {TEKST_WOORD} en {DIRK_WOORD}"
        b.bestand("dirk_notitie", "dirk", w.roots["dirk"], "Notities.txt", dirk.encode(),
                  "text/plain")  # fmt: skip
        b.tekst("dirk_notitie", dirk)
        # Iris: een foto met plek en persoon (de test wist haar)
        iris = fixtures.foto_gps(52.0800, 5.1300, zaad="iris")
        b.bestand("iris_foto", "iris", w.roots["iris"], "Iris.jpg", iris, "image/jpeg",
                  foto=True)  # fmt: skip
        b.s.flush()

        # Foto-gegevens: per inhoud ja of nee (blob_meta), per node van de eigenaar de rest
        for sleutel, gps in (("foto", 1), ("foto_klein", 1), ("dom", 0), ("bonnetje", 0),
                             ("iris_foto", 1)):  # fmt: skip
            s.add(BlobMeta(blob_sha=w.shas[sleutel], camera="fujifilm x-t30", lens=None, gps=gps))
        lat, lon = fixtures.UTRECHT
        for sleutel in ("foto", "foto_kopie", "foto_klein"):
            b.plek(sleutel, "eva", lat, lon)
        b.plek("bram_kopie", "bram", lat, lon)
        b.plek("iris_foto", "iris", 52.0800, 5.1300)
        woorden = zlib.compress(json.dumps([[FOTO_OCR_WOORD, 0.1, 0.8, 0.3, 0.05]]).encode())
        s.add(PhotoOcr(node_id=w.ids["foto"], owner_id=w.users["eva"], text=FOTO_OCR_WOORD,
                       words=woorden, angle=0.0, updated_at=DAG))  # fmt: skip
        b.persoon("anna", "eva", PERSOON, ["foto", "foto_kopie"])
        b.persoon("joop", "iris", IRIS_PERSOON, ["iris_foto"])
        a, z = sorted((w.shas["foto"], w.shas["foto_klein"]))
        s.add(DupPair(a=a, b=z, dist=2, mad=1.5, level="zeker"))
        s.add(PhotoPref(user_id=w.users["eva"], location="mij", updated_at=DAG))

        # De groep Team met Cas
        groep = Group(id=_uuid("groep:team"), name=GROEP, created_at=DAG)
        s.add(groep)
        s.flush()
        s.add(GroupMember(group_id=groep.id, user_id=w.users["cas"]))
        w.groep = groep.id

        # Het album Utrecht: Dom met de hand (gedeeld), de Utrecht-foto door de regel
        album = Node(id=_uuid("node:album"), parent_id=None, owner_id=w.users["eva"],
                     name="Utrecht", kind="album", created_at=DAG, modified_at=DAG)  # fmt: skip
        s.add(album)
        s.flush()
        w.ids["album"] = album.id
        s.add(Album(id=album.id, description="", cover_id=w.ids["dom"]))
        s.add(AlbumRule(album_id=album.id, rule=json.dumps({"place": PLAATS_ID}), share_new=0,
                        refreshed_at=DAG, count=1))  # fmt: skip
        s.add(AlbumItem(album_id=album.id, node_id=w.ids["dom"], added_by=w.users["eva"],
                        added_at=DAG, source="hand", shared=1))  # fmt: skip
        s.add(AlbumItem(album_id=album.id, node_id=w.ids["foto"], added_by=None, added_at=DAG,
                        source="rule", shared=0))  # fmt: skip

        # De delingen
        b.deel("werk_bram", werk, "eva", naar="bram")
        b.deel("werk_team", werk, "eva", groep=groep.id)
        b.deel("werk_gijs", werk, "eva", naar="gijs")
        b.deel("werk_fien", werk, "eva", naar="fien", verloopt=DAG - timedelta(days=1))
        b.deel("werk_hans", werk, "eva", naar="hans")
        b.deel("inbox_bram", inbox, "eva", naar="bram", recht="write")
        b.deel("fotos_link", fotos, "eva", token="wereldfotos" + "0" * 21)
        b.deel("album_bram", album.id, "eva", naar="bram")
        b.deel("album_link", album.id, "eva", token="wereldalbum" + "0" * 21)
        b.ref_counts()
        b.analyse()
        s.flush()
        return w


async def bouw_async(settings: Settings) -> Wereld:
    """De wereld in de database van een test (fixture settings), met de bestanden in
    settings.blob_dir; commit."""
    from app.db import get_engine

    async with get_engine(settings).begin() as conn:
        return await conn.run_sync(bouw, settings.blob_dir)


def intrekken(conn: Connection, w: Wereld) -> None:
    """Eva trekt de deling met Hans in (zoals DELETE /shares/{id})."""
    conn.execute(delete(Share).where(Share.id == w.shares["werk_hans"]))


def verhuis(conn: Connection, w: Wereld) -> None:
    """Eva zet de map Verhuisd uit Werk in haar eigen map (zoals een verplaatsing in de app:
    alleen parent_id; de triggers zetten het pad)."""
    conn.execute(update(Node).where(Node.id == w.ids["verhuisd"]).values(parent_id=w.roots["eva"]))
