"""De gids lezen (XMLTV, zoals iptv-org/epg hem maakt: guide.xml of guide.xml.gz).

    <tv>
      <channel id="een.be"><display-name>Eén</display-name></channel>
      <programme start="20261126190000 +0100" stop="20261126200000 +0100" channel="een.be">
        <title lang="nl">Het Journaal</title><sub-title>…</sub-title><desc>…</desc>
        <category>Nieuws</category><episode-num system="onscreen">S2 E5</episode-num>
      </programme>
    </tv>

Lezen gaat met iterparse en elk element gaat meteen weer weg: een gids van 100 MB past zo in
weinig geheugen. Alleen de kanalen die in de lijst staan (ids) en alleen wat in het venster valt
(een dag terug tot WINDOW_DAYS vooruit). Een tekst in de taal van de voorkeur (nl, dan en) wint;
anders de eerste. Een ongeldige tijd: dat programma slaan we over.
"""

from __future__ import annotations

import datetime as dt
import gzip
import re
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

WINDOW_DAYS = 8
LANGS = ("nl", "nl-be", "vl", "en")
_TIME = re.compile(r"^(\d{14})(?:\s*([+-]\d{4}))?")


@dataclass(frozen=True)
class Programme:
    channel: str
    start: dt.datetime
    stop: dt.datetime
    title: str
    subtitle: str | None = None
    description: str | None = None
    category: str | None = None
    episode: str | None = None


class GuideError(ValueError):
    """Geen XMLTV (vaste reden)."""


def parse_time(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    m = _TIME.match(value.strip())
    if not m:
        return None
    try:
        t = dt.datetime.strptime(m.group(1), "%Y%m%d%H%M%S")
    except ValueError:
        return None
    off = m.group(2)
    if off:
        sign = 1 if off[0] == "+" else -1
        delta = dt.timedelta(hours=int(off[1:3]), minutes=int(off[3:5]))
        return (t - sign * delta).replace(tzinfo=dt.UTC)
    return t.replace(tzinfo=dt.UTC)


def _text(elem: ET.Element, tag: str, limit: int) -> str | None:
    best: str | None = None
    rank = len(LANGS)
    for child in elem.findall(tag):
        value = " ".join((child.text or "").split())
        if not value:
            continue
        lang = (child.get("lang") or "").lower()
        r = LANGS.index(lang) if lang in LANGS else len(LANGS)
        if best is None or r < rank:
            best, rank = value, r
    return best[:limit] if best else None


def _episode(elem: ET.Element) -> str | None:
    onscreen = None
    ns = None
    for child in elem.findall("episode-num"):
        value = (child.text or "").strip()
        system = (child.get("system") or "").lower()
        if system == "onscreen" and value:
            onscreen = value
        elif system == "xmltv_ns" and value:
            ns = value
    if onscreen:
        return onscreen[:100]
    if ns:
        parts = [p.split("/")[0].strip() for p in ns.split(".")]
        out = []
        if len(parts) > 0 and parts[0].isdigit():
            out.append(f"S{int(parts[0]) + 1}")
        if len(parts) > 1 and parts[1].isdigit():
            out.append(f"E{int(parts[1]) + 1}")
        return " ".join(out) or None
    return None


def _open(path: Path):
    with open(path, "rb") as f:
        magic = f.read(2)
    if magic == b"\x1f\x8b":
        return gzip.open(path, "rb")
    return open(path, "rb")


def read(path: Path, ids: set[str], now: dt.datetime | None = None) -> Iterator[Programme]:
    """De programma's van de kanalen in ids uit het bestand, in de volgorde van de gids."""
    now = now or dt.datetime.now(dt.UTC)
    low = now - dt.timedelta(days=1)
    high = now + dt.timedelta(days=WINDOW_DAYS)
    root: ET.Element | None = None
    with _open(path) as f:
        try:
            for event, elem in ET.iterparse(f, events=("start", "end")):
                if event == "start":
                    if root is None:
                        if elem.tag != "tv":
                            raise GuideError("dit is geen XMLTV (geen <tv>)")
                        root = elem
                    continue
                if elem.tag == "channel":
                    root.clear()  # type: ignore[union-attr]
                    continue
                if elem.tag != "programme":
                    continue
                channel = elem.get("channel") or ""
                if channel in ids:
                    start = parse_time(elem.get("start"))
                    stop = parse_time(elem.get("stop"))
                    title = _text(elem, "title", 500)
                    if start and title:
                        if stop is None or stop <= start:
                            stop = start + dt.timedelta(minutes=30)
                        if stop >= low and start <= high:
                            yield Programme(
                                channel=channel[:200],
                                start=start,
                                stop=stop,
                                title=title,
                                subtitle=_text(elem, "sub-title", 500),
                                description=_text(elem, "desc", 4000),
                                category=_text(elem, "category", 200),
                                episode=_episode(elem),
                            )
                root.clear()  # type: ignore[union-attr]
        except ET.ParseError as exc:
            raise GuideError(f"de gids is geen geldige XML (regel {exc.position[0]})") from None
    if root is None:
        raise GuideError("de gids is leeg")
