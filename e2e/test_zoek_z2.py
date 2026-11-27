"""Tekst uit bestanden (fase 6, Z2): de hele keten in de echte app, als eigen proces.

Een docx uploaden → de stap text leest hem (de haak na de upload, de pomp in de rij index) →
een woord uit de tekst staat in de index, met een snippet. Standaard staat media uit: dan leest
de terugval in het renderproces van de app (batches tot 50). Met media aan (marker media, job
e2e-media) leest de worker hem, met de handdruk.

Sinds de samenvoeging met Z1 zoekt GET /search de tekst (FtsEngine, names_only false): het
snippet komt uit het antwoord van /search (Keuze 8), met de treffer gemarkeerd. De index zelf
(fts_node, alleen lezen naast de server) gebruikt alleen de test van de backfill.

En de backfill: documenten van vóór Z2 (zonder rij in analysis en zonder blob_text) leest de
app na een herstart vanzelf, om de beurt per eigenaar.
"""

from __future__ import annotations

import contextlib
import sqlite3
import time
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from tests.search import fixtures as F

from conftest import Account, Server, make_admin

pytestmark = pytest.mark.zoek

API = "/api/v1"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
WACHT_S = 60.0


@contextlib.contextmanager
def alleen_lezen(server: Server) -> Iterator[sqlite3.Connection]:
    """De database naast de draaiende server (WAL), alleen lezen."""
    pad = server.data_dir / "platform.db"
    con = sqlite3.connect(f"file:{pad}?mode=ro", uri=True, timeout=10)
    try:
        yield con
    finally:
        con.close()


def upload(server: Server, account: Account, naam: str, data: bytes) -> dict[str, Any]:
    return server.call(
        account,
        "PUT",
        f"{API}/files/{account.root_id}/upload",
        params={"name": naam},
        content=data,
        expect=201,
    ).json()


def analyse(server: Server, account: Account, node_id: str) -> dict[str, Any]:
    return server.call(account, "GET", f"{API}/files/{node_id}/analysis", expect=200).json()


def wacht_op_tekst(server: Server, account: Account, *node_ids: str) -> dict[str, dict]:
    """Tot de stap text van elk bestand klaar is (done); geeft de laatste stand per bestand."""
    eind = time.monotonic() + WACHT_S
    stand: dict[str, dict] = {}
    while True:
        stand = {i: analyse(server, account, i) for i in node_ids}
        if all(s["text"] == "done" for s in stand.values()):
            return stand
        if time.monotonic() > eind:
            raise AssertionError(f"de tekst kwam niet binnen {WACHT_S:.0f} s: {stand}")
        time.sleep(0.2)


def snippets(server: Server, woord: str) -> dict[str, str]:
    """{node-id: snippet} uit de index voor een woord in de tekst (kolom body)."""
    with alleen_lezen(server) as con:
        rijen = con.execute(
            "SELECT n.id, snippet(fts_node, 2, '[', ']', '…', 12) FROM fts_node "
            "JOIN node n ON n.num = fts_node.rowid WHERE fts_node MATCH ?",
            (f'body : "{woord}"',),
        ).fetchall()
    return {str(_uuid(i)): s for i, s in rijen}


def _uuid(waarde: Any) -> str:
    return str(uuid.UUID(bytes=waarde) if isinstance(waarde, bytes) else uuid.UUID(waarde))


def gevonden(server: Server, account: Account, woord: str, node_id: str) -> str:
    """Zoeken op een woord uit de tekst via /search (FtsEngine): het snippet van dit bestand,
    met elke treffer tussen [ en ] (zoals snippets())."""
    antwoord = server.call(account, "GET", f"{API}/search", params={"q": woord}, expect=200).json()
    assert antwoord["engine"] == "fts" and antwoord["names_only"] is False, antwoord
    hits = {h["node"]["id"]: h for h in antwoord["results"]}
    assert node_id in hits, antwoord
    assert hits[node_id]["match"] == "text", hits[node_id]
    delen = hits[node_id]["snippet"]
    assert delen, hits[node_id]
    return "".join(f"[{d['t']}]" if d["m"] else d["t"] for d in delen)


