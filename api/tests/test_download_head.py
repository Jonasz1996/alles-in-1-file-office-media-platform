"""F2 (fase 5, §3.5): HEAD op de drie downloadroutes.

Voor de reparatie kende alleen GET de routes; een HEAD viel door naar de catch-all van de
web-app, en die geeft 404 voor alles onder /api/ (spike S12). Nu geeft HEAD dezelfde koppen als
GET (Accept-Ranges, Content-Length, ETag, het soort), zonder body, en met Range een 206.
"""

import pytest

from tests.test_files import put, root
from tests.test_public import new_link, open_link

DATA = b"0123456789" * 1000


async def routes(client, headers) -> tuple[dict, list[tuple[str, dict]]]:
    """De node en de drie routes: de downloadlink, /content met Bearer, de publieke link."""
    root_id = (await root(client, headers))["folder"]["id"]
    node = (await put(client, headers, root_id, "film.bin", DATA)).json()
    link = (await client.post(f"/api/v1/files/{node['id']}/download-link", headers=headers)).json()
    shared = await new_link(client, headers, node["id"], permission="read")
    key = (await open_link(client, shared))["key"]
    return node, [
        (link["url"], {}),
        (f"/api/v1/files/{node['id']}/content", headers),
        (f"/api/v1/public/download/{node['id']}?key={key}", {}),
    ]


@pytest.mark.anyio
async def test_head_geeft_de_koppen_zonder_body(client, admin):
    headers, _ = admin
    node, found = await routes(client, headers)
    for url, extra in found:
        get = await client.get(url, headers=extra)
        head = await client.head(url, headers=extra)
        assert head.status_code == 200, (url, head.status_code, head.text)
        assert head.content == b""
        assert head.headers["content-length"] == str(len(DATA))
        assert head.headers["accept-ranges"] == "bytes"
        assert head.headers["etag"] == get.headers["etag"] == node["etag"]
        assert head.headers["content-type"] == get.headers["content-type"]
        assert head.headers["content-disposition"] == get.headers["content-disposition"]


@pytest.mark.anyio
async def test_head_met_range_en_if_none_match(client, admin):
    headers, _ = admin
    node, found = await routes(client, headers)
    for url, extra in found:
        part = await client.head(url, headers={**extra, "Range": "bytes=10-19"})
        assert part.status_code == 206, url
        assert part.headers["content-range"] == f"bytes 10-19/{len(DATA)}"
        assert part.headers["content-length"] == "10"
        assert part.content == b""
        same = await client.head(url, headers={**extra, "If-None-Match": node["etag"]})
        assert same.status_code == 304, url


@pytest.mark.anyio
async def test_head_controleert_de_toegang_net_als_get(client, admin):
    headers, _ = admin
    node, _found = await routes(client, headers)
    assert (await client.head(f"/api/v1/download/{node['id']}?token=onzin")).status_code == 401
    assert (await client.head(f"/api/v1/files/{node['id']}/content")).status_code == 401
    assert (await client.head(f"/api/v1/public/download/{node['id']}?key=onzin")).status_code in (
        401,
        404,
    )
