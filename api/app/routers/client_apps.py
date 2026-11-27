"""De downloadpagina voor de client-apps (fase 11, stap 9).

GET /api/v1/apps                → [ClientApp] (welke apps er zijn, en of ze al te downloaden zijn)
GET /api/v1/apps/webos.ipk      → het .ipk voor de LG webOS-tv, met het adres van dit platform
GET /api/v1/apps/file/{name}    → een gebouwde app uit data_dir/apps (bijv. platform-android.apk)

De webOS-app is klein (apps/webos): ze opent de tv-stand van het platform. Het .ipk maakt de app
hier zelf (ar met debian-binary, control.tar.gz en data.tar.gz, zoals ares-package), met in
config.js het adres waarop de pagina gevraagd werd. De andere apps (Android, Windows, Linux)
bouwt GitHub; het platform haalt ze zelf op naar data_dir/apps (app.client_fetch). Zolang een
app er niet is, staat ze als "later".
Alleen voor wie is ingelogd.
"""

from __future__ import annotations

import io
import json
import tarfile
import time
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

from app.config import Settings, get_settings
from app.errors import fail
from app.security import AdminUser, CurrentUser

router = APIRouter(prefix="/api/v1/apps", tags=["apps"])

WEBOS_FILES = ("appinfo.json", "index.html", "icon.png", "largeIcon.png")


class ClientApp(BaseModel):
    id: str
    platform: Literal["webos", "android", "windows", "linux", "linuxarm", "macos", "ios"]
    # web: geen bestand, de app is het platform zelf in de browser (iOS: Zet op beginscherm)
    state: Literal["ready", "later", "web"]
    version: str | None = None
    file: str | None = None  # het adres om te downloaden
    size: int | None = None


# De apps die een beheerder zelf in data_dir/apps legt: (id, platform, bestandsnaam)
BUILT = (
    ("android", "android", "platform-android.apk"),
    ("windows", "windows", "platform-windows.exe"),
    ("linux", "linux", "platform-linux.deb"),
    ("linuxarm", "linuxarm", "platform-linux-arm64.deb"),
    ("macos", "macos", "platform-macos.dmg"),
)


def _webos_dir(settings: Settings) -> Path | None:
    for base in (settings.clients_dir, Path(__file__).resolve().parents[3] / "apps"):
        folder = base / "webos"
        if all((folder / name).is_file() for name in WEBOS_FILES):
            return folder
    return None


def _built_dir(settings: Settings) -> Path:
    return settings.data_dir / "apps"


def _ar(members: list[tuple[str, bytes]]) -> bytes:
    out = io.BytesIO()
    out.write(b"!<arch>\n")
    stamp = int(time.time())
    for name, data in members:
        header = f"{name:<16}{stamp:<12}{0:<6}{0:<6}{'100644':<8}{len(data):<10}`\n".encode("ascii")
        out.write(header)
        out.write(data)
        if len(data) % 2:
            out.write(b"\n")
    return out.getvalue()


def _tgz(files: list[tuple[str, bytes, int]]) -> bytes:
    """Een tar.gz met deze bestanden (pad, inhoud, modus) en de mappen ervoor."""
    buf = io.BytesIO()
    stamp = int(time.time())
    with tarfile.open(fileobj=buf, mode="w:gz", format=tarfile.GNU_FORMAT) as tar:
        dirs: set[str] = set()
        for path, _, _ in files:
            parts = path.split("/")[:-1]
            for i in range(1, len(parts) + 1):
                d = "/".join(parts[:i])
                if d and d != "." and d not in dirs:
                    dirs.add(d)
                    info = tarfile.TarInfo(d)
                    info.type = tarfile.DIRTYPE
                    info.mode = 0o755
                    info.mtime = stamp
                    tar.addfile(info)
        for path, data, mode in files:
            info = tarfile.TarInfo(path)
            info.size = len(data)
            info.mode = mode
            info.mtime = stamp
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def webos_ipk(folder: Path, url: str) -> tuple[str, str, bytes]:
    """(app-id, versie, het .ipk) met het adres `url` in config.js."""
    info = json.loads((folder / "appinfo.json").read_text("utf-8"))
    app_id, version = str(info["id"]), str(info["version"])
    base = f"./usr/palm/applications/{app_id}"
    config = (
        "// Gemaakt door de downloadpagina van het platform\n"
        f"window.PLATFORM_URL = {json.dumps(url)};\n"
    ).encode()
    files = [(f"{base}/{name}", (folder / name).read_bytes(), 0o644) for name in WEBOS_FILES]
    files.append((f"{base}/config.js", config, 0o644))
    package = json.dumps({"app": app_id, "id": app_id, "version": version, "loc_name": "Platform"})
    files.append((f"./usr/palm/packages/{app_id}/packageinfo.json", package.encode(), 0o644))
    control = (
        f"Package: {app_id}\nVersion: {version}\nSection: misc\nPriority: optional\n"
        f"Architecture: all\nInstalled-Size: {sum(len(d) for _, d, _ in files) // 1024 + 1}\n"
        "Maintainer: Platform\nDescription: Platform voor de tv\nwebOS-Package-Format-Version: 2\n"
        "webOS-Packager-Version: x.y.x\n"
    ).encode()
    ipk = _ar(
        [
            ("debian-binary", b"2.0\n"),
            ("control.tar.gz", _tgz([("./control", control, 0o644)])),
            ("data.tar.gz", _tgz(files)),
        ]
    )
    return app_id, version, ipk


