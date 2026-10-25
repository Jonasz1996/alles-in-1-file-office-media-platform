"""Het serveren van de web-app (P1): 404 voor ontbrekende bestanden, de SPA-terugval,
voorgecomprimeerde varianten, ETag/304, media-types en de CSP."""

import gzip
import os

import pytest

from app.main import WEB_CSP, accepted_codings, accepts

EXACT_CSP = (
    "default-src 'self'; img-src 'self' data: blob:; style-src 'self'; object-src 'none'; "
    "base-uri 'self'; frame-ancestors 'none'; manifest-src 'self'; worker-src 'self'"
)


@pytest.fixture
def dist(settings):
    root = settings.web_dist
    (root / "assets").mkdir(parents=True)
    (root / "icons").mkdir()
    (root / "index.html").write_text("<html>app</html>")
    js = "console.log('platform');" * 100
    (root / "assets" / "app-123.js").write_text(js)
    (root / "assets" / "app-123.js.gz").write_bytes(gzip.compress(js.encode()))
    (root / "assets" / "app-123.js.br").write_bytes(b"br-variant")
    (root / "sw.js").write_text("(function(){})();")
    (root / "manifest.webmanifest").write_text('{"name": "Platform"}')
    (root / "icons" / "icon-192.png").write_bytes(b"\x89PNG")
    return root


@pytest.mark.anyio
async def test_ontbrekende_bestanden_geven_404(client, dist):
    for path in ("/assets/x.js", "/assets/nope.js", "/foo.png", "/icons/weg.svg", "/a/b.css"):
        r = await client.get(path)
        assert r.status_code == 404, path
        assert r.json()["detail"]["code"] == "not_found"
    assert (await client.get("/api")).status_code == 404
    assert (await client.get("/api/v1/bestaat-niet")).status_code == 404


@pytest.mark.anyio
async def test_spa_terugval(client, dist):
    for path in (
        "/",
        "/map/0b5e5f8e-2f9a-4a8e-9c47-2b2f8a1b3c4d",
        "/fotos",
        "/s/abc_DEF-123",
        "/beheer/gebruikers",
    ):
        r = await client.get(path)
        assert r.status_code == 200, path
        assert r.text == "<html>app</html>"
        assert r.headers["content-type"].startswith("text/html")
        assert r.headers["cache-control"] == "no-cache"


@pytest.mark.anyio
async def test_br_en_gzip_naar_accept_encoding(client, dist):
    url = "/assets/app-123.js"
    r = await client.get(url, headers={"Accept-Encoding": "gzip, deflate, br"})
    assert r.headers["content-encoding"] == "br"
    assert r.headers["vary"] == "Accept-Encoding"
    assert r.headers["content-type"].startswith("text/javascript")
    assert r.headers["cache-control"] == "public, max-age=31536000, immutable"

    r = await client.get(url, headers={"Accept-Encoding": "gzip"})
    assert r.headers["content-encoding"] == "gzip"
    assert r.text == (dist / "assets" / "app-123.js").read_text()  # httpx pakt gzip uit

    # q=0 betekent: niet; dan gzip, of zonder codering
    r = await client.get(url, headers={"Accept-Encoding": "br;q=0, gzip;q=0.5"})
    assert r.headers["content-encoding"] == "gzip"
    r = await client.get(url, headers={"Accept-Encoding": "br;q=0, gzip;q=0"})
    assert "content-encoding" not in r.headers
    assert r.headers["vary"] == "Accept-Encoding"
    assert r.text.startswith("console.log")
    r = await client.get(url, headers={"Accept-Encoding": "identity"})
    assert "content-encoding" not in r.headers
    # Hogere q wint, ook als dat gzip is
    r = await client.get(url, headers={"Accept-Encoding": "br;q=0.2, gzip;q=0.9"})
    assert r.headers["content-encoding"] == "gzip"
    r = await client.get(url, headers={"Accept-Encoding": "*"})
    assert r.headers["content-encoding"] == "br"


@pytest.mark.anyio
async def test_oude_variant_wordt_niet_geserveerd(client, dist):
    js = dist / "assets" / "app-123.js"
    later = js.stat().st_mtime_ns + 5_000_000_000
    os.utime(js, ns=(later, later))  # het origineel is na de varianten veranderd
    r = await client.get("/assets/app-123.js", headers={"Accept-Encoding": "br, gzip"})
    assert "content-encoding" not in r.headers
    assert r.text.startswith("console.log")


