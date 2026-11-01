"""Plaatjes van providers (Keuze 44, M8): ophalen, controleren, omzetten naar WebP, bewaren en
tonen via een ondertekend adres.

- **Ophalen** gaat via http.py (de enige weg naar buiten): alleen `image/jpeg`, `image/png` of
  `image/webp`, hooguit 10 MB. Hier nog een keer gecontroleerd, op de eerste bytes (de
  Content-Type van de provider zegt niet alles).
- **Omzetten** in een eigen proces, zoals de previews (regel 5): `python -I`, een kale omgeving,
  een eigen procesgroep en een geheugengrens (RLIMIT_AS). Pillow opent alleen JPEG, PNG en WebP en
  weigert een decompressiebom. Het maakt WebP van 256, 512 en 1.280 px (nooit groter dan het
  origineel). Zo wordt elk plaatje opnieuw gecodeerd; vreemde inhoud overleeft dat niet.
- **Bewaard** als `<media_dir>/art/<sha>/w256.webp` (en w512, w1280), op de sha van het
  bronplaatje: twee films met dezelfde poster delen hem. Kandidaten in het koppelvenster komen
  in `<media_dir>/art-tmp/<sha>/`; die map gaat na 24 uur leeg.
- **Getoond** via `GET /api/v1/media/art/{sha}/{maat}.webp`. Een kandidaat krijgt een adres met
  `exp` (12 uur geldig). Art die bij een item, serie of album staat, krijgt een adres zonder
  `exp`, alleen met `sig`: M2 en M9 tonen het adres zoals het in `art` staat (`{"art": sha,
  "url": …}`, Reader.art_url), dus dat adres moet blijven werken (afwijking in m8.md). De
  handtekening is een HMAC met een eigen sleutel uit het app-geheim.
- **Opruimen** (eens per dag, `cleanup`): art waar geen item, serie of album meer naar verwijst,
  en de tijdelijke map van ouder dan 24 uur.

Bovenaan alleen de standaardbibliotheek: het renderproces importeert deze module (`child_main`).
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import hmac
import json
import os
import re
import resource
import shutil
import signal
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

SIZES = {"w256": 256, "w512": 512, "w1280": 1280}
SIZE_ORDER = ("w1280", "w512", "w256")  # groot eerst: de kleinere komen uit de grotere
DEFAULT_SIZE = {"poster": "w512", "cover": "w512", "backdrop": "w1280", "still": "w512"}
MAX_BYTES = 10 * 1024 * 1024
MAX_PIXELS = 50_000_000
MEMORY_MB = 768
QUALITY = 80
TYPES = ("image/jpeg", "image/png", "image/webp")
FORMATS = ["JPEG", "PNG", "WEBP"]
TMP_KEEP = 24 * 3600
URL_HOURS = 12
CLEAN_EVERY = 24 * 3600.0
CHILD_SECONDS = 60
OUTPUT_BYTES = 64 * 1024
SHA = re.compile(r"^[0-9a-f]{64}$")
CHILD = (
    "import sys; sys.path.insert(0, sys.argv[1]); "
    "from app.media.providers.art import child_main; child_main()"
)
CODE_ROOT = str(Path(__file__).resolve().parents[3])


class Refused(Exception):
    """Geen toegestaan plaatje (soort, grootte, of het renderproces kon het niet lezen)."""


def sniff(body: bytes) -> str | None:
    """Het soort op de eerste bytes: image/jpeg, image/png, image/webp of None."""
    if body[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if body[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if len(body) >= 12 and body[:4] == b"RIFF" and body[8:12] == b"WEBP":
        return "image/webp"
    return None


# --- waar het staat -----------------------------------------------------------------------------


def art_dir(settings) -> Path:
    return settings.media_dir / "art"


def tmp_dir(settings) -> Path:
    return settings.media_dir / "art-tmp"


def work_dir(settings) -> Path:
    return settings.media_dir / "art-work"


def find(settings, sha: str, size: str) -> Path | None:
    """Het bestand van deze maat: eerst de vaste art, dan de tijdelijke map. None als het er
    niet is (of sha of maat geen geldige vorm heeft)."""
    if not SHA.match(sha) or size not in SIZES:
        return None
    for base in (art_dir(settings), tmp_dir(settings)):
        path = base / sha / f"{size}.webp"
        if path.is_file():
            return path
    return None


# --- adressen -----------------------------------------------------------------------------------


def url_key(settings) -> bytes:
    """Een eigen sleutel voor de adressen van art, uit het app-geheim."""
    return hmac.new(settings.secret().encode(), b"platform-media-art-url", hashlib.sha256).digest()


def signature(key: bytes, sha: str, size: str, exp: int = 0) -> str:
    mac = hmac.new(key, f"{sha}/{size}/{exp}".encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(mac).rstrip(b"=").decode()


def expiry(now: float | None = None) -> int:
    """Over 12 uur, naar boven afgerond op een uur (zodat de browser het adres een tijd kan
    hergebruiken)."""
    t = int(time.time() if now is None else now) + URL_HOURS * 3600
    return -(-t // 3600) * 3600


def url(key: bytes, sha: str, size: str, exp: int = 0) -> str:
    """Het ondertekende adres. exp 0: zonder einddatum (art bij een item, serie of album)."""
    sig = signature(key, sha, size, exp)
    query = f"exp={exp}&sig={sig}" if exp else f"sig={sig}"
    return f"/api/v1/media/art/{sha}/{size}.webp?{query}"


def valid(key: bytes, sha: str, size: str, exp: int, sig: str, now: float | None = None) -> bool:
    if exp < 0 or (exp and exp < (time.time() if now is None else now)):
        return False
    expected = signature(key, sha, size, exp).encode()
    return hmac.compare_digest(expected, sig.encode())


def entry(key: bytes, sha: str, role: str) -> dict[str, str]:
    """Wat er in `art` van een item, serie of album komt: {"art": sha, "url": …} (het contract
    met M2 en M9: Reader.art_url toont "url")."""
    return {"art": sha, "url": url(key, sha, DEFAULT_SIZE.get(role, "w512"))}


def shas_in(art: Any) -> set[str]:
    """De sha's van art van een provider in een veld `art`."""
    out = set()
    if isinstance(art, dict):
        for value in art.values():
            if isinstance(value, dict) and isinstance(value.get("art"), str):
                if SHA.match(value["art"]):
                    out.add(value["art"])
    return out


