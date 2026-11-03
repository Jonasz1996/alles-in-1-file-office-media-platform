#!/usr/bin/env python3
# ruff: noqa: E501
"""Zoekbench: hoe snel zoeken is op déze machine (bouwontwerp fase 6, Keuze 54; spike S9).

Alleen de standaardbibliotheek. Maakt het schema van migratie 0010 (een bevroren kopie, onderaan)
en een boom van 100.000 bestanden in een tijdelijke map, meet zoeken voor "eigenaar" (één
hoofdmap) en "alles" (30 hoofdmappen), en ruimt alles weer op. Er verandert niets aan de app of
aan /data: het draait naast de app, in dezelfde container, met dezelfde SQLite.

In de container van 0.7.1 (of later), zonder iets te installeren:

    docker compose exec -T platform python -I - --los < tools/zoekbench/bench.py

Of met een gewone python3 (≥ 3.10, een SQLite met FTS5):

    python3 tools/zoekbench/bench.py --los [--n 100000] [--seed 6] [--map PAD] [--json UIT]

--los      alles in een tijdelijke map: onder /data/cache als die er is en schrijfbaar is, anders
           in de tijdelijke map van het systeem. Bij 100.000 even ±1,1 GB (de database en de
           WAL), daarna ±530 MB. Na afloop weg, ook na een fout, Ctrl-C of SIGTERM. Z7 maakt
           hiervan `app.cli bench --los`, met de uitkomst op /beheer.
--vormen   meet ook de vormen van Keuze 4a (alleen filters, de kaart, de duplicaten; spike S10).
           Er komt dan een gebruiker "fotograaf" bij met 55.000 foto's met GPS (de poort van
           §4.4 voor de kaart en de duplicaten).
--triggers vult via de triggers (zoals de app schrijft) in plaats van met één herbouw aan het
           eind; trager, en dan is de bouwtijd die van de echte triggers (spike S7).

De boom lijkt op die van §3.6: 30 gebruikers, ±5.900 mappen tot diepte 5, 55% foto's (60% met
GPS), 20% documenten met samen ±235 miljoen tekens tekst (Zipf over een vaste woordenschat met
Nederlandse en Engelse woorden, diakrieten, ß en kunstwoorden), 2% kopieën bij een ander, 3% van
de mappen en 1% van de bestanden in de prullenbak.

Een vraag gaat zoals in Keuze 4 en 9: ronde A gerangschikt met een grens van 120 ms; stopt die,
dan ronde B zonder volgorde met wat er over is van 120 + 40 ms (spike S1); stopt ook die, dan
ronde C op de namen (trigram). Gemeten wordt de tijd tot het antwoord, de tijd van ronde C apart,
en de gerangschikte vraag zonder grens.

Uitvoer: een tabel per bereik (p50, p95, max in ms; hoe vaak B of C nodig was), en met --json
alles als JSON. Exitcode 0; 3 als SQLite geen FTS5 kent (dan zoekt de app met LIKE).
"""

from __future__ import annotations

import argparse
import contextlib
import json
import math
import os
import platform
import random
import shutil
import signal
import sqlite3
import statistics
import sys
import tempfile
import threading
import time
import unicodedata
from datetime import UTC, datetime, timedelta
from pathlib import Path

DAG = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)
GEBRUIKERS = 30
MAPPEN = 0.059
FOTO, DOCUMENT = 0.55, 0.20  # de rest: overig
KOPIE_ANDER = 0.02
PRULLENBAK_MAPPEN, PRULLENBAK_BESTANDEN = 0.03, 0.01
GPS = 0.60
BIJNA = 0.01  # foto's met een bijna-gelijke variant (dup_pair)
TEKENS_GEMIDDELD = 11_750
TEKST_MAX = 1_048_576
DIEPTE = 5
FOTOGRAAF = 55_000
GRENS_A, GRENS_B = 120, 40  # ms, Keuze 9
PAGINA = 30

DOC_SOORTEN = (
    ("pdf", "application/pdf", 0.40),
    ("docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", 0.30),
    ("txt", "text/plain", 0.15),
    ("odt", "application/vnd.oasis.opendocument.text", 0.05),
    ("xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", 0.10),
)
OVERIG = (
    ("zip", "application/zip"),
    ("mp3", "audio/mpeg"),
    ("mp4", "video/mp4"),
    ("bin", "application/octet-stream"),
    ("pptx", "application/vnd.openxmlformats-officedocument.presentationml.presentation"),
)
MAPNAMEN = (
    "Werk Administratie Belasting Projecten Foto's Vakantie School Huis Archief Recepten "
    "Notulen Offertes Facturen Contracten Muziek Video Scans Reizen Familie Verzekering"
).split()

# Echte woorden (NL en EN, met diakrieten en ß), daarna kunstwoorden uit lettergrepen
ECHT = (
    "de het een en van in is dat op te zijn met voor niet aan er om ook als bij of door naar "
    "the of and to in is for on with as by at from this that be are was have not "
    "factuur offerte contract vergadering notulen rapport verslag begroting jaarverslag "
    "belasting klant project planning budget kosten datum bijlage onderwerp groet levering "
    "betaling huur woning school vakantie reis hotel energie salaris recept invoice meeting "
    "report budget schedule delivery payment contract agreement summary quarter annual "
    "café privé reünie coöperatie überweisung Straße naïef ideeën financiële geïnteresseerd "
    "crème brûlée façade déjà résumé jalapeño smørrebrød Größe weiß Fuß"
).split()
LETTERGREPEN = (
    "ba be bo bi da de di do ga ge go ka ke ko la le lo li ma me mo mi na ne no ni pa pe po "
    "ra re ro ri sa se so si ta te to ti va ve vo wa we ze zo"
).split()


