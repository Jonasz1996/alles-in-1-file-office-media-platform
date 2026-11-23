"""python -m app.cli db-downgrade, db-backup, geo-load zonder bronnen en office-checkpoint (§3.5,
§3.8): db-downgrade 0007 vraagt om bevestiging en doet daarna precies dat; een onbekende of
latere revisie doet niets. db-backup maakt een kopie van de database terwijl de app draait (fase
6, Keuze 52). geo-load is geen stub meer (Z3): hier alleen zonder bronnen, de rest staat in
tests/geo/test_geo_load.py."""

import os
import sqlite3
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

import anyio
import pytest
import sqlalchemy as sa

from app import cli
from app.db import get_engine
from app.migrate import upgrade

API_DIR = Path(__file__).resolve().parents[1]
HEAD = "0013"


async def run_cli(settings, *args: str, stdin: bytes | None = None):
    env = {
        **os.environ,
        "PLATFORM_DATA_DIR": str(settings.data_dir),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    if settings.database_url:  # PostgreSQL: het subproces moet dezelfde database zien
        env["PLATFORM_DATABASE_URL"] = settings.database_url
    return await anyio.run_process(
        [sys.executable, "-m", "app.cli", *args],
        cwd=API_DIR,
        env=env,
        check=False,
        input=stdin if stdin is not None else b"",
    )


async def revision(settings) -> str:
    async with get_engine(settings).connect() as conn:
        return await conn.scalar(sa.text("SELECT version_num FROM alembic_version"))


async def has_table(settings, name: str) -> bool:
    async with get_engine(settings).connect() as conn:
        return await conn.run_sync(lambda c: sa.inspect(c).has_table(name))


def test_check_downgrade():
    cli.check_downgrade("0007", "0006")
    cli.check_downgrade("0007", "0001")
    for current, target in (
        ("0007", "0007"),  # gelijk: niets te doen
        ("0006", "0007"),  # later: dat is geen downgrade
        ("0007", "9999"),  # bestaat niet
        ("0007", "kapot"),
        (None, "0006"),  # een lege database
    ):
        with pytest.raises(cli.UnknownRevision):
            cli.check_downgrade(current, target)


@pytest.mark.anyio
async def test_downgrade_vraagt_om_bevestiging(settings):
    assert await revision(settings) == HEAD

    result = await run_cli(settings, "db-downgrade", "0007", stdin=b"nee\n")
    assert result.returncode == 1
    out = result.stdout.decode()
    assert f"van revisie {HEAD} terug naar 0007" in out
    # Wat er weggaat, de nieuwste revisie eerst (§3.1a)
    assert cli.BEELD_LOST in out and cli.FACES_LOST in out and cli.SEARCH_LOST in out
    assert out.index(cli.BEELD_LOST) < out.index(cli.FACES_LOST) < out.index(cli.SEARCH_LOST)
    assert 'Typ "ja" om door te gaan' in out
    assert "Niets gedaan." in result.stderr.decode()
    assert await revision(settings) == HEAD

    # Zonder invoer (EOF) ook niet
    result = await run_cli(settings, "db-downgrade", "0007")
    assert result.returncode == 1
    assert await revision(settings) == HEAD

    result = await run_cli(settings, "db-downgrade", "0007", stdin=b"ja\n")
    assert result.returncode == 0, result.stderr.decode()
    assert "De database staat nu op revisie 0007." in result.stdout.decode()
    assert await revision(settings) == "0007"
    assert not await has_table(settings, "media_library")
    assert await has_table(settings, "office_session")
    assert not await has_table(settings, "search_state")

    # Een latere of onbekende revisie: niets
    for target in ("0008", "9999"):
        result = await run_cli(settings, "db-downgrade", target, "--ja")
        assert result.returncode == 1
        assert "bestaat niet of ligt niet vóór de huidige (0007)" in result.stderr.decode()
    assert await revision(settings) == "0007"

    # De app zet hem bij het starten weer op head
    await upgrade(settings)
    assert await revision(settings) == HEAD
    assert await has_table(settings, "media_library")
    assert await has_table(settings, "search_state")


@pytest.mark.anyio
async def test_downgrade_met_ja_vraagt_niets(settings):
    result = await run_cli(settings, "db-downgrade", "0005", "--ja")
    assert result.returncode == 0, result.stderr.decode()
    assert "Typ" not in result.stdout.decode()
    assert await revision(settings) == "0005"


def test_welke_revisies_een_downgrade_weghaalt():
    """Alleen een downgrade die 0010 weghaalt, raakt zoeken en foto's (fase 6, §3.1); 0011 de
    gezichten en 0012 de beeldindex (§3.1a). 0013 (PostgreSQL, 0.8.2) maakt op SQLite niets
    kwijt; op PostgreSQL de plaatsen van foto's (review PG13-2, test hieronder)."""
    assert cli.removed_revisions(HEAD, "0012") == ["0013"]
    assert cli.removed_revisions(HEAD, "0011") == ["0013", "0012"]
    assert cli.removed_revisions(HEAD, "0010") == ["0013", "0012", "0011"]
    assert cli.removed_revisions(HEAD, "0009") == ["0013", "0012", "0011", "0010"]
    assert cli.removed_revisions(HEAD, "0007") == [
        "0013", "0012", "0011", "0010", "0009", "0008"
    ]  # fmt: skip
    assert cli.lost_lines(["0013"]) == []
    assert cli.PG_REVISION == "0013"
    assert cli.removed_revisions("0009", "0008") == ["0009"]
    assert cli.SEARCH_REVISION == "0010"
    assert cli.FACES_REVISION == "0011"
    assert cli.BEELD_REVISION == "0012"


def test_wat_een_downgrade_kwijtmaakt():
    """Per weggehaalde revisie één regel, de nieuwste eerst; een downgrade vóór 0010 zegt niets
    extra."""
    assert cli.lost_lines(["0012"]) == [cli.BEELD_LOST]
    assert cli.lost_lines(["0012", "0011"]) == [cli.BEELD_LOST, cli.FACES_LOST]
    assert cli.lost_lines(["0012", "0011", "0010"]) == [
        cli.BEELD_LOST,
        cli.FACES_LOST,
        cli.SEARCH_LOST,
    ]
    assert cli.lost_lines(["0009", "0008"]) == []


def test_wat_een_downgrade_op_postgresql_kwijtmaakt():
    """Review PG13-2: 0013 terug naar 0012 maakt op PostgreSQL place_id boven int32 NULL, en dat
    zijn alle plaatsen (48 bits, app/geo/load.place_id). Dat zegt de vraag nu, als nieuwste
    eerst. Op SQLite doet 0013 niets, dus daar niet."""
    assert cli.lost_lines(["0013"], postgres=True) == [cli.PG_LOST]
    assert cli.lost_lines(["0013"], postgres=False) == []
    assert cli.lost_lines(["0013", "0012", "0011", "0010"], postgres=True) == [
        cli.PG_LOST,
        cli.BEELD_LOST,
        cli.FACES_LOST,
        cli.SEARCH_LOST,
    ]
    assert cli.lost_lines(["0012"], postgres=True) == [cli.BEELD_LOST]
    assert "plaatsen" in cli.PG_LOST


@pytest.mark.parametrize("postgres", [True, False], ids=["postgresql", "sqlite"])
def test_de_downgrade_vraagt_met_de_regel_van_postgresql(tmp_path, monkeypatch, postgres):
    """_downgrade geeft _confirm de regel van PostgreSQL alleen op PostgreSQL (zonder database:
    de revisie en het antwoord zijn nagemaakt; er komt geen verbinding)."""
    from app.config import Settings

    url = "postgresql+asyncpg://p@127.0.0.1:1/x" if postgres else ""
    settings = Settings(data_dir=tmp_path, web_dist=tmp_path / "web", jwt_secret="test" * 8,
                        database_url=url)  # fmt: skip
    seen: list[list[str]] = []

    async def at_head(_settings):
        return HEAD

    def confirm(_current, _revision, *, lost):
        seen.append(lost)
        return False

    monkeypatch.setattr(cli, "db_revision", at_head)
    monkeypatch.setattr(cli, "_confirm", confirm)
    assert cli._downgrade(settings, "0012", yes=False) == 1
    assert seen == [[cli.PG_LOST] if postgres else []]


async def _album_met_regel_items(settings) -> tuple[uuid.UUID, uuid.UUID]:
    """Een album met twee items van een regel die niet gedeeld worden, en één met de hand."""
    user, root, album = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    foto1, foto2, foto3 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    pg = bool(settings.database_url)
    moment = datetime(2026, 11, 3, 9, 0, tzinfo=UTC)
    if not pg:
        moment = moment.isoformat()

    def h(x: uuid.UUID) -> uuid.UUID | str:
        return x if pg else x.hex

    async with get_engine(settings).begin() as conn:
        await conn.execute(
            sa.text(
                'INSERT INTO "user" (id, email, display_name, password_hash, role, '
                "quota_bytes, root_id, created_at) VALUES (:id, 'eva@example.nl', 'Eva', 'x', "
                "'user', 0, :root, :t)"
            ),
            {"id": h(user), "root": h(root), "t": moment},
        )
        for node_id, name, kind in (
            (root, "", "dir"),
            (album, "Vakantie", "album"),
            (foto1, "een.jpg", "file"),
            (foto2, "twee.jpg", "file"),
            (foto3, "drie.jpg", "file"),
        ):
            await conn.execute(
                sa.text(
                    "INSERT INTO node (id, parent_id, owner_id, name, kind, size, created_at, "
                    "modified_at, search_name) VALUES (:id, :parent, :owner, :name, :kind, 0, "
                    ":t, :t, :sname)"
                ),
                {
                    "id": h(node_id),
                    "parent": None if kind != "file" else h(root),
                    "owner": h(user),
                    "name": name,
                    "sname": name,
                    "kind": kind,
                    "t": moment,
                },
            )
        for node_id, source, shared in (
            (foto1, "rule", 0),
            (foto2, "rule", 0),
            (foto3, "hand", 1),
        ):
            await conn.execute(
                sa.text(
                    "INSERT INTO album_item (album_id, node_id, added_at, source, shared) "
                    "VALUES (:album, :node, :t, :source, :shared)"
                ),
                {
                    "album": h(album),
                    "node": h(node_id),
                    "t": moment,
                    "source": source,
                    "shared": shared,
                },
            )
    return user, album


async def _items(settings) -> int:
    async with get_engine(settings).connect() as conn:
        return await conn.scalar(sa.text("SELECT count(*) FROM album_item"))


@pytest.mark.anyio
async def test_downgrade_vraagt_naar_regel_items_die_niet_gedeeld_worden(settings):
    """Na 0009 deelt een album elk item, zoals in 0.7.0. db-downgrade noemt het aantal en haalt
    ze eruit als je ja zegt (fase 6, §3.1)."""
    await _album_met_regel_items(settings)
    assert await cli.unshared_rule_items(settings) == 2

    # "nee" op de vraag: ze blijven staan, en de downgrade zelf gaat gewoon door
    result = await run_cli(settings, "db-downgrade", "0009", stdin=b"nee\nja\n")
    assert result.returncode == 0, result.stderr.decode()
    out = result.stdout.decode()
    assert "2 item(s) in een album komen van een regel en worden niet gedeeld" in out
    assert "Die items eerst uit het album halen? [ja]" in out
    assert "Die items blijven in hun album staan." in out
    assert "de personen die je met de hand benoemde" in out
    assert await revision(settings) == "0009"
    assert await _items(settings) == 3

    # Weer vooruit: source en shared zijn nieuw, dus elk item staat weer op 'hand' en gedeeld.
    # Dat is precies waarom de vraag er is.
    await upgrade(settings)
    assert await cli.unshared_rule_items(settings) == 0
    assert await _items(settings) == 3


@pytest.mark.anyio
async def test_downgrade_haalt_de_regel_items_eruit(settings):
    await _album_met_regel_items(settings)
    result = await run_cli(settings, "db-downgrade", "0009", stdin=b"\nja\n")
    assert result.returncode == 0, result.stderr.decode()
    assert "2 item(s) zijn uit hun album gehaald." in result.stdout.decode()
    assert await revision(settings) == "0009"
    assert await _items(settings) == 1  # alleen het item van de hand


@pytest.mark.anyio
async def test_downgrade_met_ja_haalt_ze_eruit_zonder_vragen(settings):
    """Standaard ja: met --ja vraagt hij niets en haalt ze er toch uit."""
    await _album_met_regel_items(settings)
    result = await run_cli(settings, "db-downgrade", "0009", "--ja")
    assert result.returncode == 0, result.stderr.decode()
    assert "Typ" not in result.stdout.decode()
    assert "2 item(s) zijn uit hun album gehaald." in result.stdout.decode()
    assert await _items(settings) == 1


@pytest.mark.anyio
@pytest.mark.parametrize(
    "stdin",
    [b"\nnee\n", b"ja\n", b""],
    ids=["ja-dan-nee", "ja-dan-eof", "eof"],
)
async def test_downgrade_die_niet_doorgaat_haalt_ook_geen_items_weg(settings, stdin):
    """Review Z0, veilig F1: het antwoord op de vraag naar de regel-items doet pas iets na het
    laatste "ja". Wie daarna "nee" zegt, of geen invoer meer heeft, houdt alles: de items en
    de head."""
    await _album_met_regel_items(settings)
    result = await run_cli(settings, "db-downgrade", "0009", stdin=stdin)
    assert result.returncode == 1
    assert "Niets gedaan." in result.stderr.decode()
    assert "uit hun album gehaald" not in result.stdout.decode()
    assert await revision(settings) == HEAD
    assert await _items(settings) == 3
    assert await cli.unshared_rule_items(settings) == 2


@pytest.mark.anyio
async def test_regel_items_en_downgrade_in_een_transactie(settings, monkeypatch):
    """Review Z0, veilig F1: faalt de downgrade halverwege, dan staan de items er nog."""
    await _album_met_regel_items(settings)

    def kapot(cfg, revision):
        raise RuntimeError("downgrade stuk")

    monkeypatch.setattr(cli.command, "downgrade", kapot)
    with pytest.raises(RuntimeError, match="downgrade stuk"):
        await cli.db_downgrade(settings, "0009", drop_rule_items=True)
    assert await revision(settings) == HEAD
    assert await _items(settings) == 3


@pytest.mark.anyio
async def test_downgrade_zonder_regel_items_vraagt_niets_extra(settings):
    result = await run_cli(settings, "db-downgrade", "0009", stdin=b"ja\n")
    assert result.returncode == 0, result.stderr.decode()
    assert "uit het album" not in result.stdout.decode()
    assert await revision(settings) == "0009"


def _backups(settings) -> list[Path]:
    return sorted((settings.data_dir / "backup").glob("*.db"))


@pytest.mark.anyio
@pytest.mark.sqlite_only("sqlite3.backup; op PostgreSQL maakt db-backup een pg_dump")
async def test_db_backup_maakt_een_kopie_terwijl_de_app_draait(settings):
    """Keuze 52: een kopie met sqlite3.Connection.backup, genoemd naar de head en de tijd. De
    app blijft draaien: deze test houdt een schrijver open."""
    async with get_engine(settings).begin() as conn:
        await conn.execute(
            sa.text(
                'INSERT INTO "user" (id, email, display_name, password_hash, role, '
                "quota_bytes, root_id, created_at) VALUES (:id, 'eva@example.nl', 'Eva', 'x', "
                "'user', 0, NULL, :t)"
            ),
            {"id": uuid.uuid4().hex, "t": datetime(2026, 11, 3, tzinfo=UTC).isoformat()},
        )
    # Een open transactie die nog niet gecommit is: de kopie hoort hem niet te bevatten
    live = sqlite3.connect(settings.data_dir / "platform.db")
    try:
        live.execute("BEGIN IMMEDIATE")
        live.execute(
            'INSERT INTO "user" (id, email, display_name, password_hash, role, quota_bytes, '
            "root_id, created_at) VALUES (?, 'bram@example.nl', 'Bram', 'x', 'user', 0, NULL, ?)",
            (uuid.uuid4().hex, "2026-11-03T00:00:00+00:00"),
        )
        result = await run_cli(settings, "db-backup")
        assert result.returncode == 0, result.stderr.decode()
    finally:
        live.rollback()
        live.close()

    [copy] = _backups(settings)
    assert str(copy) in result.stdout.decode()
    assert copy.name.startswith(f"platform-{HEAD}-") and cli.BACKUP_RE.match(copy.name)
    assert not list((settings.data_dir / "backup").glob("*.part"))
    # Het is een echte database op dezelfde revisie, met de gecommitte rij en zonder de andere
    with sqlite3.connect(f"file:{copy}?mode=ro", uri=True) as kopie:
        assert kopie.execute("SELECT version_num FROM alembic_version").fetchall() == [(HEAD,)]
        assert kopie.execute('SELECT count(*) FROM "user"').fetchone() == (1,)
        assert kopie.execute('SELECT email FROM "user"').fetchone() == ("eva@example.nl",)
        assert kopie.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    # De database zelf is niet gemigreerd of veranderd
    assert await revision(settings) == HEAD


@pytest.mark.anyio
@pytest.mark.sqlite_only("sqlite3.backup; op PostgreSQL maakt db-backup een pg_dump")
async def test_db_backup_houdt_per_head_de_nieuwste(settings):
    folder = settings.data_dir / "backup"
    folder.mkdir()
    # Twee oudere kopieën: een van vóór de migratie (0009) en een van deze head
    oud_0009 = folder / "platform-0009-20260101T000000.000Z.db"
    oud_head = folder / f"platform-{HEAD}-20260101T000000.000Z.db"
    vreemd = folder / "platform-met-de-hand.db"
    for path in (oud_0009, oud_head, vreemd):
        path.write_bytes(b"")

    result = await run_cli(settings, "db-backup")
    assert result.returncode == 0, result.stderr.decode()
    namen = {p.name for p in _backups(settings)}
    assert oud_0009.name in namen  # de laatste kopie van vóór de migratie blijft
    assert oud_head.name not in namen  # van deze head blijft alleen de nieuwste
    assert vreemd.name in namen  # wat niet van ons lijkt, blijft ook staan
    assert len(namen) == 3

    # Nog een keer: nog steeds één per head, en de nieuwste
    result = await run_cli(settings, "db-backup")
    assert result.returncode == 0, result.stderr.decode()
    van_head = sorted(p.name for p in _backups(settings) if p.name.startswith(f"platform-{HEAD}"))
    assert len(van_head) == 1
    assert van_head[0] > oud_head.name


@pytest.mark.anyio
@pytest.mark.sqlite_only("sqlite3.backup; op PostgreSQL maakt db-backup een pg_dump")
async def test_db_backup_zonder_database(settings, tmp_path):
    leeg = tmp_path / "leeg"
    leeg.mkdir()
    result = await run_cli(settings.model_copy(update={"data_dir": leeg}), "db-backup")
    assert result.returncode == 1
    assert "Er is nog geen database" in result.stderr.decode()
    assert not (leeg / "backup").exists()


@pytest.mark.anyio
async def test_de_commandos_van_zoeken_zijn_gebouwd(settings):
    """Geen stub meer: geo-load (Z3) bouwt places.sqlite. Zonder bronnen in geo_src (data_dir/geo)
    zegt hij dat, exitcode 1, en maakt hij niets; de rest van het gedrag staat in
    tests/geo/test_geo_load.py."""
    assert cli.SEARCH_STUBS == {}
    result = await run_cli(settings, "geo-load")
    assert result.returncode == 1, result.stderr.decode()
    assert "De bronnen staan niet in" in result.stderr.decode()
    assert not (settings.data_dir / "geo" / "places.sqlite").exists()


@pytest.mark.anyio
async def test_de_stubs_van_gezichten_en_beeld(settings):
    """Er is geen stub van gezichten en beeld meer (A0 zette ze, §3.5): analyse-bench en
    analyse-kwaliteit bouwde X2b, beeld-status, beeld-bench en beeld-kwaliteit X3b. Met de
    analyse uit (zoals 0.8.0) doen ze niets: exitcode 1, één regel op stderr, niets op stdout, en
    niets in de datamap. Met de analyse aan: tests/people/faces/test_faces_meten.py en
    tests/beeld/test_beeld_cli.py."""
    assert cli.ANALYSE_STUBS == {}
    before = sorted(str(p.relative_to(settings.data_dir)) for p in settings.data_dir.rglob("*"))
    for name in ("beeld-status", "beeld-bench", "beeld-kwaliteit"):
        result = await run_cli(settings, name)
        assert result.returncode == 1, result.stderr.decode()
        assert result.stderr.decode() == (
            "Zoeken op beeld staat uit: de analyse staat uit (ANALYSE).\n"
        )
        assert result.stdout == b""
    for name in ("analyse-bench", "analyse-kwaliteit"):
        result = await run_cli(settings, name)
        assert result.returncode == 1, result.stderr.decode()
        assert result.stderr.decode() == (
            f"{name}: niets gemeten: gezichten staan uit (ANALYSE=uit of geen"
            " PLATFORM_FACES_KEYS)\n"
        )
        assert result.stdout == b""
    after = sorted(str(p.relative_to(settings.data_dir)) for p in settings.data_dir.rglob("*"))
    assert after == before


@pytest.mark.anyio
async def test_office_checkpoint_zonder_office(settings):
    """Zonder office is er niets open: 0 documenten, en geen fout."""
    result = await run_cli(settings, "office-checkpoint", "--wait", "1")
    assert result.returncode == 0, result.stderr.decode()
    assert "Tussentijds opgeslagen: 0 document(en)." in result.stdout.decode()
