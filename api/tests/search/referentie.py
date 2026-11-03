"""De referentie: zoeken in gewoon Python, de definitie van "wat moet gevonden worden"
(bouwontwerp fase 6, §4.3; Keuze 4, 5 en 25a). Z1 legt de app ernaast: elke vraag van
vragen.tsv op de boom van 2.000 nodes (BOOM, uit app/search/bench.py) en op de wereld, door de
app en door deze module; de sets moeten gelijk zijn.

    boom = Referentie.lees(conn)                   # een synchrone verbinding, database op head
    uit = boom.zoek(user_id, "factuur 2024")
    uit.woorden    # treffers op hele woorden (FTS: naam, titel, tekst, OCR, meta)
    uit.namen      # daarnaast: stukken van namen (fts_name), niet al in woorden
    uit.alles      # beide

De regels (wat de app moet doen, niet hoe):

- **De vraag** (Keuze 5): NFKC, dan fold() (kleine letters, zonder accenten, ß en ẞ als "ss"),
  gesplitst op alles wat geen letter of cijfer is. Hooguit 200 tekens en 10 woorden, anders
  TeLang (de app: 400 search_query_too_long). Wat tussen aanhalingstekens staat, is een frase
  (een aanhalingsteken zonder sluiter loopt tot het eind). Operators bestaan niet: AND, NEAR,
  *, : en ( zijn gewone tekens of woorden.
- **Hele woorden**: elk woord (of elke frase) moet in de node staan, in een van de kolommen
  naam, titel (media_item), tekst, OCR of meta; niet per se in dezelfde kolom. Een frase:
  opeenvolgende woorden in één kolom.
- **Voorvoegsel**: het laatste woord, als het ≥ 3 tekens heeft en de vraag eindigt op een
  letter of cijfer (niet op een spatie of een aanhalingsteken).
- **Stukken van namen**: elk woord staat als stuk in de gevouwen naam ("port" in "rapport").
  Ook voor korte woorden (de LIKE van Keuze 5).
- **Wat je ziet** (Keuze 4, access.py): levend (de node en elke map erboven niet in de
  prullenbak), geen album, en onder je eigen hoofdmap (niet voor een gast) of onder een
  geldige deling met jou of een groep van jou (geen link, niet verlopen, niet van jezelf).
  Een album delen geeft niets om te zoeken. in_: alleen onder die map.
- **OCR-tekst uit een foto** (photo_ocr) telt alleen voor de eigenaar van de node (Keuze 25a):
  Bram vindt bonwoordy niet op Eva's gedeelde foto. De view search_src zet die tekst in
  dezelfde kolom ocr als de OCR van een PDF (blob_text.ocr), die wel voor iedereen telt die
  het bestand ziet: de app moet het verschil zelf maken (zie docs/fase-6/z0-e2e.md).

    uv run python -m tests.search.referentie --vragen    # schrijft vragen.tsv opnieuw
"""

from __future__ import annotations

import random
import re
import sys
import unicodedata
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from app.search import bench
from app.text import fold

MAX_TEKENS = 200
MAX_WOORDEN = 10
PREFIX_MIN = 3
# De boom van de vergelijking: ±2.000 nodes, korte teksten (dan blijft de referentie snel)
BOOM = bench.Plan(n=1_840, seed=6, tekens=2_000)
VRAGEN = Path(__file__).with_name("vragen.tsv")
KOLOMMEN = ("name", "extra", "body", "ocr", "meta")
_SPLIT = re.compile(r"[\W_]+")


class TeLang(ValueError):
    """Meer dan 200 tekens of 10 woorden (search_query_too_long)."""


def vouw(tekst: str) -> str:
    return fold(unicodedata.normalize("NFKC", tekst))


def woorden(tekst: str) -> list[str]:
    return [w for w in _SPLIT.split(vouw(tekst)) if w]