def fold(text: str) -> str:
    """Zoals app/text.py: zonder accenten en hoofdletters (ß wordt ss)."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold()


def woordenschat(rng: random.Random, kunst: int = 15_000) -> list[str]:
    out, gezien = list(ECHT), set(ECHT)
    while len(out) < len(ECHT) + kunst:
        begin = rng.choice(("ver", "ont", "be", "ge", "", "", "", "re", "in", "over"))
        w = begin + "".join(rng.choice(LETTERGREPEN) for _ in range(rng.randint(2, 4)))
        if w not in gezien:
            gezien.add(w)
            out.append(w)
    return out


def zipf_gewichten(n: int, s: float = 1.07) -> list[float]:
    acc, out = 0.0, []
    for r in range(1, n + 1):
        acc += 1.0 / r**s
        out.append(acc)
    return out


def tegel16(lat: float, lon: float) -> tuple[int, int]:
    """De tegel op zoom 16 (Webmercator), zoals photo_geo.x16 en y16."""
    n = 1 << 16
    x = int((lon + 180.0) / 360.0 * n)
    y = int((1.0 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2.0 * n)
    return min(max(x, 0), n - 1), min(max(y, 0), n - 1)


def _tijd(t: datetime) -> str:
    return t.strftime("%Y-%m-%d %H:%M:%S.%f")


# --- De boom ---------------------------------------------------------------------------------


class Boom:
    """De boom in het geheugen, als rijen voor SQLite. Dezelfde seed geeft dezelfde boom."""

    def __init__(self, n: int, seed: int, fotograaf: bool = False) -> None:
        self.rng = random.Random(seed)
        self.n = n
        self.users: list[tuple] = []  # (id, naam, root)
        self.nodes: list[tuple] = []  # in volgorde: een ouder vóór zijn kinderen
        self.blobs: dict[str, tuple] = {}
        self.docs: list[tuple[str, int]] = []  # (sha, tekens)
        self.geo: list[tuple] = []
        self.pairs: list[tuple] = []
        self.voc = woordenschat(self.rng)
        self._mappen: dict[str, list[tuple[str, int]]] = {}
        self._bestanden: list[tuple[str, str, str]] = []  # (sha, eigenaar, soort)
        self._bouw()
        if fotograaf:
            self._fotograaf()

    def _id(self) -> str:
        return f"{self.rng.getrandbits(128):032x}"

    def _blob(self, mime: str, size: int) -> str:
        sha = f"{self.rng.getrandbits(256):064x}"
        self.blobs[sha] = (sha, size, 1, _tijd(DAG), mime)
        return sha

    def _naam(self, k: int = 2) -> str:
        return " ".join(self.rng.choice(self.voc[:3000]) for _ in range(k))

    def _moment(self) -> datetime:
        return DAG - timedelta(seconds=self.rng.randrange(10 * 365 * 86400))

    def _node(self, parent, owner, name, kind, mime=None, blob=None, deleted=False, taken=None):
        node_id = self._id()
        t = self._moment()
        weg = _tijd(DAG - timedelta(days=self.rng.randint(1, 20))) if deleted else None
        self.nodes.append(
            (node_id, parent, owner, name, kind, mime, 1000 if blob else 0, blob, _tijd(t),
             _tijd(t), weg, fold(name), _tijd(taken) if taken else None)
        )  # fmt: skip
        return node_id

    def _bouw(self) -> None:
        rng = self.rng
        gewicht = [2600 + 3600 * math.exp(-i / 4) for i in range(GEBRUIKERS)]
        totaal = sum(gewicht)
        per = [max(10, round(self.n * g / totaal)) for g in gewicht]
        per[0] += self.n - sum(per)
        for i, aantal in enumerate(per):
            uid, naam = self._id(), f"b{i:02}"
            root = self._node(None, uid, "", "dir")
            self.users.append((uid, naam, root))
            mappen = [(root, 0)]
            for j in range(max(1, round(aantal * MAPPEN))):
                ouder, diepte = rng.choice([m for m in mappen if m[1] < DIEPTE] or mappen[:1])
                naam_map = f"{rng.choice(MAPNAMEN)} {j}"
                weg = rng.random() < PRULLENBAK_MAPPEN
                mappen.append((self._node(ouder, uid, naam_map, "dir", deleted=weg), diepte + 1))
            self._mappen[uid] = mappen
            for j in range(aantal):
                self._bestand(uid, rng.choice(mappen)[0], j)
        # Kopieën bij een ander: dezelfde inhoud (sha), een eigen node
        for _ in range(round(self.n * KOPIE_ANDER)):
            sha, eigenaar, soort = rng.choice(self._bestanden)
            uid = rng.choice([u for u in self.users if u[0] != eigenaar])[0]
            self.blobs[sha] = (*self.blobs[sha][:2], self.blobs[sha][2] + 1, *self.blobs[sha][3:])
            mime = self.blobs[sha][4]
            self._node(rng.choice(self._mappen[uid])[0], uid, f"kopie {self._naam(1)}.{soort}",
                       "file", mime, sha)  # fmt: skip

    def _bestand(self, uid: str, map_id: str, j: int) -> None:
        rng = self.rng
        weg = rng.random() < PRULLENBAK_BESTANDEN
        r = rng.random()
        if r < FOTO:
            sha = self._blob("image/jpeg", rng.randint(200_000, 6_000_000))
            taken = self._moment()
            node = self._node(map_id, uid, f"IMG_{j:05}.jpg", "file", "image/jpeg", sha, weg, taken)
            if rng.random() < GPS:
                self._gps(node, uid)
            if rng.random() < BIJNA:
                self._variant(map_id, uid, f"IMG_{j:05} bewerkt.jpg", sha)
            self._bestanden.append((sha, uid, "jpg"))
        elif r < FOTO + DOCUMENT:
            ext, mime = self._doc_soort()
            sha = self._blob(mime, rng.randint(20_000, 3_000_000))
            tekens = min(
                TEKST_MAX, max(200, int(rng.lognormvariate(math.log(TEKENS_GEMIDDELD) - 0.5, 1.0)))
            )
            self.docs.append((sha, tekens))
            jaar = rng.randint(2016, 2026)
            self._node(map_id, uid, f"{self._naam(2)} {jaar}.{ext}", "file", mime, sha, weg)
            self._bestanden.append((sha, uid, ext))
        else:
            ext, mime = rng.choice(OVERIG)
            sha = self._blob(mime, rng.randint(1_000, 50_000_000))
            self._node(map_id, uid, f"{self._naam(1)}.{ext}", "file", mime, sha, weg)
            self._bestanden.append((sha, uid, ext))

    def _doc_soort(self) -> tuple[str, str]:
        r, acc = self.rng.random(), 0.0
        for ext, mime, p in DOC_SOORTEN:
            acc += p
            if r < acc:
                return ext, mime
        return DOC_SOORTEN[0][:2]

    def _gps(self, node: str, uid: str) -> None:
        # Vooral Nederland en Europa, een deel verder weg
        if self.rng.random() < 0.7:
            lat, lon = self.rng.uniform(50.8, 53.5), self.rng.uniform(3.4, 7.2)
        else:
            lat, lon = self.rng.uniform(-40, 65), self.rng.uniform(-120, 150)
        x, y = tegel16(lat, lon)
        self.geo.append((node, uid, lat, lon, x, y, "exif"))

    def _variant(self, map_id: str, uid: str, naam: str, sha: str) -> None:
        """Een bijna-gelijke foto naast het origineel (bewerkt, kleiner), met hun paar in
        dup_pair zoals de job hash het schrijft (a < b)."""
        ander = self._blob("image/jpeg", self.rng.randint(100_000, 2_000_000))
        self._node(map_id, uid, naam, "file", "image/jpeg", ander, taken=self._moment())
        a, b = sorted((sha, ander))
        self.pairs.append((a, b, self.rng.randint(0, 10), self.rng.uniform(0.5, 4.0),
                           self.rng.choice(("zeker", "lijkt"))))  # fmt: skip

    def _fotograaf(self) -> None:
        """Eén eigenaar met 55.000 foto's met GPS: de poort van de kaart en de duplicaten."""
        rng = self.rng
        uid = self._id()
        root = self._node(None, uid, "", "dir")
        self.users.append((uid, "fotograaf", root))
        jaren = {}
        for jaar in range(2016, 2027):
            jaren[jaar] = [self._node(root, uid, f"{jaar}", "dir")]
            for maand in range(1, 13):
                jaren[jaar].append(self._node(jaren[jaar][0], uid, f"{jaar}-{maand:02}", "dir"))
        vorige = None
        for j in range(FOTOGRAAF):
            jaar = rng.randint(2016, 2026)
            if vorige and rng.random() < 0.005:
                sha = vorige  # precies gelijk: dezelfde foto twee keer
                self.blobs[sha] = (
                    *self.blobs[sha][:2],
                    self.blobs[sha][2] + 1,
                    *self.blobs[sha][3:],
                )
            else:
                sha = self._blob("image/jpeg", rng.randint(200_000, 6_000_000))
            node = self._node(rng.choice(jaren[jaar][1:]), uid, f"DSC_{j:05}.jpg", "file",
                              "image/jpeg", sha, rng.random() < PRULLENBAK_BESTANDEN,
                              self._moment())  # fmt: skip
            self._gps(node, uid)
            if rng.random() < BIJNA:
                self._variant(jaren[jaar][1], uid, f"DSC_{j:05} bewerkt.jpg", sha)
            vorige = sha

    def teksten(self, stuk: int = 200):
        """De tekst per document, in stukken (rij voor blob_text). Uit een vaste reeks woorden
        (Zipf), per document een stuk vanaf een willekeurige plek."""
        rng = random.Random(self.rng.random())
        cw = zipf_gewichten(len(self.voc))
        reeks = rng.choices(self.voc, cum_weights=cw, k=400_000)
        reeks = reeks + reeks[:200_000]
        now = _tijd(DAG)
        rijen = []
        for sha, tekens in self.docs:
            woorden, delen, lengte = tekens // 7 + 1, [], 0
            while lengte < tekens:
                start = rng.randrange(400_000)
                deel = " ".join(reeks[start : start + min(woorden, 200_000)])
                delen.append(deel)
                lengte += len(deel) + 1
            rijen.append((sha, " ".join(delen)[:tekens], "", "", 0, now))
            if len(rijen) >= stuk:
                yield rijen
                rijen = []
        if rijen:
            yield rijen


# --- Schema en vullen --------------------------------------------------------------------


def maak_schema(con: sqlite3.Connection) -> None:
    for sql in DDL:
        con.execute(sql)
    con.execute("INSERT INTO search_seq VALUES (1, 1)")
    con.execute("INSERT INTO search_state VALUES (1, NULL, NULL, NULL)")


