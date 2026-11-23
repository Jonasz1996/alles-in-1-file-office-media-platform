"""De client-apps op het platform zelf (fase 11, stap 9-11).

GitHub bouwt de apps (workflows android.yml en desktop.yml) en zet ze in de release `clients`
van het repo. Het platform haalt ze daar zelf op en bewaart ze in data_dir/apps, zodat de
downloadpagina /apps ze van de eigen server geeft (ook zonder internet bij wie downloadt).

Bij de start en daarna elke 6 uur; een beheerder kan ook meteen laten ophalen
(POST /api/v1/apps/refresh). Een bestand wordt alleen opnieuw gehaald als het op GitHub nieuwer
is (updated_at van de asset, bewaard in apps/manifest.json). Alleen in productie: tests en de
ontwikkelomgeving gaan niet naar het internet.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path

import httpx

from app.config import Settings

log = logging.getLogger(__name__)

TAG = "clients"
ROUND_S = 6 * 3600
NAMES = (
    "platform-android.apk",
    "platform-windows.exe",
    "platform-linux.deb",
    "platform-linux-arm64.deb",
    "platform-macos.dmg",
)
MAX_BYTES = 500 * 1024 * 1024


def apps_dir(settings: Settings) -> Path:
    return settings.data_dir / "apps"


def _manifest(folder: Path) -> dict[str, str]:
    try:
        data = json.loads((folder / "manifest.json").read_text("utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


async def fetch(settings: Settings) -> list[str]:
    """Eén ronde; geeft de namen die nieuw binnenkwamen."""
    repo = settings.clients_repo
    if not repo:
        return []
    folder = apps_dir(settings)
    folder.mkdir(parents=True, exist_ok=True)
    seen = _manifest(folder)
    got: list[str] = []
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(30, read=300), follow_redirects=True
    ) as http:
        res = await http.get(
            f"https://api.github.com/repos/{repo}/releases/tags/{TAG}",
            headers={"Accept": "application/vnd.github+json"},
        )
        if res.status_code == 404:
            return []
        res.raise_for_status()
        for asset in res.json().get("assets", []):
            name = asset.get("name")
            if name not in NAMES or int(asset.get("size") or 0) > MAX_BYTES:
                continue
            stamp = str(asset.get("updated_at") or "")
            target = folder / name
            if seen.get(name) == stamp and target.is_file():
                continue
            tmp = folder / f".{name}.part"
            async with http.stream(
                "GET", asset["url"], headers={"Accept": "application/octet-stream"}
            ) as dl:
                dl.raise_for_status()
                total = 0
                with tmp.open("wb") as out:
                    async for chunk in dl.aiter_bytes():
                        total += len(chunk)
                        if total > MAX_BYTES:
                            raise ValueError(f"{name} is te groot")
                        out.write(chunk)
            os.replace(tmp, target)
            seen[name] = stamp
            got.append(name)
    (folder / "manifest.json").write_text(json.dumps(seen), "utf-8")
    if got:
        log.info("client-apps opgehaald: %s", ", ".join(got))
    return got


async def loop(settings: Settings) -> None:
    while True:
        try:
            await fetch(settings)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - de volgende ronde opnieuw
            log.warning("client-apps ophalen mislukte: %s", exc)
        await asyncio.sleep(ROUND_S)
