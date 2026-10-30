"""De toelating bij een wissel en bij de kijkers van een link, via de echte play (na-controle
golf 2).

- Een publieke link heeft hooguit 2 sessies, en de derde sluit de oudste (M3, §2 M3). Bij remux
  gold sinds de review van golf 2 ook 1 per link in de toelating: de tweede kijker van een mkv
  kreeg 503 `media_busy` (reden `link`), en de derde ook, in plaats van dat de oudste dichtging.
- `replace` (een ander audiospoor, een lagere trede) sluit de oude sessie in dezelfde beurt, "zodat
  een wissel niet tegen de grens loopt" (§2 M3). De toelating keek vóór dat sluiten, en telde de
  plaats van de oude sessie dus nog mee: een wissel van de ene remux via een link, of van de ene
  transcode via een link (`public_transcode`), gaf 503.

De producent is de nep van test_media_public.py (`remux`: streams.open geeft een StreamInfo); de
schijf heeft ruim plaats, zodat de echte vrije ruimte hier niet meetelt.
"""

# De fixtures komen uit test_media_public.py en heten in de tests net zo
# ruff: noqa: F811

from __future__ import annotations

import pytest

from app.config import MediaConfig
from app.main import app
from app.media import admission, analysis
from app.media.state import MediaPolicy
from tests.media.media_contract import failed
from tests.test_files import put
from tests.test_media_decide import synthetic
from tests.test_media_public import (  # noqa: F401
    clock,
    films,
    media,
    public_play,
    remux,
)
from tests.test_media_sessions import blob_of, content, file, maker, session_of, store_probe

pytestmark = pytest.mark.anyio


@pytest.fixture(autouse=True)
def room(monkeypatch):
    monkeypatch.setattr(admission, "disk_free", lambda path=None: admission.DISK_MIN * 4)


async def _serie(settings, films) -> str:
    serie = films["serie"]
    await store_probe(settings, serie["sha"], "vp9_opus.mkv", index=True)
    return serie["id"]


async def test_twee_kijkers_van_een_link_spelen_een_mkv(client, films, settings, remux, clock):
    node = await _serie(settings, films)
    sids = []
    for _ in range(3):
        out = await public_play(client, films["key"], node, fixture="vp9_opus.mkv")
        assert out["mode"] == "remux"
        sids.append(out["sid"])
        clock.advance(1)
    # de derde sluit de oudste (2 per link), zoals bij direct play
    failed(await file(client, sids[0], "HEAD"), 403, "media_access_gone", "stopped")
    assert [mode for _, mode in remux] == ["remux"] * 3
    links = [g for g in admission.snapshot().grants if g.link_id is not None]
    assert len(links) <= 3  # de oudste geeft zijn plaats op de achtergrond terug


async def test_een_wissel_via_een_link_bij_remux(client, films, settings, remux):
    node = await _serie(settings, films)
    first = await public_play(client, films["key"], node, fixture="vp9_opus.mkv")
    again = await public_play(
        client, films["key"], node, fixture="vp9_opus.mkv", replace=first["sid"]
    )
    assert again["mode"] == "remux" and again["sid"] != first["sid"]
    failed(await file(client, first["sid"], "HEAD"), 410, "media_session_gone")


async def test_een_wissel_via_een_link_bij_transcode(client, films, settings, monkeypatch, remux):
    """1 transcode per link (Keuze 34): de wissel van die ene transcode mag wel."""
    monkeypatch.setattr(
        app.state, "media_policy", MediaPolicy(MediaConfig(public_transcode=True)), raising=False
    )
    data = content("groot.mkv")
    node = (await put(client, films["headers"], films["folder"], "groot.mkv", data)).json()
    sha = await blob_of(settings, node["id"])
    big = synthetic("mpeg4", "mp4v.20.9", 1920, 1080, container="matroska")
    big.keyframes_state, big.max_gop_ms = "ready", 2000
    await analysis.save_probe(maker(settings), sha, big)
    first = await public_play(client, films["key"], node["id"], fixture=None)
    assert first["mode"] == "transcode"
    rung = session_of(first["sid"]).plan.video
    # zonder replace: een tweede transcode via dezelfde link mag niet (zoals het ontwerp zegt)
    out = await public_play(client, films["key"], node["id"], fixture=None, expect=503)
    assert out["detail"]["params"]["reason"] == "link"
    # met replace wel, en op dezelfde trede: de oude telt niet meer mee
    again = await public_play(client, films["key"], node["id"], fixture=None, replace=first["sid"])
    assert again["mode"] == "transcode"
    assert session_of(again["sid"]).plan.video == rung
    failed(await file(client, first["sid"], "HEAD"), 410, "media_session_gone")