def vul(con: sqlite3.Connection, boom: Boom, *, triggers: bool = False) -> dict:
    """De boom in de database. Zonder triggers: de cursor van de herbouw op 0 (de triggers zetten
    num, pad en dead, maar schrijven niets naar FTS), en aan het eind één herbouw, zoals de
    migratie. Met triggers: eerst de tekst, dan de nodes, en elke node_ai schrijft zijn rij."""
    t0 = time.perf_counter()
    con.execute("BEGIN")
    if not triggers:
        con.execute("UPDATE search_state SET cursor = 0 WHERE id = 1")
    con.executemany(
        'INSERT INTO "user" (id, email, display_name, password_hash, role, quota_bytes, root_id, '
        "created_at) VALUES (?, ?, ?, 'x', 'user', 0, ?, ?)",
        [(u, f"{naam}@bench.voorbeeld.nl", naam, root, _tijd(DAG)) for u, naam, root in boom.users],
    )
    con.executemany(
        "INSERT INTO blob (sha256, size, ref_count, created_at, mime) VALUES (?, ?, ?, ?, ?)",
        list(boom.blobs.values()),
    )
    tekens = 0
    for rijen in boom.teksten():
        tekens += sum(len(r[1]) for r in rijen)
        con.executemany(
            "INSERT INTO blob_text (blob_sha, body, ocr, meta, truncated, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            rijen,
        )
    t_tekst = time.perf_counter()
    con.executemany(
        "INSERT INTO node (id, parent_id, owner_id, name, kind, mime, size, blob_id, created_at, "
        "modified_at, deleted_at, search_name, taken_at) VALUES "
        "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        boom.nodes,
    )
    con.executemany(
        "INSERT INTO photo_geo (node_id, owner_id, lat, lon, x16, y16, source) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        boom.geo,
    )
    con.executemany(
        "INSERT INTO dup_pair (a, b, dist, mad, level) VALUES (?, ?, ?, ?, ?)", boom.pairs
    )
    t_nodes = time.perf_counter()
    if not triggers:
        con.execute("INSERT INTO fts_node(fts_node) VALUES ('rebuild')")
        con.execute("INSERT INTO fts_name(fts_name) VALUES ('rebuild')")
        con.execute("UPDATE search_state SET cursor = NULL WHERE id = 1")
    con.execute("COMMIT")
    t_klaar = time.perf_counter()
    return {
        "nodes": len(boom.nodes),
        "documenten": len(boom.docs),
        "tekens": tekens,
        "foto's met GPS": len(boom.geo),
        "paren": len(boom.pairs),
        "tekst s": round(t_tekst - t0, 1),
        "nodes s": round(t_nodes - t_tekst, 1),
        "index s": round(t_klaar - t_nodes, 1),
        "totaal s": round(t_klaar - t0, 1),
    }


# --- Zoeken --------------------------------------------------------------------------------


def bereiken(paden: list[str]) -> tuple[str, dict]:
    """De bereiken van Keuze 4: tot en met 64 een OR, daarboven één JSON-parameter."""
    if len(paden) > 64:
        ranges = json.dumps([[p, p[:-1] + "0"] for p in paden])
        sql = ("EXISTS (SELECT 1 FROM json_each(:ranges) AS r WHERE n.mpath >= r.value ->> '$[0]' "
               "AND n.mpath < r.value ->> '$[1]')")  # fmt: skip
        return sql, {"ranges": ranges}
    delen, args = [], {}
    for i, p in enumerate(paden):
        delen.append(f"(n.mpath >= :p{i} AND n.mpath < :e{i})")
        args[f"p{i}"], args[f"e{i}"] = p, p[:-1] + "0"
    return "(" + " OR ".join(delen) + ")", args


def sql_a(bereik: str) -> str:
    return (
        "SELECT n.id, bm25(fts_node, 10.0, 6.0, 1.0, 0.8, 0.5) AS s "
        "FROM fts_node CROSS JOIN node AS n ON n.num = fts_node.rowid "
        f"WHERE fts_node MATCH :expr AND n.kind <> 'album' AND n.dead = 0 AND {bereik} "
        "ORDER BY s, n.num LIMIT :n"
    )


def sql_b(bereik: str) -> str:
    return (
        "SELECT n.id FROM fts_node CROSS JOIN node AS n ON n.num = fts_node.rowid "
        f"WHERE fts_node MATCH :expr AND n.kind <> 'album' AND n.dead = 0 AND {bereik} LIMIT :n"
    )


def sql_c(bereik: str) -> str:
    return (
        "SELECT n.id FROM fts_name CROSS JOIN node AS n ON n.num = fts_name.rowid "
        f"WHERE fts_name MATCH :names AND n.kind <> 'album' AND n.dead = 0 AND {bereik} LIMIT :n"
    )


def met_grens(con: sqlite3.Connection, ms: int, sql: str, args: dict) -> list | None:
    """De vraag met een tijdsgrens: een timer roept interrupt() aan. None als hij stopte."""
    timer = threading.Timer(ms / 1000, con.interrupt)
    timer.start()
    try:
        return con.execute(sql, args).fetchall()
    except sqlite3.OperationalError as e:
        if "interrupt" not in str(e):
            raise
        return None
    finally:
        timer.cancel()
        timer.join()


def uitdrukking(vraag: str) -> tuple[str, str | None]:
    """Een eenvoudige parse() zoals Keuze 5: elk woord tussen aanhalingstekens, het laatste als
    voorvoegsel bij ≥ 3 tekens (niet na een spatie); een vraag tussen aanhalingstekens is een
    frase. Voor de namen het laatste woord als stuk (trigram)."""
    frase = len(vraag) > 2 and vraag.startswith('"') and vraag.endswith('"')
    woorden = [w for w in "".join(c if c.isalnum() else " " for c in fold(vraag)).split() if w]
    if not woorden:
        return "", None
    names = f'"{woorden[-1]}"' if len(woorden[-1]) >= 3 else None
    if frase:
        return '"' + " ".join(woorden) + '"', names
    delen = [f'"{w}"' for w in woorden]
    if len(woorden[-1]) >= 3 and not vraag.endswith(" "):
        delen[-1] += "*"
    return " ".join(delen), names


def vragenlijst(boom: Boom) -> list[tuple[str, str]]:
    """Vaste vragen uit de woordenschat, met hun soort: gewone woorden, zeldzame, voorvoegsels,
    twee woorden, frases en stukken van namen. Een spatie aan het eind: het hele woord, geen
    voorvoegsel."""
    rng = random.Random(54)
    voc = boom.voc
    uit = [("gewoon", voc[i] + " ") for i in rng.sample(range(0, 300), 15)]
    uit += [("zeldzaam", voc[i] + " ") for i in rng.sample(range(1000, 4000), 15)]
    voor = [voc[i][:3] for i in rng.sample(range(0, 2000), 10) if len(voc[i]) >= 4][:8]
    uit += [("voorvoegsel", v) for v in voor + ["ver", "ont"]]
    uit += [("twee woorden", f"{voc[rng.randrange(300)]} {voc[rng.randrange(300, 3000)]} ")
            for _ in range(8)]  # fmt: skip
    uit += [("frase", f'"{voc[i]} {voc[i + 1]}"') for i in rng.sample(range(0, 200), 4)]
    uit += [("naam", v) for v in ("img_0", "rapport", "kopie", "factuur 2024", "dsc")]
    return uit


def zoek(con, bereik: str, args: dict, vraag: str) -> dict:
    expr, names = uitdrukking(vraag)
    a = {**args, "expr": expr, "n": PAGINA + 1}
    start = time.perf_counter()
    rows = met_grens(con, GRENS_A, sql_a(bereik), a)
    ronde, c_ms = "A", None
    if rows is None:
        # B krijgt de rest: A stopt pas 2–22 ms na zijn grens (S1)
        rest = GRENS_A + GRENS_B - (time.perf_counter() - start) * 1000
        ronde, rows = "B", met_grens(con, max(1, round(rest)), sql_b(bereik), a)
    if rows is None:
        ronde, rows = "C", []
        c_start = time.perf_counter()
        if names:
            rows = con.execute(sql_c(bereik), {**a, "names": names}).fetchall()
        c_ms = (time.perf_counter() - c_start) * 1000
    return {"ms": (time.perf_counter() - start) * 1000, "ronde": ronde, "rijen": len(rows),
            "C ms": c_ms}  # fmt: skip


def zonder_grens(con, bereik: str, args: dict, vraag: str) -> float:
    expr, _ = uitdrukking(vraag)
    start = time.perf_counter()
    con.execute(sql_a(bereik), {**args, "expr": expr, "n": PAGINA + 1}).fetchall()
    return (time.perf_counter() - start) * 1000


def p(waarden: list[float], q: float) -> float:
    s = sorted(waarden)
    return s[min(len(s) - 1, max(0, math.ceil(q * len(s)) - 1))]


def samenvatting(tijden: list[float]) -> dict:
    return {
        "p50": round(statistics.median(tijden), 1),
        "p95": round(p(tijden, 0.95), 1),
        "max": round(max(tijden), 1),
    }


