import httpx
import pytest

from app.cluster import cluster_status

NODES = [("node1", "10.0.0.1"), ("node2", "10.0.0.2"), ("node3", "10.0.0.3")]


def fake_cluster(down: frozenset[str] = frozenset()):
    """Nep-antwoorden van etcd, Patroni, SeaweedFS en Traefik; `down` = onbereikbare IP's."""

    def handler(request: httpx.Request) -> httpx.Response:
        ip, port, path = request.url.host, request.url.port, request.url.path
        if ip in down:
            raise httpx.ConnectError("onbereikbaar", request=request)
        primary = "10.0.0.2" if "10.0.0.1" in down else "10.0.0.1"
        match port, path:
            case 2379, "/health":
                return httpx.Response(200, json={"health": "true"})
            case 8008, "/patroni":
                role = "primary" if ip == primary else "replica"
                return httpx.Response(200, json={"state": "running", "role": role})
            case 8008, "/cluster":
                members = [
                    {
                        "name": f"node{i}",
                        "role": "leader"
                        if f"10.0.0.{i}" == primary
                        else ("sync_standby" if i == 3 else "replica"),
                        "state": "running",
                        "lag": 0,
                    }
                    for i in (1, 2, 3)
                    if f"10.0.0.{i}" not in down
                ]
                return httpx.Response(200, json={"members": members})
            case 9333, "/cluster/status":
                return httpx.Response(200, json={"IsLeader": ip == primary})
            case 8081, "/status":
                return httpx.Response(
                    200,
                    json={
                        "DiskStatuses": [
                            {"all": 100, "used": 85 if ip == "10.0.0.3" else 10, "free": 15}
                        ]
                    },
                )
            case 8082, "/ping":
                return httpx.Response(200, text="OK")
            case 8000, "/health":
                return httpx.Response(200, json={"status": "ok"})
        return httpx.Response(404)

    return handler


async def status(down=frozenset()):
    async with httpx.AsyncClient(transport=httpx.MockTransport(fake_cluster(down))) as c:
        return await cluster_status(c, NODES)


@pytest.mark.anyio
async def test_gezond_op_disk_na():
    s = await status()
    assert s.status == "degraded"
    assert s.messages == ["node3: datadisk meer dan 80 % vol."]
    assert [n.db_role for n in s.nodes] == ["primary", "replica", "replica"]


@pytest.mark.anyio
async def test_uitval_van_node1():
    s = await status(frozenset({"10.0.0.1"}))
    assert s.status == "degraded"
    assert not any(s.nodes[0].checks.values())
    assert any(m.role == "leader" and m.name == "node2" for m in s.database)


@pytest.mark.anyio
async def test_geen_meerderheid():
    s = await status(frozenset({"10.0.0.1", "10.0.0.2"}))
    assert s.status == "down"
    assert any("meerderheid" in m for m in s.messages)
