"""De plannen van de vragen per foto en per id (fase 6, X2b; face_rows.no_index).

Zonder sqlite_stat1 kiest SQLite (ook 3.46.1) bij face.node_id = ? samen met face.owner_id = ? en
hidden = 0 de gedeeltelijke index ix_face_new, en leest dan alle gezichten van de eigenaar: bij
60.000 gezichten 50 ms per vraag, en in een batch per foto, met het schrijfslot. Net zo
ix_face_scan_todo (alles wat wacht) bij face_scan.node_id IN (...) met state = ?. De test draait
een scan, een ja, een kader met de hand, de foto in de viewer, voorstellen en groepen, met
EXPLAIN QUERY PLAN bij elke vraag op face of face_scan, en houdt vast dat zo'n vraag (ook
face_scan.node_id = ?, de viewer) langs ix_face_node of de sleutel loopt.

Op PostgreSQL krijgt de vraag de kolom zelf (geen unaire +): daar kiest de planner met de
statistiek die autovacuum bijhoudt. Bij deze paar rijen is ix_face_new daar terecht goedkoper;
dus zoals het echt is: na de vragen 5000 gezichten van Eva en 5000 foto's die wachten op een scan,
ANALYZE, en dan EXPLAIN van dezelfde vragen (de parameters $n als ?): ook daar geen plan langs
ix_face_new of ix_face_scan_todo.
"""

from __future__ import annotations

import re
import uuid

import pytest
from sqlalchemy import event, select, text
from sqlalchemy.engine import Engine

from app.models import Face
from app.people.faces import group
from tests.factories import create_user, maker
from tests.people.faces.conftest import aan, draai, foto
from tests.test_photos import login

pytestmark = pytest.mark.anyio

A = (0.10, 0.10, 0.20, 0.25)
B = (0.60, 0.20, 0.20, 0.25)
F = "/api/v1/people/faces"
STATEMENT = re.compile(r"^\s*(SELECT|UPDATE|DELETE|WITH)\b", re.I)
# Een parameter van asyncpg ($1, $2::UUID): in de tekst hier een ? zoals op SQLite. Een type
# van meer woorden alleen bij naam: anders zou ::UUID AND ook het AND erachter meenemen.
PG_PARAM = re.compile(
    r"\$\d+(::(TIMESTAMP WITH(OUT)? TIME ZONE|DOUBLE PRECISION|[A-Z_]+)(\(\d+\))?(\[\])?)?"
)


# Op PostgreSQL, na de vragen: 5000 foto's van Eva, elk met een gezicht en wachtend op een scan
VUL = (
    "INSERT INTO node (id, parent_id, owner_id, name, kind, mime, size, created_at, modified_at, "
    "search_name) SELECT gen_random_uuid(), :root, :owner, 'vul' || g || '.jpg', 'file', "
    "'image/jpeg', 0, now(), now(), 'vul' || g || '.jpg' FROM generate_series(1, 5000) AS g",
    "INSERT INTO face (id, node_id, owner_id, x, y, w, h, score, px, version, hidden, created_at) "
    "SELECT gen_random_uuid(), n.id, :owner, 0.1, 0.1, 0.2, 0.2, 0.9, 100, 1, 0, now() "
    "FROM node n WHERE n.parent_id = :root AND n.name LIKE 'vul%'",
    "INSERT INTO face_scan (node_id, owner_id, version, state, prio, queued_at, updated_at) "
    "SELECT n.id, :owner, 1, 'wait', 'scan', now(), now() "
    "FROM node n WHERE n.parent_id = :root AND n.name LIKE 'vul%'",
)


