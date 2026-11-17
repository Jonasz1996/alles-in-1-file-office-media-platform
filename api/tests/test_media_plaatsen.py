"""Play en de previews delen de plaatsen "kort" van de worker (m4.md, open punt; Keuze 12).

Besluit van M10 (in de geest van het ontwerp): play krijgt geen eigen plaats en geen voorrang.
Zitten de twee plaatsen "kort" vol (de previews van een map die net geüpload is), dan krijgt de
probe van play `busy` van de worker, en antwoordt play 503 media_busy {reason: worker} met
Retry-After (§3.4, de tabel van de fouten). De speler (M6, player/session.ts) wacht dan en vraagt
het opnieuw; een preview duurt seconden. Zo blijft de worker de enige die zijn plaatsen telt, en
verandert er niets aan M0.
"""

from __future__ import annotations

import pytest

from app.media import admission
from app.media.wire import Op
from tests.media.media_contract import failed
from tests.test_files import put, root
from tests.test_media_contract import media_app  # noqa: F401
from tests.test_media_sessions import content, play, profile_json

pytestmark = pytest.mark.anyio


async def test_play_wacht_op_een_plaats_kort(media_app, client, admin):  # noqa: F811
    settings, fake = media_app
    headers, _ = admin
    data = content("kort.mp4")
    fake.register(data, "h264_aac.mp4")
    parent = (await root(client, headers))["folder"]["id"]
    node = (await put(client, headers, parent, "kort.mp4", data)).json()
    assert admission.snapshot().in_use == 0
    # de twee plaatsen "kort" zitten vol (de previews): de probe van play krijgt busy
    fake.fault("busy", op=Op.PROBE)
    r = await client.post(
        f"/api/v1/media/{node['id']}/play", json={"profile": profile_json()}, headers=headers
    )
    params = failed(r, 503, "media_busy", "worker")
    assert int(r.headers["retry-after"]) >= 1 and params["retry_after"] >= 1
    # na Retry-After is er weer plaats: play lukt
    out = await play(client, headers, node["id"])
    assert out["mode"] == "direct"