def _origin(request: Request) -> str:
    return str(request.base_url).rstrip("/")


@router.get("", response_model=list[ClientApp])
async def list_apps(user: CurrentUser, settings: Settings = Depends(get_settings)):  # noqa: B008
    out: list[ClientApp] = []
    folder = _webos_dir(settings)
    if folder is not None:
        info = json.loads((folder / "appinfo.json").read_text("utf-8"))
        out.append(
            ClientApp(
                id="webos",
                platform="webos",
                state="ready",
                version=str(info.get("version")),
                file="/api/v1/apps/webos.ipk",
            )
        )
    else:
        out.append(ClientApp(id="webos", platform="webos", state="later"))
    built = _built_dir(settings)
    for app_id, platform, name in BUILT:
        path = built / name
        if path.is_file():
            out.append(
                ClientApp(
                    id=app_id,
                    platform=platform,  # type: ignore[arg-type]
                    state="ready",
                    file=f"/api/v1/apps/file/{name}",
                    size=path.stat().st_size,
                )
            )
        else:
            out.append(ClientApp(id=app_id, platform=platform, state="later"))  # type: ignore[arg-type]
    # iPhone en iPad: een eigen app in de App Store kost elk jaar geld; het platform zelf is een
    # web-app (manifest.webmanifest) die je met Safari op het beginscherm zet
    out.append(ClientApp(id="ios", platform="ios", state="web"))
    return out


@router.get("/webos.ipk")
async def webos(user: CurrentUser, request: Request, settings: Settings = Depends(get_settings)):  # noqa: B008
    folder = _webos_dir(settings)
    if folder is None:
        raise fail(404, "not_found", "De webOS-app zit niet in deze installatie")
    app_id, version, ipk = webos_ipk(folder, _origin(request))
    name = f"{app_id}_{version}_all.ipk"
    return Response(
        ipk,
        media_type="application/vnd.webos.ipk",
        headers={
            "Content-Disposition": f'attachment; filename="{name}"',
            "Cache-Control": "no-store",
        },
    )


@router.post("/refresh", response_model=list[ClientApp])
async def refresh(admin: AdminUser, settings: Settings = Depends(get_settings)):  # noqa: B008
    """Meteen de nieuwste apps van GitHub halen (anders elke 6 uur vanzelf)."""
    from app import client_fetch

    try:
        await client_fetch.fetch(settings)
    except Exception as exc:  # noqa: BLE001
        raise fail(502, "clients_fetch", f"Ophalen bij GitHub mislukte: {exc}") from exc
    return await list_apps(admin, settings)


@router.get("/file/{name}")
async def built_file(name: str, user: CurrentUser, settings: Settings = Depends(get_settings)):  # noqa: B008
    allowed = {n for _, _, n in BUILT}
    path = _built_dir(settings) / name
    if name not in allowed or not path.is_file():
        raise fail(404, "not_found", "Niet gevonden")
    return FileResponse(path, filename=name, media_type="application/octet-stream")
