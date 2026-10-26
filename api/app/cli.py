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
"""

import argparse
import asyncio
import sys

from alembic import command
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from alembic.util import CommandError
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
    args = parser.parse_args(argv)
    settings = get_settings()
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
