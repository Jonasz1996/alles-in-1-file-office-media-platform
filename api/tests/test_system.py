import pytest


@pytest.mark.anyio
async def test_health(client):
    r = await client.get("/health")
    assert r.json() == {"status": "ok"}


@pytest.mark.anyio
async def test_info(client):
    r = await client.get("/api/v1/info")
    assert r.json()["mode"] == "standalone"


@pytest.mark.anyio
async def test_openapi(client):
    r = await client.get("/api/openapi.json")
    assert r.json()["info"]["title"] == "Platform API"


@pytest.mark.anyio
async def test_web_app_wordt_geserveerd(client, settings):
    (settings.web_dist / "assets").mkdir(parents=True)
    (settings.web_dist / "index.html").write_text("<html>app</html>")
    (settings.web_dist / "assets" / "a.js").write_text("js")
    r = await client.get("/beheer")
    assert r.text == "<html>app</html>"
    r = await client.get("/assets/a.js")
    assert "immutable" in r.headers["cache-control"]
    assert (await client.get("/../../etc/passwd")).text == "<html>app</html>"
    assert (await client.get("/api/v1/bestaat-niet")).status_code == 404


@pytest.mark.anyio
async def test_beveiligingsheaders(client, settings):
    r = await client.get("/health")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
    settings.web_dist.mkdir(parents=True)
    (settings.web_dist / "index.html").write_text("<html>app</html>")
    r = await client.head("/")
    assert r.status_code == 200
    csp = r.headers["content-security-policy"]
    # media-src 'self' blob: sinds fase 5 (F4)
    assert csp == (
        "default-src 'self'; img-src 'self' data: blob:; media-src 'self' blob:; "
        "style-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'; "
        "manifest-src 'self'; worker-src 'self'"
    )
    assert "unsafe-inline" not in csp
    assert r.headers["permissions-policy"] == "camera=(), microphone=(), geolocation=()"
