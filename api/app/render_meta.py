"""Metadata van foto's en video's lezen (opnamedatum, camera, objectief, belichting), in het
renderproces (app/render.py). Bovenaan alleen de standaardbibliotheek, zoals render.py; Pillow
en ffprobe pas als ze nodig zijn.

- image_meta(img) -> {"taken_at", "taken_kind": "local", "exif": {...}} voor een geopende
  Pillow-afbeelding. Alleen wat er echt in staat: zonder datum geen taken_at, zonder
  camerawaarden geen exif (alleen een draaiing telt niet: breedte en hoogte zijn daar al op
  aangepast);
- video_meta(probe) -> {"taken_at", "taken_kind": "utc" | "local"} uit de JSON van ffprobe;
- run_meta(request) -> {"state": "ready", "results": {sha: meta}} voor mode "meta": alleen de
  headers van veel bestanden in één proces, een fout per bestand ({"error": ...}).

taken_at is een ISO-tekst zonder tijdzone. "local" is de kloktijd van de camera (een
zwevende tijd, ook als er een offset bij stond: die kloktijd houden we); "utc" is een echt
moment dat de API zelf omzet naar settings.timezone (het renderproces heeft geen tijdzone).
Zie app/photodate.py.

GPS en locatie lezen we nooit (fase 6): de GPS-IFD (0x8825) wordt niet geopend en
locatietags van video's worden overgeslagen.
"""

import json
import math
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Omhoog bij elke wijziging in wat er gelezen wordt: de backfill (app/photometa.py) leest dan
# alles met een lagere Blob.meta_version opnieuw
VERSION = 1

FIRST_YEAR, LAST_YEAR = 1900, 2100
TEXT_CHARS = 200
EXIF_IFD = 0x8769
# IFD0
MAKE, MODEL, ORIENTATION, SOFTWARE, DATETIME = 0x010F, 0x0110, 0x0112, 0x0131, 0x0132
# Exif-IFD
EXPOSURE, FNUMBER, ISO = 0x829A, 0x829D, 0x8827
ORIGINAL, DIGITIZED = 0x9003, 0x9004
BIAS, FLASH, FOCAL = 0x9204, 0x9209, 0x920A
FOCAL35, LENS_MAKE, LENS_MODEL = 0xA405, 0xA433, 0xA434
# Een klok die nooit gezet is: als "geen datum"
EPOCHS = {datetime(1904, 1, 1), datetime(1970, 1, 1)}
EXIF_DATE = re.compile(r"(\d{4}):(\d{2}):(\d{2})[ T](\d{2}):(\d{2}):(\d{2})")


def _text(value: Any) -> str | None:
    """Een EXIF-tekst zonder NUL-tekens en witruimte eromheen, ingekort; leeg = None."""
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    if not isinstance(value, str):
        return None
    value = value.replace("\x00", "").strip()
    return value[:TEXT_CHARS] or None


def _number(value: Any) -> float | None:
    """Een rationaal (IFDRational), geheel getal of lijstje daarvan als float; NaN, oneindig
    (een noemer 0) en onzin geven None."""
    if isinstance(value, tuple | list):
        value = value[0] if value else None
    try:
        number = float(value)
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    return number if math.isfinite(number) else None


def _whole(value: Any) -> int | None:
    number = _number(value)
    return int(number) if number is not None and number >= 0 else None


def _valid(moment: datetime | None) -> datetime | None:
    if moment is None:
        return None
    naive = moment.replace(tzinfo=None)
    if not FIRST_YEAR <= naive.year <= LAST_YEAR or naive in EPOCHS:
        return None
    return moment


def exif_date(value: Any) -> str | None:
    """ "2024:07:01 12:30:00" als "2024-07-01T12:30:00"; 0000:00:00, een onmogelijke datum of
    een jaar buiten 1900-2100 geeft None."""
    text = _text(value)
    match = EXIF_DATE.match(text or "")
    if not match:
        return None
    try:
        moment = datetime(*(int(part) for part in match.groups()))
    except ValueError:
        return None
    moment = _valid(moment)
    return moment.isoformat() if moment else None


