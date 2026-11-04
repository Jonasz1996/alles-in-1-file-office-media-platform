"""De boom van 100.000 voor zoeken: de generator als module (bouwontwerp fase 6, §3.6 en §4.4;
spike S7 en S9). Z0 bouwt hem; Z1 meet ermee (test_perf_100k.py, e2e/perf/zoek_100k.py), de
referentie (tests/search/referentie.py) zoekt in een kleine boom van dezelfde generator, en
tools/zoekbench/bench.py (S9) neemt de logica over.

Bij n = 100.000 en seed 6: 30 gebruikers met elk een eigen boom (samen ±5.900 mappen tot diepte
5), 100.000 bestanden, waarvan ±20.000 documenten met samen ±235 miljoen tekens tekst (Zipf over
een vaste woordenschat: Nederlands en Engels met diakrieten, ß en kunstwoorden), ±55.000 foto's
(60% met GPS in photo_geo van hun eigenaar), ±2.000 kopieën bij een ander, 3% van de mappen en 1%
van de bestanden in de prullenbak (samen ±4,5% van de nodes dood), en delingen naar personen,
een groep en een gast. De eigenaar (b00) heeft ±6.200 levende nodes, de kleinste ±2.600. Daarnaast
twee gebruikers zonder eigen bestanden: een gast met 2 delingen en "alles", met een deling op
elke hoofdmap (het ergste geval van §4.4). Twee profielen leggen er iets bovenop: "groot" (200
documenten van 1 MB tekst) en "kopieen" (één tekst van 1 MB met 10 kopieën, C11).

Alles gaat via de echte migratie en de echte triggers: vul() schrijft met gewone INSERTs in een
database die al op head staat, en de triggers van 0010 zetten num, mpath, dead en de FTS-rijen.
De tekst komt vóór de nodes in blob_text, één gewone INSERT per sha: dan schrijft node_ai de
tekst meteen mee, in plaats van eerst de naam en bij de tekst nog eens alles (Keuze 3 verbiedt
alleen upserts; de app zelf schrijft blob_text alleen via analysis.write_text).

Alleen de standaardbibliotheek, zodat tools/zoekbench/bench.py (draait in de container van
0.7.0 en mag de app niet importeren) dit kan overnemen: Plan, WOORDEN, de generator en vul().
maak() en main() importeren de app pas als ze draaien (de migratie en de wachtwoord-hash).

    uv run python -m app.search.bench --data /tmp/boom --n 100000 --seed 6 [--profiel groot]
"""

from __future__ import annotations

import argparse
import bisect
import hashlib
import itertools
import json
import math
import random
import sqlite3
import sys
import time
import unicodedata
from collections import Counter
from collections.abc import Iterable, Iterator
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # alleen voor de typen: de app pas bij draaien (zie boven)
    from app.config import Settings
    from app.search.rebuild import Checked

# Een vaste dag, niet nu: dezelfde seed geeft overal dezelfde boom (ook de datums)
DAG = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)
JAREN = 10
DOMEIN = "bench.voorbeeld.nl"
PROFIELEN = ("basis", "groot", "kopieen")
# De toegangsprofielen van §4.4 en de gebruiker die ze speelt
ROLLEN = {
    "eigenaar": "b00",
    "ontvanger5": "b01",
    "ontvanger20": "b02",
    "groepslid": "b03",
    "gast": "gast",
    "alles": "alles",
}
GROEP = "Bench-team"
MB = 1_000_000

# Verhoudingen per bestand (bij 100.000: 5.900 mappen, 20.000 documenten, 55.000 foto's, ...)
MAPPEN = 0.059
SOORTEN = (("foto", 0.55), ("document", 0.20), ("overig", 0.25))
KOPIE_ANDER = 0.02  # een bestand van een ander, met dezelfde sha, in mijn boom
KOPIE_EIGEN = 0.005  # een foto twee keer bij dezelfde eigenaar (precies gelijk)
BIJNA = 0.01  # een foto met een bijna-gelijke variant (dup_pair)
PRULLENBAK_MAPPEN = 0.03
PRULLENBAK_BESTANDEN = 0.01
GPS = 0.60
TEKENS_GEMIDDELD = 11_750  # ±235 miljoen tekens over 20.000 documenten
TEKST_MAX = 1_048_576  # PLATFORM_SEARCH_MAX_TEXT
DIEPTE = 5

DOC_SOORTEN = (
    ("pdf", "application/pdf", 0.40),
    ("docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", 0.30),
    ("xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", 0.10),
    ("pptx", "application/vnd.openxmlformats-officedocument.presentationml.presentation", 0.10),
    ("txt", "text/plain", 0.05),
    ("md", "text/markdown", 0.05),
)
OVERIG = (
    ("mp3", "audio/mpeg"),
    ("mp4", "video/mp4"),
    ("zip", "application/zip"),
    ("bin", "application/octet-stream"),
    ("csv", "text/csv"),
)
CAMERAS = ("fujifilm x-t30", "apple iphone 13", "canon eos 80d", "samsung sm-g991b", "sony a7 iii")
# Waar foto's met GPS liggen: (naam, lat, lon); elk punt krijgt wat ruis
PLEKKEN = (
    ("Utrecht", 52.0907, 5.1214),
    ("Amsterdam", 52.3676, 4.9041),
    ("Rotterdam", 51.9244, 4.4777),
    ("Groningen", 53.2194, 6.5665),
    ("Maastricht", 50.8514, 5.6910),
    ("Parijs", 48.8566, 2.3522),
    ("Berlijn", 52.5200, 13.4050),
    ("Barcelona", 41.3874, 2.1686),
    ("Rome", 41.9028, 12.4964),
    ("Lissabon", 38.7223, -9.1393),
)

# --- De woordenschat ----------------------------------------------------------------------

