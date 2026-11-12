"""Beheer vanaf de commandoregel, voor als het via de web-app niet meer gaat.

    docker exec <container> python -m app.cli reset-2fa <e-mailadres>
    docker exec <container> python -m app.cli make-admin <e-mailadres>

reset-2fa is er voor de laatste beheerder die zijn telefoon én de herstelcodes kwijt is,
of die niet meer binnenkomt omdat iemand anders de tweede factor met foute pogingen
geblokkeerd houdt (zie FREE_ATTEMPTS in app/routers/auth.py). Het zet 2FA uit, heft de
blokkade op en trekt alle logins van dat account in. Daarna log je in met alleen het
wachtwoord en zet je 2FA opnieuw aan.

make-admin is er voor als er geen (actieve) beheerder meer is om het via /beheer te doen: het
maakt het account beheerder en heft een blokkade op. Niet voor een account dat al wordt
verwijderd (een taak user.delete staat klaar of loopt): die taak zou gewoon doorgaan.

    docker exec <container> python -m app.cli db-downgrade <revisie> [--ja]
    docker exec <container> python -m app.cli db-backup
    docker exec <container> python -m app.cli office-checkpoint [--wait 30]

db-downgrade zet het schema van de database terug naar een eerdere revisie (Alembic
downgrade), bijvoorbeeld 0006 vóór het terugzetten van een image van fase 3 (M19): een oudere
app start niet op een nieuwer schema. Het vraagt eerst om bevestiging (typ "ja"), tenzij --ja.
Wat alleen in de nieuwere kolommen en tabellen stond, is daarna weg. Stop de app eerst.
Haalt de downgrade 0010 weg (fase 6), dan zegt hij ook wat er van zoeken en foto's weggaat, en
vraagt hij of album-items van een regel die niet gedeeld worden eerst uit hun album moeten
(standaard ja): in 0.7.1 deelt een album elk item. Haalt hij 0012 (zoeken op beeld) of 0011
(gezichten) weg, dan zegt hij ook dat; 0011 wist de gezichten met secure_delete. Na de downgrade
gaat de WAL leeg (wal_checkpoint(TRUNCATE)), zodat er niets van in -wal blijft. Terug naar 0.8.0
is db-downgrade 0010, naar 0.7.1 db-downgrade 0009.

db-backup maakt een reservekopie van de database met sqlite3.Connection.backup, terwijl de app
draait (Keuze 52 van fase 6). Het bestand komt in /data/backup en heet naar de alembic-head en
de tijd: platform-0012-<tijd>.db. Per head blijft de nieuwste; zo staat de kopie van vóór een
migratie er na nieuwe kopieën nog. Het migreert niets. De kopie heeft geen gezichten en geen
beeld. install.sh draait dezelfde tekst (app/backup.py, alleen de standaardbibliotheek) met het
image dat er nog draait, want dit commando bestaat in oudere images niet.

office-checkpoint vraagt de document server om alle open documenten tussentijds op te slaan
en wacht daar hooguit --wait seconden op (vóór het stoppen of bijwerken van office). Het
zegt hoeveel documenten zijn opgeslagen.

    docker exec <container> python -m app.cli previews-recheck

previews-recheck is "Alles nalopen" van /beheer → Taken: elke foto, video en ander bestand
zonder (complete) preview gaat opnieuw in de rij, ook een mislukte of een waarvan het bestand
op schijf ontbreekt. De draaiende app pakt het binnen enkele seconden op (docs/fotos-indexeren.md).

Media (fase 5), elk een dunne aanroep van een functie in app/media (de pakketten bouwen ze; tot
dan zegt een stub dat media uit staat):

    python -m app.cli media-import /data/import/Films --naar /Films --eigenaar <e-mail>
                                    [--verplaats]
    python -m app.cli media-parse <pad>…
    python -m app.cli media-status [--wait 20] [--sessions]
    python -m app.cli media-provider-check [--opnemen]
    python -m app.cli media-loadtest [--sessies 3] [--duur 300] [--codec h264] [--json uit.json]
                                     [--extern <adres>] [--film <id> …]

Een fout van media (zoals 503 media_off) geeft de melding en exitcode 1.

Zoeken en foto's (fase 6), elk een dunne aanroep van app/search/bench.py (Z1):

    python -m app.cli search-bench --data /tmp/boom [--n 100000] [--seed 6] [--profiel groot]
    python -m app.cli search-rebuild [--stuk 2000]
    python -m app.cli search-triggers
    python -m app.cli search-check [--n 1000] [--volledig] [--seed 6]

    python -m app.cli bench --los [--n 100000] [--seed 6]

search-bench maakt de boom van 100.000 om zoeken te meten, in een nieuwe map (nooit in die van
de app). search-rebuild herbouwt de zoekindex in stukken terwijl de app draait; search-triggers
zet de triggers van zoeken terug en herbouwt daarna; search-check vergelijkt willekeurige nodes
met de bron (exitcode 1 bij een verschil). bench --los (Z7, Keuze 54; app/search/benchlos.py)
meet zoeken op deze machine met tools/zoekbench/bench.py, in een tijdelijke map onder de data,
en zet de uitkomst op /beheer.

Plaatsnamen en landen (Z3, Keuze 29): places.sqlite bouwen uit de bronnen in geo_src (de app
doet het zelf bij de start als hij ontbreekt; exitcode 1 als een bron ontbreekt of niet klopt):

    python -m app.cli geo-load [--force]
"""

