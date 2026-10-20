import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings, get_settings
from app.main import app, http_client

NODES = "node1=10.0.0.1,node2=10.0.0.2,node3=10.0.0.3"


def fake_cluster(down: set[str] = frozenset()):
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


async def make_client(down=frozenset(), token="geheim"):
    async def fake_http_client():
        async with httpx.AsyncClient(transport=httpx.MockTransport(fake_cluster(down))) as c:
            yield c

    app.dependency_overrides[get_settings] = lambda: Settings(
        cluster_nodes=NODES, admin_token=token
    )
    app.dependency_overrides[http_client] = fake_http_client
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.fixture(autouse=True)
def reset():
    yield
    app.dependency_overrides.clear()


@pytest.mark.anyio
async def test_status_vereist_token():
    async with await make_client() as c:
        assert (await c.get("/api/v1/admin/status")).status_code == 401
        r = await c.get("/api/v1/admin/status", headers={"Authorization": "Bearer fout"})
        assert r.status_code == 401


@pytest.mark.anyio
async def test_status_zonder_ingesteld_token():
    async with await make_client(token="") as c:
        r = await c.get("/api/v1/admin/status", headers={"Authorization": "Bearer "})
        assert r.status_code == 503


@pytest.mark.anyio
async def test_status_alles_gezond_op_disk_na():
    async with await make_client() as c:
        r = await c.get("/api/v1/admin/status", headers={"Authorization": "Bearer geheim"})
    body = r.json()
    assert r.status_code == 200
    assert body["status"] == "degraded"
    assert body["messages"] == ["node3: datadisk meer dan 80 % vol."]
    assert [n["db_role"] for n in body["nodes"]] == ["primary", "replica", "replica"]
    assert {m["role"] for m in body["database"]} == {"leader", "sync_standby", "replica"}


@pytest.mark.anyio
async def test_status_na_uitval_van_node1():
    async with await make_client(down={"10.0.0.1"}) as c:
        r = await c.get("/api/v1/admin/status", headers={"Authorization": "Bearer geheim"})
    body = r.json()
    assert body["status"] == "degraded"
    assert body["nodes"][0]["checks"] == dict.fromkeys(
        ["etcd", "postgres", "seaweed_master", "seaweed_volume", "traefik", "api"], False
    )
    assert any(m["role"] == "leader" and m["name"] == "node2" for m in body["database"])
    assert any("node1:" in m for m in body["messages"])


@pytest.mark.anyio
async def test_status_zonder_meerderheid():
    async with await make_client(down={"10.0.0.1", "10.0.0.2"}) as c:
        r = await c.get("/api/v1/admin/status", headers={"Authorization": "Bearer geheim"})
    body = r.json()
    assert body["status"] == "down"
    assert any("meerderheid" in m for m in body["messages"])


def test_nodes_parsen():
    s = Settings(cluster_nodes=" node1=10.0.0.1, node2=10.0.0.2 ")
    assert s.nodes() == [("node1", "10.0.0.1"), ("node2", "10.0.0.2")]
    assert Settings(node_name="n1").nodes() == [("n1", "127.0.0.1")]


@pytest.mark.anyio
async def test_single_node(monkeypatch):
    async def tcp_ok(ip, port):
        return True

    monkeypatch.setattr("app.cluster._tcp_ok", tcp_ok)

    async def fake_http_client():
        async with httpx.AsyncClient(transport=httpx.MockTransport(fake_cluster())) as c:
            yield c

    app.dependency_overrides[get_settings] = lambda: Settings(
        mode="single", node_name="node1", cluster_nodes="node1=10.0.0.1", admin_token="geheim"
    )
    app.dependency_overrides[http_client] = fake_http_client
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/api/v1/admin/status", headers={"Authorization": "Bearer geheim"})
    body = r.json()
    assert body["status"] == "ok"
    assert "etcd" not in body["nodes"][0]["checks"]
    assert body["nodes"][0]["db_role"] == "primary"