@dataclass(frozen=True)
class Vraag:
    """termen: elk een tuple van woorden (meer dan één: een frase). prefix: het laatste woord
    van de laatste term is een voorvoegsel. alle: alle woorden, voor de stukken van namen."""

    termen: tuple[tuple[str, ...], ...]
    prefix: bool
    alle: tuple[str, ...]

    @property
    def kort(self) -> bool:
        """Alleen woorden van 1–2 tekens."""
        return all(len(w) < PREFIX_MIN for w in self.alle)


def ontleed(q: str) -> Vraag | None:
    """De vraag volgens Keuze 5; None als er niets te zoeken is."""
    if len(q) > MAX_TEKENS:
        raise TeLang(q)
    q = unicodedata.normalize("NFKC", q)
    termen: list[tuple[str, ...]] = []
    for i, stuk in enumerate(q.split('"')):
        ws = woorden(stuk)
        if not ws:
            continue
        if i % 2:  # tussen aanhalingstekens
            termen.append(tuple(ws))
        else:
            termen.extend((w,) for w in ws)
    alle = tuple(w for t in termen for w in t)
    if not alle:
        return None
    if len(alle) > MAX_WOORDEN:
        raise TeLang(q)
    laatste = q.rstrip("\N{ZERO WIDTH SPACE}")[-1:]
    prefix = bool(laatste) and laatste.isalnum() and len(alle[-1]) >= PREFIX_MIN
    return Vraag(tuple(termen), prefix, alle)


@dataclass
class Uitkomst:
    woorden: set[uuid.UUID] = field(default_factory=set)
    namen: set[uuid.UUID] = field(default_factory=set)

    @property
    def alles(self) -> set[uuid.UUID]:
        return self.woorden | self.namen


@dataclass
class _Node:
    id: uuid.UUID
    parent: uuid.UUID | None
    owner: uuid.UUID
    kind: str
    naam: str  # gevouwen
    weg: bool
    kolommen: dict[str, list[str]]  # zonder de OCR-tekst van een foto
    foto_ocr: list[str]  # alleen voor de eigenaar


def _uuid(waarde) -> uuid.UUID:
    if isinstance(waarde, uuid.UUID):
        return waarde
    return uuid.UUID(hex=str(waarde))


def _tijd(waarde) -> datetime | None:
    if waarde is None:
        return None
    if isinstance(waarde, datetime):
        moment = waarde
    else:
        moment = datetime.fromisoformat(str(waarde))
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


