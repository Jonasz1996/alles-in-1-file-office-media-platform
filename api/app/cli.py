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
    docker exec <container> python -m app.cli office-checkpoint [--wait 30]

db-downgrade zet het schema van de database terug naar een eerdere revisie (Alembic
downgrade), bijvoorbeeld 0006 vóór het terugzetten van een image van fase 3 (M19): een oudere
app start niet op een nieuwer schema. Het vraagt eerst om bevestiging (typ "ja"), tenzij --ja.
Wat alleen in de nieuwere kolommen en tabellen stond, is daarna weg. Stop de app eerst.

office-checkpoint vraagt de document server om alle open documenten tussentijds op te slaan
en wacht daar hooguit --wait seconden op (vóór het stoppen of bijwerken van office). Het
zegt hoeveel documenten zijn opgeslagen.

Media (fase 5), elk een dunne aanroep van een functie in app/media (de pakketten bouwen ze; tot
dan zegt een stub dat media uit staat):

    python -m app.cli media-import /data/import/Films --naar /Films --eigenaar <e-mail>
                                    [--verplaats]
    python -m app.cli media-parse <pad>…
    python -m app.cli media-status [--wait 20] [--sessions]
    python -m app.cli media-provider-check [--opnemen]
    python -m app.cli media-loadtest [--sessies 3] [--duur 300] [--codec h264] [--json uit.json]
                                     [--extern <adres>]

Een fout van media (zoals 503 media_off) geeft de melding en exitcode 1.
"""

import argparse
import asyncio
import json
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from alembic import command
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from alembic.util import CommandError
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import Settings, get_settings
from app.db import get_engine
from app.migrate import alembic_config
from app.models import User
from app.routers.auth import being_deleted, reset_totp


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


async def db_downgrade(settings: Settings, revision: str) -> None:
    """Alembic downgrade naar revision, in één transactie."""
    cfg = alembic_config(settings.db_url)

    def run(connection) -> None:
        cfg.attributes["connection"] = connection
        cfg.attributes["timezone"] = settings.timezone
        command.downgrade(cfg, revision)

    async with get_engine(settings).begin() as conn:
        await conn.run_sync(run)


async def office_checkpoint(settings: Settings, wait: float) -> int:
    """Tussentijds opslaan van alle open documenten (sweep.checkpoint_all); het aantal."""
    from app.office import runtime, sweep

    runtime.bind(None, settings)
    try:
        return await sweep.checkpoint_all(wait)
    finally:
        runtime.unbind()


def _confirm(current: str | None, revision: str) -> bool:
    print(
        f"De database gaat van revisie {current} terug naar {revision}. Wat alleen in de "
        "nieuwere kolommen en tabellen staat, is daarna weg. Is de app gestopt?"
    )
    try:
        answer = input('Typ "ja" om door te gaan: ')
    except EOFError:
        return False
    return answer.strip().lower() == "ja"


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
    if not yes and not _confirm(now, revision):
        print("Niets gedaan.", file=sys.stderr)
        return 1

    async def run() -> None:
        try:
            await db_downgrade(settings, revision)
        finally:
            await get_engine(settings).dispose()

    asyncio.run(run())
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
            return 0 if found.get("ok", False) else 1
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
    _media_parsers(commands)
    args = parser.parse_args(argv)
    settings = get_settings()
    if args.command in MEDIA_COMMANDS:
        return _media(settings, args)
    if args.command == "db-downgrade":
        return _downgrade(settings, args.revision, args.ja)
    if args.command == "office-checkpoint":
        return _checkpoint(settings, args.wait)
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
