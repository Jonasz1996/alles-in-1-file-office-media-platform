"""Prowlarr als bronnenbrug (keuze van Jonas, 17-11-2026).

Prowlarr draait in een eigen container naast de app. De beheerder voegt daar de torrentsites
toe; de app vraagt Prowlarr alleen welke er zijn (`indexers`, GET `/api/v1/indexer` met de kop
`X-Api-Key`) en zoekt daarna per site via het Torznab-adres dat Prowlarr ervoor geeft
(`torznab_base(base, id)` = `<base>/<id>/api`, met dezelfde API-sleutel). Zo blijft het zoeken
in `torznab.py`, dat ook zonder Prowlarr werkt.

Alleen sites die aan staan en torrents doen (geen usenet, keuze van 16-11-2026). Fouten zijn
`ProwlarrError` met een vaste reden; de sleutel komt nooit in een fout of log.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx

TIMEOUT = httpx.Timeout(15.0, connect=5.0)
MAX_BODY = 4 * 1024 * 1024


class ProwlarrError(Exception):
    """Een vaste reden, nooit de sleutel."""


@dataclass(frozen=True)
class Indexer:
    id: int
    name: str
    privacy: str  # public, semiPrivate, private


def check_base(base: str) -> str:
    parts = urlsplit(base)
    if parts.scheme not in ("http", "https") or not parts.hostname or parts.query:
        raise ProwlarrError("ongeldig adres van Prowlarr")
    return base.rstrip("/")


def torznab_base(base: str, indexer_id: int) -> str:
    return f"{check_base(base)}/{int(indexer_id)}/api"


async def indexers(client: httpx.AsyncClient, base: str, api_key: str) -> list[Indexer]:
    url = check_base(base) + "/api/v1/indexer"
    try:
        resp = await client.get(
            url, headers={"X-Api-Key": api_key}, timeout=TIMEOUT, follow_redirects=False
        )
    except httpx.TimeoutException as exc:
        raise ProwlarrError("Prowlarr antwoordt niet op tijd") from exc
    except httpx.HTTPError as exc:
        raise ProwlarrError("Prowlarr is niet bereikbaar") from exc
    if resp.status_code in (401, 403):
        raise ProwlarrError("Prowlarr weigert de API-sleutel")
    if resp.status_code != 200:
        raise ProwlarrError(f"Prowlarr antwoordt {resp.status_code}")
    if len(resp.content) > MAX_BODY:
        raise ProwlarrError("antwoord van Prowlarr te groot")
    try:
        raw = resp.json()
    except ValueError as exc:
        raise ProwlarrError("Prowlarr geeft geen geldige lijst") from exc
    if not isinstance(raw, list):
        raise ProwlarrError("Prowlarr geeft geen geldige lijst")
    found: list[Indexer] = []
    for item in raw:
        if not isinstance(item, dict) or not item.get("enable", False):
            continue
        if str(item.get("protocol", "")).lower() != "torrent":
            continue
        try:
            found.append(
                Indexer(
                    id=int(item["id"]),
                    name=str(item.get("name", ""))[:200],
                    privacy=str(item.get("privacy", "")),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return sorted(found, key=lambda i: i.name.lower())