class Referentie:
    """Een momentopname van de database: nodes, tekst, delingen en groepen."""

    def __init__(self) -> None:
        self.nodes: dict[uuid.UUID, _Node] = {}
        self.rollen: dict[uuid.UUID, str] = {}
        self.wortels: dict[uuid.UUID, uuid.UUID] = {}  # gebruiker -> hoofdmap
        self.groepen: dict[uuid.UUID, set[uuid.UUID]] = defaultdict(set)  # gebruiker -> groepen
        self.delingen: list[tuple[uuid.UUID, uuid.UUID | None, uuid.UUID | None, datetime | None]]
        self.delingen = []
        self.index: dict[str, set[uuid.UUID]] = defaultdict(set)  # woord -> nodes
        self.foto_index: dict[str, set[uuid.UUID]] = defaultdict(set)
        self.gesorteerd: list[str] = []

    @classmethod
    def lees(cls, conn) -> Referentie:
        """conn: een synchrone SQLAlchemy-verbinding (of iets met exec_driver_sql)."""
        r = cls()
        sql = conn.exec_driver_sql
        tekst = {
            row[0]: (row[1] or "", row[2] or "", row[3] or "")
            for row in sql("SELECT blob_sha, body, ocr, meta FROM blob_text")
        }
        titels: dict[str, list[str]] = defaultdict(list)
        for node_id, titel in sql("SELECT node_id, title FROM media_item"):
            titels[str(node_id)].append(titel or "")
        foto_ocr = {str(row[0]): row[1] or "" for row in sql("SELECT node_id, text FROM photo_ocr")}
        rijen = sql(
            "SELECT id, parent_id, owner_id, kind, name, deleted_at, blob_id FROM node"
        ).all()
        for node_id, parent, owner, kind, naam, weg, blob in rijen:
            body, ocr, meta = tekst.get(blob, ("", "", ""))
            node = _Node(
                id=_uuid(node_id),
                parent=_uuid(parent) if parent else None,
                owner=_uuid(owner),
                kind=kind,
                naam=vouw(naam),
                weg=weg is not None,
                kolommen={
                    "name": woorden(naam),
                    "extra": woorden(" ".join(titels.get(str(node_id), []))),
                    "body": woorden(body),
                    "ocr": woorden(ocr),
                    "meta": woorden(meta),
                },
                foto_ocr=woorden(foto_ocr.get(str(node_id), "")),
            )
            r.nodes[node.id] = node
            for kolom in node.kolommen.values():
                for w in kolom:
                    r.index[w].add(node.id)
            for w in node.foto_ocr:
                r.foto_index[w].add(node.id)
        r.gesorteerd = sorted(set(r.index) | set(r.foto_index))
        for user_id, rol, root in sql('SELECT id, role, root_id FROM "user"'):
            r.rollen[_uuid(user_id)] = rol
            if root:
                r.wortels[_uuid(user_id)] = _uuid(root)
        for group_id, user_id in sql("SELECT group_id, user_id FROM group_member"):
            r.groepen[_uuid(user_id)].add(_uuid(group_id))
        for node_id, grantee, group_id, token, verloopt in sql(
            "SELECT node_id, grantee_id, group_id, token, expires_at FROM share"
        ):
            if token is None:
                r.delingen.append(
                    (
                        _uuid(node_id),
                        _uuid(grantee) if grantee else None,
                        _uuid(group_id) if group_id else None,
                        _tijd(verloopt),
                    )
                )
        return r

    # -- wat je ziet

    def keten(self, node_id: uuid.UUID) -> list[uuid.UUID]:
        keten, cur = [], node_id
        while cur is not None and cur in self.nodes and len(keten) <= len(self.nodes):
            keten.append(cur)
            cur = self.nodes[cur].parent
        return keten

    def levend(self, node_id: uuid.UUID) -> bool:
        return not any(self.nodes[k].weg for k in self.keten(node_id))

    def wortels_van(self, user: uuid.UUID, nu: datetime | None = None) -> set[uuid.UUID]:
        """De eigen hoofdmap (niet voor een gast) en elke geldige deling met user."""
        nu = nu or datetime.now(UTC)
        uit = set()
        if self.rollen.get(user) != "guest" and user in self.wortels:
            uit.add(self.wortels[user])
        groepen = self.groepen.get(user, set())
        for node_id, grantee, group_id, verloopt in self.delingen:
            if grantee != user and group_id not in groepen:
                continue
            if verloopt is not None and verloopt <= nu:
                continue
            node = self.nodes.get(node_id)
            if node is None or node.owner == user or node.kind == "album":
                continue
            uit.add(node_id)
        return uit

    def zichtbaar(
        self, user: uuid.UUID | None, *, in_: uuid.UUID | None = None, nu: datetime | None = None
    ) -> set[uuid.UUID]:
        """De nodes die zoeken voor user mag geven. user None: alles wat levend is (voor een
        vergelijking met de index zelf)."""
        wortels = None if user is None else self.wortels_van(user, nu)
        uit = set()
        for node_id, node in self.nodes.items():
            if node.kind == "album":
                continue
            keten = self.keten(node_id)
            if any(self.nodes[k].weg for k in keten):
                continue
            if wortels is not None and not wortels.intersection(keten):
                continue
            if in_ is not None and in_ not in keten:
                continue
            uit.add(node_id)
        return uit

    # -- zoeken

    def _met_woord(self, w: str, prefix: bool, index: dict[str, set[uuid.UUID]]) -> set[uuid.UUID]:
        if not prefix:
            return set(index.get(w, ()))
        uit: set[uuid.UUID] = set()
        for woord in bench.zoek_binnen(self.gesorteerd, w):
            uit |= index.get(woord, set())
        return uit

    def _heeft_frase(self, kolom: list[str], frase: tuple[str, ...], prefix: bool) -> bool:
        n = len(frase)
        for i in range(len(kolom) - n + 1):
            if kolom[i : i + n - 1] != list(frase[:-1]):
                continue
            laatste = kolom[i + n - 1]
            if laatste == frase[-1] or (prefix and laatste.startswith(frase[-1])):
                return True
        return False

    def _term(self, term: tuple[str, ...], prefix: bool, user: uuid.UUID | None) -> set[uuid.UUID]:
        """De nodes waar deze term in staat; de OCR-tekst van een foto alleen bij de eigenaar."""
        foto = {n for n in self._met_woord(term[-1], prefix, self.foto_index)
                if user is None or self.nodes[n].owner == user}  # fmt: skip
        if len(term) == 1:
            return self._met_woord(term[0], prefix, self.index) | foto
        kandidaten = self._met_woord(term[-1], prefix, self.index) | foto
        for w in term[:-1]:
            kandidaten &= self.index.get(w, set()) | self.foto_index.get(w, set())
        uit = set()
        for node_id in kandidaten:
            node = self.nodes[node_id]
            kolommen = list(node.kolommen.values())
            if user is None or node.owner == user:
                kolommen.append(node.foto_ocr)
            if any(self._heeft_frase(k, term, prefix) for k in kolommen):
                uit.add(node_id)
        return uit

    def zoek(
        self,
        user: uuid.UUID | None,
        q: str,
        *,
        in_: uuid.UUID | None = None,
        nu: datetime | None = None,
    ) -> Uitkomst:
        """Wat de app voor user moet vinden met vraag q (TeLang bij een te lange vraag)."""
        vraag = ontleed(q)
        if vraag is None:
            return Uitkomst()
        mag = self.zichtbaar(user, in_=in_, nu=nu)
        treffers = set(mag)
        for i, term in enumerate(vraag.termen):
            laatste = i == len(vraag.termen) - 1
            treffers &= self._term(term, vraag.prefix and laatste, user)
            if not treffers:
                break
        namen = {n for n in mag if all(w in self.nodes[n].naam for w in vraag.alle)}
        return Uitkomst(woorden=treffers, namen=namen - treffers)

    def op_naam(
        self,
        user: uuid.UUID,
        q: str,
        *,
        in_: uuid.UUID | None = None,
        nu: datetime | None = None,
    ) -> set[uuid.UUID]:
        """Zoeken zoals in 0.7.0 (LikeFallback, ook de terugval van Keuze 10): elk woord van
        fold(q), gesplitst op spaties (hooguit 10), staat in de gevouwen naam; zonder je eigen
        hoofdmap en zonder in_ zelf."""
        stukken = fold(q).split()[:MAX_WOORDEN]
        zonder = {in_} if in_ is not None else {self.wortels.get(user)}
        return {
            n
            for n in self.zichtbaar(user, in_=in_, nu=nu) - zonder
            if all(w in self.nodes[n].naam for w in stukken)
        }