# --- bewaren ------------------------------------------------------------------------------------


def check(body: bytes) -> str:
    """Het soort, of Refused (te groot, of geen JPEG, PNG of WebP)."""
    if len(body) > MAX_BYTES:
        raise Refused("te groot")
    kind = sniff(body)
    if kind not in TYPES:
        raise Refused("geen jpeg, png of webp")
    return kind


async def store(settings, bodies: list[bytes], *, tmp: bool = False) -> list[str | None]:
    """Plaatjes omzetten en bewaren; per plaatje de sha, of None als het geweigerd werd. Alles
    in één renderproces. Wat er al staat, wordt niet opnieuw gemaakt."""
    import anyio.to_thread

    base = tmp_dir(settings) if tmp else art_dir(settings)
    out: list[str | None] = []
    todo: dict[str, bytes] = {}
    for body in bodies:
        try:
            check(body)
        except Refused:
            out.append(None)
            continue
        sha = hashlib.sha256(body).hexdigest()
        out.append(sha)
        if find(settings, sha, "w256") is not None and (tmp or (base / sha).is_dir()):
            if (base / sha).is_dir():
                # Weer vers: art-tmp weer 24 uur; een oude map in art/ die nu opnieuw gebruikt
                # wordt, ruimt cleanup niet weg voor de verwijzing er is (review golf 5, C9)
                with contextlib.suppress(OSError):
                    await anyio.to_thread.run_sync(os.utime, base / sha)
            continue
        todo[sha] = body
    if not todo:
        return out
    root = work_dir(settings)
    await anyio.to_thread.run_sync(lambda: root.mkdir(parents=True, exist_ok=True))
    work = Path(await anyio.to_thread.run_sync(lambda: tempfile.mkdtemp(dir=root)))
    try:
        sources = []
        for sha, body in todo.items():
            source = work / f"{sha}.src"
            await anyio.to_thread.run_sync(source.write_bytes, body)
            sources.append({"sha": sha, "source": str(source), "out": str(work / sha)})
        result = await render({"items": sources}, work)
        ready = {s for s in result.get("ready") or [] if isinstance(s, str)}
        for sha in todo:
            if sha in ready:
                await anyio.to_thread.run_sync(_place, work / sha, base / sha)
            else:
                out = [None if s == sha else s for s in out]
    finally:
        await anyio.to_thread.run_sync(lambda: shutil.rmtree(work, ignore_errors=True))
    return out


