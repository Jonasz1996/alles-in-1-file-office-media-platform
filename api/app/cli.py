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
"""

import argparse
import asyncio
import sys

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import Settings, get_settings
from app.db import get_engine
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
    args = parser.parse_args(argv)
    settings = get_settings()
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