# --- De vragenlijst ------------------------------------------------------------------------


def maak_vragen() -> list[tuple[str, str]]:
    """De 200 vragen van §4.3 als (soort, vraag): vast, uit de woordenschat van bench.py."""
    rng = random.Random("vragen:6")
    gewoon = [w for w in bench.NL + bench.EN if len(w) >= 5 and w.isalpha() and w.isascii()]
    kunst = bench.WOORDEN[len(bench.NL) + len(bench.EN) + len(bench.DE) + len(bench.EXTRA) :]
    vragen: list[tuple[str, str]] = []

    def voeg(soort: str, lijst) -> None:
        vragen.extend((soort, v) for v in lijst)

    voeg("heel", rng.sample(gewoon, 18) + rng.sample(kunst[:2000], 12))
    voeg(
        "twee",
        [f"{a} {b}" for a, b in zip(rng.sample(gewoon, 20), rng.sample(gewoon, 20), strict=True)],
    )
    voeg(
        "voorvoegsel",
        [w[: rng.randint(3, 5)] for w in rng.sample(gewoon, 18) + rng.sample(kunst[:3000], 12)],
    )
    voeg(
        "naamstuk",
        ["port", "img", "foto", "ouder", "_2019", "0612", "ectuur", "aard"]
        + [w[1:5] for w in rng.sample(bench.NAAMWOORDEN[:400], 7)],
    )
    voeg(
        "kort",
        [
            "de",
            "q3",
            "a4",
            "nl",
            "vu",
            "op",
            "en",
            "2",
            "it",
            "of",
            "de q3",
            "a4 nl",
            "x",
            "kp",
            "in",
        ],
    )
    voeg(
        "diakriet",
        ["café", "cafe", "reünie", "reunie", "coördinatie", "coordinatie", "één", "naïef",
         "naief", "patiënt", "patient", "ideeën", "ideeen", "résumé", "resume", "façade",
         "facade", "überhaupt", "uberhaupt", "CAFÉ"],
    )  # fmt: skip
    voeg(
        "ss",
        ["grosse", "große", "GROẞE", "strasse", "Straße", "STRASSE", "fussball", "fußball",
         "grüße", "grusse", "weiß", "weiss"],
    )  # fmt: skip
    voeg(
        "cijfer",
        ["2019", "2024", "2026", "117", "nl91abna0417164300", "2016 factuur", "q3 2024", "1",
         "20", "2025 rapport", "iban nl91abna0417164300", "202"],
    )  # fmt: skip
    voeg(
        "frase",
        ['"de factuur"', '"annual report"', '"het project"', '"van de"', '"in de"',
         '"factuur voor"', '"de klant"', '"meeting minutes"', '"in het"', '"de rekening"',
         '"op de"', '"het verslag"', '"de offerte"', '"voor de"', '"en de factuur"', '"de fact'],
    )  # fmt: skip
    voeg(
        "leeg",
        ["xyzzyqq", "onbestaandwoord", "qqqq", "zzzzzzz", "nietbestaand 2019", "factuur xyzzyqq",
         '"factuur offerte rekening jaarverslag"', "wwwwvvvv", "kqkqkq", "ççç"],
    )  # fmt: skip
    voeg(
        "operator",
        ["factuur AND offerte", "NEAR(factuur offerte)", "fact*", '"factuur', "name:factuur",
         "body:rapport", "factuur OR offerte", "NOT factuur", "-factuur", "factuur^2",
         "(factuur)", "a:b", "*", '""', "AND", "NEAR", "factuur NEAR offerte", "{name}: test",
         "factuur + offerte", "'factuur'"],
    )  # fmt: skip
    if len(vragen) != 200:
        raise AssertionError(f"{len(vragen)} vragen, niet 200")
    return vragen


def schrijf_vragen(pad: Path = VRAGEN) -> None:
    regels = ["# soort\tvraag  (gemaakt door referentie.maak_vragen(); niet met de hand)"]
    regels += [f"{soort}\t{vraag}" for soort, vraag in maak_vragen()]
    pad.write_text("\n".join(regels) + "\n", encoding="utf-8")


def lees_vragen(pad: Path = VRAGEN) -> list[tuple[str, str]]:
    uit = []
    for regel in pad.read_text(encoding="utf-8").splitlines():
        if regel and not regel.startswith("#"):
            soort, vraag = regel.split("\t", 1)
            uit.append((soort, vraag))
    return uit


if __name__ == "__main__":
    if sys.argv[1:] == ["--vragen"]:
        schrijf_vragen()
        print(f"{VRAGEN}: {len(maak_vragen())} vragen")
    else:
        print(__doc__)