@pytest.mark.anyio
async def test_etag_en_304(client, dist):
    r = await client.get("/sw.js")
    assert r.status_code == 200
    assert r.headers["cache-control"] == "no-cache"
    assert r.headers["content-type"].startswith("text/javascript")
    etag = r.headers["etag"]
    st = (dist / "sw.js").stat()
    assert etag == f'W/"{st.st_mtime_ns}-{st.st_size}"'

    r = await client.get("/sw.js", headers={"If-None-Match": etag})
    assert r.status_code == 304
    assert r.content == b""
    assert r.headers["etag"] == etag
    assert r.headers["content-security-policy"] == EXACT_CSP
    # Zwak vergelijken, ook in een lijst
    r = await client.get("/sw.js", headers={"If-None-Match": f'"x", {etag.removeprefix("W/")}'})
    assert r.status_code == 304
    r = await client.get("/sw.js", headers={"If-None-Match": 'W/"1-2"'})
    assert r.status_code == 200

    # index.html via een diepe link ook
    r = await client.get("/map/abc")
    r2 = await client.get("/fotos", headers={"If-None-Match": r.headers["etag"]})
    assert r2.status_code == 304

    # Een variant heeft zijn eigen ETag
    plain = await client.get("/assets/app-123.js", headers={"Accept-Encoding": "identity"})
    br = await client.get("/assets/app-123.js", headers={"Accept-Encoding": "br"})
    assert plain.headers["etag"] != br.headers["etag"]
    r = await client.get(
        "/assets/app-123.js",
        headers={"Accept-Encoding": "br", "If-None-Match": br.headers["etag"]},
    )
    assert r.status_code == 304
    assert r.headers["content-encoding"] == "br"


@pytest.mark.anyio
async def test_media_types(client, dist):
    r = await client.get("/manifest.webmanifest")
    assert r.headers["content-type"].startswith("application/manifest+json")
    r = await client.get("/icons/icon-192.png")
    assert r.headers["content-type"] == "image/png"
    r = await client.head("/sw.js")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/javascript")


@pytest.mark.anyio
async def test_exacte_csp_en_beveiligingsheaders(client, dist):
    assert WEB_CSP == EXACT_CSP
    for path in ("/", "/assets/app-123.js", "/sw.js", "/manifest.webmanifest"):
        r = await client.get(path)
        assert r.headers["content-security-policy"] == EXACT_CSP, path
        assert "unsafe-inline" not in r.headers["content-security-policy"]
        assert r.headers["permissions-policy"] == "camera=(), microphone=(), geolocation=()"
        assert r.headers["x-content-type-options"] == "nosniff"
    # De API krijgt de CSP van de web-app niet, wel de andere headers
    r = await client.get("/health")
    assert "content-security-policy" not in r.headers
    assert r.headers["permissions-policy"] == "camera=(), microphone=(), geolocation=()"


def test_accept_encoding_lezen():
    assert accepted_codings("gzip, br;q=0.5") == {"gzip": 1.0, "br": 0.5}
    assert accepted_codings("BR ; q=0 , *;q=0.1") == {"br": 0.0, "*": 0.1}
    assert accepted_codings("gzip;q=x") == {"gzip": 0.0}
    assert accepted_codings("") == {}
    codings = accepted_codings("br;q=0, *;q=0.3")
    assert accepts(codings, "br") == 0.0
    assert accepts(codings, "gzip") == 0.3
    assert accepts({}, "gzip") == 0.0


def test_nginx_conf_heeft_dezelfde_headers():
    """De losse web-image (web/nginx.conf) zet dezelfde CSP en beveiligingsheaders als de API,
    in elke location (add_header in een location vervangt die van daarboven)."""
    import re
    from pathlib import Path

    from app.main import SECURITY_HEADERS

    conf = (Path(__file__).resolve().parents[2] / "web" / "nginx.conf").read_text()
    locations = len(re.findall(r"^\s*location ", conf, re.M))
    assert locations >= 4
    assert conf.count(f'add_header Content-Security-Policy "{EXACT_CSP}" always;') == locations
    for name, value in SECURITY_HEADERS:
        header = "-".join(p.capitalize() for p in name.decode().split("-"))
        line = f'add_header {header} "{value.decode()}" always;'
        assert conf.count(line) == locations, line
    assert "gzip_static on;" in conf
    assert "try_files $uri =404;" in conf