import argparse
import asyncio
import json
import sqlite3
import sys
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from alembic import command
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from alembic.util import CommandError
from fastapi import HTTPException
from sqlalchemy import delete, func, make_url, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app import backup, previews
from app.config import Settings, get_settings
from app.db import get_engine
from app.geo import load as geo_load
from app.migrate import alembic_config
from app.migrate import checkpoint as wal_checkpoint
from app.models import AlbumItem, User
from app.routers.auth import being_deleted, reset_totp
from app.search import bench as search_bench
from app.search import benchlos


class BeingDeleted(Exception):
    """Voor dit account staat een taak user.delete klaar of loopt er een."""


async def reset_2fa(settings: Settings, email: str) -> bool:
    """False als er geen account met dit e-mailadres is."""
    maker = async_sessionmaker(get_engine(settings), expire_on_commit=False)
    async with maker() as session:
        user = await session.scalar(
            select(User).where(func.lower(User.email) == email.strip().lower())
        )
        if user is None:
            return False
        await reset_totp(session, user, keep=None)
        return True


async def make_admin(settings: Settings, email: str) -> bool:
    """Maakt het account beheerder en heft een blokkade op. False als het er niet is;
    BeingDeleted als het al wordt verwijderd (zoals PATCH /users/{id}: 409
    user_being_deleted)."""
    maker = async_sessionmaker(get_engine(settings), expire_on_commit=False)
    async with maker() as session:
        user = await session.scalar(
            select(User).where(func.lower(User.email) == email.strip().lower())
        )
        if user is None:
            return False
        if await being_deleted(session, [user.id]):
            raise BeingDeleted(email)
        user.role = "admin"
        user.disabled_at = None
        await session.commit()
        return True


class UnknownRevision(Exception):
    """De revisie bestaat niet, of ligt niet vóór de huidige."""


class NoDatabase(Exception):
    """Er is nog geen database om een kopie van te maken."""


class NoRevision(Exception):
    """De database staat op geen enkele (leesbare) revisie."""


class NotSqlite(Exception):
    """Dit commando werkt alleen met SQLite."""


async def db_revision(settings: Settings) -> str | None:
    """De revisie waar de database nu op staat (None: nog leeg)."""

    def read(connection) -> str | None:
        return MigrationContext.configure(connection).get_current_revision()

    async with get_engine(settings).connect() as conn:
        return await conn.run_sync(read)


def check_downgrade(current: str | None, revision: str) -> None:
    """UnknownRevision als revision geen eerdere revisie van current is."""
    script = ScriptDirectory.from_config(alembic_config(""))
    try:
        script.get_revision(revision)
    except CommandError as exc:
        raise UnknownRevision(revision) from exc
    if current is None or revision == current:
        raise UnknownRevision(revision)
    earlier = {r.revision for r in script.iterate_revisions(current, "base")}
    if revision not in earlier:
        raise UnknownRevision(revision)