def _place(made: Path, target: Path) -> None:
    """De gemaakte map op zijn plek; staat hij er al (een ander was eerder), dan blijft die."""
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.rename(made, target)
    except OSError:
        if not (target / "w256.webp").is_file():
            raise


async def render(request: dict[str, Any], cwd: Path) -> dict[str, Any]:
    """Het renderproces (zoals previews.render): `python -I`, kale omgeving, eigen procesgroep;
    bij een fout of als het te lang duurt gaat de hele groep dood. Refused als het proces faalt."""
    import asyncio

    import anyio

    request = {"memory_mb": MEMORY_MB, "max_pixels": MAX_PIXELS, **request}
    env = {"PATH": os.environ.get("PATH", os.defpath), "LANG": "C.UTF-8", "HOME": str(cwd)}
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-I",
        "-c",
        CHILD,
        CODE_ROOT,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
        env=env,
        start_new_session=True,
    )
    try:
        with anyio.fail_after(CHILD_SECONDS):
            out, _err = await proc.communicate(json.dumps(request).encode())
    except TimeoutError:
        raise Refused("het renderproces duurde te lang") from None
    finally:
        if proc.returncode != 0:  # nog bezig of gecrasht: de hele groep weg (op pid)
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(proc.pid, signal.SIGKILL)
            with anyio.CancelScope(shield=True):
                await proc.wait()
    if proc.returncode != 0 or len(out) > OUTPUT_BYTES:
        raise Refused("het renderproces faalde")
    try:
        result = json.loads(out)
    except ValueError:
        raise Refused("het renderproces faalde") from None
    if not isinstance(result, dict):
        raise Refused("het renderproces faalde")
    return result


# --- het renderproces ---------------------------------------------------------------------------


def _limit(memory_mb: int) -> None:
    limit = int(memory_mb) * 1024 * 1024
    _soft, hard = resource.getrlimit(resource.RLIMIT_AS)
    if hard != resource.RLIM_INFINITY:
        limit = min(limit, hard)
    resource.setrlimit(resource.RLIMIT_AS, (limit, hard))


def convert(source: Path, out: Path, max_pixels: int) -> dict[str, int]:
    """Eén plaatje naar WebP in drie maten (in het renderproces). Gooit bij alles wat geen goed
    JPEG, PNG of WebP is."""
    import warnings

    from PIL import Image, ImageOps

    Image.MAX_IMAGE_PIXELS = max_pixels
    warnings.simplefilter("error", Image.DecompressionBombWarning)
    out.mkdir(parents=True, exist_ok=True)
    with Image.open(source, formats=FORMATS) as opened:
        width, height = opened.size
        opened.draft("RGB", (SIZES["w1280"], SIZES["w1280"]))
        img = ImageOps.exif_transpose(opened)
        if img.mode in ("RGBA", "LA", "PA") or (img.mode == "P" and "transparency" in img.info):
            img = img.convert("RGBA")
        elif img.mode != "RGB":
            img = img.convert("RGB")
        for name in SIZE_ORDER:
            size = SIZES[name]
            img.thumbnail((size, size), Image.Resampling.LANCZOS)  # nooit groter
            img.save(out / f"{name}.webp", "WEBP", quality=QUALITY, method=4)
    return {"width": width, "height": height}


