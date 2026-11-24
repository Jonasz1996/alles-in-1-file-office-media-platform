# Noodreset zonder update: 2FA uit + tijdelijk wachtwoord voor één account.
# Gebruik (op de node waar het platform draait, in de Proxmox-console):
#   docker exec -i -e EMAIL=jij@voorbeeld.be $(docker ps -qf label=com.docker.compose.service=platform) python - < noodreset.py
# Zonder EMAIL leest het alleen: per account de rol, 2FA aan/uit, en hoeveel bestanden en bytes het
# heeft (ook in de prullenbak), plus het totaal aan opgeslagen inhoud. Draai het voor en na de reset:
# de cijfers moeten gelijk blijven. Met EMAIL wijzigt het alleen de aanmelding van dat ene account
# (wachtwoord, 2FA, herstelcodes, logins); bestanden, mappen, versies en de opslag raakt het niet.
# Alleen voor een versie zonder `app.cli reset-account` (tot en met 0.8.2 in het cluster). Vanaf die
# versie vraagt de gebruiker op de inlogpagina ("Wachtwoord vergeten of kan niet inloggen?") een
# code, en plakt de beheerder die in `python -m app.cli reset-account <code>`: zie
# infra/single/README.md, "Kan niet inloggen".
import asyncio
import os
import secrets

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import get_settings
from app.db import get_engine
from app.models import Blob, Node, User
from app.routers.auth import reset_totp
from app.security import new_password_hash


async def main() -> None:
    engine = get_engine(get_settings())
    maker = async_sessionmaker(engine, expire_on_commit=False)
    email = os.environ.get("EMAIL", "").strip().lower()
    try:
        async with maker() as session:
            if not email:
                files = (
                    select(
                        Node.owner_id,
                        func.count().label("n"),
                        func.coalesce(func.sum(Node.size), 0).label("b"),
                        func.count(Node.deleted_at).label("weg"),
                    )
                    .where(Node.kind == "file")
                    .group_by(Node.owner_id)
                    .subquery()
                )
                rows = await session.execute(
                    select(
                        User.email, User.role, User.totp_secret, files.c.n, files.c.b, files.c.weg
                    )
                    .outerjoin(files, files.c.owner_id == User.id)
                    .order_by(User.email)
                )
                for mail, role, secret, n, b, weg in rows:
                    print(
                        f"{mail}  ({role}, 2FA {'aan' if secret else 'uit'}): "
                        f"{n or 0} bestanden, {b or 0} bytes, waarvan {weg or 0} in de prullenbak"
                    )
                count, size = (
                    await session.execute(
                        select(func.count(), func.coalesce(func.sum(Blob.size), 0)).select_from(
                            Blob
                        )
                    )
                ).one()
                print(f"Opgeslagen inhoud (ontdubbeld): {count} blobs, {size} bytes")
                return
            user = await session.scalar(select(User).where(func.lower(User.email) == email))
            if user is None:
                print(f"Geen account met e-mailadres {email}. Draai zonder EMAIL voor de lijst.")
                return
            password = secrets.token_urlsafe(12)
            password_hash = await new_password_hash(password)
            await session.execute(
                update(User).where(User.id == user.id).values(password_hash=password_hash)
            )
            # 2FA uit, herstelcodes weg, blokkade weg, alle logins ingetrokken; commit
            await reset_totp(session, user, keep=None)
            print(f"Klaar voor {user.email}.")
            print(f"Tijdelijk wachtwoord: {password}")
            print("Log in, wijzig meteen je wachtwoord (Account) en zet 2FA opnieuw aan.")
    finally:
        await engine.dispose()


asyncio.run(main())
