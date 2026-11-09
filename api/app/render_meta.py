"""Metadata van foto's en video's lezen (opnamedatum, camera, objectief, belichting), in het
renderproces (app/render.py). Bovenaan alleen de standaardbibliotheek, zoals render.py; Pillow
en de mediaworker pas als ze nodig zijn.

- image_meta(img) -> {"taken_at", "taken_kind": "local", "exif": {...}} voor een geopende
  Pillow-afbeelding. Alleen wat er echt in staat: zonder datum geen taken_at, zonder
  camerawaarden geen exif (alleen een draaiing telt niet: breedte en hoogte zijn daar al op
  aangepast);
- video_meta(probe) -> {"taken_at", "taken_kind": "utc" | "local"} uit de JSON van ffprobe
  (sinds fase 5 het antwoord van `meta` van de mediaworker, in dezelfde vorm);
- run_meta(request) -> {"state": "ready", "results": {sha: meta}} voor mode "meta": alleen de
  headers van veel bestanden in één proces, een fout per bestand ({"error": ...}). Video's via
  `meta` van de mediaworker (fase 5, M1): met media aan die van de app (request["worker"], van
  previews.py), anders een eigen worker voor dit proces, pas bij de eerste video.

taken_at is een ISO-tekst zonder tijdzone. "local" is de kloktijd van de camera (een
zwevende tijd, ook als er een offset bij stond: die kloktijd houden we); "utc" is een echt
moment dat de API zelf omzet naar settings.timezone (het renderproces heeft geen tijdzone).
Zie app/photodate.py.

De plek (fase 6, Keuze 25; VERSION 2) lezen we alleen in mode "meta" (run_meta, de batch van
app/photometa.py), nooit voor de preview: image_meta en video_meta geven hem niet, en wat in
preview_meta komt, gaat ook nog door previews.clean_meta (SECRET_META). Per bestand zet run_meta
"location": {"lat", "lon", "alt", "source"} of None (gelezen, geen plek); zonder die sleutel is
het onbekend (een video zonder ffprobe). De bronnen:

- de GPS-IFD (0x8825) van JPEG, HEIC, TIFF, PNG (eXIf) en WebP, via Pillow;
- anders de XMP in de bytes van im.info["xmp"] (hooguit 1 MB), gelezen met office.reader.sax()
  (weigert DOCTYPE en entiteiten), alleen exif:GPSLatitude, exif:GPSLongitude en
  exif:GPSAltitude;
- bij een video de tags die de worker-opdracht meta doorgeeft (ISO 6709:
  com.apple.quicktime.location.ISO6709, en ©xyz dat ffprobe als location toont).

Elke waarde is onvertrouwd: NaN en oneindig weg (IFDRational(0, 0) geeft NaN), |lat| > 90 of
|lon| > 180 weg, (0, 0) is "geen plek", en zonder GPSLatitudeRef of GPSLongitudeRef ook geen
plek. Een hoogte die niet klopt, laat alleen de hoogte weg.
"""

import contextlib
import math
import numbers
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Omhoog bij elke wijziging in wat er gelezen wordt: de backfill (app/photometa.py) leest dan
# alles met een lagere Blob.meta_version opnieuw. 2: ook de plek, camera en objectief (Z3)
VERSION = 2

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
# GPS-IFD (Keuze 25)
GPS_IFD = 0x8825
GPS_LAT_REF, GPS_LAT, GPS_LON_REF, GPS_LON, GPS_ALT_REF, GPS_ALT = 1, 2, 3, 4, 5, 6
XMP_BYTES = 1 << 20
XMP_EXIF = "{http://ns.adobe.com/exif/1.0/}"
MAX_ALT = 100_000.0  # meter; daarbuiten is de hoogte onzin
# Video: de tags met een plek, zoals de worker ze doorgeeft (kleine letters)
VIDEO_LOCATION_TAGS = ("com.apple.quicktime.location.iso6709", "location", "location-eng")
ISO6709 = re.compile(
    r"([+-])(\d{2}(?:\d{2}(?:\d{2})?)?(?:\.\d{1,12})?)"
    r"([+-])(\d{3}(?:\d{2}(?:\d{2})?)?(?:\.\d{1,12})?)"
    r"([+-]\d{1,6}(?:\.\d{1,6})?)?(?:CRS[A-Za-z0-9_:]{0,32})?/?"
)
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