async def db_downgrade(settings: Settings, revision: str, *, drop_rule_items: bool = False) -> int:
    """Alembic downgrade naar revision, in één transactie. drop_rule_items: eerst de album-items
    van een regel die niet gedeeld worden uit hun album (§3.1), in dezelfde transactie; faalt de
    downgrade, dan staan ze er nog. Daarna de WAL leeg (app.migrate.checkpoint, §3.1a): wat 0011
    met secure_delete wiste, staat dan ook niet meer in -wal. Het aantal items dat eruit ging."""
    cfg = alembic_config(settings.db_url)

    def run(connection) -> int:
        removed = 0
        if drop_rule_items:
            result = connection.execute(
                delete(AlbumItem).where(AlbumItem.source == "rule", AlbumItem.shared == 0)
            )
            removed = int(result.rowcount)
        cfg.attributes["connection"] = connection
        cfg.attributes["timezone"] = settings.timezone
        command.downgrade(cfg, revision)
        return removed

    async with get_engine(settings).begin() as conn:
        removed = await conn.run_sync(run)
    await wal_checkpoint(settings)
    return removed


# De revisie van zoeken en foto's: alleen een downgrade die hém weghaalt, raakt de zoekindex,
# de plekken, de hashes, de personen en de regels van slimme albums (fase 6, §3.1)
SEARCH_REVISION = "0010"
# De revisies van gezichten en van zoeken op beeld (fase 6, §3.1a)
FACES_REVISION = "0011"
BEELD_REVISION = "0012"


def removed_revisions(current: str, revision: str) -> list[str]:
    """De revisies die een downgrade van current naar revision weghaalt."""
    script = ScriptDirectory.from_config(alembic_config(""))
    return [
        rev.revision
        for rev in script.iterate_revisions(current, revision)
        if rev.revision != revision
    ]


async def unshared_rule_items(settings: Settings) -> int:
    """Hoeveel album-items van een regel niet gedeeld worden (source 'rule', shared 0).

    Na een downgrade naar 0009 is de kolom shared weg en deelt een album elk item, zoals in
    0.7.1 (fase 6, §3.1). Die items zouden dus ineens wél gedeeld zijn.
    """
    async with get_engine(settings).connect() as conn:
        found = await conn.scalar(
            select(func.count())
            .select_from(AlbumItem.__table__)
            .where(AlbumItem.source == "rule", AlbumItem.shared == 0)
        )
    return int(found or 0)


BACKUP_DIR = "backup"
# De kopie zelf staat in app/backup.py: één tekst, die install.sh ook draait (Z7, Keuze 52)
BACKUP_RE = backup.NAME_RE


def sqlite_file(settings: Settings) -> Path:
    """Het bestand van de database; NotSqlite als het geen SQLite is."""
    url = make_url(settings.db_url)
    if url.get_backend_name() != "sqlite" or not url.database or url.database == ":memory:":
        raise NotSqlite(settings.db_url)
    return Path(url.database)


def db_backup(settings: Settings) -> Path:
    """Een reservekopie van de database naar data_dir/backup, terwijl de app draait (Keuze 52).

    sqlite3.Connection.backup geeft een kopie die klopt, ook met schrijvers erbij; de kopie heeft
    geen gezichten en geen beeld (app/backup.py). Dit commando migreert niets en raakt de
    database zelf niet aan. Het bestand heet naar de alembic-head en de tijd; per head blijft de
    nieuwste.
    """
    source = sqlite_file(settings)
    if not source.exists():
        raise NoDatabase(source)
    try:
        return backup.copy(source, settings.data_dir / BACKUP_DIR)
    except backup.NoHead as exc:
        raise NoRevision(str(source)) from exc


async def office_checkpoint(settings: Settings, wait: float) -> int:
    """Tussentijds opslaan van alle open documenten (sweep.checkpoint_all); het aantal."""
    from app.office import runtime, sweep

    runtime.bind(None, settings)
    try:
        return await sweep.checkpoint_all(wait)
    finally:
        runtime.unbind()


# Wat een downgrade die 0010 weghaalt, kwijtmaakt (fase 6, §3.1 en §3.10)
SEARCH_LOST = (
    "Weg zijn daarna: de zoekindex, de plekken van foto's, de hashes voor duplicaten, de "
    "personen die je met de hand benoemde en de regels van slimme albums. Bestanden, delingen "
    "en albums blijven, en zoeken op naam werkt gewoon."
)


# Wat een downgrade die 0012 of 0011 weghaalt, kwijtmaakt (§3.1a, Keuze 76)
BEELD_LOST = "De beeldindex gaat weg. Je foto's blijven."
FACES_LOST = (
    "De gezichten gaan weg: wat de analyse vond, met de voorstellen en de groepen, en wie welk "
    "bestand uploadde. Je foto's blijven."
)