def image_meta(img: Any) -> dict[str, Any]:
    """Opnamedatum en camerawaarden van een geopende Pillow-afbeelding (alleen de headers)."""
    try:
        top = img.getexif()
    except Exception:  # noqa: BLE001 - kapotte EXIF: dan zonder
        return {}
    if not top:
        return {}
    try:
        sub = top.get_ifd(EXIF_IFD)
    except Exception:  # noqa: BLE001 - zie hierboven
        sub = {}
    exif: dict[str, Any] = {
        "make": _text(top.get(MAKE)),
        "model": _text(top.get(MODEL)),
        "lens": _text(sub.get(LENS_MODEL)) or _text(sub.get(LENS_MAKE)),
        "software": _text(top.get(SOFTWARE)),
        "fnumber": _number(sub.get(FNUMBER)),
        "exposure": _number(sub.get(EXPOSURE)),
        "iso": _whole(sub.get(ISO)),
        "focal": _number(sub.get(FOCAL)),
        "focal35": _whole(sub.get(FOCAL35)),
        "flash": _whole(sub.get(FLASH)),
        "bias": _number(sub.get(BIAS)),
    }
    exif = {k: v for k, v in exif.items() if v is not None}
    meta: dict[str, Any] = {}
    if exif:
        orientation = _whole(top.get(ORIENTATION))
        if orientation is not None and 1 <= orientation <= 8:
            exif["orientation"] = orientation
        meta["exif"] = exif
    # Een offset (OffsetTimeOriginal) laten we staan: de kloktijd van de camera telt
    taken = exif_date(sub.get(ORIGINAL)) or exif_date(sub.get(DIGITIZED))
    taken = taken or exif_date(top.get(DATETIME))
    if taken:
        meta.update(taken_at=taken, taken_kind="local")
    return meta


def _iso(value: Any) -> datetime | None:
    text = _text(value)
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _tags(*sources: Any) -> dict[str, str]:
    """Tags van het formaat en de streams samen, met kleine letters als sleutel; de eerste
    bron wint."""
    tags: dict[str, str] = {}
    for source in sources:
        if not isinstance(source, dict):
            continue
        for key, value in (source.get("tags") or {}).items():
            if isinstance(key, str) and isinstance(value, str):
                tags.setdefault(key.lower(), value)
    return tags


def video_meta(probe: dict[str, Any]) -> dict[str, Any]:
    """De opnamedatum uit de JSON van ffprobe (-show_format -show_streams). De QuickTime-datum
    van een telefoon heeft een offset en is de kloktijd daar ("local"); creation_time is een
    echt moment in UTC ("utc"). Locatietags (ISO 6709) slaan we over."""
    if not isinstance(probe, dict):
        return {}
    streams = [s for s in probe.get("streams") or [] if isinstance(s, dict)]
    tags = _tags(probe.get("format"), *streams)
    apple = _valid(_iso(tags.get("com.apple.quicktime.creationdate")))
    if apple is not None:
        return {"taken_at": apple.replace(tzinfo=None).isoformat(), "taken_kind": "local"}
    created = _valid(_iso(tags.get("creation_time")))
    if created is not None:
        # Een moment met tijdzone als UTC zonder tijdzone; zonder tijdzone is het al UTC
        if created.tzinfo is not None:
            created = created.astimezone(UTC).replace(tzinfo=None)
        return {"taken_at": created.isoformat(), "taken_kind": "utc"}
    return {}


def _probe(source: Path) -> dict[str, Any]:
    from app import render

    with open(source, "rb") as f:
        head = f.read(render.HEAD)
    fmt = render.container(head)
    ffprobe = render.shutil.which("ffprobe")
    if fmt is None or ffprobe is None:
        return {}
    probe = render._run(
        [ffprobe, "-v", "error", "-f", fmt, "-print_format", "json", "-show_format"]
        + ["-show_streams", f"file:{source}"]
    )
    if probe.returncode != 0:
        return {}
    return video_meta(json.loads(probe.stdout or b"{}"))


def _image(source: Path, max_pixels: int) -> dict[str, Any]:
    from app import render

    Image = render._pillow(max_pixels)
    with Image.open(source, formats=render.IMAGE_FORMATS) as img:  # leest alleen de headers
        return image_meta(img)


def run_meta(request: dict[str, Any]) -> dict[str, Any]:
    """Mode "meta": {"items": [{"sha", "source", "mime"}], "max_pixels"}. Per bestand de
    metadata, of {"error": ...} als het niet lukte; een fout in één bestand stopt de rest niet."""
    max_pixels = int(request.get("max_pixels") or 100_000_000)
    results: dict[str, Any] = {}
    for item in request.get("items") or []:
        sha = str(item.get("sha", ""))
        mime = str(item.get("mime") or "").lower()
        try:
            source = Path(item["source"])
            if mime.startswith("image/"):
                results[sha] = _image(source, max_pixels)
            elif mime.startswith("video/"):
                results[sha] = _probe(source)
            else:
                results[sha] = {}
        except Exception as e:  # noqa: BLE001 - per bestand: kapot, weg of te groot
            results[sha] = {"error": f"{type(e).__name__}: {e}"[:200]}
    return {"state": "ready", "results": results}
