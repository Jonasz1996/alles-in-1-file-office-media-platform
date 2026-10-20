"""Beheer vanaf de commandoregel, voor als het via de web-app niet meer gaat.

    docker exec <container> python -m app.cli reset-2fa <e-mailadres>

reset-2fa is er voor de laatste beheerder die zijn telefoon én de herstelcodes kwijt is:
het zet 2FA uit, heft een blokkade na foute codes op en trekt alle logins van dat account
in. Daarna log je in met alleen het wachtwoord en zet je 2FA opnieuw aan.
"""

import argparse
import asyncio
import sys

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import Settings, get_settings
from app.db import get_engine
from app.models import User
from app.routers.auth import reset_totp


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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli", description=__doc__.split("\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    reset = commands.add_parser(
        "reset-2fa", help="2FA uitzetten voor een account waarvan de telefoon kwijt is"
    )
    reset.add_argument("email", help="e-mailadres van het account")
    args = parser.parse_args(argv)
    settings = get_settings()

    async def run() -> bool:
        try:
            return await reset_2fa(settings, args.email)
        finally:
            await get_engine(settings).dispose()

    if not asyncio.run(run()):
        print(f"Er is geen account met e-mailadres {args.email}.", file=sys.stderr)
        return 1
    print(
        f"2FA staat uit voor {args.email} en alle logins van dit account zijn ingetrokken. "
        "Log in met het wachtwoord en zet 2FA opnieuw aan."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