def meet_zoeken(con, boom: Boom, rondes: int) -> dict:
    paden = dict(
        con.execute("SELECT u.display_name, n.mpath FROM user u JOIN node n ON n.id = u.root_id")
    )
    profielen = {
        "eigenaar": [paden["b00"]],
        "alles": [paden[f"b{i:02}"] for i in range(GEBRUIKERS)],
    }
    vragen = vragenlijst(boom)
    for _, vraag in vragen[:20]:  # opwarmen
        zoek(con, *bereiken(profielen["eigenaar"]), vraag)
    uit = {}
    for naam, wortels in profielen.items():
        bereik, args = bereiken(wortels)
        p95s, alle, rondes_bc, leeg_c, c_ms = [], [], {"B": 0, "C": 0}, 0, []
        per_soort: dict[str, dict] = {}
        for _ in range(rondes):
            ronde = []
            for soort, vraag in vragen:
                r = zoek(con, bereik, args, vraag)
                ronde.append(r["ms"])
                s = per_soort.setdefault(soort, {"ms": [], "B": 0, "C": 0, "zonder grens": []})
                s["ms"].append(r["ms"])
                if r["ronde"] != "A":
                    rondes_bc[r["ronde"]] += 1
                    s[r["ronde"]] += 1
                    leeg_c += r["ronde"] == "C" and r["rijen"] == 0
                if r["C ms"] is not None:
                    c_ms.append(r["C ms"])
            p95s.append(p(ronde, 0.95))
            alle += ronde
        ruw = []
        for soort, vraag in vragen:
            ms = zonder_grens(con, bereik, args, vraag)
            ruw.append(ms)
            per_soort[soort]["zonder grens"].append(ms)
        uit[naam] = {
            "wortels": len(wortels),
            "zichtbaar": con.execute(
                f"SELECT count(*) FROM node AS n WHERE n.dead = 0 AND {bereik}", args
            ).fetchone()[0],
            "vragen": len(vragen) * rondes,
            **samenvatting(alle),
            "p95 (mediaan van de rondes)": round(statistics.median(p95s), 1),
            "ronde B": rondes_bc["B"],
            "ronde C": rondes_bc["C"],
            "C zonder rijen": leeg_c,
            "ronde C zelf": samenvatting(c_ms) if c_ms else None,
            "zonder grens": samenvatting(ruw),
            "per soort": {
                soort: {
                    "met grens": samenvatting(s["ms"]),
                    "B": s["B"],
                    "C": s["C"],
                    "zonder grens": samenvatting(s["zonder grens"]),
                }
                for soort, s in per_soort.items()
            },
        }
    return uit


# --- De vormen van Keuze 4a (S10) ----------------------------------------------------------


def meet_vormen(con, rondes: int = 20) -> dict:
    users = {naam: (uid, root) for uid, naam, root in con.execute(
        'SELECT id, display_name, root_id FROM "user"')}  # fmt: skip
    paden = dict(
        con.execute("SELECT u.display_name, n.mpath FROM user u JOIN node n ON n.id = u.root_id")
    )
    uit: dict = {}

    def tijd(sql: str, args: dict) -> list[float]:
        out = []
        for _ in range(rondes):
            start = time.perf_counter()
            con.execute(sql, args).fetchall()
            out.append((time.perf_counter() - start) * 1000)
        return out

    jaar_terug = _tijd(DAG - timedelta(days=365))
    for naam, wortels in (("eigenaar", [paden["b00"]]),
                          ("alles", [paden[f"b{i:02}"] for i in range(GEBRUIKERS)])):  # fmt: skip
        bereik, args = bereiken(wortels)
        for filt, extra, index in (
            ("geen", "", "ix_node_live_mod"),
            ("type pdf", "AND n.ftype = 'pdf'", "ix_node_ftype_mod"),
            ("laatste jaar", "AND n.modified_at >= :sinds", "ix_node_live_mod"),
        ):
            sql = (f"SELECT n.id FROM node AS n INDEXED BY {index} WHERE n.dead = 0 AND "
                   f"n.kind <> 'album' AND {bereik} {extra} "
                   "ORDER BY n.modified_at DESC, n.num DESC LIMIT 31")  # fmt: skip
            uit[f"filters {naam}: {filt}"] = samenvatting(tijd(sql, {**args, "sinds": jaar_terug}))
        # Het kleine bereik: vanaf de paden en sorteren (de code kiest dit onder 20.000 nodes)
        sql = (f"SELECT n.id FROM node AS n INDEXED BY ix_node_mpath WHERE n.dead = 0 AND "
               f"n.kind <> 'album' AND {bereik} ORDER BY n.modified_at DESC, n.num DESC LIMIT 31")  # fmt: skip
        uit[f"filters {naam}: vanaf de paden"] = samenvatting(tijd(sql, args))

    kaart = (
        "SELECT g.x16 >> :s AS cx, g.y16 >> :s AS cy, count(*) AS n, max(n.taken_at) AS t, "
        "n.id AS cover FROM photo_geo AS g CROSS JOIN node AS n ON n.id = g.node_id "
        "WHERE g.owner_id = :me AND g.x16 BETWEEN :x0 AND :x1 AND g.y16 BETWEEN :y0 AND :y1 "
        "AND n.dead = 0 AND n.kind = 'file' GROUP BY cx, cy LIMIT 501"
    )
    nl0, nl1 = tegel16(53.6, 3.3), tegel16(50.7, 7.3)
    exact = (
        "SELECT n.blob_id, count(*) FROM node AS n WHERE n.owner_id = :me AND n.dead = 0 "
        "AND n.kind = 'file' GROUP BY n.blob_id HAVING count(*) > 1"
    )
    # Bijna gelijk. De vorm uit Keuze 4a ("ontwerp") laat SQLite de twee IN-lijsten als
    # gelijkheden op de sleutel (a, b) van dup_pair gebruiken: een kruisproduct van mine × mine.
    # Daarom met een grens van 2 s. "met EXISTS": alle paren langs, per kant één zoekstap in
    # ix_node_owner_blob. "vanaf de eigen sha's": langs de eigen nodes, per sha de paren.
    mine = (
        "WITH mine(sha) AS (SELECT DISTINCT blob_id FROM node WHERE owner_id = :me AND dead = 0 "
        "AND kind = 'file' AND ftype = 'image') "
    )
    bijna = mine + (
        "SELECT p.a, p.b, p.dist, p.level FROM dup_pair AS p WHERE p.a IN mine AND p.b IN mine"
    )
    bijna_exists = (
        "SELECT p.a, p.b, p.dist, p.level FROM dup_pair AS p WHERE EXISTS (SELECT 1 FROM node AS n "
        "WHERE n.owner_id = :me AND n.blob_id = p.a AND n.dead = 0 AND n.kind = 'file') AND EXISTS "
        "(SELECT 1 FROM node AS n WHERE n.owner_id = :me AND n.blob_id = p.b AND n.dead = 0 "
        "AND n.kind = 'file')"
    )
    bijna_eigen = (
        "SELECT p.a, p.b, p.dist, p.level FROM node AS n INDEXED BY ix_node_owner_blob "
        "CROSS JOIN dup_pair AS p ON p.a = n.blob_id WHERE n.owner_id = :me AND n.dead = 0 "
        "AND n.kind = 'file' AND EXISTS (SELECT 1 FROM node AS m WHERE m.owner_id = :me "
        "AND m.blob_id = p.b AND m.dead = 0 AND m.kind = 'file') GROUP BY p.a, p.b"
    )
    # De kaart zonder de join met node (alleen ter vergelijking: dan telt een foto in de
    # prullenbak mee)
    kaart_los = (
        "SELECT g.x16 >> :s AS cx, g.y16 >> :s AS cy, count(*) AS n FROM photo_geo AS g "
        "WHERE g.owner_id = :me AND g.x16 BETWEEN :x0 AND :x1 AND g.y16 BETWEEN :y0 AND :y1 "
        "GROUP BY cx, cy LIMIT 501"
    )
    for naam in ("b00", "fotograaf"):
        if naam not in users:
            continue
        me = users[naam][0]
        wie = "eigenaar" if naam == "b00" else "fotograaf (55.000)"
        wereld = {"me": me, "s": 10, "x0": 0, "x1": 65535, "y0": 0, "y1": 65535}
        nl = {"me": me, "s": 6, "x0": nl0[0], "x1": nl1[0], "y0": nl0[1], "y1": nl1[1]}
        uit[f"kaart {wie}: wereld"] = samenvatting(tijd(kaart, wereld))
        uit[f"kaart {wie}: Nederland"] = samenvatting(tijd(kaart, nl))
        uit[f"kaart {wie}: Nederland, zonder join"] = samenvatting(tijd(kaart_los, nl))
        uit[f"duplicaten {wie}: precies"] = samenvatting(tijd(exact, {"me": me}))
        uit[f"duplicaten {wie}: bijna, met EXISTS"] = samenvatting(tijd(bijna_exists, {"me": me}))
        uit[f"duplicaten {wie}: bijna, vanaf de eigen sha's"] = samenvatting(
            tijd(bijna_eigen, {"me": me})
        )
        uit[f"duplicaten {wie}: paren"] = len(con.execute(bijna_exists, {"me": me}).fetchall())
        start = time.perf_counter()
        klaar = met_grens(con, 2000, bijna, {"me": me})
        ms = round((time.perf_counter() - start) * 1000, 1)
        uit[f"duplicaten {wie}: bijna, ontwerp"] = (
            {"p50": ms, "p95": ms, "max": ms}
            if klaar is not None
            else {"p50": None, "p95": None, "max": None, "gestopt na ms": 2000}
        )
    return uit