def zoek_en_zie_het_snippet(server: Server, account: Account, woord: str) -> str:
    """Upload een docx met woord in de tekst (niet in de naam), wacht op de stap text en zoek
    het woord: het snippet bevat het woord."""
    node = upload(server, account, "Verslag van de week.docx", F.docx(f"Dit is {woord} hier."))
    stand = wacht_op_tekst(server, account, node["id"])[node["id"]]
    assert stand == {
        "text": "done", "hash": "skip", "ocr": "skip", "truncated": False, "code": None,
    }, stand  # fmt: skip
    snippet = gevonden(server, account, woord, node["id"])
    assert woord in snippet.lower(), snippet
    return snippet


# --- media uit: de terugval -----------------------------------------------------------------


@pytest.fixture(scope="module")
def app(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[Server, Account]]:
    """Een eigen server met media uit (de standaard): de stap text leest in het renderproces
    van de app."""
    server = Server(Path(tmp_path_factory.mktemp("zoek-z2")) / "data")
    server.start()
    try:
        yield server, make_admin(server)
    finally:
        server.stop()


def test_docx_uploaden_en_een_woord_uit_de_tekst_vinden(app: tuple[Server, Account]) -> None:
    server, admin = app
    snippet = zoek_en_zie_het_snippet(server, admin, "zoekwoordterugval")
    assert "[zoekwoordterugval]" in snippet.lower(), snippet


def test_de_backfill_na_een_herstart(app: tuple[Server, Account]) -> None:
    """Documenten van vóór Z2: geen rij in analysis en geen blob_text. Na een herstart leest de
    backfill ze vanzelf, en staan hun woorden weer in de index."""
    server, admin = app
    stukken = {
        "notitie.txt": (b"Een notitie met backfillwoordeen erin.\n", "backfillwoordeen"),
        "brief.docx": (F.docx("De brief met backfillwoordtwee."), "backfillwoordtwee"),
        "verslag.pdf": (F.pdf_tekst(2, tekst="backfillwoorddrie"), "backfillwoorddrie"),
    }
    nodes = {naam: upload(server, admin, naam, data) for naam, (data, _) in stukken.items()}
    wacht_op_tekst(server, admin, *(n["id"] for n in nodes.values()))
    for naam, (_, woord) in stukken.items():
        assert nodes[naam]["id"] in snippets(server, woord), naam

    def voor_z2(data_dir: Path) -> None:
        con = sqlite3.connect(data_dir / "platform.db")
        try:
            con.execute("DELETE FROM analysis")
            con.execute("DELETE FROM blob_text")  # de triggers halen de tekst uit fts_node
            con.commit()
        finally:
            con.close()

    server.seeded(voor_z2)
    for naam, (_, woord) in stukken.items():
        assert nodes[naam]["id"] not in snippets(server, woord), naam
    stand = wacht_op_tekst(server, admin, *(n["id"] for n in nodes.values()))
    assert {s["text"] for s in stand.values()} == {"done"}
    for naam, (_, woord) in stukken.items():
        assert nodes[naam]["id"] in snippets(server, woord), naam
    # De rij staat op naam van de eigenaar (om de beurt per eigenaar, Keuze 20)
    with alleen_lezen(server) as con:
        eigenaren = {r[0] for r in con.execute("SELECT DISTINCT owner_id FROM analysis")}
    assert len(eigenaren) == 1 and _uuid(eigenaren.pop()) == admin.id


# --- media aan: de worker -------------------------------------------------------------------


@pytest.mark.media
def test_met_media_aan_leest_de_worker(media: Any) -> None:
    """Dezelfde keten met media aan: de stap text gaat naar de worker (één kind per eigenaar,
    met de handdruk). Met media aan is er geen terugval: was de worker er niet, dan wachtte
    de stap (Defer van 30 s) en kwam de tekst niet binnen deze test."""
    server, admin = media.server, media.admin
    assert server.extra_env["PLATFORM_MEDIA_ENABLED"] == "true"
    snippet = zoek_en_zie_het_snippet(server, admin, "zoekwoordworker")
    assert "zoekwoordworker" in snippet.lower()