def _real(value: Any) -> float | None:
    """Een getal zoals de GPS-IFD het hoort te geven (rationaal of geheel), eindig; tekst, bool
    en andere soorten geven None (Keuze 25: elke waarde nagekeken)."""
    if isinstance(value, bool) or not isinstance(value, numbers.Real):
        return None
    return _number(value)


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


def location(
    lat: float | None, lon: float | None, alt: float | None, source: str
) -> dict[str, Any] | None:
    """De controle op elke plek (Keuze 25): None als hij niet klopt of (0, 0) is."""
    if lat is None or lon is None:
        return None
    if not (math.isfinite(lat) and math.isfinite(lon)) or abs(lat) > 90 or abs(lon) > 180:
        return None
    if lat == 0 and lon == 0:
        return None
    if alt is not None and (not math.isfinite(alt) or abs(alt) > MAX_ALT):
        alt = None
    return {
        "lat": round(lat, 7),
        "lon": round(lon, 7),
        "alt": None if alt is None else round(alt, 2),
        "source": source,
    }


def _degrees(value: Any) -> float | None:
    """Graden uit de GPS-IFD: (graden, minuten, seconden) of één getal; elk deel eindig en niet
    negatief, minuten en seconden onder 60."""
    if isinstance(value, tuple | list):
        parts = list(value)
    else:
        parts = [value]
    if not 1 <= len(parts) <= 3:
        return None
    numbers = []
    for part in parts:
        if isinstance(part, tuple | list):
            return None
        number = _real(part)
        if number is None or number < 0:
            return None
        numbers.append(number)
    numbers += [0.0] * (3 - len(numbers))
    d, m, sec = numbers
    if m >= 60 or sec >= 60:
        return None
    return d + m / 60 + sec / 3600


def _ref(value: Any, allowed: str) -> str | None:
    text = _text(value)
    if text is None:
        return None
    text = text.upper()[:1]
    return text if text and text in allowed else None


def exif_location(top: Any) -> dict[str, Any] | None:
    """De plek uit de GPS-IFD van een Pillow-Exif, of None."""
    try:
        gps = top.get_ifd(GPS_IFD)
    except Exception:  # noqa: BLE001 - kapotte GPS-IFD: dan geen plek
        return None
    if not gps:
        return None
    lat_ref, lon_ref = _ref(gps.get(GPS_LAT_REF), "NS"), _ref(gps.get(GPS_LON_REF), "EW")
    if lat_ref is None or lon_ref is None:
        return None
    lat, lon = _degrees(gps.get(GPS_LAT)), _degrees(gps.get(GPS_LON))
    if lat is None or lon is None:
        return None
    lat = -lat if lat_ref == "S" else lat
    lon = -lon if lon_ref == "W" else lon
    alt = _real(gps.get(GPS_ALT))
    if alt is not None and alt >= 0:
        below = gps.get(GPS_ALT_REF)
        if isinstance(below, bytes):
            below = below[:1] == b"\x01"
        elif isinstance(below, int):
            below = below == 1
        else:
            below = False
        alt = -alt if below else alt
    else:
        alt = None
    return location(lat, lon, alt, "exif")


def _xmp_coordinate(text: str | None, positive: str, negative: str) -> float | None:
    """Een GPSCoordinate van XMP: "DDD,MM,SSk" of "DDD,MM.mmk" (k is N, S, E of W)."""
    if not text:
        return None
    text = text.strip()
    if len(text) > 40 or len(text) < 2:
        return None
    ref = text[-1].upper()
    if ref not in positive + negative:
        return None
    parts = text[:-1].split(",")
    if not 2 <= len(parts) <= 3:
        return None
    try:
        numbers = [float(p) for p in parts]
    except ValueError:
        return None
    value = _degrees(tuple(numbers))
    if value is None:
        return None
    return -value if ref == negative else value


def _xmp_rational(text: str | None) -> float | None:
    if not text or len(text) > 40:
        return None
    top, _, bottom = text.strip().partition("/")
    try:
        number = float(top) / float(bottom) if bottom else float(top)
    except (ValueError, ZeroDivisionError):
        return None
    return number if math.isfinite(number) else None


