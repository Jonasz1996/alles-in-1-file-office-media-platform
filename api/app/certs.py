"""Certificaten voor https: aanvragen bij Let's Encrypt, bewaren in data/tls,
op tijd vernieuwen en zonder herstart laden in de https-listener.

Het domein, e-mailadres en de testmodus komen uit /beheer en staan in de tabel
setting. Een vernieuwd certificaat wordt in dezelfde SSLContext geladen; nieuwe
verbindingen krijgen het meteen, lopende verbindingen merken er niets van.
"""

import asyncio
import json
import logging
import os
import ssl
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
from cryptography import x509
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.acme import (
    LETS_ENCRYPT,
    LETS_ENCRYPT_STAGING,
    AcmeClient,
    AcmeError,
    key_from_pem,
    key_to_pem,
    new_key,
)
from app.config import Settings
from app.db import get_engine
from app.models import Setting

log = logging.getLogger("platform.certs")

SETTING_KEY = "https"
CHECK_INTERVAL = 6 * 3600  # ook zonder aanleiding elke 6 uur kijken
RETRY_START = 600  # na een fout eerst 10 minuten wachten, daarna steeds dubbel
RETRY_MAX = 6 * 3600

# Uitleg bij de fouten die je het vaakst ziet als het domein of de router nog niet klopt
HINTS = {
    "dns": "Het domein wijst (nog) niet naar je publieke IP-adres. Controleer de DNS-instelling.",
    "connection": "Let's Encrypt kan poort 80 niet bereiken. Stuur poort 80 in je router door "
    "naar deze container.",
    "unauthorized": "Let's Encrypt kwam bij een andere server uit dan deze. Controleer de DNS "
    "en het doorsturen van poort 80.",
    "rateLimited": "Te veel aanvragen; Let's Encrypt laat je even wachten. Het platform probeert "
    "het later vanzelf opnieuw.",
    "rejectedIdentifier": "Let's Encrypt geeft voor dit domein geen certificaat.",
}


class HttpsConfig(BaseModel):
    domain: str = ""
    email: str = ""
    staging: bool = False


async def load_config(session: AsyncSession) -> HttpsConfig:
    row = await session.get(Setting, SETTING_KEY)
    return HttpsConfig.model_validate(row.value) if row else HttpsConfig()


async def save_config(session: AsyncSession, config: HttpsConfig) -> None:
    row = await session.get(Setting, SETTING_KEY)
    if row is None:
        session.add(Setting(key=SETTING_KEY, value=config.model_dump()))
    else:
        row.value = config.model_dump()
    await session.commit()


def _write(path: Path, data: bytes, mode: int = 0o644) -> None:
    """Eerst naar een tijdelijk bestand, dan atomair op zijn plek."""
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def _hint(error: Exception) -> str:
    if isinstance(error, AcmeError):
        kinds = [error.type.rsplit(":", 1)[-1]]
        kinds += [
            s.get("type", "").rsplit(":", 1)[-1] for s in error.problem.get("subproblems", [])
        ]
        for kind in kinds:
            if kind in HINTS:
                return f"{HINTS[kind]} ({error})"
        return str(error)
    if isinstance(error, httpx.HTTPError):
        return f"Let's Encrypt is niet bereikbaar: {error}"
    return f"Onverwachte fout: {error}"


