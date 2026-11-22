"""De cli van mediabeheer (fase 11).

    python -m app.cli arr-koppel [--qbit-url http://qbittorrent:8080] [--prowlarr-url http://prowlarr:9696]

Voor de start van de containers qbittorrent en prowlarr (install.sh met ARR=aan). Maakt de mappen
`<data>/arr/qbittorrent`, `<data>/arr/prowlarr` en `<data>/downloads`, en:

- qBittorrent: is er in de app nog geen wachtwoord, dan een nieuw (willekeurig) wachtwoord, als
  hash in `qBittorrent.conf` en versleuteld in de app. Is er al een, dan blijft het zoals het is.
- Prowlarr: de API-sleutel uit `config.xml`; staat daar geen, dan een nieuwe. Dezelfde sleutel
  komt versleuteld in de app.

Draait meer dan eens zonder iets te breken. Drukt nooit een wachtwoord of sleutel af.
"""

from __future__ import annotations

import argparse
import asyncio
import secrets
from pathlib import Path

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.arr import linkconf
from app.arr import settings as arr_settings
from app.config import Settings
from app.db import get_engine

COMMANDS = {"arr-koppel"}
USERNAME = "platform"


def parsers(commands: argparse._SubParsersAction) -> None:
    koppel = commands.add_parser(
        "arr-koppel", help="qBittorrent en Prowlarr vooraf instellen en aan de app koppelen"
    )
    koppel.add_argument("--qbit-url", default="http://qbittorrent:8080")
    koppel.add_argument("--prowlarr-url", default="http://prowlarr:9696")
    koppel.add_argument("--downloads", default="/downloads", help="de map in qBittorrent")


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".nieuw")
    tmp.write_text(text)
    tmp.replace(path)


async def koppel(settings: Settings, qbit_url: str, prowlarr_url: str, downloads: str) -> list[str]:
    data = Path(settings.data_dir)
    qbit_dir = data / "arr" / "qbittorrent"
    prowlarr_dir = data / "arr" / "prowlarr"
    for folder in (qbit_dir, prowlarr_dir, data / "downloads"):
        folder.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    maker = async_sessionmaker(get_engine(settings), expire_on_commit=False)
    async with maker() as session:
        conf_file = qbit_dir / "qBittorrent" / "qBittorrent.conf"
        old = await arr_settings.load_qbit(session, settings)
        text = conf_file.read_text() if conf_file.exists() else ""
        if old is not None and "WebUI\\Password_PBKDF2" in text:
            lines.append("qBittorrent: al gekoppeld, niets veranderd")
        else:
            password = old.password if old is not None else secrets.token_urlsafe(24)
            _write(
                conf_file,
                linkconf.qbit_conf(text, username=USERNAME, password=password, save_path=downloads),
            )
            await arr_settings.store_qbit(
                session,
                settings,
                arr_settings.QBitConf(
                    url=qbit_url, username=USERNAME, password=password, save_path=downloads
                ),
            )
            lines.append(f"qBittorrent: gekoppeld als gebruiker {USERNAME}")

        xml_file = prowlarr_dir / "config.xml"
        xml = xml_file.read_text() if xml_file.exists() else ""
        key = linkconf.read_prowlarr_key(xml)
        if key is None:
            key = secrets.token_hex(16)
            _write(xml_file, linkconf.prowlarr_conf(xml, key))
            lines.append("Prowlarr: nieuwe API-sleutel gezet")
        stored = await arr_settings.load_prowlarr(session, settings)
        if stored is None or stored.key != key or stored.url != prowlarr_url.rstrip("/"):
            await arr_settings.store_prowlarr(
                session, settings, arr_settings.ProwlarrConf(prowlarr_url, key)
            )
            lines.append("Prowlarr: gekoppeld")
        else:
            lines.append("Prowlarr: al gekoppeld, niets veranderd")
    await get_engine(settings).dispose()
    return lines


def run(settings: Settings, args: argparse.Namespace) -> int:
    for line in asyncio.run(koppel(settings, args.qbit_url, args.prowlarr_url, args.downloads)):
        print(line)
    return 0