def lost_lines(removed: list[str]) -> list[str]:
    """Wat een downgrade die deze revisies weghaalt kwijtmaakt, de nieuwste eerst."""
    lines = []
    if BEELD_REVISION in removed:
        lines.append(BEELD_LOST)
    if FACES_REVISION in removed:
        lines.append(FACES_LOST)
    if SEARCH_REVISION in removed:
        lines.append(SEARCH_LOST)
    return lines


def _confirm(current: str | None, revision: str, *, lost: list[str]) -> bool:
    print(
        f"De database gaat van revisie {current} terug naar {revision}. Wat alleen in de "
        "nieuwere kolommen en tabellen staat, is daarna weg. Is de app gestopt?"
    )
    for line in lost:
        print(line)
    try:
        answer = input('Typ "ja" om door te gaan: ')
    except EOFError:
        return False
    return answer.strip().lower() == "ja"


def _ask_rule_items(count: int) -> bool:
    """De vraag van §3.1: standaard ja, dus leeg of "ja" is ja."""
    print(
        f"{count} item(s) in een album komen van een regel en worden niet gedeeld. Na de "
        "downgrade deelt een album elk item, zoals in 0.7.1: die items zijn dan ineens wél "
        "gedeeld met iedereen die het album heeft."
    )
    try:
        answer = input("Die items eerst uit het album halen? [ja] ")
    except EOFError:
        return True
    return answer.strip().lower() in ("", "ja", "j")


def _rule_items_answer(settings: Settings, yes: bool, search: bool) -> tuple[int, bool]:
    """Hoeveel regel-items niet gedeeld worden, en of ze eerst uit hun album gaan (§3.1). Alleen
    als de downgrade 0010 weghaalt: daarvóór bestaan source en shared niet. Dit vraagt alleen;
    er verandert pas iets na het laatste "ja", in de transactie van de downgrade."""
    if not search:
        return 0, False

    async def count() -> int:
        try:
            return await unshared_rule_items(settings)
        finally:
            await get_engine(settings).dispose()

    found = asyncio.run(count())
    if not found:
        return 0, False
    return found, yes or _ask_rule_items(found)


def _downgrade(settings: Settings, revision: str, yes: bool) -> int:
    async def current() -> str | None:
        try:
            return await db_revision(settings)
        finally:
            await get_engine(settings).dispose()

    now = asyncio.run(current())
    try:
        check_downgrade(now, revision)
    except UnknownRevision:
        print(
            f"Revisie {revision} bestaat niet of ligt niet vóór de huidige ({now}).",
            file=sys.stderr,
        )
        return 1
    assert now is not None  # check_downgrade laat None niet door
    removed_revs = removed_revisions(now, revision)
    search = SEARCH_REVISION in removed_revs
    found, drop = _rule_items_answer(settings, yes, search)
    if not yes and not _confirm(now, revision, lost=lost_lines(removed_revs)):
        print("Niets gedaan.", file=sys.stderr)
        return 1

    async def run() -> int:
        try:
            return await db_downgrade(settings, revision, drop_rule_items=drop)
        finally:
            await get_engine(settings).dispose()

    removed = asyncio.run(run())
    if drop:
        print(f"{removed} item(s) zijn uit hun album gehaald.")
    elif found:
        print("Die items blijven in hun album staan.")
    print(f"De database staat nu op revisie {revision}.")
    return 0


def _checkpoint(settings: Settings, wait: float) -> int:
    async def run() -> int:
        try:
            return await office_checkpoint(settings, wait)
        finally:
            await get_engine(settings).dispose()

    saved = asyncio.run(run())
    print(f"Tussentijds opgeslagen: {saved} document(en).")
    return 0


def _recheck(settings: Settings) -> int:
    async def run() -> bool:
        try:
            return await previews.enqueue_recheck(settings)
        finally:
            await get_engine(settings).dispose()

    if asyncio.run(run()):
        print("Alles nalopen staat klaar; de app begint er binnen enkele seconden aan.")
    else:
        print("Alles nalopen liep al (of stond al klaar).")
    return 0