def xmp_location(data: Any) -> dict[str, Any] | None:
    """De plek uit de ruwe XMP, of None. Een DOCTYPE of entiteit ("billion laughs") weigert
    office.reader.sax(); dan geen plek."""
    import io

    from app.office import reader

    if isinstance(data, str):
        data = data.encode("utf-8", errors="replace")
    if not isinstance(data, bytes | bytearray) or not data or len(data) > XMP_BYTES:
        return None
    wanted = {f"{XMP_EXIF}GPSLatitude", f"{XMP_EXIF}GPSLongitude", f"{XMP_EXIF}GPSAltitude"}
    found: dict[str, str] = {}
    current: list[str] = []
    texts: list[str] = []

    def start(tag: str, attrs: dict[str, str]) -> None:
        for name, value in attrs.items():
            if name in wanted and name not in found:
                found[name] = value[:64]
        current.append(tag)
        texts.clear()

    def end(tag: str) -> None:
        if tag in wanted and tag not in found and texts:
            found[tag] = "".join(texts)[:64]
        if current:
            current.pop()
        texts.clear()

    def text(data: str) -> None:
        if current and current[-1] in wanted and sum(map(len, texts)) < 64:
            texts.append(data)

    try:
        reader.sax(io.BytesIO(bytes(data)), start, end, text)
    except reader.Unreadable:
        return None
    lat = _xmp_coordinate(found.get(f"{XMP_EXIF}GPSLatitude"), "N", "S")
    lon = _xmp_coordinate(found.get(f"{XMP_EXIF}GPSLongitude"), "E", "W")
    alt = _xmp_rational(found.get(f"{XMP_EXIF}GPSAltitude"))
    return location(lat, lon, alt, "xmp")


def image_location(img: Any) -> dict[str, Any] | None:
    """De plek van een geopende Pillow-afbeelding: eerst de GPS-IFD, dan de XMP."""
    try:
        top = img.getexif()
    except Exception:  # noqa: BLE001 - kapotte EXIF: dan de XMP nog
        top = None
    found = exif_location(top) if top else None
    if found is None:
        info = getattr(img, "info", None) or {}
        xmp = info.get("xmp") or info.get("XML:com.adobe.xmp")
        found = xmp_location(xmp) if xmp else None
    return found


def iso6709(text: Any) -> dict[str, Any] | None:
    """Een plek in ISO 6709 ("+52.0907+005.1214+010.000/", ook DDMM en DDMMSS), of None."""
    text = _text(text)
    if text is None or len(text) > 64:
        return None
    match = ISO6709.fullmatch(text)
    if match is None:
        return None
    lat_sign, lat_text, lon_sign, lon_text, alt_text = match.groups()

    def value(digits: str, deg: int) -> float | None:
        whole, _, frac = digits.partition(".")
        frac = f".{frac}" if frac else ""
        if len(whole) == deg:
            return float(whole + frac)
        if len(whole) == deg + 2:
            return _degrees((float(whole[:deg]), float(whole[deg:] + frac)))
        return _degrees(
            (float(whole[:deg]), float(whole[deg : deg + 2]), float(whole[deg + 2 :] + frac))
        )

    lat, lon = value(lat_text, 2), value(lon_text, 3)
    if lat is None or lon is None:
        return None
    lat = -lat if lat_sign == "-" else lat
    lon = -lon if lon_sign == "-" else lon
    alt = float(alt_text) if alt_text else None
    return location(lat, lon, alt, "video")


def video_location(probe: Any) -> dict[str, Any] | None:
    """De plek van een video uit het antwoord van meta van de worker, of None."""
    if not isinstance(probe, dict):
        return None
    streams = [s for s in probe.get("streams") or [] if isinstance(s, dict)]
    tags = _tags(probe.get("format"), *streams)
    for name in VIDEO_LOCATION_TAGS:
        found = iso6709(tags.get(name))
        if found is not None:
            return found
    return None