# --- Hoofdprogramma -------------------------------------------------------------------------


def basis_map(gekozen: str | None) -> Path:
    if gekozen:
        return Path(gekozen)
    cache = Path("/data/cache")
    if cache.is_dir() and os.access(cache, os.W_OK):
        return cache
    return Path(tempfile.gettempdir())


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Zoekbench: meet zoeken op deze machine.")
    ap.add_argument("--los", action="store_true", help="in een tijdelijke map, en daarna weg")
    ap.add_argument("--n", type=int, default=100_000, help="aantal bestanden")
    ap.add_argument("--seed", type=int, default=6)
    ap.add_argument("--rondes", type=int, default=3)
    ap.add_argument("--map", help="waar de tijdelijke map komt")
    ap.add_argument("--vormen", action="store_true", help="ook de vormen van Keuze 4a (S10)")
    ap.add_argument("--triggers", action="store_true", help="vullen via de triggers (S7)")
    ap.add_argument("--json", help="schrijf de uitkomst ook als JSON hierheen")
    ap.add_argument("--houd", action="store_true", help="laat de map staan (zoeken naar fouten)")
    args = ap.parse_args(argv)
    if not args.los:
        ap.error("alleen --los bestaat: een eigen boom in een tijdelijke map")

    versie = sqlite3.sqlite_version
    probe = sqlite3.connect(":memory:")
    fts5 = probe.execute("SELECT sqlite_compileoption_used('ENABLE_FTS5')").fetchone()[0]
    probe.close()
    print(f"python {platform.python_version()}, SQLite {versie}, FTS5 {'ja' if fts5 else 'nee'}, "
          f"{os.cpu_count()} cores", flush=True)  # fmt: skip
    if not fts5:
        print("Deze SQLite kent geen FTS5: de app zoekt dan met LIKE (Keuze 10).")
        return 3

    # Ook bij een kill (SIGTERM, zoals docker stop) de map weghalen: dan loopt finally
    vorige = signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))
    map_ = Path(tempfile.mkdtemp(prefix="zoekbench-", dir=basis_map(args.map)))
    start = time.perf_counter()
    uit: dict = {"sqlite": versie, "python": platform.python_version(), "n": args.n,
                 "seed": args.seed, "map": str(map_)}  # fmt: skip
    try:
        con = sqlite3.connect(map_ / "bench.db", isolation_level=None, check_same_thread=False)
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA synchronous=OFF")
        con.execute("PRAGMA cache_size=-262144")
        maak_schema(con)
        print(f"Boom van {args.n} maken in {map_} ...", flush=True)
        boom = Boom(args.n, args.seed, fotograaf=args.vormen)
        uit["bouw"] = vul(con, boom, triggers=args.triggers)
        # De piek op schijf is de database plus de WAL van de ene grote transactie; daarna is de
        # WAL leeg en blijft de database
        uit["bouw"]["MB met WAL"] = round(sum(f.stat().st_size for f in map_.iterdir()) / 1e6)
        con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        uit["bouw"]["MB"] = round((map_ / "bench.db").stat().st_size / 1e6)
        print(f"Gemaakt: {uit['bouw']}", flush=True)
        con.execute("PRAGMA synchronous=NORMAL")
        uit["zoeken"] = meet_zoeken(con, boom, args.rondes)
        if args.vormen:
            uit["vormen"] = meet_vormen(con)
        con.close()
    finally:
        if not args.houd:
            with contextlib.suppress(OSError):
                shutil.rmtree(map_)
        signal.signal(signal.SIGTERM, vorige)
    uit["duur s"] = round(time.perf_counter() - start, 1)
    uit["opgeruimd"] = not map_.exists()

    print()
    print("=== zoekbench ===")
    print(f"SQLite {versie}; {uit['bouw']['nodes']} nodes, {uit['bouw']['tekens'] / 1e6:.0f} M "
          f"tekens; bouwen {uit['bouw']['totaal s']} s; alles {uit['duur s']} s")  # fmt: skip
    for naam, z in uit["zoeken"].items():
        for soort, v in z["per soort"].items():
            print(f"  {naam}, {soort}: met grens p95 {v['met grens']['p95']} ms (B {v['B']}x, "
                  f"C {v['C']}x); zonder grens p50 {v['zonder grens']['p50']} "
                  f"max {v['zonder grens']['max']} ms")  # fmt: skip
        print(f"{naam:9} ({z['wortels']} wortels, {z['zichtbaar']} zichtbaar): p50 {z['p50']} "
              f"p95 {z['p95']} max {z['max']} ms; B {z['ronde B']}x, C {z['ronde C']}x; "
              f"zonder grens p95 {z['zonder grens']['p95']} ms")  # fmt: skip
        if z["ronde C zelf"]:
            c = z["ronde C zelf"]
            print(f"{naam:9} ronde C zelf: p50 {c['p50']} p95 {c['p95']} max {c['max']} ms; "
                  f"{z['C zonder rijen']}x zonder rijen")  # fmt: skip
    for naam, v in uit.get("vormen", {}).items():
        if isinstance(v, int):
            print(f"{naam}: {v}")
        elif v["p95"] is None:
            print(f"{naam}: gestopt na {v['gestopt na ms']} ms")
        else:
            print(f"{naam}: p50 {v['p50']} p95 {v['p95']} max {v['max']} ms")
    print(f"opgeruimd: {'ja' if uit['opgeruimd'] else 'nee, ' + str(map_)}")
    if args.json:
        Path(args.json).write_text(json.dumps(uit, indent=2, ensure_ascii=False) + "\n")
    return 0