def _backup(settings: Settings) -> int:
    try:
        target = db_backup(settings)
    except NotSqlite:
        print("Een reservekopie maakt dit commando alleen van SQLite.", file=sys.stderr)
        return 1
    except NoDatabase as exc:
        print(f"Er is nog geen database ({exc.args[0]}).", file=sys.stderr)
        return 1
    except NoRevision:
        print(
            "De database staat op geen enkele revisie; start eerst de app.",
            file=sys.stderr,
        )
        return 1
    except (OSError, sqlite3.Error) as exc:
        print(f"De reservekopie lukte niet: {exc}", file=sys.stderr)
        return 1
    mb = target.stat().st_size / 1_000_000
    print(f"De reservekopie staat in {target} ({mb:.1f} MB).")
    return 0


# De commando's van zoeken en foto's die hun pakket nog moet bouwen (fase 6, §3.5); geo-load
# is gebouwd (Z3, app/geo/load.py)
SEARCH_STUBS: dict[str, tuple[str, str]] = {}


def _stub(name: str) -> int:
    package, what = SEARCH_STUBS[name]
    print(f"{name} ({what}) is nog niet gebouwd ({package}).", file=sys.stderr)
    return 1


def _media_failed(exc: HTTPException) -> int:
    detail = exc.detail if isinstance(exc.detail, dict) else {"message": str(exc.detail)}
    code = detail.get("code")
    print(f"{detail.get('message')}" + (f" ({code})" if code else ""), file=sys.stderr)
    return 1


def _media_run(settings: Settings, run: Callable[[], Awaitable[Any]]) -> Any:
    """Een functie van media; daarna de database dicht (zoals de andere commando's)."""

    async def wrapped() -> Any:
        try:
            return await run()
        finally:
            await get_engine(settings).dispose()

    return asyncio.run(wrapped())


def _media(settings: Settings, args: argparse.Namespace) -> int:
    from app.media import importer, loadtest, names, providers, status

    try:
        if args.command == "media-parse":
            for path in args.paths:
                print(json.dumps({"pad": path, **names.parse(path)}, ensure_ascii=False))
            return 0
        if args.command == "media-import":
            job = _media_run(
                settings,
                lambda: importer.start_cli(
                    settings, args.source, args.naar, args.eigenaar, move=args.verplaats
                ),
            )
            print(f"De import staat klaar (taak {job}). Volg hem op /beheer → Media.")
            return 0
        if args.command == "media-status":
            found = _media_run(
                settings,
                lambda: status.collect(settings, sessions=args.sessions, wait=args.wait),
            )
            for line in found.get("lines", []):
                print(line)
            # M10 (M0-hotfix): status zegt zelf de exitcode (0 met --wait, ook bij een probleem)
            return int(found.get("exit", 0 if found.get("ok", False) else 1))
        if args.command == "media-provider-check":
            found = _media_run(settings, lambda: providers.check(settings, record=args.opnemen))
            for name, state in found.items():
                print(f"{name}: {state}")
            return 1 if any(s in ("auth", "unavailable") for s in found.values()) else 0
        # media-loadtest
        result = _media_run(
            settings,
            lambda: loadtest.run_cli(
                settings,
                streams=args.sessies,
                seconds=args.duur,
                codec=args.codec,
                external=args.extern,
                sources=args.film,
            ),
        )
        text = json.dumps(result, ensure_ascii=False, indent=2, default=str)
        if args.json:
            Path(args.json).write_text(text + "\n")
            print(f"De uitkomst staat in {args.json}.")
        else:
            print(text)
        return 0 if result.get("passed", True) else 1
    except HTTPException as exc:
        return _media_failed(exc)


MEDIA_COMMANDS = (
    "media-import",
    "media-parse",
    "media-status",
    "media-provider-check",
    "media-loadtest",
)


