"""Systeemstatus van het cluster, rechtstreeks uit de API's van de onderdelen.

Elke node wordt parallel bevraagd; een onderdeel dat niet binnen de timeout
antwoordt, telt als onbereikbaar. Er is geen aparte monitoringtool nodig.
"""

import asyncio
from typing import Literal

import httpx
from pydantic import BaseModel

TIMEOUT = httpx.Timeout(2.0)

# Poorten zoals ze in infra/ansible/roles/platform/templates/compose.yaml.j2 staan
PORTS = {
    "etcd": 2379,
    "patroni": 8008,
    "seaweed_master": 9333,
    "seaweed_volume": 8081,
    "traefik": 8082,
    "api": 8000,
}


class Disk(BaseModel):
    total_bytes: int
    used_bytes: int
    free_bytes: int


class NodeStatus(BaseModel):
    name: str
    ip: str
    checks: dict[str, bool]
    db_role: str | None = None
    seaweed_leader: bool = False
    disk: Disk | None = None


class DbMember(BaseModel):
    name: str
    role: str
    state: str
    lag_bytes: int | None = None


class Notice(BaseModel):
    """Een melding op /beheer: een vaste code met params (de web-app vertaalt op code) en de
    Nederlandse tekst. messages in ClusterStatus zijn dezelfde teksten (compatibel)."""

    code: str
    params: dict[str, str | int | float] = {}
    text: str


def notice(code: str, text: str, **params: str | int | float) -> Notice:
    return Notice(code=code, params=params, text=text)


# Een datadisk is vol vanaf zoveel procent
DISK_FULL = 80


def disk_notice(node: str, disk: Disk | None) -> Notice | None:
    if disk and disk.total_bytes and disk.used_bytes / disk.total_bytes > DISK_FULL / 100:
        text = f"{node}: datadisk meer dan {DISK_FULL} % vol."
        return notice("disk_full", text, node=node, percent=DISK_FULL)
    return None


class ClusterStatus(BaseModel):
    status: Literal["ok", "degraded", "down"]
    nodes: list[NodeStatus]
    database: list[DbMember]
    messages: list[str]
    notices: list[Notice] = []


async def _get_json(client: httpx.AsyncClient, url: str) -> dict | None:
    try:
        r = await client.get(url, timeout=TIMEOUT)
    except httpx.HTTPError:
        return None
    if r.status_code >= 500:
        return None
    try:
        return r.json()
    except ValueError:
        return {} if r.is_success else None


async def _ok(client: httpx.AsyncClient, url: str) -> bool:
    try:
        r = await client.get(url, timeout=TIMEOUT)
    except httpx.HTTPError:
        return False
    return r.is_success


def _disk(volume: dict | None) -> Disk | None:
    if not volume or not volume.get("DiskStatuses"):
        return None
    d = volume["DiskStatuses"][0]
    return Disk(
        total_bytes=d.get("all", 0), used_bytes=d.get("used", 0), free_bytes=d.get("free", 0)
    )


async def node_status(client: httpx.AsyncClient, name: str, ip: str) -> NodeStatus:
    base = {k: f"http://{ip}:{p}" for k, p in PORTS.items()}
    etcd, patroni, master, volume, traefik, api = await asyncio.gather(
        _get_json(client, f"{base['etcd']}/health"),
        _get_json(client, f"{base['patroni']}/patroni"),
        _get_json(client, f"{base['seaweed_master']}/cluster/status"),
        _get_json(client, f"{base['seaweed_volume']}/status"),
        _ok(client, f"{base['traefik']}/ping"),
        _ok(client, f"{base['api']}/health"),
    )
    return NodeStatus(
        name=name,
        ip=ip,
        checks={
            "etcd": bool(etcd) and str(etcd.get("health")).lower() == "true",
            "postgres": bool(patroni) and patroni.get("state") in ("running", "streaming"),
            "seaweed_master": master is not None,
            "seaweed_volume": volume is not None,
            "traefik": traefik,
            "api": api,
        },
        db_role=patroni.get("role") if patroni else None,
        seaweed_leader=bool(master and master.get("IsLeader")),
        disk=_disk(volume),
    )


async def database_members(
    client: httpx.AsyncClient, nodes: list[tuple[str, str]]
) -> list[DbMember]:
    for _, ip in nodes:
        data = await _get_json(client, f"http://{ip}:{PORTS['patroni']}/cluster")
        if data and "members" in data:
            return [
                DbMember(
                    name=m.get("name", "?"),
                    role=m.get("role", "?"),
                    state=m.get("state", "?"),
                    lag_bytes=m["lag"] if isinstance(m.get("lag"), int) else None,
                )
                for m in data["members"]
            ]
    return []


def summarize(nodes: list[NodeStatus], members: list[DbMember]) -> tuple[str, list[Notice]]:
    """De status van het cluster en de meldingen erbij (code, params en tekst)."""
    notices: list[Notice] = []
    leaders = [m for m in members if m.role == "leader"]
    sync = [m for m in members if m.role in ("sync_standby", "quorum_standby")]
    etcd_up = sum(n.checks["etcd"] for n in nodes)

    if not leaders:
        notices.append(notice("db_no_primary", "De database heeft geen primary."))
    if leaders and not sync:
        notices.append(
            notice("db_no_sync", "Geen synchrone standby: schrijven wacht tot er een terug is.")
        )
    if etcd_up * 2 <= len(nodes):
        notices.append(
            notice("etcd_no_quorum", "etcd heeft geen meerderheid; failover is niet mogelijk.")
        )
    if not any(n.seaweed_leader for n in nodes):
        notices.append(notice("storage_no_leader", "SeaweedFS heeft geen leidende master."))
    for n in nodes:
        down = [k for k, ok in n.checks.items() if not ok]
        if down:
            parts = ", ".join(down)
            notices.append(
                notice("node_down", f"{n.name}: {parts} onbereikbaar.", node=n.name, parts=parts)
            )
        if full := disk_notice(n.name, n.disk):
            notices.append(full)

    if not leaders or etcd_up * 2 <= len(nodes):
        return "down", notices
    return ("degraded" if notices else "ok"), notices


async def cluster_status(client: httpx.AsyncClient, nodes: list[tuple[str, str]]) -> ClusterStatus:
    statuses, members = await asyncio.gather(
        asyncio.gather(*(node_status(client, name, ip) for name, ip in nodes)),
        database_members(client, nodes),
    )
    status, notices = summarize(list(statuses), members)
    return ClusterStatus(
        status=status,
        nodes=list(statuses),
        database=members,
        messages=[n.text for n in notices],
        notices=notices,
    )
