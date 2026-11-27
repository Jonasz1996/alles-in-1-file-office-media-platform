"""GPX en TCX lezen naar een lijst punten.

Namespaces tellen niet: alleen de lokale naam van een element (trkpt, ele, time, hr, ...), zodat
GPX 1.0, 1.1 en de extensies van Garmin, Suunto, Wahoo en Strava allemaal werken. ElementTree
(expat) haalt geen externe entiteiten op en weert de "billion laughs" (expat 2.4+).

Een bestand zonder één punt met een plek geeft ParseError (een binnenrit op de rollers zonder GPS
kan het platform nog niet tonen).
"""

from __future__ import annotations

import io
import math
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import UTC, datetime

# Groter dan dit leest het platform niet (ook niet uit een zip): 64 MB XML is een rit van dagen
MAX_BYTES = 64 * 1024 * 1024
MAX_POINTS = 500_000


class ParseError(ValueError):
    """Geen GPX of TCX, of zonder punten met een plek."""


@dataclass(slots=True)
class Point:
    lat: float
    lon: float
    ele: float | None = None
    time: datetime | None = None
    hr: float | None = None
    cad: float | None = None
    power: float | None = None
    # Een nieuw stuk (trkseg in GPX, Lap/Track in TCX): de pauzeknop
    seg_start: bool = False


@dataclass
class Parsed:
    points: list[Point]
    name: str | None = None
    # Wat het bestand zelf over de sport zegt (type in GPX, Sport in TCX), ongewijzigd
    sport_hint: str | None = None
    fmt: str = "gpx"
    description: str | None = None
    extra: dict = field(default_factory=dict)


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _num(text: str | None) -> float | None:
    if text is None:
        return None
    try:
        v = float(text.strip())
    except ValueError:
        return None
    return v if math.isfinite(v) else None


def parse_time(text: str | None) -> datetime | None:
    """ISO 8601 zoals in GPX en TCX ("2024-05-01T07:03:12Z", met of zonder fracties of offset).
    Zonder zone: UTC (zo schrijven alle apparaten het)."""
    if not text:
        return None
    s = text.strip()
    if s.endswith("Z") or s.endswith("z"):
        s = s[:-1] + "+00:00"
    try:
        t = datetime.fromisoformat(s)
    except ValueError:
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=UTC)
    return t.astimezone(UTC)


def _text(el: ET.Element) -> str | None:
    return el.text.strip() if el.text and el.text.strip() else None


HR = {"hr", "heartrate", "heart_rate"}
CAD = {"cad", "cadence", "runcadence"}
POWER = {"power", "watts", "pwr"}


def _extras(pt: Point, el: ET.Element) -> None:
    """hr, cad en power uit wat er onder het punt hangt (extensions, ook genest)."""
    for sub in el.iter():
        name = _local(sub.tag)
        if name in HR and pt.hr is None:
            pt.hr = _num(sub.text)
        elif name in CAD and pt.cad is None:
            pt.cad = _num(sub.text)
        elif name in POWER and pt.power is None:
            pt.power = _num(sub.text)


def _gpx(root: ET.Element) -> Parsed:
    points: list[Point] = []
    name = None
    desc = None
    hint = None
    for top in root:
        tn = _local(top.tag)
        if tn == "metadata":
            for m in top:
                if _local(m.tag) == "name" and name is None:
                    name = _text(m)
        elif tn in ("trk", "rte"):
            for child in top:
                cn = _local(child.tag)
                if cn == "name" and _text(child):
                    name = _text(child)
                elif cn == "desc" and desc is None:
                    desc = _text(child)
                elif cn == "type" and hint is None:
                    hint = _text(child)
                elif cn == "trkseg":
                    first = True
                    for p in child:
                        if _local(p.tag) != "trkpt":
                            continue
                        pt = _gpx_point(p)
                        if pt is None:
                            continue
                        pt.seg_start = first and bool(points)
                        first = False
                        points.append(pt)
                        if len(points) > MAX_POINTS:
                            raise ParseError("te veel punten")
                elif cn == "rtept":
                    pt = _gpx_point(child)
                    if pt is not None:
                        points.append(pt)
        elif tn == "wpt":
            continue
    return Parsed(points=points, name=name, sport_hint=hint, fmt="gpx", description=desc)


def _gpx_point(p: ET.Element) -> Point | None:
    lat = _num(p.get("lat"))
    lon = _num(p.get("lon"))
    if lat is None or lon is None or not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    pt = Point(lat=lat, lon=lon)
    for c in p:
        cn = _local(c.tag)
        if cn == "ele":
            pt.ele = _num(c.text)
        elif cn == "time":
            pt.time = parse_time(c.text)
        elif cn == "extensions":
            _extras(pt, c)
    return pt


def _tcx(root: ET.Element) -> Parsed:
    points: list[Point] = []
    hint = None
    name = None
    for act in root.iter():
        if _local(act.tag) != "activity":
            continue
        hint = hint or act.get("Sport")
        for el in act.iter():
            ln = _local(el.tag)
            if ln == "notes" and name is None:
                name = _text(el)
            if ln == "track":
                first = True
                for tp in el:
                    if _local(tp.tag) != "trackpoint":
                        continue
                    pt = _tcx_point(tp)
                    if pt is None:
                        continue
                    pt.seg_start = first and bool(points)
                    first = False
                    points.append(pt)
                    if len(points) > MAX_POINTS:
                        raise ParseError("te veel punten")
    return Parsed(points=points, name=name, sport_hint=hint, fmt="tcx")


def _tcx_point(tp: ET.Element) -> Point | None:
    lat = lon = ele = hr = cad = power = None
    when = None
    for c in tp:
        cn = _local(c.tag)
        if cn == "time":
            when = parse_time(c.text)
        elif cn == "position":
            for q in c:
                qn = _local(q.tag)
                if qn == "latitudedegrees":
                    lat = _num(q.text)
                elif qn == "longitudedegrees":
                    lon = _num(q.text)
        elif cn == "altitudemeters":
            ele = _num(c.text)
        elif cn == "heartratebpm":
            for q in c:
                if _local(q.tag) == "value":
                    hr = _num(q.text)
        elif cn == "cadence":
            cad = _num(c.text)
        elif cn == "extensions":
            for q in c.iter():
                qn = _local(q.tag)
                if qn == "watts":
                    power = _num(q.text)
                elif qn == "runcadence" and cad is None:
                    cad = _num(q.text)
    if lat is None or lon is None or not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    return Point(lat=lat, lon=lon, ele=ele, time=when, hr=hr, cad=cad, power=power)


def parse(data: bytes) -> Parsed:
    """GPX of TCX (op het wortelelement, niet op de naam van het bestand)."""
    if len(data) > MAX_BYTES:
        raise ParseError("te groot")
    if b"<!ENTITY" in data[:4096] or b"<!DOCTYPE" in data[:4096]:
        # Een GPX heeft nooit een DTD; zo kan er ook geen entiteit in
        raise ParseError("DTD in het bestand")
    try:
        root = ET.parse(io.BytesIO(data)).getroot()
    except ET.ParseError as e:
        raise ParseError("geen geldige XML") from e
    kind = _local(root.tag)
    if kind == "gpx":
        out = _gpx(root)
    elif kind == "trainingcenterdatabase":
        out = _tcx(root)
    else:
        raise ParseError("geen GPX of TCX")
    if not out.points:
        raise ParseError("geen punten met een plek")
    # Punten zonder tijd tussen punten met tijd: de volgorde blijft, de tijd blijft leeg
    return out