def _media_parsers(commands) -> None:
    imp = commands.add_parser("media-import", help="een map uit /data/import importeren")
    imp.add_argument("source", metavar="bron", help="bijvoorbeeld /data/import/Films")
    imp.add_argument("--naar", required=True, help="de doelmap, bijvoorbeeld /Films")
    imp.add_argument("--eigenaar", required=True, help="e-mailadres van de eigenaar")
    imp.add_argument("--verplaats", action="store_true", help="verplaatsen in plaats van kopiëren")
    parse = commands.add_parser("media-parse", help="laten zien wat de parser in een naam ziet")
    parse.add_argument("paths", metavar="pad", nargs="+")
    stat = commands.add_parser("media-status", help="de staat van media")
    stat.add_argument(
        "--wait", type=float, default=0.0, help="hooguit zoveel seconden op de worker wachten"
    )
    stat.add_argument("--sessions", action="store_true", help="ook wie er nu kijkt")
    check = commands.add_parser(
        "media-provider-check", help="de providers van metadata nakijken (TMDB, MusicBrainz)"
    )
    check.add_argument(
        "--opnemen", action="store_true", help="de vorm van de antwoorden bewaren (§4.4)"
    )
    load = commands.add_parser("media-loadtest", help="de belastingtest van media (§4.6)")
    load.add_argument("--sessies", type=int, default=3)
    load.add_argument("--duur", type=int, default=300, help="seconden")
    load.add_argument("--codec", choices=("h264", "vp9"), default="h264")
    load.add_argument("--json", metavar="pad", help="de uitkomst naar dit bestand")
    load.add_argument("--extern", metavar="adres", help="spelers buiten de app, tegen dit adres")
    load.add_argument(
        "--film",
        metavar="id",
        action="append",
        type=uuid.UUID,
        help="een eigen film in plaats van de bronnen van de test (het id uit /kijken/<id>); "
        "hooguit één per sessie, niet met --extern",
    )


def main(argv: list[str] | None = None) -> int:
    # Geen __doc__: dat is None onder python -OO
    parser = argparse.ArgumentParser(
        prog="python -m app.cli", description="Beheer vanaf de commandoregel"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    reset = commands.add_parser(
        "reset-2fa", help="2FA uitzetten voor een account waarvan de telefoon kwijt is"
    )
    reset.add_argument("email", help="e-mailadres van het account")
    promote = commands.add_parser(
        "make-admin", help="een account beheerder maken (en een blokkade opheffen)"
    )
    promote.add_argument("email", help="e-mailadres van het account")
    down = commands.add_parser(
        "db-downgrade", help="het schema van de database terugzetten naar een eerdere revisie"
    )
    down.add_argument("revision", metavar="revisie", help="bijvoorbeeld 0006")
    down.add_argument("--ja", action="store_true", help="niet om bevestiging vragen")
    checkpoint = commands.add_parser(
        "office-checkpoint", help="alle open documenten in office tussentijds opslaan"
    )
    checkpoint.add_argument(
        "--wait", type=float, default=30.0, help="hooguit zoveel seconden wachten (standaard 30)"
    )
    commands.add_parser(
        "previews-recheck", help="alles nalopen: previews die ontbreken of mislukten opnieuw"
    )
    commands.add_parser(
        "db-backup", help="een reservekopie van de database maken, terwijl de app draait"
    )
    search_bench.parsers(commands)
    benchlos.parsers(commands)
    geo_load.parsers(commands)
    for name, (package, what) in SEARCH_STUBS.items():
        commands.add_parser(name, help=f"{what} (nog niet gebouwd, {package})")
    _media_parsers(commands)
    args = parser.parse_args(argv)
    settings = get_settings()
    if args.command in MEDIA_COMMANDS:
        return _media(settings, args)
    if args.command in search_bench.COMMANDS:
        return search_bench.run(settings, args)
    if args.command == benchlos.COMMAND:
        return benchlos.run(settings, args)
    if args.command in geo_load.COMMANDS:
        return geo_load.run(settings, args)
    if args.command in SEARCH_STUBS:
        return _stub(args.command)
    if args.command == "db-backup":
        return _backup(settings)
    if args.command == "db-downgrade":
        return _downgrade(settings, args.revision, args.ja)
    if args.command == "office-checkpoint":
        return _checkpoint(settings, args.wait)
    if args.command == "previews-recheck":
        return _recheck(settings)
    action = make_admin if args.command == "make-admin" else reset_2fa

    async def run() -> bool:
        try:
            return await action(settings, args.email)
        finally:
            await get_engine(settings).dispose()

    try:
        found = asyncio.run(run())
    except BeingDeleted:
        print(
            f"Het account {args.email} wordt al verwijderd en kan geen beheerder meer worden. "
            "Kies een ander account.",
            file=sys.stderr,
        )
        return 1
    if not found:
        print(f"Er is geen account met e-mailadres {args.email}.", file=sys.stderr)
        return 1
    if args.command == "make-admin":
        print(f"{args.email} is nu beheerder (en niet geblokkeerd).")
        return 0
    print(
        f"2FA staat uit voor {args.email} en alle logins van dit account zijn ingetrokken. "
        "Log in met het wachtwoord en zet 2FA opnieuw aan."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