# --- Het schema van 0010 (bevroren kopie) ---------------------------------------------------
# Een kopie van wat migratie 0010 maakt, voor zover zoeken het gebruikt: de tabellen zoals op
# head (media_item en photo_person alleen met wat de view en de triggers lezen), de indexen op
# node, photo_geo en dup_pair, de view, de twee FTS-tabellen en alle triggers.
# api/tests/search/test_zoekbench.py eist dat de view, de FTS-tabellen, de indexen en de
# triggers letterlijk gelijk zijn aan die in een database op head.
DDL = (
    # tabel user
    "CREATE TABLE user (\n\tid CHAR(32) NOT NULL, \n\temail VARCHAR(320) NOT NULL, \n\tdisplay_name VARCHAR(100) NOT NULL, \n\tpassword_hash VARCHAR(255) NOT NULL, \n\trole VARCHAR(16) NOT NULL, \n\tquota_bytes BIGINT NOT NULL, \n\troot_id CHAR(32), \n\tcreated_at DATETIME NOT NULL, totp_secret VARCHAR(64), totp_pending VARCHAR(64), totp_last_step BIGINT DEFAULT '0' NOT NULL, totp_failures INTEGER DEFAULT '0' NOT NULL, totp_locked_until DATETIME, locale VARCHAR(5), disabled_at DATETIME, \n\tPRIMARY KEY (id), \n\tUNIQUE (email)\n)",
    # tabel blob
    "CREATE TABLE blob (\n\tsha256 VARCHAR(64) NOT NULL, \n\tsize BIGINT NOT NULL, \n\tref_count INTEGER NOT NULL, \n\tcreated_at DATETIME NOT NULL, mime VARCHAR(255), preview VARCHAR(16), preview_meta JSON, taken_at DATETIME, meta_version INTEGER DEFAULT '0' NOT NULL, render_version INTEGER DEFAULT '0' NOT NULL, pdf_state VARCHAR(8), pdf_version INTEGER DEFAULT '0' NOT NULL, \n\tPRIMARY KEY (sha256)\n)",
    # tabel node
    "CREATE TABLE node (\n\tid CHAR(32) NOT NULL, \n\tparent_id CHAR(32), \n\towner_id CHAR(32) NOT NULL, \n\tname VARCHAR(255) NOT NULL, \n\tkind VARCHAR(8) NOT NULL, \n\tmime VARCHAR(255), \n\tsize BIGINT NOT NULL, \n\tblob_id VARCHAR(64), \n\tcreated_at DATETIME NOT NULL, \n\tmodified_at DATETIME NOT NULL, \n\tdeleted_at DATETIME, search_name TEXT DEFAULT '' NOT NULL, taken_at DATETIME, taken_day INTEGER, num INTEGER, mpath TEXT, dead INTEGER NOT NULL DEFAULT 0, ftype TEXT GENERATED ALWAYS AS (CASE WHEN kind = 'dir' THEN 'folder' WHEN kind <> 'file' OR mime IS NULL OR mime = '' THEN 'other' WHEN mime IN ('application/vnd.ms-excel', 'application/vnd.ms-excel.sheet.macroenabled.12', 'application/vnd.oasis.opendocument.spreadsheet', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', 'text/csv', 'text/tab-separated-values') THEN 'spreadsheet' WHEN mime IN ('application/vnd.ms-powerpoint', 'application/vnd.ms-powerpoint.presentation.macroenabled.12', 'application/vnd.oasis.opendocument.presentation', 'application/vnd.openxmlformats-officedocument.presentationml.presentation') THEN 'presentation' WHEN mime IN ('application/gzip', 'application/java-archive', 'application/vnd.rar', 'application/x-7z-compressed', 'application/x-bzip2', 'application/x-tar', 'application/x-xz', 'application/zip', 'application/zstd') THEN 'other' WHEN mime = 'application/pdf' THEN 'pdf' WHEN mime LIKE 'image/%' THEN 'image' WHEN mime LIKE 'video/%' THEN 'video' WHEN mime LIKE 'audio/%' THEN 'audio' WHEN mime LIKE 'text/%' OR mime IN ('application/epub+zip', 'application/javascript', 'application/json', 'application/msword', 'application/rtf', 'application/sql', 'application/toml', 'application/vnd.ms-outlook', 'application/vnd.ms-word.document.macroenabled.12', 'application/vnd.oasis.opendocument.graphics', 'application/vnd.oasis.opendocument.text', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', 'application/vnd.openxmlformats-officedocument.wordprocessingml.template', 'application/x-sh', 'application/xhtml+xml', 'application/xml', 'application/yaml') THEN 'document' ELSE 'other' END) VIRTUAL, \n\tPRIMARY KEY (id), \n\tFOREIGN KEY(blob_id) REFERENCES blob (sha256), \n\tFOREIGN KEY(owner_id) REFERENCES user (id) ON DELETE CASCADE, \n\tFOREIGN KEY(parent_id) REFERENCES node (id) ON DELETE CASCADE\n)",
    # tabel blob_text
    "CREATE TABLE blob_text (\n\tblob_sha VARCHAR(64) NOT NULL, \n\tbody TEXT DEFAULT '' NOT NULL, \n\tocr TEXT DEFAULT '' NOT NULL, \n\tmeta TEXT DEFAULT '' NOT NULL, \n\ttruncated INTEGER DEFAULT '0' NOT NULL, \n\tupdated_at DATETIME NOT NULL, \n\tPRIMARY KEY (blob_sha), \n\tFOREIGN KEY(blob_sha) REFERENCES blob (sha256) ON DELETE CASCADE\n)",
    # tabel photo_ocr
    "CREATE TABLE photo_ocr (\n\tnode_id CHAR(32) NOT NULL, \n\towner_id CHAR(32) NOT NULL, \n\ttext TEXT DEFAULT '' NOT NULL, \n\twords BLOB NOT NULL, \n\tangle FLOAT DEFAULT '0' NOT NULL, \n\tupdated_at DATETIME NOT NULL, \n\tPRIMARY KEY (node_id), \n\tFOREIGN KEY(node_id) REFERENCES node (id) ON DELETE CASCADE, \n\tFOREIGN KEY(owner_id) REFERENCES user (id) ON DELETE CASCADE\n)",
    # tabel photo_geo
    "CREATE TABLE photo_geo (\n\tnode_id CHAR(32) NOT NULL, \n\towner_id CHAR(32) NOT NULL, \n\tlat FLOAT NOT NULL, \n\tlon FLOAT NOT NULL, \n\talt FLOAT, \n\tx16 INTEGER NOT NULL, \n\ty16 INTEGER NOT NULL, \n\tplace_id INTEGER, \n\tcountry VARCHAR(2), \n\tadmin1 TEXT, \n\tsource VARCHAR(8) NOT NULL, \n\tPRIMARY KEY (node_id), \n\tFOREIGN KEY(node_id) REFERENCES node (id) ON DELETE CASCADE, \n\tFOREIGN KEY(owner_id) REFERENCES user (id) ON DELETE CASCADE\n)",
    # tabel dup_pair
    "CREATE TABLE dup_pair (\n\ta VARCHAR(64) NOT NULL, \n\tb VARCHAR(64) NOT NULL, \n\tdist INTEGER NOT NULL, \n\tmad FLOAT NOT NULL, \n\tlevel VARCHAR(8) NOT NULL, \n\tPRIMARY KEY (a, b), \n\tCHECK (a < b), \n\tFOREIGN KEY(a) REFERENCES blob (sha256) ON DELETE CASCADE, \n\tFOREIGN KEY(b) REFERENCES blob (sha256) ON DELETE CASCADE\n)\n WITHOUT ROWID\n\n",
    # tabel search_seq
    "CREATE TABLE search_seq (id INTEGER PRIMARY KEY CHECK (id = 1), next INTEGER NOT NULL)",
    # tabel search_state
    "CREATE TABLE search_state (id INTEGER PRIMARY KEY CHECK (id = 1), cursor INTEGER, started_at TEXT, what TEXT)",
    # tabel media_item (alleen wat de view en de triggers lezen)
    "CREATE TABLE media_item (node_id CHAR(32) NOT NULL PRIMARY KEY, title VARCHAR(255) NOT NULL)",
    # tabel photo_person (alleen wat de triggers lezen)
    "CREATE TABLE photo_person (person_id CHAR(32) NOT NULL, node_id CHAR(32) NOT NULL, PRIMARY KEY (person_id, node_id))",
    # index ix_dup_b
    "CREATE INDEX ix_dup_b ON dup_pair (b)",
    # index ix_node_blob
    "CREATE INDEX ix_node_blob ON node (blob_id)",
    # index ix_node_ftype_mod
    "CREATE INDEX ix_node_ftype_mod ON node (ftype, modified_at DESC, num DESC) WHERE dead = 0",
    # index ix_node_live_mod
    "CREATE INDEX ix_node_live_mod ON node (modified_at DESC, num DESC) WHERE dead = 0",
    # index ix_node_mpath
    "CREATE INDEX ix_node_mpath ON node (mpath)",
    # index ix_node_num
    "CREATE UNIQUE INDEX ix_node_num ON node (num)",
    # index ix_node_owner_blob
    "CREATE INDEX ix_node_owner_blob ON node (owner_id, blob_id) WHERE dead = 0",
    # index ix_node_owner_taken
    "CREATE INDEX ix_node_owner_taken ON node (owner_id, taken_day, taken_at, id)",
    # index ix_node_parent_name
    "CREATE INDEX ix_node_parent_name ON node (parent_id, name)",
    # index ix_node_parent_order
    "CREATE INDEX ix_node_parent_order ON node (parent_id, kind, search_name, id)",
    # index ix_node_trash
    "CREATE INDEX ix_node_trash ON node (mpath) WHERE deleted_at IS NOT NULL",
    # index ix_photo_geo_country
    "CREATE INDEX ix_photo_geo_country ON photo_geo (owner_id, country)",
    # index ix_photo_geo_place
    "CREATE INDEX ix_photo_geo_place ON photo_geo (owner_id, place_id)",
    # index ix_photo_geo_tile
    "CREATE INDEX ix_photo_geo_tile ON photo_geo (owner_id, x16, y16)",
    # search_src
    "CREATE VIEW search_src AS\n  SELECT n.num AS num, n.blob_id AS blob_id, n.id AS node_id, n.search_name AS name,\n         replace(replace(coalesce((SELECT group_concat(m.title, ' ') FROM media_item m WHERE m.node_id = n.id), ''), 'ß', 'ss'), 'ẞ', 'ss') AS extra,\n         replace(replace(coalesce(t.body, ''), 'ß', 'ss'), 'ẞ', 'ss') AS body,\n         replace(replace(trim(coalesce(t.ocr, '') || ' ' || coalesce(po.text, '')), 'ß', 'ss'), 'ẞ', 'ss') AS ocr,\n         replace(replace(coalesce(t.meta, ''), 'ß', 'ss'), 'ẞ', 'ss') AS meta\n  FROM node n\n  LEFT JOIN blob_text t ON t.blob_sha = n.blob_id\n  LEFT JOIN photo_ocr po ON po.node_id = n.id",
    # fts_node
    "CREATE VIRTUAL TABLE fts_node USING fts5(name, extra, body, ocr, meta, content='search_src', content_rowid='num', tokenize='unicode61 remove_diacritics 2')",
    # fts_name
    "CREATE VIRTUAL TABLE fts_name USING fts5(search_name, content='node', content_rowid='num', tokenize='trigram')",
    # trigger node_ai
    "CREATE TRIGGER node_ai AFTER INSERT ON node BEGIN\n  UPDATE search_seq SET next = next + 1 WHERE id = 1;\n  UPDATE node SET num = (SELECT next - 1 FROM search_seq WHERE id = 1) WHERE id = NEW.id;\n  UPDATE node SET\n    mpath = coalesce((SELECT p.mpath FROM node p WHERE p.id = NEW.parent_id), '/') || num || '/',\n    dead = coalesce((SELECT p.dead FROM node p WHERE p.id = NEW.parent_id), 0) OR NEW.deleted_at IS NOT NULL\n  WHERE id = NEW.id;\n  INSERT INTO fts_node(rowid, name, extra, body, ocr, meta) SELECT num, name, extra, body, ocr, meta FROM search_src WHERE (num = (SELECT num FROM node WHERE id = NEW.id)) AND ((SELECT cursor FROM search_state WHERE id = 1) IS NULL OR num <= (SELECT cursor FROM search_state WHERE id = 1));\n  INSERT INTO fts_name(rowid, search_name) SELECT (SELECT num FROM node WHERE id = NEW.id), NEW.search_name WHERE ((SELECT cursor FROM search_state WHERE id = 1) IS NULL OR (SELECT num FROM node WHERE id = NEW.id) <= (SELECT cursor FROM search_state WHERE id = 1));\nEND",
    # trigger node_bu_text
    "CREATE TRIGGER node_bu_text BEFORE UPDATE OF search_name, blob_id ON node\nWHEN OLD.num IS NOT NULL AND (OLD.search_name IS NOT NEW.search_name OR OLD.blob_id IS NOT NEW.blob_id) BEGIN\n  DELETE FROM photo_ocr WHERE node_id = OLD.id AND OLD.blob_id IS NOT NEW.blob_id;\n  DELETE FROM photo_geo WHERE node_id = OLD.id AND OLD.blob_id IS NOT NEW.blob_id;\n  INSERT INTO fts_node(fts_node, rowid, name, extra, body, ocr, meta) SELECT 'delete', num, name, extra, body, ocr, meta FROM search_src WHERE (num = OLD.num) AND ((SELECT cursor FROM search_state WHERE id = 1) IS NULL OR num <= (SELECT cursor FROM search_state WHERE id = 1));\n  INSERT INTO fts_name(fts_name, rowid, search_name) SELECT 'delete', OLD.num, OLD.search_name WHERE ((SELECT cursor FROM search_state WHERE id = 1) IS NULL OR OLD.num <= (SELECT cursor FROM search_state WHERE id = 1));\nEND",
    # trigger node_au_text
    "CREATE TRIGGER node_au_text AFTER UPDATE OF search_name, blob_id ON node\nWHEN NEW.num IS NOT NULL AND (OLD.search_name IS NOT NEW.search_name OR OLD.blob_id IS NOT NEW.blob_id) BEGIN\n  INSERT INTO fts_node(rowid, name, extra, body, ocr, meta) SELECT num, name, extra, body, ocr, meta FROM search_src WHERE (num = NEW.num) AND ((SELECT cursor FROM search_state WHERE id = 1) IS NULL OR num <= (SELECT cursor FROM search_state WHERE id = 1));\n  INSERT INTO fts_name(rowid, search_name) SELECT NEW.num, NEW.search_name WHERE ((SELECT cursor FROM search_state WHERE id = 1) IS NULL OR NEW.num <= (SELECT cursor FROM search_state WHERE id = 1));\nEND",
    # trigger node_au_owner
    "CREATE TRIGGER node_au_owner AFTER UPDATE OF owner_id ON node\nWHEN OLD.owner_id IS NOT NEW.owner_id BEGIN\n  DELETE FROM photo_ocr WHERE node_id = NEW.id;\n  DELETE FROM photo_geo WHERE node_id = NEW.id;\n  DELETE FROM photo_person WHERE node_id = NEW.id;\nEND",
    # trigger node_bd
    "CREATE TRIGGER node_bd BEFORE DELETE ON node WHEN OLD.num IS NOT NULL BEGIN\n  INSERT INTO fts_node(fts_node, rowid, name, extra, body, ocr, meta) SELECT 'delete', num, name, extra, body, ocr, meta FROM search_src WHERE (num = OLD.num) AND ((SELECT cursor FROM search_state WHERE id = 1) IS NULL OR num <= (SELECT cursor FROM search_state WHERE id = 1));\n  INSERT INTO fts_name(fts_name, rowid, search_name) SELECT 'delete', OLD.num, OLD.search_name WHERE ((SELECT cursor FROM search_state WHERE id = 1) IS NULL OR OLD.num <= (SELECT cursor FROM search_state WHERE id = 1));\nEND",
    # trigger node_au_move
    "CREATE TRIGGER node_au_move AFTER UPDATE OF parent_id ON node\nWHEN OLD.parent_id IS NOT NEW.parent_id AND OLD.mpath IS NOT NULL BEGIN\n  UPDATE node SET mpath = coalesce((SELECT p.mpath FROM node p WHERE p.id = NEW.parent_id), '/') || NEW.num || '/'\n                          || substr(mpath, length(OLD.mpath) + 1)\n   WHERE mpath >= OLD.mpath AND mpath < substr(OLD.mpath, 1, length(OLD.mpath) - 1) || '0';\n  UPDATE node SET dead =\n        coalesce((SELECT p.dead FROM node p WHERE p.id = NEW.parent_id), 0)\n     WHERE mpath >= (SELECT mpath FROM node WHERE id = NEW.id) AND mpath < substr((SELECT mpath FROM node WHERE id = NEW.id), 1, length((SELECT mpath FROM node WHERE id = NEW.id)) - 1) || '0';\n  UPDATE node SET dead = 1\n    FROM (SELECT mpath AS tp FROM node WHERE deleted_at IS NOT NULL\n            AND mpath >= (SELECT mpath FROM node WHERE id = NEW.id) AND mpath < substr((SELECT mpath FROM node WHERE id = NEW.id), 1, length((SELECT mpath FROM node WHERE id = NEW.id)) - 1) || '0') AS t\n     WHERE node.mpath >= t.tp AND node.mpath < substr(t.tp, 1, length(t.tp) - 1) || '0';\nEND",
    # trigger node_au_trash
    "CREATE TRIGGER node_au_trash AFTER UPDATE OF deleted_at ON node\nWHEN (OLD.deleted_at IS NULL) <> (NEW.deleted_at IS NULL) AND OLD.mpath IS NOT NULL BEGIN\n  UPDATE node SET dead =\n        coalesce((SELECT p.dead FROM node p WHERE p.id = NEW.parent_id), 0)\n     WHERE mpath >= (SELECT mpath FROM node WHERE id = NEW.id) AND mpath < substr((SELECT mpath FROM node WHERE id = NEW.id), 1, length((SELECT mpath FROM node WHERE id = NEW.id)) - 1) || '0';\n  UPDATE node SET dead = 1\n    FROM (SELECT mpath AS tp FROM node WHERE deleted_at IS NOT NULL\n            AND mpath >= (SELECT mpath FROM node WHERE id = NEW.id) AND mpath < substr((SELECT mpath FROM node WHERE id = NEW.id), 1, length((SELECT mpath FROM node WHERE id = NEW.id)) - 1) || '0') AS t\n     WHERE node.mpath >= t.tp AND node.mpath < substr(t.tp, 1, length(t.tp) - 1) || '0';\nEND",
    # trigger bt_bi
    "CREATE TRIGGER bt_bi BEFORE INSERT ON blob_text BEGIN INSERT INTO fts_node(fts_node, rowid, name, extra, body, ocr, meta) SELECT 'delete', num, name, extra, body, ocr, meta FROM search_src WHERE (blob_id = NEW.blob_sha) AND ((SELECT cursor FROM search_state WHERE id = 1) IS NULL OR num <= (SELECT cursor FROM search_state WHERE id = 1)); END",
    # trigger bt_ai
    "CREATE TRIGGER bt_ai AFTER INSERT ON blob_text BEGIN INSERT INTO fts_node(rowid, name, extra, body, ocr, meta) SELECT num, name, extra, body, ocr, meta FROM search_src WHERE (blob_id = NEW.blob_sha) AND ((SELECT cursor FROM search_state WHERE id = 1) IS NULL OR num <= (SELECT cursor FROM search_state WHERE id = 1)); END",
    # trigger bt_bu
    "CREATE TRIGGER bt_bu BEFORE UPDATE ON blob_text BEGIN INSERT INTO fts_node(fts_node, rowid, name, extra, body, ocr, meta) SELECT 'delete', num, name, extra, body, ocr, meta FROM search_src WHERE (blob_id = OLD.blob_sha) AND ((SELECT cursor FROM search_state WHERE id = 1) IS NULL OR num <= (SELECT cursor FROM search_state WHERE id = 1)); END",
    # trigger bt_au
    "CREATE TRIGGER bt_au AFTER UPDATE ON blob_text BEGIN INSERT INTO fts_node(rowid, name, extra, body, ocr, meta) SELECT num, name, extra, body, ocr, meta FROM search_src WHERE (blob_id = NEW.blob_sha) AND ((SELECT cursor FROM search_state WHERE id = 1) IS NULL OR num <= (SELECT cursor FROM search_state WHERE id = 1)); END",
    # trigger bt_bd
    "CREATE TRIGGER bt_bd BEFORE DELETE ON blob_text BEGIN INSERT INTO fts_node(fts_node, rowid, name, extra, body, ocr, meta) SELECT 'delete', num, name, extra, body, ocr, meta FROM search_src WHERE (blob_id = OLD.blob_sha) AND ((SELECT cursor FROM search_state WHERE id = 1) IS NULL OR num <= (SELECT cursor FROM search_state WHERE id = 1)); END",
    # trigger bt_ad
    "CREATE TRIGGER bt_ad AFTER DELETE ON blob_text BEGIN INSERT INTO fts_node(rowid, name, extra, body, ocr, meta) SELECT num, name, extra, body, ocr, meta FROM search_src WHERE (blob_id = OLD.blob_sha) AND ((SELECT cursor FROM search_state WHERE id = 1) IS NULL OR num <= (SELECT cursor FROM search_state WHERE id = 1)); END",
    # trigger po_bi
    "CREATE TRIGGER po_bi BEFORE INSERT ON photo_ocr BEGIN INSERT INTO fts_node(fts_node, rowid, name, extra, body, ocr, meta) SELECT 'delete', num, name, extra, body, ocr, meta FROM search_src WHERE (node_id = NEW.node_id) AND ((SELECT cursor FROM search_state WHERE id = 1) IS NULL OR num <= (SELECT cursor FROM search_state WHERE id = 1)); END",
    # trigger po_ai
    "CREATE TRIGGER po_ai AFTER INSERT ON photo_ocr BEGIN INSERT INTO fts_node(rowid, name, extra, body, ocr, meta) SELECT num, name, extra, body, ocr, meta FROM search_src WHERE (node_id = NEW.node_id) AND ((SELECT cursor FROM search_state WHERE id = 1) IS NULL OR num <= (SELECT cursor FROM search_state WHERE id = 1)); END",
    # trigger po_bu
    "CREATE TRIGGER po_bu BEFORE UPDATE ON photo_ocr BEGIN INSERT INTO fts_node(fts_node, rowid, name, extra, body, ocr, meta) SELECT 'delete', num, name, extra, body, ocr, meta FROM search_src WHERE (node_id = OLD.node_id) AND ((SELECT cursor FROM search_state WHERE id = 1) IS NULL OR num <= (SELECT cursor FROM search_state WHERE id = 1)); END",
    # trigger po_au
    "CREATE TRIGGER po_au AFTER UPDATE ON photo_ocr BEGIN INSERT INTO fts_node(rowid, name, extra, body, ocr, meta) SELECT num, name, extra, body, ocr, meta FROM search_src WHERE (node_id = NEW.node_id) AND ((SELECT cursor FROM search_state WHERE id = 1) IS NULL OR num <= (SELECT cursor FROM search_state WHERE id = 1)); END",
    # trigger po_bd
    "CREATE TRIGGER po_bd BEFORE DELETE ON photo_ocr BEGIN INSERT INTO fts_node(fts_node, rowid, name, extra, body, ocr, meta) SELECT 'delete', num, name, extra, body, ocr, meta FROM search_src WHERE (node_id = OLD.node_id) AND ((SELECT cursor FROM search_state WHERE id = 1) IS NULL OR num <= (SELECT cursor FROM search_state WHERE id = 1)); END",
    # trigger po_ad
    "CREATE TRIGGER po_ad AFTER DELETE ON photo_ocr BEGIN INSERT INTO fts_node(rowid, name, extra, body, ocr, meta) SELECT num, name, extra, body, ocr, meta FROM search_src WHERE (node_id = OLD.node_id) AND ((SELECT cursor FROM search_state WHERE id = 1) IS NULL OR num <= (SELECT cursor FROM search_state WHERE id = 1)); END",
    # trigger mi_bi
    "CREATE TRIGGER mi_bi BEFORE INSERT ON media_item BEGIN INSERT INTO fts_node(fts_node, rowid, name, extra, body, ocr, meta) SELECT 'delete', num, name, extra, body, ocr, meta FROM search_src WHERE (num = (SELECT num FROM node WHERE id = NEW.node_id)) AND ((SELECT cursor FROM search_state WHERE id = 1) IS NULL OR num <= (SELECT cursor FROM search_state WHERE id = 1)); END",
    # trigger mi_ai
    "CREATE TRIGGER mi_ai AFTER INSERT ON media_item BEGIN INSERT INTO fts_node(rowid, name, extra, body, ocr, meta) SELECT num, name, extra, body, ocr, meta FROM search_src WHERE (num = (SELECT num FROM node WHERE id = NEW.node_id)) AND ((SELECT cursor FROM search_state WHERE id = 1) IS NULL OR num <= (SELECT cursor FROM search_state WHERE id = 1)); END",
    # trigger mi_bu
    "CREATE TRIGGER mi_bu BEFORE UPDATE OF title ON media_item BEGIN INSERT INTO fts_node(fts_node, rowid, name, extra, body, ocr, meta) SELECT 'delete', num, name, extra, body, ocr, meta FROM search_src WHERE (num = (SELECT num FROM node WHERE id = OLD.node_id)) AND ((SELECT cursor FROM search_state WHERE id = 1) IS NULL OR num <= (SELECT cursor FROM search_state WHERE id = 1)); END",
    # trigger mi_au
    "CREATE TRIGGER mi_au AFTER UPDATE OF title ON media_item BEGIN INSERT INTO fts_node(rowid, name, extra, body, ocr, meta) SELECT num, name, extra, body, ocr, meta FROM search_src WHERE (num = (SELECT num FROM node WHERE id = NEW.node_id)) AND ((SELECT cursor FROM search_state WHERE id = 1) IS NULL OR num <= (SELECT cursor FROM search_state WHERE id = 1)); END",
    # trigger mi_bd
    "CREATE TRIGGER mi_bd BEFORE DELETE ON media_item BEGIN INSERT INTO fts_node(fts_node, rowid, name, extra, body, ocr, meta) SELECT 'delete', num, name, extra, body, ocr, meta FROM search_src WHERE (num = (SELECT num FROM node WHERE id = OLD.node_id)) AND ((SELECT cursor FROM search_state WHERE id = 1) IS NULL OR num <= (SELECT cursor FROM search_state WHERE id = 1)); END",
    # trigger mi_ad
    "CREATE TRIGGER mi_ad AFTER DELETE ON media_item BEGIN INSERT INTO fts_node(rowid, name, extra, body, ocr, meta) SELECT num, name, extra, body, ocr, meta FROM search_src WHERE (num = (SELECT num FROM node WHERE id = OLD.node_id)) AND ((SELECT cursor FROM search_state WHERE id = 1) IS NULL OR num <= (SELECT cursor FROM search_state WHERE id = 1)); END",
)

if __name__ == "__main__":
    sys.exit(main())
