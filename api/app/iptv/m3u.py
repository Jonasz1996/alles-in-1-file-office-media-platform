"""Een m3u-kanaallijst lezen (extended M3U, zoals IPTV-aanbieders en iptv-org ze maken).

    #EXTM3U url-tvg="…"
    #EXTINF:-1 tvg-id="een.be" tvg-logo="https://…" tvg-chno="1" group-title="BE",Eén HD
    #EXTGRP:BE
    #EXTVLCOPT:http-user-agent=…
    http://aanbieder:8080/live/gebruiker/wachtwoord/123.ts

Alleen http en https: een ander adres (udp, rtmp, rtp) slaan we over en tellen we. De vaste
sleutel van een kanaal (key) is een hash van tvg-id en naam; staat dezelfde twee keer in de lijst,
dan krijgt de tweede een volgnummer erbij. Zo blijven favorieten staan als de aanbieder de
volgorde of de adressen verandert.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

MAX_CHANNELS = 20_000
_ATTR = re.compile(r'([A-Za-z0-9_-]+)="([^"]*)"')


@dataclass(frozen=True)
class Entry:
    key: str
    position: int
    name: str
    url: str
    tvg_id: str | None = None
    logo: str | None = None
    group: str | None = None
    number: int | None = None
    user_agent: str | None = None


@dataclass
class Playlist:
    entries: list[Entry] = field(default_factory=list)
    guide_url: str | None = None  # url-tvg / x-tvg-url in de kop
    skipped: int = 0  # geen http(s), of geen adres


class M3UError(ValueError):
    """Geen m3u (een vaste reden, nooit met een stuk van de inhoud: daar staan logins in)."""


def _clean(value: str | None, limit: int) -> str | None:
    if value is None:
        return None
    value = " ".join(value.split())
    return value[:limit] or None


def _split_extinf(rest: str) -> tuple[dict[str, str], str]:
    """De attributen en de naam (na de eerste komma buiten aanhalingstekens)."""
    quoted = False
    for i, ch in enumerate(rest):
        if ch == '"':
            quoted = not quoted
        elif ch == "," and not quoted:
            head, name = rest[:i], rest[i + 1 :]
            break
    else:
        head, name = rest, ""
    attrs = {k.lower(): v for k, v in _ATTR.findall(head)}
    return attrs, name.strip()


def channel_key(tvg_id: str | None, name: str) -> str:
    return hashlib.sha256(f"{tvg_id or ''}|{name}".encode()).hexdigest()[:32]


def _number(value: str | None) -> int | None:
    if value and value.strip().isdigit():
        n = int(value.strip())
        return n if 0 < n < 100_000 else None
    return None


def parse(text: str) -> Playlist:
    lines = text.lstrip("﻿").splitlines()
    if not lines or not lines[0].strip().upper().startswith("#EXTM3U"):
        raise M3UError("dit is geen m3u (de eerste regel is niet #EXTM3U)")
    out = Playlist()
    head = dict((k.lower(), v) for k, v in _ATTR.findall(lines[0]))
    guide = head.get("url-tvg") or head.get("x-tvg-url")
    if guide:
        first = guide.split(",")[0].strip()
        out.guide_url = first if first.startswith(("http://", "https://")) else None
    pending: tuple[dict[str, str], str] | None = None
    group: str | None = None
    agent: str | None = None
    seen: dict[str, int] = {}
    for raw in lines[1:]:
        line = raw.strip()
        if not line:
            continue
        upper = line.upper()
        if upper.startswith("#EXTINF:"):
            pending = _split_extinf(line[8:])
            group = None
            agent = None
            continue
        if upper.startswith("#EXTGRP:"):
            group = line[8:].strip() or None
            continue
        if upper.startswith("#EXTVLCOPT:"):
            opt = line[11:]
            if opt.lower().startswith("http-user-agent="):
                agent = opt.split("=", 1)[1].strip() or None
            continue
        if line.startswith("#"):
            continue
        if pending is None:
            out.skipped += 1
            continue
        attrs, title = pending
        pending = None
        if not line.lower().startswith(("http://", "https://")) or len(line) > 4000:
            out.skipped += 1
            continue
        name = _clean(title or attrs.get("tvg-name") or "", 200) or "?"
        tvg_id = _clean(attrs.get("tvg-id"), 200)
        key = channel_key(tvg_id, name)
        if key in seen:
            seen[key] += 1
            key = channel_key(tvg_id, f"{name}#{seen[key]}")
        else:
            seen[key] = 1
        logo = attrs.get("tvg-logo") or None
        if logo and (not logo.startswith(("http://", "https://")) or len(logo) > 1000):
            logo = None
        out.entries.append(
            Entry(
                key=key,
                position=len(out.entries),
                name=name,
                url=line,
                tvg_id=tvg_id,
                logo=logo,
                group=_clean(attrs.get("group-title") or group, 200),
                number=_number(attrs.get("tvg-chno") or attrs.get("channel-number")),
                user_agent=_clean(attrs.get("http-user-agent") or agent, 300),
            )
        )
        if len(out.entries) >= MAX_CHANNELS:
            break
    if not out.entries:
        raise M3UError("de m3u bevat geen kanalen met een http- of https-adres")
    return out