class Plannen:
    """Elke vraag op face of face_scan met zijn plan (de tekst van EXPLAIN QUERY PLAN). Op
    PostgreSQL eerst alleen de vragen; de plannen pas met pg_plans()."""

    def __init__(self) -> None:
        self.seen: dict[str, str] = {}
        self.later: dict[str, object] = {}

    def __call__(self, conn, cursor, statement, parameters, context, executemany) -> None:
        if not STATEMENT.match(statement) or statement in self.seen or statement in self.later:
            return
        if not re.search(r"\b(face|face_scan)\b", statement):
            return
        if executemany:
            parameters = parameters[0] if parameters else ()
        if conn.dialect.name == "postgresql":
            self.later[statement] = parameters
            return
        explain = conn.connection.dbapi_connection.cursor()
        try:
            explain.execute("EXPLAIN QUERY PLAN " + statement, parameters)
            self.seen[statement] = " | ".join(row[3] for row in explain.fetchall())
        finally:
            explain.close()

    async def pg_plans(self, settings, owner: uuid.UUID, root: uuid.UUID) -> None:
        """Vul, ANALYZE, en EXPLAIN van elke vraag; daarna alles terug (rollback)."""
        async with maker(settings)() as session:
            conn = await session.connection()
            for vraag in VUL:
                await conn.execute(text(vraag), {"owner": owner, "root": root})
            await conn.exec_driver_sql("ANALYZE node, face, face_scan")
            for statement, parameters in self.later.items():
                rows = await conn.exec_driver_sql("EXPLAIN " + statement, parameters)
                self.seen[statement] = " | ".join(row[0] for row in rows)
            await session.rollback()

    def met(self, pattern: str) -> dict[str, str]:
        out = {}
        for s, p in self.seen.items():
            flat = PG_PARAM.sub("?", " ".join(s.split()))
            if re.search(pattern, flat):
                out[flat] = p
        return out


async def test_een_vraag_per_foto_of_per_id_leest_niet_alle_gezichten(client, settings, nep):
    async with maker(settings)() as session:
        user = await create_user(session, "eva@example.nl")
        await session.commit()
    fotos = [await foto(settings, nep, user.id, f"{i}.jpg", [(A, 1), (B, 2)]) for i in range(4)]
    await aan(settings, user.id)
    plannen = Plannen()
    event.listen(Engine, "before_cursor_execute", plannen)
    try:
        await draai(settings)
        h = await login(client, "eva@example.nl")
        async with maker(settings)() as session:
            ids = list(
                await session.scalars(
                    select(Face.id).where(Face.node_id.in_(fotos[:3])).order_by(Face.x).limit(3)
                )
            )
        r = await client.post(
            f"{F}/confirm", json={"faces": [str(i) for i in ids], "name": "Oma"}, headers=h
        )
        assert r.status_code == 204, r.text
        r = await client.get(f"/api/v1/files/{fotos[3]}/faces", headers=h)
        assert r.status_code == 200 and len(r.json()) == 2, r.text
        box = [B[0] + 0.01, B[1] + 0.01, B[2] - 0.01, B[3] - 0.01]
        r = await client.post(
            f"/api/v1/files/{fotos[3]}/people", json={"name": "Neef", "box": box}, headers=h
        )
        assert r.status_code == 201, r.text
        for path in ("/status", "/suggestions", "/groups"):
            assert (await client.get(F + path, headers=h)).status_code == 200
        await group.run_group(settings, user.id, "full")
        await group.run_group(settings, user.id, "suggest")
    finally:
        event.remove(Engine, "before_cursor_execute", plannen)
    if plannen.later:
        await plannen.pg_plans(settings, user.id, user.root_id)

    per_foto = plannen.met(r"\bface\.node_id = \?|\bface\.id IN \(")
    scans = plannen.met(r"\bface_scan\.node_id (= \?|IN \()")
    # De vragen waar het om gaat, zijn er echt (de viewer, het kader, de tag, de ja, de batch)
    assert any("ORDER BY face.x" in s for s in per_foto), sorted(per_foto)
    assert any(
        s.startswith("SELECT face.id, face.x, face.y, face.w, face.h FROM face") for s in per_foto
    )
    assert any(s.startswith("SELECT face.id FROM face WHERE face.node_id") for s in per_foto)
    assert any(s.startswith("SELECT face.id, face.node_id FROM face JOIN node") for s in per_foto)
    assert any(s.startswith("UPDATE face_scan SET state=?") for s in scans), sorted(scans)
    assert any(s.startswith("SELECT face_scan.node_id FROM face_scan JOIN node") for s in scans)
    slecht = {s: p for s, p in per_foto.items() if "ix_face_new" in p}
    slecht |= {s: p for s, p in scans.items() if "ix_face_scan_todo" in p}
    assert not slecht, "\n".join(f"{s}\n    {p}" for s, p in slecht.items())
