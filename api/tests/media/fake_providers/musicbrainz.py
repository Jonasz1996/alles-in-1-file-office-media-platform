"""De nep-MusicBrainz (`/ws/2/`), de nep-Cover Art Archive en de tweede host ("archive") waar de
CAA naar omleidt, naar de documentatie [doc] (§4.4, Keuze 41).

- Zonder User-Agent in de vorm `Naam/versie ( contact )`: 403. Meer dan `musicbrainz_rate` (1)
  per seconde: 503, zonder `Retry-After` (de app wacht dan zelf).
- `fmt=json` (of `Accept: application/json`) geeft JSON; anders een kleine XML in de vorm van
  MMD 2.0, zodat een vergeten `fmt=json` meteen opvalt.
- `GET /ws/2/release/{mbid}?inc=…`: `inc` (gescheiden door `+` of een spatie) kiest
  `artist-credits`, `release-groups`, `media` en `recordings`; andere geldige namen mogen en
  doen niets, een onbekende geeft 400.
- `GET /ws/2/release/?query=…&limit&offset`: een kleine Lucene: `veld:"woorden"`, `veld:woord`,
  losse woorden (op de titel), `AND`, `OR`, `NOT` en haakjes (die negeert de nep). Velden:
  `release`, `artist` (`artistname`), `date`, `country`, `barcode`, `tracks`, `reid`, `rgid`,
  `arid`, `primarytype`. Een score van 0 tot 100.
- CAA `GET /release/{mbid}/front[-250|-500|-1200]`: 307 naar
  `{archive_url}/download/mbid-{mbid}/mbid-{mbid}-{id}[_thumb250|_thumb500|_thumb1200].jpg`; die
  geeft een vierkante JPEG in die maat. Geen release of geen voorkant: 404.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from xml.sax.saxutils import escape, quoteattr

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

if TYPE_CHECKING:
    from .app import FakeProviders

USER_AGENT = re.compile(r"^\S+/\S+ \( \S.* \)$")
HELP = "For usage, please see: https://musicbrainz.org/development/mmd"
RATE_MESSAGE = (
    "Your requests are exceeding the allowable rate limit. Please see "
    "https://wiki.musicbrainz.org/MusicBrainz_API/Rate_Limiting for more information."
)
INCLUDES = {
    "artist-credits", "release-groups", "media", "recordings", "labels", "artists",
    "collections", "discids", "isrcs", "aliases", "annotation", "tags", "genres", "ratings",
    "user-tags", "user-genres", "artist-rels", "label-rels", "recording-rels",
    "release-rels", "release-group-rels", "url-rels", "work-rels",
}  # fmt: skip
FIELDS = {
    "release", "artist", "artistname", "date", "country", "barcode", "tracks", "reid", "rgid",
    "arid", "primarytype",
}  # fmt: skip
MMD = "http://musicbrainz.org/ns/mmd-2.0#"
THUMBS = {"front": ("", None), "front-250": ("_thumb250", 250), "front-500": ("_thumb500", 500)}
THUMBS["front-1200"] = ("_thumb1200", 1200)
ARCHIVE_FILE = re.compile(r"^mbid-([0-9a-f-]{36})-([0-9]+)(_thumb250|_thumb500|_thumb1200)?\.jpg$")
STATUS_ID = "4e304316-386d-3409-af2e-78857eec5cfe"  # Official
TYPE_IDS = {
    "Album": "f529b476-6e62-324f-b0aa-1f3e33d313fc",
    "Single": "d6038452-8ee0-3f68-affc-2de9a1ede0b9",
    "EP": "6d0c5bf6-7a33-3420-a519-44fc63eedebf",
}


def mbid(value: str) -> str | None:
    """De MBID in de vaste vorm, of None als het er geen is."""
    try:
        return str(uuid.UUID(value)) if len(value) == 36 else None
    except ValueError:
        return None


def image_id(release_id: str) -> int:
    """Het id van de voorkant in de CAA: vast per release, zodat een test het kan voorspellen."""
    return 10_000_000_000 + int(hashlib.sha256(release_id.encode()).hexdigest()[:8], 16)


def wants_json(request: Request) -> bool:
    if request.query_params.get("fmt") == "json":
        return True
    return "application/json" in request.headers.get("accept", "")


def error(request: Request, status: int, message: str) -> Response:
    if wants_json(request):
        return JSONResponse({"error": message, "help": HELP}, status_code=status)
    body = f'<?xml version="1.0" encoding="UTF-8"?><error><text>{escape(message)}</text></error>'
    return Response(body, status_code=status, media_type="application/xml")


# --- de vormen van MusicBrainz ---


def artist_credit(release: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "name": release["artist"],
            "joinphrase": "",
            "artist": {
                "id": release["artist_id"],
                "name": release["artist"],
                "sort-name": release.get("artist_sort") or release["artist"],
                "disambiguation": "",
                "type": release.get("artist_type", "Group"),
            },
        }
    ]


def release_group(release: dict[str, Any]) -> dict[str, Any] | None:
    group = release.get("release_group")
    if group is None:
        return None
    kind = group.get("primary_type", "Album")
    return {
        "id": group["id"],
        "title": group.get("title", release["title"]),
        "primary-type": kind,
        "primary-type-id": TYPE_IDS.get(kind),
        "secondary-types": [],
        "first-release-date": group.get("first_release_date", release.get("date", "")),
        "disambiguation": "",
    }


def track_count(release: dict[str, Any]) -> int:
    tracks = release.get("tracks")
    return len(tracks) if tracks else int(release.get("track_count", 0))


def media(release: dict[str, Any], *, recordings: bool) -> list[dict[str, Any]]:
    medium: dict[str, Any] = {
        "position": 1,
        "format": release.get("format", "CD"),
        "title": "",
        "track-count": track_count(release),
        "track-offset": 0,
    }
    if recordings and release.get("tracks"):
        medium["tracks"] = [
            {
                "id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"{release['id']}/track/{i}")),
                "position": i,
                "number": str(i),
                "title": track["title"],
                "length": track.get("length_ms"),
                "recording": {
                    "id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"{release['id']}/recording/{i}")),
                    "title": track["title"],
                    "length": track.get("length_ms"),
                    "disambiguation": "",
                    "video": False,
                },
            }
            for i, track in enumerate(release["tracks"], start=1)
        ]
    return [medium]


def cover_art_archive(release: dict[str, Any]) -> dict[str, Any]:
    cover = bool(release.get("cover"))
    return {"artwork": cover, "count": int(cover), "front": cover, "back": False, "darkened": False}


def release_events(release: dict[str, Any]) -> list[dict[str, Any]]:
    if not release.get("date"):
        return []
    country = release.get("country")
    area = {"name": country, "iso-3166-1-codes": [country]} if country else None
    return [{"date": release["date"], "area": area}]


def release_lookup(release: dict[str, Any], includes: set[str]) -> dict[str, Any]:
    body: dict[str, Any] = {
        "id": release["id"],
        "title": release["title"],
        "status": "Official",
        "status-id": STATUS_ID,
        "date": release.get("date", ""),
        "country": release.get("country"),
        "barcode": release.get("barcode", ""),
        "packaging": None,
        "disambiguation": "",
        "quality": "normal",
        "asin": None,
        "text-representation": {"language": "eng", "script": "Latn"},
        "release-events": release_events(release),
        "cover-art-archive": cover_art_archive(release),
    }
    if includes & {"artist-credits", "artists"}:
        body["artist-credit"] = artist_credit(release)
    if "release-groups" in includes and release.get("release_group"):
        body["release-group"] = release_group(release)
    if includes & {"media", "recordings", "discids"}:
        body["media"] = media(release, recordings="recordings" in includes)
    return body


def release_hit(release: dict[str, Any], points: int) -> dict[str, Any]:
    body: dict[str, Any] = {
        "id": release["id"],
        "score": points,
        "status-id": STATUS_ID,
        "count": 1,
        "title": release["title"],
        "status": "Official",
        "text-representation": {"language": "eng", "script": "Latn"},
        "artist-credit": artist_credit(release),
        "date": release.get("date", ""),
        "country": release.get("country"),
        "release-events": release_events(release),
        "barcode": release.get("barcode", ""),
        "track-count": track_count(release),
        "media": [{"format": release.get("format", "CD"), "disc-count": 0}],
    }
    body["media"][0]["track-count"] = body["track-count"]
    group = release_group(release)
    if group is not None:
        body["release-group"] = {
            "id": group["id"],
            "type-id": group["primary-type-id"],
            "primary-type-id": group["primary-type-id"],
            "title": group["title"],
            "primary-type": group["primary-type"],
        }
    return body


# --- zoeken (een kleine Lucene) ---


def norm(text: str) -> list[str]:
    text = unicodedata.normalize("NFKD", text.casefold())
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"[^0-9a-z]+", " ", text).split()


TOKEN = re.compile(
    r'\s*(?:(?P<op>AND|OR|NOT|&&|\|\||!)(?=[\s("]|$)|(?P<paren>[()])'
    r'|(?:(?P<field>[a-z]+):)?(?:"(?P<phrase>(?:\\.|[^"\\])*)"|(?P<word>(?:\\.|[^\s"()\\])+)))'
)


class QueryError(ValueError):
    pass


def parse(query: str) -> list[list[tuple[str, str, bool]]]:
    """Een query als lijst van groepen (OF); elke groep is een lijst van (veld, tekst, niet) die
    allemaal moeten kloppen (EN)."""
    groups: list[list[tuple[str, str, bool]]] = [[]]
    negate = False
    pos = 0
    query = query.strip()
    while pos < len(query):
        m = TOKEN.match(query, pos)
        if m is None or m.end() == pos:
            raise QueryError(f"Cannot parse query at {pos}")
        pos = m.end()
        op = m.group("op")
        if op in ("OR", "||"):
            groups.append([])
        elif op in ("NOT", "!"):
            negate = True
        elif op or m.group("paren"):
            continue
        else:
            raw = m.group("phrase") if m.group("phrase") is not None else m.group("word")
            text = re.sub(r"\\(.)", r"\1", raw)
            field = m.group("field") or "release"
            groups[-1].append(("artist" if field == "artistname" else field, text, negate))
            negate = False
    return [g for g in groups if g]


def _quality(want: list[str], have: str) -> float:
    words = norm(have)
    if not want:
        return 1.0
    if words == want:
        return 1.0
    if all(w in words for w in want):
        return 0.8
    return 0.0


def term_quality(release: dict[str, Any], field: str, text: str) -> float:
    group = release.get("release_group") or {}
    exact = {
        "date": release.get("date", ""),
        "country": release.get("country") or "",
        "barcode": release.get("barcode", ""),
        "tracks": str(track_count(release)),
        "reid": release["id"],
        "rgid": group.get("id", ""),
        "arid": release.get("artist_id", ""),
        "primarytype": group.get("primary_type", ""),
    }
    if field in exact:
        value = exact[field].casefold()
        if field == "date":
            return 1.0 if value.startswith(text.casefold()) and text else 0.0
        return 1.0 if value == text.casefold() else 0.0
    if field not in FIELDS:
        return 0.0  # een onbekend veld vindt niets, zoals bij MusicBrainz
    want = norm(text)
    if field == "release":
        return _quality(want, release["title"])
    return max(_quality(want, release["artist"]), _quality(want, release.get("artist_sort", "")))


def match(release: dict[str, Any], groups: list[list[tuple[str, str, bool]]]) -> int:
    best = 0.0
    for group in groups:
        qualities = []
        for field, text, negate in group:
            q = term_quality(release, field, text)
            if negate:
                if q:
                    break
                continue
            if not q:
                break
            qualities.append(q)
        else:
            if qualities:
                best = max(best, sum(qualities) / len(qualities))
    return round(best * 100)


def _number(request: Request, key: str, default: int, low: int, high: int) -> int | None:
    raw = request.query_params.get(key)
    if raw is None or raw == "":
        return default
    if not raw.isdigit():
        return None
    return min(max(int(raw), low), high)


def search(fake: FakeProviders, request: Request) -> Response:
    query = request.query_params.get("query", "")
    if not query.strip():
        return error(request, 400, "Missing required parameter: query")
    try:
        groups = parse(query)
    except QueryError as e:
        return error(request, 400, str(e))
    limit = _number(request, "limit", 25, 1, 100)
    offset = _number(request, "offset", 0, 0, 1_000_000)
    if limit is None or offset is None:
        return error(request, 400, "Invalid limit or offset")
    hits = []
    for release in fake.data.releases.values():
        points = match(release, groups)
        if points:
            hits.append((-points, release["title"].casefold(), release["id"], points, release))
    hits.sort(key=lambda t: t[:3])
    page = hits[offset : offset + limit]
    created = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    if not wants_json(request):
        items = "".join(
            f'<release id={quoteattr(h[4]["id"])} ns2:score="{h[3]}">'
            f"<title>{escape(h[4]['title'])}</title></release>"
            for h in page
        )
        body = (
            f'<?xml version="1.0" encoding="UTF-8"?><metadata xmlns="{MMD}" '
            f'xmlns:ns2="http://musicbrainz.org/ns/ext#-2.0" created="{created}">'
            f'<release-list count="{len(hits)}" offset="{offset}">{items}</release-list></metadata>'
        )
        return Response(body, media_type="application/xml")
    return JSONResponse(
        {
            "created": created,
            "count": len(hits),
            "offset": offset,
            "releases": [release_hit(h[4], h[3]) for h in page],
        }
    )


def lookup(fake: FakeProviders, request: Request, raw_id: str) -> Response:
    release_id = mbid(raw_id)
    if release_id is None:
        return error(request, 400, "Invalid mbid.")
    includes = set(re.split(r"[+\s]+", request.query_params.get("inc", "").strip())) - {""}
    unknown = includes - INCLUDES
    if unknown:
        name = sorted(unknown)[0]
        return error(request, 400, f"{name} is not a valid inc parameter for the release resource.")
    release = fake.data.releases.get(release_id)
    if release is None:
        return error(request, 404, "Not Found")
    if not wants_json(request):
        body = (
            f'<?xml version="1.0" encoding="UTF-8"?><metadata xmlns="{MMD}">'
            f"<release id={quoteattr(release['id'])}><title>{escape(release['title'])}</title>"
            f"</release></metadata>"
        )
        return Response(body, media_type="application/xml")
    return JSONResponse(release_lookup(release, includes))


def _user_agent_ok(request: Request) -> bool:
    return bool(USER_AGENT.match(request.headers.get("user-agent", "")))


async def api(fake: FakeProviders, request: Request, path: str) -> Response:
    """`musicbrainz.org`: `/ws/2/release/{mbid}` en `/ws/2/release/?query=…`."""
    if not _user_agent_ok(request):
        return error(
            request,
            403,
            "Your requests are being blocked: please use a meaningful User-Agent with contact "
            "information (Name/version ( contact )).",
        )
    if fake.over_rate("musicbrainz", fake.config.musicbrainz_rate) is not None:
        return error(request, 503, RATE_MESSAGE)
    if request.method not in ("GET", "HEAD"):
        return error(request, 405, "Method not allowed")
    match path.rstrip("/").split("/"):
        case ["", "ws", "2", "release"]:
            return search(fake, request)
        case ["", "ws", "2", "release", raw_id]:
            return lookup(fake, request, raw_id)
    return error(request, 404, "Not Found")


async def caa(fake: FakeProviders, request: Request, path: str) -> Response:
    """`coverartarchive.org`: `/release/{mbid}/front[-250|-500|-1200]` geeft een 307."""
    match path.split("/"):
        case ["", "release", raw_id, size] if size in THUMBS:
            pass
        case _:
            return Response("Not Found", status_code=404, media_type="text/plain")
    release_id = mbid(raw_id)
    if release_id is None:
        return Response(f"{raw_id} is not a valid MBID", status_code=400, media_type="text/plain")
    release = fake.data.releases.get(release_id)
    if release is None or not release.get("cover"):
        message = f"No cover art found for release {release_id}"
        return Response(message, status_code=404, media_type="text/plain")
    suffix = THUMBS[size][0]
    base = fake.config.archive_url.rstrip("/")
    target = (
        f"{base}/download/mbid-{release_id}/mbid-{release_id}-{image_id(release_id)}{suffix}.jpg"
    )
    return Response(f"See: {target}", status_code=307, headers={"Location": target})


async def archive(fake: FakeProviders, request: Request, path: str) -> Response:
    """De tweede host: `/download/mbid-{mbid}/mbid-{mbid}-{id}[_thumbN].jpg` geeft de JPEG."""
    match path.split("/"):
        case ["", "download", folder, name]:
            pass
        case _:
            return Response("Not Found", status_code=404, media_type="text/plain")
    m = ARCHIVE_FILE.match(name)
    if m is None or folder != f"mbid-{m.group(1)}":
        return Response("Not Found", status_code=404, media_type="text/plain")
    release = fake.data.releases.get(m.group(1))
    if release is None or not release.get("cover") or int(m.group(2)) != image_id(m.group(1)):
        return Response("Not Found", status_code=404, media_type="text/plain")
    width = {"_thumb250": 250, "_thumb500": 500, "_thumb1200": 1200}.get(m.group(3) or "")
    body = fake.images.jpeg(f"caa/{release['id']}", "cover", width)
    return Response(body, media_type="image/jpeg")
