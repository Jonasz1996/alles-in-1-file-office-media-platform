"""Afspelen via een publieke link (M3, Keuze 34): direct en remux mogen, transcode niet (403),
behalve met public_transcode (dan hooguit 720); 2 sessies per link; play hooguit 30 per minuut
per IP; een verlopen link, een ander wachtwoord of een link die weg is sluit de sessie (403); een
bestand buiten de link is 404, net als bij /public/download."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.config import MediaConfig
from app.main import app
from app.media import admission, analysis, keyframes, ladder, sessions, streams
from app.media import public as media_public
from app.media.state import MediaPolicy, MediaState
from app.media.types import Grant, Rung, StreamInfo
from app.routers import public_media
from tests.media.media_contract import failed
from tests.test_files import mkdir, put, root
from tests.test_media_decide import budget, keuze24, synthetic
from tests.test_media_sessions import (
    Clock,
    blob_of,
    content,
    file,
    maker,
    profile_json,
    session_of,
    store_probe,
    video,
)
from tests.test_public import bearer, fresh_limits, new_link, open_link, set_expiry  # noqa: F401
from tests.test_shares import person

PLAY = "/api/v1/public/media/{}/play"


@pytest.fixture
def clock(monkeypatch) -> Clock:
    c = Clock()
    monkeypatch.setattr(sessions, "clock", c)
    return c


@pytest.fixture(autouse=True)
def media(monkeypatch) -> MediaState:
    """Zoals in test_media_sessions.py: media aan, de worker gezond, lege tabel en limieten."""
    sessions.reset()
    public_media.plays.reset()
    state = MediaState(enabled=True, worker="ok")
    monkeypatch.setattr(app.state, "media", state, raising=False)
    monkeypatch.setattr(app.state, "media_policy", MediaPolicy(MediaConfig()), raising=False)
    yield state
    sessions.reset()
    public_media.plays.reset()


async def public_play(
    client, key: str, node_id: str, *, browser: str = "chrome_ci",
    fixture: str | None = "h264_aac.mp4", expect: int = 200, **extra,
) -> dict:  # fmt: skip
    body = {"profile": profile_json(browser, fixture), **extra}
    r = await client.post(PLAY.format(node_id), json=body, headers=bearer(key))
    assert r.status_code == expect, r.text
    return r.json()


@pytest.fixture
async def films(client, admin, settings):
    """De beheerder met /Films/{film.mp4, serie.mkv} en /prive.mp4, en een leeslink op /Films."""
    headers, tokens = admin
    top = (await root(client, headers))["folder"]["id"]
    folder = await mkdir(client, headers, top, "Films")
    film = await video(client, headers, settings, "film.mp4", parent=folder)
    serie = await video(client, headers, settings, "serie.mkv", "vp9_opus.mkv", parent=folder)
    prive = await video(client, headers, settings, "prive.mp4", parent=top)
    link = await new_link(client, headers, folder, permission="read")
    key = (await open_link(client, link))["key"]
    return {"headers": headers, "folder": folder, "film": film, "serie": serie, "prive": prive,
            "link": link, "key": key, "owner_id": tokens["user"]["id"]}  # fmt: skip


@pytest.fixture
def remux(monkeypatch):
    """Een producent van M4 in plaats van de stub: open geeft een StreamInfo."""
    opened: list[tuple[sessions.Session, str]] = []

    async def open_stream(session, plan):
        opened.append((session, plan.mode))
        return StreamInfo(
            mode=plan.mode,
            url="master.m3u8",
            map_url="map.json",
            mime='video/mp4; codecs="vp09.00.31.08,opus"',
            duration_ms=5000,
            offset_ms=-1000,
        )

    monkeypatch.setattr(streams, "open", open_stream)
    return opened


# --- wat mag ------------------------------------------------------------------------------------


@pytest.mark.anyio
async def test_direct_via_een_link(client, films):
    out = await public_play(client, films["key"], films["film"]["id"])
    assert out["mode"] == "direct" and out["urls"]["file"] == "file"
    s = session_of(out["sid"])
    assert s.public and s.link_id == uuid.UUID(films["link"]["id"])
    assert s.user_id == uuid.UUID(films["owner_id"]) and s.family is None
    r = await file(client, out["sid"])
    assert r.status_code == 200 and r.content == films["film"]["data"]
    r = await file(client, out["sid"], range="bytes=10-19")
    assert r.status_code == 206 and r.content == films["film"]["data"][10:20]
    # geen sleutel: 401, net als de rest van /public
    r = await client.post(PLAY.format(films["film"]["id"]), json={"profile": profile_json()})
    assert r.status_code == 401
    # de sessie van een link staat in de lijst zonder sid
    view = next(v for v in sessions.list() if v.id == s.id)
    assert view.link_id == s.link_id and "sid" not in view.model_dump()


@pytest.mark.anyio
async def test_remux_via_een_link(client, films, settings, remux):
    serie = films["serie"]
    await store_probe(settings, serie["sha"], "vp9_opus.mkv", index=True)
    out = await public_play(client, films["key"], serie["id"], fixture="vp9_opus.mkv")
    assert out["mode"] == "remux" and out["reasons"] == ["container"]
    assert out["urls"] == {"file": None, "map": "map.json", "master": "master.m3u8", "audio": None}
    assert out["offset"] == -1.0
    assert [mode for _, mode in remux] == ["remux"]
    # /file is alleen voor direct play
    r = await file(client, out["sid"])
    failed(r, 404, "not_found")


@pytest.mark.anyio
async def test_een_mkv_zonder_index_wacht_op_de_keyframes(client, films, settings, remux):
    """Zonder index is het transcode, en dat mag niet: 409 media_probe_pending (keyframes),
    zodat de speler het na de analyse opnieuw vraagt. Leest play de index meteen (Keuze 17),
    dan speelt hij als remux."""
    serie = films["serie"]
    out = await public_play(client, films["key"], serie["id"], fixture="vp9_opus.mkv", expect=409)
    assert out["detail"]["code"] == "media_probe_pending"
    assert out["detail"]["params"]["reason"] == "keyframes"
    assert remux == []

    async def read_now(sha):
        await store_probe(settings, sha, "vp9_opus.mkv", index=True)
        return "ready"

    from pytest import MonkeyPatch

    with MonkeyPatch.context() as patch:
        patch.setattr(keyframes, "read_now", read_now)
        out = await public_play(client, films["key"], serie["id"], fixture="vp9_opus.mkv")
    assert out["mode"] == "remux"


# --- wat niet mag --------------------------------------------------------------------------------


@pytest.mark.anyio
async def test_transcode_geeft_403(client, films, settings):
    for name, fixture in (("hdr.mkv", "hevc10_hdr10.mkv"), ("tv.ts", "interlaced.ts")):
        node = await video(
            client, films["headers"], settings, name, fixture, parent=films["folder"], index=True
        )
        r = await client.post(
            PLAY.format(node["id"]),
            json={"profile": profile_json("chrome_ci", fixture)},
            headers=bearer(films["key"]),
        )
        failed(r, 403, "media_public_transcode")
    # een andere kwaliteit vragen is ook omzetten
    r = await client.post(
        PLAY.format(films["film"]["id"]),
        json={"profile": profile_json(), "quality": 480},
        headers=bearer(films["key"]),
    )
    failed(r, 403, "media_public_transcode")


@pytest.mark.anyio
async def test_met_public_transcode_hooguit_720(client, films, settings, monkeypatch, remux):
    monkeypatch.setattr(
        app.state, "media_policy", MediaPolicy(MediaConfig(public_transcode=True)), raising=False
    )
    try:
        ladder.cost(ladder.ORDER[0], hdr=False)
    except NotImplementedError:
        monkeypatch.setattr(ladder, "cost", keuze24)
    asked: list[tuple] = []

    def view(user_id, link_id):
        asked.append((user_id, link_id))
        return budget(link_free=1)

    def reserve(plan, user_id, link_id):
        return Grant(user_id=user_id, link_id=link_id, mode=plan.mode, cost=plan.cost, places=1)

    monkeypatch.setattr(admission, "view", view)
    monkeypatch.setattr(admission, "reserve", reserve)
    data = content("groot.mkv")
    node = (await put(client, films["headers"], films["folder"], "groot.mkv", data)).json()
    sha = await blob_of(settings, node["id"])
    big = synthetic("mpeg4", "mp4v.20.9", 1920, 1080, container="matroska")
    big.keyframes_state, big.max_gop_ms = "ready", 2000
    await analysis.save_probe(maker(settings), sha, big)
    out = await public_play(client, films["key"], node["id"], fixture=None)
    assert out["mode"] == "transcode"
    s = session_of(out["sid"])
    assert isinstance(s.plan.video, Rung) and s.plan.video.height <= 720
    assert asked == [(uuid.UUID(films["owner_id"]), uuid.UUID(films["link"]["id"]))]
    assert all(option == "auto" or option == "source" or option <= 720
               for option in out["quality"]["options"])  # fmt: skip
    # ook met de vraag om 1080: niet boven 720
    out = await public_play(client, films["key"], node["id"], fixture=None, quality=1080)
    assert session_of(out["sid"]).plan.video.height <= 720


@pytest.mark.anyio
async def test_een_bestand_buiten_de_link(client, films):
    key = films["key"]
    for node_id in (films["prive"]["id"], films["folder"], str(uuid.uuid4())):
        r = await client.post(
            PLAY.format(node_id), json={"profile": profile_json()}, headers=bearer(key)
        )
        failed(r, 404, "not_found")


@pytest.mark.anyio
async def test_een_link_naar_een_bestand(client, films):
    link = await new_link(client, films["headers"], films["film"]["id"], permission="read")
    key = (await open_link(client, link))["key"]
    out = await public_play(client, key, films["film"]["id"])
    assert out["mode"] == "direct"
    await public_play(client, key, films["prive"]["id"], expect=404)


@pytest.mark.anyio
async def test_een_uploadlink_speelt_niet(client, films):
    link = await new_link(client, films["headers"], films["folder"], permission="upload")
    key = (await open_link(client, link))["key"]
    out = await public_play(client, key, films["film"]["id"], expect=403)
    assert out["detail"]["code"] == "upload_only"


@pytest.mark.anyio
async def test_media_uit_via_een_link(client, films, media):
    media.enabled = False
    out = await public_play(client, films["key"], films["film"]["id"], expect=503)
    assert out["detail"]["code"] == "media_off"


# --- de grenzen ---------------------------------------------------------------------------------


@pytest.mark.anyio
async def test_2_per_link_de_oudste_stopt(client, films, clock):
    sids = []
    for _ in range(3):
        sids.append((await public_play(client, films["key"], films["film"]["id"]))["sid"])
        clock.advance(1)
    failed(await file(client, sids[0], "HEAD"), 403, "media_access_gone", "stopped")
    for sid in sids[1:]:
        assert (await file(client, sid, "HEAD")).status_code == 200
    # een tweede link heeft zijn eigen 2
    other = await new_link(client, films["headers"], films["folder"], permission="read")
    key = (await open_link(client, other))["key"]
    extra = (await public_play(client, key, films["film"]["id"]))["sid"]
    for sid in [*sids[1:], extra]:
        assert (await file(client, sid, "HEAD")).status_code == 200
    # en de eigenaar telt los: zijn eigen sessies duwen de link niet weg
    r = await client.post(
        f"/api/v1/media/{films['film']['id']}/play",
        json={"profile": profile_json()},
        headers=films["headers"],
    )
    assert r.status_code == 200, r.text
    assert (await file(client, sids[1], "HEAD")).status_code == 200


@pytest.mark.anyio
async def test_play_hooguit_30_per_minuut_per_ip(client, films):
    key = films["key"]
    missing = str(uuid.uuid4())
    for _ in range(media_public.PUBLIC_PLAYS_PER_MINUTE):
        r = await client.post(
            PLAY.format(missing), json={"profile": profile_json()}, headers=bearer(key)
        )
        assert r.status_code == 404, r.text
    r = await client.post(
        PLAY.format(films["film"]["id"]), json={"profile": profile_json()}, headers=bearer(key)
    )
    failed(r, 429, "rate_limited")
    assert int(r.headers["retry-after"]) >= 1
    # de rest van de link (bekijken, downloaden) telt niet mee
    r = await client.get("/api/v1/public/children", headers=bearer(key))
    assert r.status_code == 200


# --- intrekken ----------------------------------------------------------------------------------


@pytest.mark.anyio
async def test_een_verlopen_link_sluit_de_sessie(client, films, settings, clock):
    out = await public_play(client, films["key"], films["film"]["id"])
    await set_expiry(settings, films["link"]["id"], datetime.now(UTC) - timedelta(minutes=1))
    clock.advance(10)
    assert (await file(client, out["sid"], "HEAD")).status_code == 200  # de cache van 15 s
    clock.advance(6)
    failed(await file(client, out["sid"], "HEAD"), 403, "media_access_gone", "revoked")
    failed(await file(client, out["sid"]), 403, "media_access_gone", "revoked")  # de grafsteen
    out = await public_play(client, films["key"], films["film"]["id"], expect=410)
    assert out["detail"]["code"] == "link_expired"


@pytest.mark.anyio
async def test_een_ander_wachtwoord_sluit_de_sessie(client, films, clock):
    headers = films["headers"]
    link = await new_link(client, headers, films["folder"], permission="read", password="appel1")
    key = (await open_link(client, link, password="appel1"))["key"]
    out = await public_play(client, key, films["film"]["id"])
    assert session_of(out["sid"]).link_print is not None
    r = await client.patch(f"/api/v1/shares/{link['id']}", json={"label": "Nieuw"}, headers=headers)
    assert r.status_code == 200
    clock.advance(16)
    assert (await file(client, out["sid"], "HEAD")).status_code == 200  # een label raakt niets
    r = await client.patch(
        f"/api/v1/shares/{link['id']}", json={"password": "peer12"}, headers=headers
    )
    assert r.status_code == 200
    clock.advance(16)
    failed(await file(client, out["sid"], "HEAD"), 403, "media_access_gone", "revoked")
    # de oude sleutel: 401, zoals de rest van /public
    r = await client.post(
        PLAY.format(films["film"]["id"]), json={"profile": profile_json()}, headers=bearer(key)
    )
    failed(r, 401, "key_expired")
    key = (await open_link(client, link, password="peer12"))["key"]
    assert (await public_play(client, key, films["film"]["id"]))["mode"] == "direct"


@pytest.mark.anyio
async def test_een_link_die_weg_is(client, films, clock):
    out = await public_play(client, films["key"], films["film"]["id"])
    r = await client.delete(f"/api/v1/shares/{films['link']['id']}", headers=films["headers"])
    assert r.status_code == 204
    clock.advance(16)
    failed(await file(client, out["sid"], "HEAD"), 403, "media_access_gone", "revoked")
    out = await public_play(client, films["key"], films["film"]["id"], expect=404)
    assert out["detail"]["code"] == "link_not_found"


@pytest.mark.anyio
async def test_een_bestand_dat_de_link_uit_gaat(client, films, settings, clock):
    """Verplaatst naar buiten de gedeelde map of in de prullenbak: de sessie stopt."""
    headers = films["headers"]
    out = await public_play(client, films["key"], films["film"]["id"])
    top = (await root(client, headers))["folder"]["id"]
    r = await client.patch(
        f"/api/v1/files/{films['film']['id']}", json={"parent_id": top}, headers=headers
    )
    assert r.status_code == 200, r.text
    clock.advance(16)
    failed(await file(client, out["sid"], "HEAD"), 403, "media_access_gone", "revoked")
    # in de prullenbak: meteen (files.notify)
    clip = await video(client, headers, settings, "clip.webm", "vp9_opus.webm",
                       parent=films["folder"])  # fmt: skip
    out = await public_play(client, films["key"], clip["id"], fixture="vp9_opus.webm")
    assert out["mode"] == "direct"
    r = await client.delete(f"/api/v1/files/{clip['id']}", headers=headers)
    assert r.status_code == 204
    failed(await file(client, out["sid"], "HEAD"), 403, "media_access_gone", "revoked")
    # een nieuwe versie: 410 replaced
    other = await video(client, headers, settings, "ander.webm", "vp9_opus.webm",
                        parent=films["folder"])  # fmt: skip
    out = await public_play(client, films["key"], other["id"], fixture="vp9_opus.webm")
    new = content("nieuwer.webm")
    r = await client.put(f"/api/v1/files/{other['id']}/content", content=new, headers=headers)
    assert r.status_code == 200, r.text
    failed(await file(client, out["sid"], "HEAD"), 410, "media_session_gone", "replaced")


@pytest.mark.anyio
async def test_de_eigenaar_geblokkeerd(client, admin, settings, clock):
    """De link van Bert: blokkeert de beheerder Bert, dan stopt de sessie van de link; uitloggen
    van Bert raakt de link niet (de sessie hoort bij de link, niet bij een login)."""
    admin_headers, _ = admin
    bert, bert_id = await person(client, admin_headers, "bert@example.nl")
    r = await client.post(
        "/api/v1/auth/login", json={"email": "bert@example.nl", "password": "lang-genoeg-123"}
    )
    bert_tokens = r.json()
    top = (await root(client, bert))["folder"]["id"]
    node = await video(client, bert, settings, "bert.mp4", parent=top)
    link = await new_link(client, bert, node["id"], permission="read")
    key = (await open_link(client, link))["key"]
    out = await public_play(client, key, node["id"])
    r = await client.post(
        "/api/v1/auth/logout", json={"refresh_token": bert_tokens["refresh_token"]}
    )
    assert r.status_code == 204
    assert (await file(client, out["sid"], "HEAD")).status_code == 200
    r = await client.patch(
        f"/api/v1/users/{bert_id}", json={"disabled": True}, headers=admin_headers
    )
    assert r.status_code == 200, r.text
    failed(await file(client, out["sid"], "HEAD"), 403, "media_access_gone", "revoked")


@pytest.mark.anyio
async def test_ondertitels_buiten_de_link_staan_er_niet_in(client, films, settings):
    from tests.test_media_sessions import library_item

    headers = films["headers"]
    top = (await root(client, headers))["folder"]["id"]
    me = (await client.get("/api/v1/auth/me", headers=headers)).json()
    inside = (await put(client, headers, films["folder"], "film.nl.srt", b"1\n")).json()
    outside = (await put(client, headers, top, "film.en.srt", b"2\n")).json()
    subs = [{"node": outside["id"], "lang": "en"}, {"node": inside["id"], "lang": "nl"}]
    await library_item(settings, me["id"], films["film"], top, subs)
    out = await public_play(client, films["key"], films["film"]["id"])
    assert [t["lang"] for t in out["tracks"]["subtitles"]] == ["nl"]
    assert sessions.external(session_of(out["sid"]), "x0")["node"] == inside["id"]
    # de eigenaar zelf ziet ze allebei
    r = await client.post(
        f"/api/v1/media/{films['film']['id']}/play", json={"profile": profile_json()},
        headers=headers,
    )  # fmt: skip
    assert [t["lang"] for t in r.json()["tracks"]["subtitles"]] == ["en", "nl"]