# Gewone woorden eerst (die komen het vaakst voor), dan de kunstwoorden. Vast, los van de seed:
# een vragenlijst (tests/search/vragen.tsv) blijft zo bij elke boom kloppen.
NL = (
    "de het een en van in is op te dat met voor niet zijn aan er om ook als bij dan of maar "
    "door naar over nog wel uit tot heeft worden kunnen moet onze jullie hun deze die wat "
    "factuur offerte rekening jaarverslag vergadering notulen agenda planning begroting "
    "contract bijlage overzicht rapport verslag samenvatting project klant leverancier "
    "bestelling levering betaling herinnering aanmaning belasting aangifte salaris "
    "huur hypotheek verzekering polis schade garantie handleiding recept vakantie reis "
    "boeking hotel vlucht trein fiets auto huis tuin keuken school werk kantoor team "
    "één twee drie vier vijf zes zeven acht negen tien honderd duizend "
    "café privé reünie coördinatie ruïne naïef patiënt zeeën ideeën financiële "
    "geïnteresseerd beëindigen geëvalueerd één-op-één überhaupt crème logé "
    "gemeente provincie belastingdienst waterschap ziekenhuis huisarts tandarts apotheek "
    "kwartaal maand week dag januari februari maart april mei juni juli augustus "
    "september oktober november december maandag dinsdag woensdag donderdag vrijdag"
).split()
EN = (
    "the of and to in is that for it with as on be at by this have from or an they "
    "invoice quote account annual report meeting minutes agenda budget contract "
    "attachment summary project customer supplier order delivery payment reminder "
    "insurance warranty manual recipe holiday travel booking flight train bike house "
    "garden kitchen school office résumé naïve café coöperate façade fiancée déjà vu "
    "quarter month week day schedule deadline review approval request"
).split()
DE = "straße große fußball maß grüße weiß gießen ẞtraße GROẞE".split()
EXTRA = "kpn btw iban 2019 2020 2021 2022 2023 2024 2025 2026 nl91abna0417164300 a4 q3".split()
_BEGIN = ("b d f g h k l m n p r s t v w z st pl kr gr sch tr br dr sl sn sp vl zw").split()
_KERN = "a e i o u aa ee oo ie oe ui ei ou au é ë ü ö".split()
_KERN_GEWICHT = (9, 9, 7, 7, 4, 3, 3, 3, 3, 3, 2, 2, 2, 2, 1, 1, 1, 1)
_EIND = ("", "", "n", "r", "s", "k", "t", "l", "m", "nd", "rt", "st", "ng", "ft")
KUNSTWOORDEN = 30_000


def _kunstwoorden(aantal: int) -> list[str]:
    rng = random.Random(6006)
    seen: set[str] = set()
    out: list[str] = []
    while len(out) < aantal:
        delen = []
        for _ in range(rng.choice((2, 2, 3, 3, 4))):
            kern = rng.choices(_KERN, weights=_KERN_GEWICHT)[0]
            delen.append(rng.choice(_BEGIN) + kern + rng.choice(_EIND))
        woord = "".join(delen)
        if woord not in seen and len(woord) >= 5:
            seen.add(woord)
            out.append(woord)
    return out


def _woordenschat() -> list[str]:
    out: list[str] = []
    for w in itertools.chain(NL, EN, DE, EXTRA, _kunstwoorden(KUNSTWOORDEN)):
        if w not in out:
            out.append(w)
    return out


WOORDEN: list[str] = _woordenschat()
# Zipf–Mandelbrot: woord r heeft gewicht 1 / (r + 2,7)^1,07
_CUM: list[float] = list(itertools.accumulate(1 / (r + 2.7) ** 1.07 for r in range(len(WOORDEN))))
# Woorden voor namen: zelfstandige naamwoorden en kunstwoorden (geen "de" of "het")
NAAMWOORDEN = [w for w in WOORDEN if len(w) >= 4 and w.isalpha() and w.lower() == w]