def camera(exif: dict[str, Any] | None) -> tuple[str | None, str | None]:
    """(camera, objectief) voor blob_meta (Keuze 26): "merk model" gevouwen; staat het merk al
    vooraan in het model, dan niet twee keer."""
    from app.text import CONTROL, fold

    if not isinstance(exif, dict):
        return None, None

    def clean(value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        value = " ".join(CONTROL.sub(" ", value).split())
        return fold(value)[:TEXT_CHARS] or None

    make, model = clean(exif.get("make")), clean(exif.get("model"))
    if make and model and not model.startswith(make):
        name = f"{make} {model}"
    else:
        name = model or make
    return name, clean(exif.get("lens"))


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


def _probe(source: Path, media: Any) -> dict[str, Any]:
    """De opnamedatum van een video via `meta` van de mediaworker (fase 5, M1): ffprobe draait
    alleen daar. media is een render.Media; niet te lezen geeft {} (zoals 0.6.0)."""
    from app import render

    with open(source, "rb") as f:
        head = f.read(render.HEAD)
    fmt = render.container(head)
    if fmt is None:
        return {}
    if not media.ready():
        return {}  # geen ffprobe in de worker: zoals 0.6.0 zonder ffprobe
    info = media.meta(source, fmt)
    if info is None:
        return {}
    # De worker las de video: nu is ook bekend of er een plek in staat (mode meta, Keuze 25)
    return {**video_meta(info), "location": video_location(info)}


class _Videos:
    """De worker voor de video's van één run_meta, pas gestart bij de eerste video (media uit:
    een eigen worker, render.Media). Lukt dat niet, dan krijgt elke video dezelfde fout."""

    def __init__(self, worker: dict[str, Any] | None):
        self.worker = worker
        self.stack = contextlib.ExitStack()
        self.media: Any = None
        self.failed: str | None = None

    def probe(self, source: Path) -> dict[str, Any]:
        from app import render

        if self.failed is not None:
            return {"error": self.failed}
        if self.media is None and not self.worker and not render.installed():
            return {}  # media uit en geen ffprobe: zoals 0.6.0
        if self.media is None:
            try:
                self.media = self.stack.enter_context(render.Media(self.worker, Path.cwd()))
            except Exception as e:  # noqa: BLE001 - de eigen worker start niet
                self.failed = f"mediaworker: {type(e).__name__}: {e}"[:200]
                return {"error": self.failed}
        try:
            info = _probe(source, self.media)
            if not info and self.worker and not self.media.ready():
                # Media aan, maar de worker heeft nu geen ffmpeg (ffmpeg_missing): ook later
                return self._later("mediaworker zonder ffmpeg")
            return info
        except render.Later as e:
            # Weg of druk: een fout voor dit bestand. photometa laat de datum die er is dan
            # staan; de preview-taak zelf wacht wel (jobs.Defer)
            if self.worker:
                return self._later(str(e))
            return {"error": str(e)[:200]}

    @staticmethod
    def _later(reason: str) -> dict[str, Any]:
        """Media aan en de worker weg, druk of zonder ffmpeg: een fout met "later" erbij
        (integratie, punt 3). photometa markeert dat bestand dan niet als gelezen; de
        housekeeping van media zet de backfill weer klaar als de worker gezond is. Media uit
        (een eigen worker per run, zoals 0.6.0) geeft nooit "later"."""
        return {"error": reason[:200], "later": True}


def _image(source: Path, max_pixels: int) -> dict[str, Any]:
    from app import render

    Image = render._pillow(max_pixels)
    with Image.open(source, formats=render.IMAGE_FORMATS) as img:  # leest alleen de headers
        return {**image_meta(img), "location": image_location(img)}


def run_meta(request: dict[str, Any]) -> dict[str, Any]:
    """Mode "meta": {"items": [{"sha", "source", "mime"}], "max_pixels"}. Per bestand de
    metadata, of {"error": ...} als het niet lukte; een fout in één bestand stopt de rest niet."""
    max_pixels = int(request.get("max_pixels") or 100_000_000)
    results: dict[str, Any] = {}
    videos = _Videos(request.get("worker"))
    with videos.stack:
        for item in request.get("items") or []:
            sha = str(item.get("sha", ""))
            mime = str(item.get("mime") or "").lower()
            try:
                source = Path(item["source"])
                if mime.startswith("image/"):
                    results[sha] = _image(source, max_pixels)
                elif mime.startswith("video/"):
                    results[sha] = videos.probe(source)
                else:
                    results[sha] = {}
            except Exception as e:  # noqa: BLE001 - per bestand: kapot, weg of te groot
                results[sha] = {"error": f"{type(e).__name__}: {e}"[:200]}
    return {"state": "ready", "results": results}