def child_main() -> None:
    """Het renderproces: {"items": [{"sha", "source", "out"}], "memory_mb", "max_pixels"} op
    stdin; {"ready": [sha, …]} op stdout. Een plaatje dat niet lukt, ontbreekt in ready."""
    request = json.loads(sys.stdin.buffer.read())
    _limit(int(request.get("memory_mb") or MEMORY_MB))
    ready = []
    for item in request.get("items") or []:
        try:
            convert(Path(item["source"]), Path(item["out"]), int(request["max_pixels"]))
        except Exception:  # noqa: BLE001 - kapot (of te groot voor het geheugen): niet klaar
            shutil.rmtree(item["out"], ignore_errors=True)
            continue
        ready.append(item["sha"])
    sys.stdout.write(json.dumps({"ready": ready}))
    sys.stdout.flush()


# --- opruimen -----------------------------------------------------------------------------------


def _old_dirs(base: Path, before: float) -> list[Path]:
    if not base.is_dir():
        return []
    out = []
    for path in base.iterdir():
        with contextlib.suppress(OSError):
            if path.is_dir() and path.stat().st_mtime < before:
                out.append(path)
    return out


async def referenced(settings) -> set[str]:
    """Alle sha's van art waar een item, serie of album naar verwijst."""
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.db import get_engine
    from app.models import MediaAlbum, MediaItem, MediaShow

    used: set[str] = set()
    maker = async_sessionmaker(get_engine(settings), expire_on_commit=False)
    async with maker() as session:
        for column in (MediaItem.art, MediaShow.art, MediaAlbum.art):
            for art in (await session.scalars(select(column))).all():
                used |= shas_in(art)
    return used


async def cleanup(settings, now: float | None = None) -> dict[str, int]:
    """Eens per dag: art zonder verwijzing weg (wat jonger is dan een uur blijft: daar komt de
    verwijzing nog), de tijdelijke map van ouder dan 24 uur, en wat een renderproces liet
    staan. Geeft wat er weg ging."""
    import anyio.to_thread

    t = time.time() if now is None else now
    used = await referenced(settings)
    old_art = await anyio.to_thread.run_sync(_old_dirs, art_dir(settings), t - 3600)
    candidates = [p for p in old_art if p.name not in used]
    if candidates:
        # Nog eens kijken vlak voor het weggooien (review golf 5, C9): store() kan een oude map
        # intussen opnieuw gebruikt hebben (dan is hij weer vers), of de verwijzing staat er nu
        used = await referenced(settings)
        candidates = [p for p in candidates if p.name not in used]
    gone = [p for p in candidates if await anyio.to_thread.run_sync(_remove_if_old, p, t - 3600)]
    old = await anyio.to_thread.run_sync(_old_dirs, tmp_dir(settings), t - TMP_KEEP)
    work = await anyio.to_thread.run_sync(_old_dirs, work_dir(settings), t - 3600)
    for path in (*old, *work):
        await anyio.to_thread.run_sync(lambda p=path: shutil.rmtree(p, ignore_errors=True))
    return {"art": len(gone), "tmp": len(old), "work": len(work)}


def _remove_if_old(path: Path, before: float) -> bool:
    """De map weg als hij nog altijd ouder is dan before (store() maakt een map die het opnieuw
    gebruikt weer vers); geeft of hij weg ging."""
    try:
        if path.stat().st_mtime >= before:
            return False
    except OSError:
        return False
    shutil.rmtree(path, ignore_errors=True)
    return True