def fold(text: str) -> str:
    """Zoals app.text.fold: zonder hoofdletters en accenten (é -> e, ß -> ss)."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold()


def tekst(rng: random.Random, tekens: int) -> str:
    """Ongeveer zoveel tekens tekst uit de woordenschat (Zipf), in zinnen met een hoofdletter
    en een punt."""
    woorden = rng.choices(WOORDEN, cum_weights=_CUM, k=max(1, tekens // 7))
    for i in range(0, len(woorden), 12):
        woorden[i] = woorden[i][:1].upper() + woorden[i][1:]
        if i:
            woorden[i - 1] += "."
    return " ".join(woorden)[:tekens] + "."


# --- Het plan -----------------------------------------------------------------------------


@dataclass(frozen=True)
class Plan:
    """Wat er gemaakt wordt. n: het aantal bestanden (mappen en hoofdmappen komen erbij)."""

    n: int = 100_000
    seed: int = 6
    profiel: str = "basis"
    gebruikers: int = 30
    tekens: int = TEKENS_GEMIDDELD

    def __post_init__(self) -> None:
        if self.profiel not in PROFIELEN:
            raise ValueError(f"profiel: een van {PROFIELEN}, niet {self.profiel!r}")
        if self.gebruikers < 16:
            raise ValueError("minstens 16 gebruikers: de rollen van §4.4 hebben er zoveel nodig")


@dataclass
class Gebruiker:
    naam: str
    id: str
    root: str
    rol: str = "user"
    gewicht: float = 0.0

    @property
    def email(self) -> str:
        return f"{self.naam}@{DOMEIN}"


@dataclass
class Uitkomst:
    """Wat vul() maakte: per gebruiker id en e-mail, de rollen van §4.4, en de tellingen."""

    plan: Plan
    gebruikers: dict[str, dict[str, str]] = field(default_factory=dict)
    rollen: dict[str, str] = field(default_factory=dict)
    tellingen: Counter = field(default_factory=Counter)
    seconden: dict[str, float] = field(default_factory=dict)

    def json(self) -> str:
        data = asdict(self)
        data["tellingen"] = dict(self.tellingen)
        return json.dumps(data, indent=2, ensure_ascii=False)

    def kort(self) -> str:
        """De samenvatting voor het einde van een log in CI (de regel === kort === eerst)."""
        t = self.tellingen
        regels = [
            "=== kort ===",
            f"boom: n={self.plan.n} seed={self.plan.seed} profiel={self.plan.profiel}",
            f"gebruikers {t['gebruikers']}, mappen {t['mappen']}, bestanden {t['bestanden']}",
            f"documenten {t['documenten']} met {t['tekens']} tekens, foto's {t['fotos']} "
            f"(gps {t['gps']}), kopieën bij een ander {t['kopie_ander']}",
            f"prullenbak: {t['prullenbak_mappen']} mappen, {t['prullenbak_bestanden']} bestanden; "
            f"delingen {t['delingen']}",
            "seconden: " + ", ".join(f"{k} {v:.1f}" for k, v in self.seconden.items()),
        ]
        return "\n".join(regels)


def _id(rng: random.Random) -> str:
    return f"{rng.getrandbits(128):032x}"


def _tijd(moment: datetime) -> str:
    """Zoals SQLAlchemy een DateTime in SQLite schrijft (UTC, zonder zone)."""
    return moment.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")


def _sha(plan: Plan, soort: str, i: int) -> str:
    return hashlib.sha256(f"bench:{plan.seed}:{soort}:{i}".encode()).hexdigest()


def tegel16(lat: float, lon: float) -> tuple[int, int]:
    """De tegel op zoom 16 (Webmercator), zoals photo_geo.x16 en y16."""
    n = 1 << 16
    x = int((lon + 180.0) / 360.0 * n)
    r = math.radians(lat)
    y = int((1.0 - math.asinh(math.tan(r)) / math.pi) / 2.0 * n)
    return min(max(x, 0), n - 1), min(max(y, 0), n - 1)


# --- Schrijven ----------------------------------------------------------------------------


class _Schrijver:
    """INSERTs in stukken, elk stuk een eigen transactie."""

    STUK = 5000

    def __init__(self, con: sqlite3.Connection):
        self.con = con

    def rijen(self, sql: str, rijen: Iterable[tuple]) -> int:
        totaal = 0
        it = iter(rijen)
        while True:
            stuk = list(itertools.islice(it, self.STUK))
            if not stuk:
                return totaal
            self.con.execute("BEGIN")
            try:
                self.con.executemany(sql, stuk)
                self.con.execute("COMMIT")
            except BaseException:
                self.con.execute("ROLLBACK")
                raise
            totaal += len(stuk)


_NODE_SQL = (
    "INSERT INTO node (id, parent_id, owner_id, name, kind, mime, size, blob_id, created_at, "
    "modified_at, deleted_at, search_name, taken_at, taken_day) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
)
_BLOB_SQL = (
    "INSERT INTO blob (sha256, size, ref_count, created_at, mime, preview, preview_meta, "
    "taken_at, meta_version, render_version, pdf_state, pdf_version) "
    "VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, NULL, 0)"
)
_SHARE_SQL = (
    "INSERT INTO share (id, node_id, owner_id, grantee_id, token, permission, password_hash, "
    "expires_at, created_at, group_id, label) VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?)"
)


@dataclass
class _Node:
    id: str
    parent: str | None
    owner: str
    name: str
    kind: str
    diepte: int = 0
    mime: str | None = None
    size: int = 0
    blob: str | None = None
    moment: datetime = DAG
    weg: bool = False  # zelf in de prullenbak
    dood: bool = False  # zelf of een map erboven
    genomen: datetime | None = None

    def rij(self) -> tuple:
        genomen = _tijd(self.genomen) if self.genomen else None
        dag = int(self.genomen.strftime("%Y%m%d")) if self.genomen else None
        moment = _tijd(self.moment)
        return (
            self.id, self.parent, self.owner, self.name, self.kind, self.mime, self.size,
            self.blob, moment, moment, moment if self.weg else None, fold(self.name),
            genomen, dag,
        )  # fmt: skip


@dataclass
class _Blob:
    sha: str
    mime: str
    size: int
    soort: str  # foto, document, overig
    tekens: int = 0
    tekst_seed: int = 0
    refs: int = 0
    genomen: datetime | None = None
    gps: tuple[float, float] | None = None
    phash: int | None = None


class _Boom:
    """De generator zelf: gebruikers, mappen, bestanden en blobs, nog niet geschreven."""

    def __init__(self, plan: Plan):
        self.plan = plan
        self.rng = random.Random(f"{plan.seed}:boom")
        self.gebruikers: list[Gebruiker] = []
        self.nodes: list[_Node] = []
        self.mappen: dict[str, list[_Node]] = {}  # per eigenaar: zijn mappen (met hoofdmap)
        self.bestanden: dict[str, list[_Node]] = {}
        self.blobs: dict[str, _Blob] = {}
        self.dup_paren: list[tuple[str, str, int]] = []
        self.tellingen: Counter = Counter()

    # -- gebruikers

    def maak_gebruikers(self) -> None:
        rng = random.Random(f"{self.plan.seed}:gebruikers")
        for i in range(self.plan.gebruikers):
            g = Gebruiker(f"b{i:02d}", _id(rng), _id(rng), gewicht=1 / (i + 1) ** 0.25)
            self.gebruikers.append(g)
        self.gebruikers.append(Gebruiker("gast", _id(rng), _id(rng), rol="guest"))
        self.gebruikers.append(Gebruiker("alles", _id(rng), _id(rng)))
        for g in self.gebruikers:
            wortel = _Node(g.root, None, g.id, "", "dir")
            self.nodes.append(wortel)
            self.mappen[g.id] = [wortel]
            self.bestanden[g.id] = []
        self.tellingen["gebruikers"] = len(self.gebruikers)

    @property
    def eigenaren(self) -> list[Gebruiker]:
        return [g for g in self.gebruikers if g.gewicht > 0]

    def _per_eigenaar(self, totaal: int, rng: random.Random) -> list[Gebruiker]:
        eigenaren = self.eigenaren
        return rng.choices(eigenaren, weights=[g.gewicht for g in eigenaren], k=totaal)

    # -- mappen

    def maak_mappen(self) -> None:
        rng = random.Random(f"{self.plan.seed}:mappen")
        totaal = max(len(self.eigenaren), round(self.plan.n * MAPPEN))
        for g in self._per_eigenaar(totaal, rng):
            kandidaten = [m for m in self.mappen[g.id] if m.diepte < DIEPTE]
            ouder = rng.choice(kandidaten)
            if ouder.dood and rng.random() < 0.8:
                # Meestal niet in een map die al in de prullenbak ligt: anders ligt er veel
                # meer dan 3% in de prullenbak. Soms wel, voor een map onder een weggegooide.
                ouder = rng.choice([m for m in kandidaten if not m.dood])
            naam = self._mapnaam(rng)
            node = _Node(
                _id(rng), ouder.id, g.id, naam, "dir", diepte=ouder.diepte + 1,
                moment=DAG - timedelta(days=rng.uniform(0, 365 * JAREN)),
            )  # fmt: skip
            if rng.random() < PRULLENBAK_MAPPEN:
                node.weg = True
            node.dood = node.weg or ouder.dood
            self.nodes.append(node)
            self.mappen[g.id].append(node)
        self.tellingen["mappen"] = totaal
        self.tellingen["prullenbak_mappen"] = sum(
            1 for n in self.nodes if n.kind == "dir" and n.weg
        )

    def _mapnaam(self, rng: random.Random) -> str:
        keuze = rng.random()
        if keuze < 0.3:
            return rng.choice(NAAMWOORDEN).capitalize()
        if keuze < 0.5:
            return f"{rng.choice(NAAMWOORDEN).capitalize()} {rng.randint(2016, 2026)}"
        if keuze < 0.6:
            return f"Foto's {rng.choice(PLEKKEN)[0]}"
        return f"{rng.choice(NAAMWOORDEN).capitalize()} {rng.choice(NAAMWOORDEN)}"

    # -- bestanden

    def maak_bestanden(self) -> None:
        plan = self.plan
        rng = random.Random(f"{plan.seed}:bestanden")
        n_ander = round(plan.n * KOPIE_ANDER)
        n_eigen = round(plan.n * KOPIE_EIGEN)
        nieuw = plan.n - n_ander - n_eigen
        soorten = [s for s, _ in SOORTEN]
        gewichten = [w for _, w in SOORTEN]
        for i, g in enumerate(self._per_eigenaar(nieuw, rng)):
            soort = rng.choices(soorten, weights=gewichten)[0]
            ouder = rng.choice(self.mappen[g.id])
            if soort == "foto" and self.bestanden[g.id] and rng.random() < BIJNA:
                self._variant(rng, g, ouder, i)
            else:
                self._bestand(rng, soort, g, ouder, i)
        # Precies gelijk bij dezelfde eigenaar: een foto nog eens, in een andere map
        fotos = [n for n in self.nodes if n.kind == "file" and self.blobs[n.blob].soort == "foto"]
        for bron in rng.sample(fotos, min(n_eigen, len(fotos))):
            doel = rng.choice(self.mappen[bron.owner])
            self._kopie(rng, bron, doel, f"{bron.name.rsplit('.', 1)[0]} (2).jpg")
            self.tellingen["kopie_eigen"] += 1
        # Een kopie bij een ander: dezelfde sha, de eigenaar van de doelmap (V9)
        bronnen = [n for n in self.nodes if n.kind == "file"]
        for bron in rng.sample(bronnen, min(n_ander, len(bronnen))):
            ander = rng.choice([g for g in self.eigenaren if g.id != bron.owner])
            doel = rng.choice(self.mappen[ander.id])
            self._kopie(rng, bron, doel, bron.name)
            self.tellingen["kopie_ander"] += 1
        self._profiel(rng)
        self._trash_bestanden(rng)

    def _plek(self, node: _Node, ouder: _Node) -> _Node:
        node.dood = node.weg or ouder.dood
        self.nodes.append(node)
        self.bestanden[node.owner].append(node)
        return node

    def _bestand(self, rng: random.Random, soort: str, g: Gebruiker, ouder: _Node, i: int) -> None:
        moment = DAG - timedelta(days=rng.uniform(0, 365 * JAREN))
        if soort == "foto":
            genomen = DAG - timedelta(
                days=rng.uniform(0, 365 * JAREN), seconds=rng.randint(0, 86399)
            )
            if rng.random() < 0.7:
                naam = genomen.strftime("IMG_%Y%m%d_%H%M%S.jpg")
            else:
                naam = f"{rng.choice(NAAMWOORDEN)} {rng.choice(PLEKKEN)[0]} {i}.jpg"
            grootte = rng.randint(1_500_000, 6_000_000)
            blob = _Blob(_sha(self.plan, "foto", i), "image/jpeg", grootte, "foto",
                         genomen=genomen, phash=rng.getrandbits(64))  # fmt: skip
            if rng.random() < GPS:
                plek = rng.choice(PLEKKEN)
                blob.gps = (plek[1] + rng.gauss(0, 0.03), plek[2] + rng.gauss(0, 0.05))
            node = _Node(_id(rng), ouder.id, g.id, naam, "file", mime="image/jpeg",
                         size=blob.size, blob=blob.sha, moment=moment, genomen=genomen)  # fmt: skip
        elif soort == "document":
            ext, mime = rng.choices([(e, m) for e, m, _ in DOC_SOORTEN],
                                    weights=[w for _, _, w in DOC_SOORTEN])[0]  # fmt: skip
            tekens = self._lengte(rng)
            naam = self._docnaam(rng, ext, i)
            blob = _Blob(_sha(self.plan, "doc", i), mime, tekens + rng.randint(2_000, 90_000),
                         "document", tekens=tekens, tekst_seed=rng.getrandbits(32))  # fmt: skip
            node = _Node(_id(rng), ouder.id, g.id, naam, "file", mime=mime, size=blob.size,
                         blob=blob.sha, moment=moment)  # fmt: skip
        else:
            ext, mime = rng.choice(OVERIG)
            naam = f"{rng.choice(NAAMWOORDEN)}-{i}.{ext}"
            blob = _Blob(
                _sha(self.plan, "overig", i), mime, rng.randint(1_000, 50_000_000), "overig"
            )
            node = _Node(_id(rng), ouder.id, g.id, naam, "file", mime=mime, size=blob.size,
                         blob=blob.sha, moment=moment)  # fmt: skip
        blob.refs = 1
        self.blobs[blob.sha] = blob
        self._plek(node, ouder)

    def _variant(self, rng: random.Random, g: Gebruiker, ouder: _Node, i: int) -> None:
        """Een bijna-gelijke foto van een eigen foto: een eigen sha, een pHash op afstand 1–6,
        en een paar in dup_pair."""
        fotos = [n for n in self.bestanden[g.id][-50:] if self.blobs[n.blob].soort == "foto"]
        if not fotos:
            self._bestand(rng, "foto", g, ouder, i)
            return
        bron = self.blobs[rng.choice(fotos).blob]
        afstand = rng.randint(1, 6)
        phash = bron.phash or 0
        for bit in rng.sample(range(64), afstand):
            phash ^= 1 << bit
        blob = _Blob(_sha(self.plan, "variant", i), "image/jpeg", bron.size // 2, "foto",
                     genomen=bron.genomen, phash=phash, gps=bron.gps, refs=1)  # fmt: skip
        self.blobs[blob.sha] = blob
        naam = f"{(bron.genomen or DAG).strftime('IMG_%Y%m%d_%H%M%S')}-klein.jpg"
        moment = DAG - timedelta(days=rng.uniform(0, 365 * JAREN))
        self._plek(_Node(_id(rng), ouder.id, g.id, naam, "file", mime="image/jpeg",
                         size=blob.size, blob=blob.sha, moment=moment,
                         genomen=blob.genomen), ouder)  # fmt: skip
        a, b = sorted((bron.sha, blob.sha))
        self.dup_paren.append((a, b, afstand))

    def _lengte(self, rng: random.Random) -> int:
        sigma = 1.2
        mu = math.log(self.plan.tekens) - sigma * sigma / 2
        return max(200, min(TEKST_MAX, int(rng.lognormvariate(mu, sigma))))

    def _docnaam(self, rng: random.Random, ext: str, i: int) -> str:
        keuze = rng.random()
        if keuze < 0.4:
            return f"{rng.choice(NAAMWOORDEN)} {rng.randint(2016, 2026)}-{i % 997}.{ext}"
        if keuze < 0.7:
            return f"{rng.choice(NAAMWOORDEN).capitalize()} {rng.choice(NAAMWOORDEN)}.{ext}"
        return f"{rng.choice(NAAMWOORDEN)}_{rng.choice(NAAMWOORDEN)}_{i}.{ext}"

    def _kopie(self, rng: random.Random, bron: _Node, doel: _Node, naam: str) -> _Node:
        blob = self.blobs[bron.blob]
        blob.refs += 1
        node = _Node(_id(rng), doel.id, doel.owner, naam, "file", mime=bron.mime, size=bron.size,
                     blob=bron.blob, moment=DAG - timedelta(days=rng.uniform(0, 365)),
                     genomen=bron.genomen)  # fmt: skip
        return self._plek(node, doel)

    def _profiel(self, rng: random.Random) -> None:
        """De profielen bovenop de basis, in de boom van de eigenaar (§4.4)."""
        if self.plan.profiel == "basis":
            return
        eigenaar = self.gebruikers[0]
        wortel = self.mappen[eigenaar.id][0]
        naam = "Groot" if self.plan.profiel == "groot" else "Kopieën"
        map_ = _Node(_id(rng), wortel.id, eigenaar.id, naam, "dir", diepte=1)
        self.nodes.append(map_)
        self.mappen[eigenaar.id].append(map_)
        self.tellingen["mappen"] += 1
        mime = "application/pdf"
        aantal = 200 if self.plan.profiel == "groot" else 1
        for i in range(aantal):
            blob = _Blob(_sha(self.plan, self.plan.profiel, i), mime, MB + 50_000, "document",
                         tekens=MB, tekst_seed=rng.getrandbits(32), refs=1)  # fmt: skip
            self.blobs[blob.sha] = blob
            bron = self._plek(_Node(_id(rng), map_.id, eigenaar.id, f"groot-{i:03d}.pdf", "file",
                                    mime=mime, size=blob.size, blob=blob.sha), map_)  # fmt: skip
        if self.plan.profiel == "kopieen":
            for k in range(1, 11):
                self._kopie(rng, bron, map_, f"groot-kopie-{k:02d}.pdf")
        self.tellingen[f"profiel_{self.plan.profiel}"] = aantal

    def _trash_bestanden(self, rng: random.Random) -> None:
        for node in self.nodes:
            if node.kind == "file" and rng.random() < PRULLENBAK_BESTANDEN:
                node.weg = True
                node.dood = True
                self.tellingen["prullenbak_bestanden"] += 1

    # -- de rest: delingen, albums, personen

    def delingen(self) -> list[tuple]:
        rng = random.Random(f"{self.plan.seed}:delingen")
        nu = _tijd(DAG)
        voorbij = _tijd(DAG - timedelta(days=30))
        rijen: list[tuple] = []
        per_gebruiker = {g.naam: g for g in self.gebruikers}
        eigenaren = self.eigenaren
        gedeeld: set[tuple[str, str]] = set()  # (node, ontvanger): één deling per paar

        def levende_map(g: Gebruiker) -> _Node:
            kandidaten = [m for m in self.mappen[g.id][1:] if not m.dood] or self.mappen[g.id][:1]
            return rng.choice(kandidaten)

        def deel(van: Gebruiker, map_: _Node, naar: str | None, *, groep: str | None = None,
                 recht: str = "read", verloopt: str | None = None, token: str | None = None,
                 label: str | None = None) -> None:  # fmt: skip
            sleutel = (map_.id, naar or groep or token or "")
            if sleutel in gedeeld:
                return
            gedeeld.add(sleutel)
            rijen.append(
                (_id(rng), map_.id, van.id, naar, token, recht, verloopt, nu, groep, label)
            )

        rol = {r: per_gebruiker[n] for r, n in ROLLEN.items()}
        vrij = [g for g in eigenaren if g.naam not in ROLLEN.values()]
        # ontvanger5 en ontvanger20: precies 5 en 20 delingen, van verschillende eigenaren
        for ontvanger, aantal in (("ontvanger5", 5), ("ontvanger20", 20)):
            van = [g for g in eigenaren if g is not rol[ontvanger]]
            for g in rng.sample(van, min(aantal, len(van))):
                deel(g, levende_map(g), rol[ontvanger].id)
        # groepslid: zit met drie anderen in de groep; 6 delingen met de groep
        self.groep_id = _id(rng)
        self.leden = [rol["groepslid"].id] + [g.id for g in rng.sample(vrij, 3)]
        for g in rng.sample(vrij, 6):
            deel(g, levende_map(g), None, groep=self.groep_id)
        # de gast: 2 delingen
        for g in rng.sample(vrij, 2):
            deel(g, levende_map(g), rol["gast"].id)
        # alles: een deling op elke hoofdmap
        for g in eigenaren:
            deel(g, self.mappen[g.id][0], rol["alles"].id)
        # Op de achtergrond: elke andere eigenaar deelt een map met een ander (een op de vijf
        # met schrijfrecht, een op de tien verlopen), en drie publieke links
        anderen = [g for g in vrij]
        for g in vrij:
            naar = rng.choice([a for a in anderen if a is not g])
            verloopt = voorbij if rng.random() < 0.1 else None
            recht = "write" if rng.random() < 0.2 else "read"
            deel(g, levende_map(g), naar.id, recht=recht, verloopt=verloopt)
        for g in rng.sample(vrij, 3):
            deel(g, levende_map(g), None, token=f"bench{_id(rng)[:28]}", label="bench")
        self.tellingen["delingen"] = len(rijen)
        return rijen

    def albums(self) -> tuple[list[tuple], list[tuple], list[tuple]]:
        """Per eigenaar één album met hooguit 20 eigen foto's (met de hand)."""
        rng = random.Random(f"{self.plan.seed}:albums")
        nodes, albums, items = [], [], []
        for g in self.eigenaren:
            fotos = [n for n in self.bestanden[g.id] if n.mime == "image/jpeg"]
            if not fotos:
                continue
            album = _Node(_id(rng), None, g.id, f"Album {g.naam}", "album")
            nodes.append(album)
            albums.append((album.id, "", None))
            for f in rng.sample(fotos, min(20, len(fotos))):
                items.append((album.id, f.id, g.id, _tijd(DAG), "hand", 1))
        self.tellingen["albums"] = len(albums)
        return nodes, albums, items

    def personen(self) -> tuple[list[tuple], list[tuple]]:
        """Per eigenaar drie personen, op ±3% van zijn foto's (met de hand, zonder kader)."""
        rng = random.Random(f"{self.plan.seed}:personen")
        personen, op_foto = [], []
        for g in self.eigenaren:
            fotos = [n for n in self.bestanden[g.id] if n.mime == "image/jpeg"]
            if not fotos:
                continue
            for naam in rng.sample(("Anna", "Bas", "Chantal", "Daan", "Emma", "Femke"), 3):
                pid = _id(rng)
                personen.append((pid, g.id, naam, fold(naam), 0, None, _tijd(DAG), _tijd(DAG)))
                for f in rng.sample(fotos, max(1, len(fotos) * 3 // 100 // 3)):
                    op_foto.append((pid, f.id, g.id, "hand", _tijd(DAG)))
        self.tellingen["personen"] = len(personen)
        return personen, op_foto


def plan_boom(plan: Plan) -> _Boom:
    """De boom van dit plan, nog niet geschreven (voor wie hem wil bekijken)."""
    boom = _Boom(plan)
    boom.maak_gebruikers()
    boom.maak_mappen()
    boom.maak_bestanden()
    return boom


def _teksten(boom: _Boom) -> Iterator[tuple]:
    rng = random.Random()
    for blob in boom.blobs.values():
        if blob.soort != "document":
            continue
        rng.seed(f"{boom.plan.seed}:tekst:{blob.tekst_seed}")
        body = tekst(rng, blob.tekens)
        meta = ""
        if rng.random() < 0.3:
            auteur = rng.choice(("Jansen", "de Vries", "Bakker"))
            meta = f"{rng.choice(NAAMWOORDEN)} {rng.choice(NAAMWOORDEN)} {auteur}"
        boom.tellingen["tekens"] += len(body)
        boom.tellingen["documenten"] += 1
        yield (blob.sha, body, "", meta, 0, _tijd(DAG))


def vul(
    con: sqlite3.Connection,
    plan: Plan,
    *,
    wachtwoord_hash: str = "!",
    blob_standaard: dict[str, object] | None = None,
) -> Uitkomst:
    """Schrijft de boom van dit plan in een database die al op head staat (migratie 0010 met
    zijn triggers). wachtwoord_hash: voor elke gebruiker (zonder: inloggen kan niet).
    blob_standaard: preview, meta_version en render_version van elke blob (de app geeft de
    huidige versies mee, zodat de backfills bij het starten niets te doen hebben).

    De verbinding mag geen open transactie hebben; vul() zet isolation_level op None en
    commit per stuk."""
    standaard = {"preview": "none", "meta_version": 0, "render_version": 0}
    standaard.update(blob_standaard or {})
    uit = Uitkomst(plan=plan)
    klok = time.monotonic()
    boom = plan_boom(plan)
    uit.seconden["plannen"] = time.monotonic() - klok

    con.isolation_level = None
    for pragma in ("foreign_keys=ON", "synchronous=OFF", "cache_size=-262144", "temp_store=MEMORY"):
        con.execute(f"PRAGMA {pragma}")
    schrijf = _Schrijver(con)
    nu = _tijd(DAG)

    klok = time.monotonic()
    schrijf.rijen(
        'INSERT INTO "user" (id, email, display_name, password_hash, role, quota_bytes, '
        "root_id, created_at, totp_last_step, totp_failures) VALUES (?, ?, ?, ?, ?, 0, ?, ?, 0, 0)",
        ((g.id, g.email, g.naam.capitalize(), wachtwoord_hash, g.rol, g.root, nu)
         for g in boom.gebruikers),
    )  # fmt: skip
    rijen_delen = boom.delingen()
    schrijf.rijen(
        "INSERT INTO user_group (id, name, created_at) VALUES (?, ?, ?)",
        [(boom.groep_id, f"{GROEP} {plan.seed}", nu)],
    )
    schrijf.rijen(
        "INSERT INTO group_member (group_id, user_id) VALUES (?, ?)",
        ((boom.groep_id, lid) for lid in boom.leden),
    )
    schrijf.rijen(
        _BLOB_SQL,
        (
            (b.sha, b.size, b.refs, nu, b.mime, standaard["preview"],
             _tijd(b.genomen) if b.genomen else None, standaard["meta_version"],
             standaard["render_version"])
            for b in boom.blobs.values()
        ),
    )  # fmt: skip
    uit.seconden["blobs"] = time.monotonic() - klok

    klok = time.monotonic()
    schrijf.rijen(
        "INSERT INTO blob_text (blob_sha, body, ocr, meta, truncated, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        _teksten(boom),
    )
    uit.seconden["tekst"] = time.monotonic() - klok

    klok = time.monotonic()
    schrijf.rijen(_NODE_SQL, (n.rij() for n in boom.nodes))
    uit.seconden["nodes"] = time.monotonic() - klok

    klok = time.monotonic()
    nodes, albums, items = boom.albums()
    schrijf.rijen(_NODE_SQL, (n.rij() for n in nodes))
    schrijf.rijen("INSERT INTO album (id, description, cover_id) VALUES (?, ?, ?)", albums)
    schrijf.rijen(
        "INSERT INTO album_item (album_id, node_id, added_by, added_at, source, shared) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        items,
    )
    schrijf.rijen(_SHARE_SQL, rijen_delen)
    _fotos(boom, schrijf)
    personen, op_foto = boom.personen()
    schrijf.rijen(
        "INSERT INTO person (id, owner_id, name, search_name, hidden, cover_node, created_at, "
        "updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        personen,
    )
    schrijf.rijen(
        "INSERT INTO photo_person (person_id, node_id, owner_id, source, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        op_foto,
    )
    con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    uit.seconden["rest"] = time.monotonic() - klok

    t = boom.tellingen
    t["bestanden"] = sum(1 for n in boom.nodes if n.kind == "file")
    t["fotos"] = sum(1 for n in boom.nodes if n.kind == "file" and n.mime == "image/jpeg")
    t["nodes"] = len(boom.nodes) + len(nodes)
    uit.tellingen = t
    uit.gebruikers = {
        g.naam: {"id": g.id, "email": g.email, "root": g.root, "rol": g.rol}
        for g in boom.gebruikers
    }
    uit.rollen = dict(ROLLEN)
    return uit


def _fotos(boom: _Boom, schrijf: _Schrijver) -> None:
    """blob_meta, blob_hash en dup_pair per foto-inhoud; photo_geo per node (alleen voor de
    eigenaar, Keuze 25a). Elke foto-node met GPS in zijn inhoud krijgt een plek."""
    rng = random.Random(f"{boom.plan.seed}:fotos")
    fotos = [b for b in boom.blobs.values() if b.soort == "foto"]
    schrijf.rijen(
        "INSERT INTO blob_meta (blob_sha, camera, lens, gps) VALUES (?, ?, NULL, ?)",
        ((b.sha, rng.choice(CAMERAS), 1 if b.gps else 0) for b in fotos),
    )

    def stukken(h: int) -> tuple[int, int, int, int, int]:
        signed = h - (1 << 64) if h >= 1 << 63 else h
        return signed, h & 0xFFFF, (h >> 16) & 0xFFFF, (h >> 32) & 0xFFFF, (h >> 48) & 0xFFFF

    schrijf.rijen(
        "INSERT INTO blob_hash (blob_sha, state, phash, h0, h1, h2, h3, version) "
        "VALUES (?, 'ok', ?, ?, ?, ?, ?, 1)",
        ((b.sha, *stukken(b.phash or 0)) for b in fotos),
    )
    schrijf.rijen(
        "INSERT INTO dup_pair (a, b, dist, mad, level) VALUES (?, ?, ?, ?, ?)",
        ((a, b, d, 1.5, "zeker" if d <= 6 else "lijkt") for a, b, d in boom.dup_paren),
    )
    geo = []
    for node in boom.nodes:
        blob = boom.blobs.get(node.blob or "")
        if blob is None or blob.gps is None:
            continue
        lat, lon = blob.gps
        x, y = tegel16(lat, lon)
        geo.append((node.id, node.owner, lat, lon, x, y, "exif"))
    schrijf.rijen(
        "INSERT INTO photo_geo (node_id, owner_id, lat, lon, alt, x16, y16, place_id, country, "
        "admin1, source) VALUES (?, ?, ?, ?, NULL, ?, ?, NULL, NULL, NULL, ?)",
        geo,
    )
    boom.tellingen["gps"] = len(geo)
    boom.tellingen["dup_pair"] = len(boom.dup_paren)


# --- Met de app: de echte migratie en een wachtwoord -------------------------------------

WACHTWOORD = "bench-wachtwoord-lang-genoeg"


def app_blob() -> dict[str, object]:
    """preview, meta_version en render_version zoals de app ze nu zet: dan hebben de backfills
    bij het starten niets te doen. Importeert de app (dus niet in tools/zoekbench)."""
    from app import previews, render_meta

    return {
        "preview": "none",
        "meta_version": render_meta.VERSION,
        "render_version": previews.RENDER_VERSION,
    }


def maak(data_dir: Path, plan: Plan, *, wachtwoord: str = WACHTWOORD) -> Uitkomst:
    """Een nieuwe database in data_dir (platform.db) via de echte migratie, gevuld met de boom
    van dit plan. Bestaat er al een database, dan komt de boom erbij (de migratie doet dan
    niets meer)."""
    import asyncio

    from app.config import Settings
    from app.db import get_engine
    from app.migrate import upgrade
    from app.security import hash_password

    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    settings = Settings(data_dir=data_dir)

    async def migreer() -> None:
        await upgrade(settings)
        await get_engine(settings).dispose()

    klok = time.monotonic()
    asyncio.run(migreer())
    migratie = time.monotonic() - klok
    con = sqlite3.connect(data_dir / "platform.db")
    try:
        uit = vul(con, plan, wachtwoord_hash=hash_password(wachtwoord), blob_standaard=app_blob())
    finally:
        con.close()
    uit.seconden = {"migratie": migratie, **uit.seconden}
    return uit


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data", required=True, type=Path, help="de datamap (platform.db)")
    parser.add_argument("--n", type=int, default=100_000, help="aantal bestanden")
    parser.add_argument("--seed", type=int, default=6)
    parser.add_argument("--profiel", choices=PROFIELEN, default="basis")
    parser.add_argument("--uitkomst", type=Path, help="schrijf de uitkomst als JSON hierheen")
    args = parser.parse_args(argv)
    klok = time.monotonic()
    uit = maak(args.data, Plan(n=args.n, seed=args.seed, profiel=args.profiel))
    uit.seconden["totaal"] = time.monotonic() - klok
    if args.uitkomst:
        args.uitkomst.write_text(uit.json() + "\n")
    print(uit.kort())
    return 0


# --- app.cli: search-bench, search-rebuild, search-check en search-triggers (§3.5) ----------
#
# De commando's van Z1. Ze importeren de app pas als ze draaien, zoals maak(): tools/zoekbench
# neemt alleen de standaardbibliotheek over.
#
#     python -m app.cli search-bench --data /tmp/boom [--n 100000] [--seed 6] [--profiel groot]
#     python -m app.cli search-rebuild [--stuk 2000]
#     python -m app.cli search-triggers
#     python -m app.cli search-check [--n 1000] [--volledig] [--seed 6]
#
# search-bench maakt de boom van §3.6 in een eigen map, nooit in die van de app; het meten zelf
# doen test_perf_100k.py en e2e/perf/zoek_100k.py (§4.4). search-rebuild en search-triggers
# mogen terwijl de app draait (de herbouw in stukken, Keuze 12); search-check exit 1 bij een
# verschil, of als er een herbouw loopt.

COMMANDS = {
    "search-bench": "de boom van 100.000 voor het meten maken, in een eigen map (§3.6)",
    "search-rebuild": "de zoekindex in stukken herbouwen, terwijl de app draait",
    "search-check": "willekeurige nodes in de zoekindex met de bron vergelijken",
    "search-triggers": "de triggers van zoeken terugzetten, en daarna herbouwen",
}


def parsers(commands: argparse._SubParsersAction) -> None:
    bench = commands.add_parser("search-bench", help=COMMANDS["search-bench"])
    bench.add_argument("--data", required=True, type=Path, help="een nieuwe, eigen datamap")
    bench.add_argument("--n", type=int, default=100_000, help="aantal bestanden")
    bench.add_argument("--seed", type=int, default=6)
    bench.add_argument("--profiel", choices=PROFIELEN, default="basis")
    bench.add_argument("--uitkomst", type=Path, help="schrijf de uitkomst als JSON hierheen")
    rebuild = commands.add_parser("search-rebuild", help=COMMANDS["search-rebuild"])
    rebuild.add_argument("--stuk", type=int, default=2000, help="nodes per stuk (standaard 2000)")
    commands.add_parser("search-triggers", help=COMMANDS["search-triggers"])
    check = commands.add_parser("search-check", help=COMMANDS["search-check"])
    check.add_argument("--n", type=int, default=1000, help="zoveel nodes (standaard 1000)")
    check.add_argument(
        "--volledig", action="store_true", help="ook integrity-check van FTS5 (leest alles)"
    )
    check.add_argument("--seed", type=int, help="voor een vaste keuze van nodes")


def run(settings: Settings, args: argparse.Namespace) -> int:
    """Een van COMMANDS; daarna de database dicht, zoals de andere commando's van app.cli."""
    if args.command == "search-bench":
        return _bench(settings, args)
    if settings.database_url and not settings.database_url.startswith("sqlite"):
        print("Alleen voor SQLite: op PostgreSQL zoekt de app met LIKE.", file=sys.stderr)
        return 1
    import asyncio

    from app.db import get_engine
    from app.search import rebuild

    engine = get_engine(settings)

    async def herbouw() -> list[float]:
        async def stuk(cursor: int) -> None:
            nu = await rebuild.progress(engine)
            if nu is not None:
                print(f"tot num {cursor} van {nu[1]}", flush=True)

        return await rebuild.rebuild(engine, chunk=args.stuk, after_chunk=stuk)

    async def alles() -> int:
        try:
            if args.command == "search-check":
                return _checked(
                    await rebuild.check(engine, sample=args.n, full=args.volledig, seed=args.seed)
                )
            if args.command == "search-triggers":
                terug = await rebuild.restore_triggers(engine)
                print(
                    f"triggers weg: {len(terug.dropped)}, nieuw: {len(terug.created)}; "
                    f"nodes met een nieuw num: {terug.numbered}, met een ander mpath of dead: "
                    f"{terug.tree}; foto-rijen: {terug.photo_rows}"
                )
                args.stuk = rebuild.CHUNK
            tijden = await herbouw()
            if not tijden:
                print("Deze database heeft geen FTS5: niets te herbouwen.")
                return 0
            print(
                f"De index is herbouwd in {len(tijden)} stuk(ken); het langste duurde "
                f"{max(tijden):.0f} ms."
            )
            return 0
        finally:
            await engine.dispose()

    return asyncio.run(alles())


def _checked(found: Checked) -> int:
    if found.rebuilding:
        print("Er loopt een herbouw; controleer daarna.", file=sys.stderr)
        return 1
    for num, wat in found.bad[:50]:
        print(f"num {num}: {wat}")
    if len(found.bad) > 50:
        print(f"… en nog {len(found.bad) - 50}")
    for wat in found.integrity:
        print(f"integrity-check: {wat}")
    print(f"{found.sampled} node(s) vergeleken, {len(found.bad)} verschil(len).")
    if found.ok:
        return 0
    print("Herstel: python -m app.cli search-triggers (zet de triggers terug en herbouwt).")
    return 1


def _bench(settings: Settings, args: argparse.Namespace) -> int:
    data = args.data.resolve()
    if data == Path(settings.data_dir).resolve() or (data / "platform.db").exists():
        print(
            f"{data} heeft al een database (of is die van de app): kies een nieuwe map.",
            file=sys.stderr,
        )
        return 1
    klok = time.monotonic()
    uit = maak(data, Plan(n=args.n, seed=args.seed, profiel=args.profiel))
    uit.seconden["totaal"] = time.monotonic() - klok
    if args.uitkomst:
        args.uitkomst.write_text(uit.json() + "\n")
    print(uit.kort())
    return 0


def zoek_binnen(woorden: list[str], prefix: str) -> list[str]:
    """De woorden uit een gesorteerde lijst die met prefix beginnen (voor vragenlijsten)."""
    begin = bisect.bisect_left(woorden, prefix)
    eind = bisect.bisect_left(woorden, prefix + "\U0010ffff")
    return woorden[begin:eind]


if __name__ == "__main__":
    sys.exit(main())
