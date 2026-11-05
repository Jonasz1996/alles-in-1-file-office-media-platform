"""Play met de echte M5: de ondertitels, sprites en hoofdstukken (golf 2 van fase 5, bij het
samenvoegen van M4 en M5).

Play (M3) riep M5 aan zoals de stubs van M0 dat toelieten: `subtitles.tracks(probe)` zonder het
gekozen audiospoor, de losse ondertitels altijd met staat `pending`, en de hoofdstukken zoals de
probe ze gaf. Nu (m5.md, "Open"):

- het geforceerde tekstspoor volgt het audiospoor dat play koos (Keuze 29), niet het
  standaardspoor;
- een los bestand `x<i>` krijgt de staat uit de cache van M5 (`ready`, `failed`, `pending`);
- de hoofdstukken komen uit `chapters.items` (op volgorde, binnen de duur, zonder lege), net als
  `credits_ms`;
- `trick` gebruikt de instellingen van het verzoek.

De stroom zelf (streams.open, M4) is hier nagebootst: het gaat om de lijst van play.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import update

from app.main import app
from app.media import sessions, streams, subtitles, trick
from app.media.state import MediaState
from app.media.types import Chapter, Plan, StreamInfo
from app.models import MediaProbe
from tests.test_media_sessions import (
    library_item,
    maker,
    play,
    put,
    root,
    store_probe,
    video,
)


@pytest.fixture
def media(monkeypatch) -> list[Plan]:
    """Media aan, een gezonde worker, en een nagebootste stroom (M4)."""
    sessions.reset()
    monkeypatch.setattr(app.state, "media", MediaState(enabled=True, worker="ok"), raising=False)
    opened: list[Plan] = []

    async def open_stream(session, plan):
        opened.append(plan)
        return StreamInfo(
            mode=plan.mode, url="master.m3u8", map_url="map.json", duration_ms=5000,
            mime='video/mp4; codecs="avc1.64001f,mp4a.40.2"', offset_ms=-1000,
        )  # fmt: skip

    monkeypatch.setattr(streams, "open", open_stream)
    yield opened
    sessions.reset()


def subs_of(out: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {t["id"]: t for t in out["tracks"]["subtitles"]}


@pytest.mark.anyio
async def test_het_geforceerde_spoor_volgt_de_gekozen_audio(client, admin, settings, media):
    """multi_audio.mkv: audio nld (standaard) en eng, en een geforceerd SRT-spoor in het nld.
    Met nld (of zonder keuze) staat het aan, met eng niet; een eigen keuze wint altijd."""
    headers, _ = admin
    node = await video(client, headers, settings, "twee.mkv", "multi_audio.mkv", index=True)
    kw = {"fixture": "multi_audio.mkv"}
    first = await play(client, headers, node["id"], **kw)
    assert subs_of(first)["e3"]["default"] is True
    nld = await play(client, headers, node["id"], audio=1, replace=first["sid"], **kw)
    assert subs_of(nld)["e3"]["default"] is True
    eng = await play(client, headers, node["id"], audio=2, replace=nld["sid"], **kw)
    assert subs_of(eng)["e3"]["default"] is False
    assert [a["index"] for a in eng["tracks"]["audio"] if a["default"]] == [2]
    chosen = await play(
        client, headers, node["id"], audio=2, subtitle="e3", replace=eng["sid"], **kw
    )
    assert subs_of(chosen)["e3"]["default"] is True
    assert {p.mode for p in media} == {"remux"}


@pytest.mark.anyio
async def test_een_los_bestand_krijgt_de_staat_uit_de_cache(client, admin, settings, media):
    """x<i>: pending zolang M5 hem niet maakte, ready als file.vtt er is, failed na een blijvende
    fout. Een ingebed spoor e<i> deed dat al (subtitles.tracks met de instellingen)."""
    headers, _ = admin
    me = (await client.get("/api/v1/auth/me", headers=headers)).json()
    top = (await root(client, headers))["folder"]["id"]
    node = await video(client, headers, settings, "film.mp4")
    nl = await put(client, headers, top, "film.nl.srt", b"1\n00:00:01,000 --> 00:00:02,000\nA\n")
    en = await put(client, headers, top, "film.en.srt", b"1\n00:00:01,000 --> 00:00:02,000\nB\n")
    nl, en = nl.json(), en.json()
    await library_item(
        settings, me["id"], node, top,
        [{"node": nl["id"], "lang": "nld"}, {"node": en["id"], "lang": "eng"}],
    )  # fmt: skip
    out = await play(client, headers, node["id"])
    assert {i: t["state"] for i, t in subs_of(out).items()} == {"x0": "pending", "x1": "pending"}
    s = sessions.table().by_sid[out["sid"]]
    ready, gone = sessions.external(s, "x0")["sha"], sessions.external(s, "x1")["sha"]
    folder = subtitles.subs_dir(settings, ready)
    folder.mkdir(parents=True)
    (folder / "file.vtt").write_bytes(b"WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nA\n")
    failed = subtitles.subs_dir(settings, gone)
    failed.mkdir(parents=True)
    (failed / "file.failed").write_text("unreadable")
    again = await play(client, headers, node["id"], replace=out["sid"])
    assert {i: t["state"] for i, t in subs_of(again).items()} == {"x0": "ready", "x1": "failed"}


@pytest.mark.anyio
async def test_hoofdstukken_uit_chapters_items(client, admin, settings, media):
    """De hoofdstukken van play zijn die van chapters.items: op volgorde, binnen de duur, een
    titel van hooguit 200 tekens, zonder hoofdstukken van 0 ms. credits_ms komt uit dezelfde
    lijst (een "Aftiteling" in de tweede helft)."""
    headers, _ = admin
    node = await video(client, headers, settings, "met-hoofdstukken.mp4")
    raw = [
        Chapter(start_ms=6000, end_ms=9000, title="Aftiteling"),
        Chapter(start_ms=0, end_ms=3000, title="  Begin  "),
        Chapter(start_ms=3000, end_ms=3000, title="Leeg"),
        Chapter(start_ms=3000, end_ms=6000, title="x" * 300),
    ]
    await store_probe(settings, node["sha"], "h264_aac.mp4", chapters=raw, duration_ms=8000)
    out = await play(client, headers, node["id"])
    assert [(c["start_ms"], c["end_ms"], c["title"]) for c in out["chapters"]] == [
        (0, 3000, "Begin"),
        (3000, 6000, "x" * 200),
        (6000, 8000, "Aftiteling"),
    ]
    assert out["credits_ms"] == 6000


@pytest.mark.anyio
async def test_trick_met_de_instellingen_van_het_verzoek(client, admin, settings, media):
    """play geeft trick als de sprites er echt staan, in de map van de instellingen van dit
    verzoek (trick.info met settings)."""
    headers, _ = admin
    node = await video(client, headers, settings, "sprites.mp4")
    data = {"interval": 10, "w": 160, "h": 90, "cols": 10, "rows": 10, "count": 3, "tiles": 1}
    async with maker(settings)() as session:
        await session.execute(
            update(MediaProbe)
            .where(MediaProbe.blob_sha == node["sha"])
            .values(trick_state="ready", trick=data)
        )
        await session.commit()
    assert (await play(client, headers, node["id"]))["trick"] is None  # nog geen trick.vtt
    path = trick.vtt_path(settings, node["sha"])
    path.parent.mkdir(parents=True)
    path.write_bytes(b"WEBVTT\n")
    out = await play(client, headers, node["id"])
    assert out["trick"] == {"url": "trick.vtt", "interval_s": 10.0, "width": 160, "height": 90}