class CertManager:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.dir = settings.tls_dir
        self.config = HttpsConfig()
        # token -> key authorization, beantwoord op /.well-known/acme-challenge/<token>
        self.challenges: dict[str, str] = {}
        self.context: ssl.SSLContext | None = None
        self.cert_domain: str | None = None
        self.cert_directory: str | None = None
        self.not_before: datetime | None = None
        self.not_after: datetime | None = None
        self.state = "uit"  # uit, bezig, actief, fout
        self.error: str | None = None
        self.last_attempt: datetime | None = None
        self.next_check: datetime | None = None
        # Zet serve.py zodra de https-listener draait; pas dan sturen we http door.
        self.serving = False
        self.has_cert = asyncio.Event()
        self._wake = asyncio.Event()
        self._failures = 0
        self._task: asyncio.Task | None = None

    @property
    def directory(self) -> str:
        if self.settings.acme_directory:
            return self.settings.acme_directory
        return LETS_ENCRYPT_STAGING if self.config.staging else LETS_ENCRYPT

    @property
    def active(self) -> bool:
        """Er is een geldig certificaat voor het ingestelde domein, van de juiste CA."""
        return bool(
            self.config.domain
            and self.context is not None
            and self.cert_domain == self.config.domain
            and self.cert_directory == self.directory
            and self.not_after is not None
            and self.not_after > datetime.now(UTC)
        )

    @property
    def renew_at(self) -> datetime | None:
        """Vernieuwen als er nog een derde van de looptijd over is (bij 90 dagen: 30 dagen
        voor het einde). Zo werkt het ook als Let's Encrypt kortere certificaten uitgeeft."""
        if self.not_before is None or self.not_after is None:
            return None
        return self.not_after - (self.not_after - self.not_before) / 3

    def needs_certificate(self, now: datetime | None = None) -> bool:
        if not self.config.domain:
            return False
        if not self.active:
            return True
        renew_at = self.renew_at
        return renew_at is not None and (now or datetime.now(UTC)) >= renew_at

    def load_files(self) -> bool:
        """Laadt cert.pem en key.pem als die er zijn. Een bestaande SSLContext wordt
        bijgewerkt in plaats van vervangen, zodat de listener niet opnieuw hoeft te starten."""
        cert_path, key_path = self.dir / "cert.pem", self.dir / "key.pem"
        if not (cert_path.exists() and key_path.exists()):
            return False
        cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
        try:
            names = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName)
            domains = names.value.get_values_for_type(x509.DNSName)
        except x509.ExtensionNotFound:
            domains = []
        meta_path = self.dir / "cert.json"
        meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
        # Eerst in een losse context: past de sleutel niet bij het certificaat, dan blijft
        # de draaiende listener het oude gebruiken.
        self._new_context().load_cert_chain(cert_path, key_path)
        context = self.context or self._new_context()
        context.load_cert_chain(cert_path, key_path)
        self.context = context
        self.cert_domain = domains[0] if domains else None
        self.cert_directory = meta.get("directory")
        self.not_before = cert.not_valid_before_utc
        self.not_after = cert.not_valid_after_utc
        self.has_cert.set()
        return True

    @staticmethod
    def _new_context() -> ssl.SSLContext:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        return context

    def server_context(self) -> ssl.SSLContext:
        if self.context is None:
            raise RuntimeError("Nog geen certificaat")
        return self.context

    async def start(self) -> None:
        maker = async_sessionmaker(get_engine(self.settings), expire_on_commit=False)
        async with maker() as session:
            self.config = await load_config(session)
        try:
            self.load_files()
        except (OSError, ValueError, ssl.SSLError) as e:
            log.warning("Bestaand certificaat niet geladen: %s", e)
        self._task = asyncio.create_task(self._run(), name="certificaten")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    def configure(self, config: HttpsConfig) -> None:
        self.config = config
        self._failures = 0
        self.error = None
        self.wake()

    def wake(self) -> None:
        self._wake.set()

    async def _run(self) -> None:
        while True:
            delay = await self.check()
            self.next_check = datetime.now(UTC) + timedelta(seconds=delay)
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=delay)
            except TimeoutError:
                pass
            self._wake.clear()

    async def check(self) -> float:
        """Vraagt zo nodig een certificaat aan; geeft terug hoeveel seconden tot de
        volgende controle."""
        if not self.config.domain:
            self.state = "uit"
            return CHECK_INTERVAL
        if not self.needs_certificate():
            self.state = "actief"
            until_renewal = (self.renew_at - datetime.now(UTC)).total_seconds()
            return max(60.0, min(CHECK_INTERVAL, until_renewal))
        self.state = "bezig"
        self.last_attempt = datetime.now(UTC)
        try:
            await self.obtain()
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 - elke fout wordt getoond op /beheer
            self._failures += 1
            self.state = "fout"
            self.error = _hint(e)
            log.warning("Certificaat voor %s niet gekregen: %s", self.config.domain, e)
            return min(RETRY_MAX, RETRY_START * 2 ** (self._failures - 1))
        self._failures = 0
        self.error = None
        self.state = "actief"
        log.info("Certificaat voor %s binnen, geldig tot %s", self.cert_domain, self.not_after)
        return CHECK_INTERVAL

    def _account_key(self):
        path = self.dir / "account.key"
        if path.exists():
            return key_from_pem(path.read_bytes())
        key = new_key()
        _write(path, key_to_pem(key), 0o600)
        return key

    async def obtain(self) -> None:
        domain, email, directory = self.config.domain, self.config.email, self.directory
        self.dir.mkdir(parents=True, exist_ok=True)
        verify: ssl.SSLContext | bool = True
        if self.settings.acme_ca_file:
            verify = ssl.create_default_context(cafile=self.settings.acme_ca_file)
        async with httpx.AsyncClient(timeout=30, verify=verify) as http:
            client = AcmeClient(directory, self._account_key(), http)
            await client.register(email or None)
            cert = await client.issue(domain, self.challenges)
        _write(self.dir / "key.pem", cert.key_pem, 0o600)
        _write(self.dir / "cert.pem", cert.chain_pem)
        _write(self.dir / "cert.json", json.dumps({"directory": directory}).encode())
        self.load_files()

    def status(self) -> dict:
        return {
            "domain": self.config.domain,
            "email": self.config.email,
            "staging": self.config.staging,
            "state": self.state,
            "error": self.error,
            "active": self.active,
            "serving": self.serving,
            "cert_domain": self.cert_domain,
            "expires_at": self.not_after,
            "renew_at": self.renew_at,
            "last_attempt": self.last_attempt,
            "next_check": self.next_check,
        }
