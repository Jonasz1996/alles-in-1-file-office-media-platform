"""De sessies (Keuze 21, 29) via de echte routes: play, de guard op elke route onder
/api/v1/stream/, de grafsteen, intrekken (meteen bij uitloggen, blokkeren, 2FA en één login;
binnen 15 s bij een deling, met een nepklok), een nieuwe versie (410 replaced), on_discard, de
losse ondertitels, en de grenzen (8 per gebruiker, 12 uur, 30 minuten, replace, 64).

De probes zijn de opnames van M0 (tests/media/recorded/probe), in media_probe gezet; de inhoud
is gewone bytes (direct play stuurt ze alleen door). Zonder worker: direct play heeft hem niet
nodig. De helpers hier gebruiken test_media_public.py en test_media_direct.py ook.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import uuid
from typing import Any

import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import MediaConfig
from app.db import get_engine
from app.main import app
from app.media import analysis, hooks, keyframes, sessions, streams
from app.media import profile as P
from app.media.state import MediaPolicy, MediaState
from app.media.types import Plan, PlayOut, StreamInfo
from app.models import MediaItem, MediaLibrary, MediaProbe, Node
from app.routers import public_media
from tests.media.media_contract import failed
from tests.test_account import family
from tests.test_files import mkdir, put, root
from tests.test_media_decide import BROWSERS
from tests.test_media_decide import probe as recorded
from tests.test_media_routes_guarded import calls
from tests.test_shares import person, share_with

SID = re.compile(r"^[a-z0-9-]{1,63}\.[A-Za-z0-9_-]{32}$")
STREAM = "/api/v1/stream"


# --- de omgeving ----------------------------------------------------------------------------------


class Clock:
    """De nepklok van de sessies."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock(monkeypatch) -> Clock:
    c = Clock()
    monkeypatch.setattr(sessions, "clock", c)
    return c


@pytest.fixture(autouse=True)
def media(monkeypatch) -> MediaState:
    """Media aan, de worker gezond (caps leeg), lege tabel en limieten."""
    sessions.reset()
    public_media.plays.reset()
    state = MediaState(enabled=True, worker="ok")
    monkeypatch.setattr(app.state, "media", state, raising=False)
    monkeypatch.setattr(app.state, "media_policy", MediaPolicy(MediaConfig()), raising=False)
    yield state
    sessions.reset()
    public_media.plays.reset()


def maker(settings):
    return async_sessionmaker(get_engine(settings), expire_on_commit=False)


def content(name: str, size: int = 600_000) -> bytes:
    """Vaste bytes per naam (een eigen blob per bestand)."""
    seed = hashlib.sha256(name.encode()).digest()
    return (seed * (size // len(seed) + 1))[:size]


async def blob_of(settings, node_id: str) -> str:
    async with maker(settings)() as session:
        node = await session.get(Node, uuid.UUID(node_id))
        assert node is not None and node.blob_id
        return node.blob_id


async def store_probe(settings, sha: str, fixture: str, *, index: bool = False, **changes):
    """De opname van M0 als probe van deze inhoud; index: de keyframe-index staat er."""
    await analysis.save_probe(maker(settings), sha, recorded(fixture, **changes))
    if index:
        async with maker(settings)() as session:
            await session.execute(
                update(MediaProbe)
                .where(MediaProbe.blob_sha == sha)
                .values(keyframes_state="ready", max_gop_ms=2000)
            )
            await session.commit()


async def video(
    client, headers, settings, name: str = "film.mp4", fixture: str = "h264_aac.mp4", *,
    parent: str | None = None, data: bytes | None = None, index: bool = False,
) -> dict[str, Any]:  # fmt: skip
    """Een bestand met de probe van een fixture: de node plus sha en data."""
    parent = parent or (await root(client, headers))["folder"]["id"]
    data = content(name) if data is None else data
    r = await put(client, headers, parent, name, data)
    assert r.status_code == 201, r.text
    node = r.json()
    sha = await blob_of(settings, node["id"])
    await store_probe(settings, sha, fixture, index=index)
    return {**node, "sha": sha, "data": data}


def profile_json(browser: str = "chrome_ci", fixture: str | None = "h264_aac.mp4") -> dict:
    """Het profiel zoals de web-app het stuurt: de kandidaten plus de strings van het item,
    gemeten met het model van de browser (test_media_decide.py)."""
    base, client_name, model, rest = BROWSERS[browser]
    data: dict[str, Any] = {"v": 1, "base": base, "client": {"name": client_name}, **rest}
    if model is not None:
        strings = list(P.candidates())
        if fixture is not None:
            strings += [s.codec_string for s in recorded(fixture).streams if s.codec_string]
        data["codecs"] = {s: model(s).model_dump(exclude_none=True) for s in dict.fromkeys(strings)}
    return data


async def play(
    client, headers, node_id: str, *, browser: str = "chrome_ci",
    fixture: str | None = "h264_aac.mp4", expect: int = 200, **extra: Any,
) -> dict:  # fmt: skip
    body = {"profile": profile_json(browser, fixture), **extra}
    r = await client.post(f"/api/v1/media/{node_id}/play", json=body, headers=headers)
    assert r.status_code == expect, r.text
    return r.json()


async def file(client, sid: str, method: str = "GET", **headers: str):
    return await client.request(method, f"{STREAM}/{sid}/file", headers=headers)


def session_of(sid: str) -> sessions.Session:
    return sessions.table().by_sid[sid]


async def settle() -> None:
    """De taken op de achtergrond (sluiten, opnieuw controleren) laten aflopen."""
    for _ in range(5):
        await asyncio.sleep(0)
    pending = [t for t in sessions._tasks if not t.done()]
    if pending:
        await asyncio.wait_for(asyncio.gather(*pending, return_exceptions=True), 10)


@pytest.fixture
async def own(client, admin, settings):
    """De beheerder met één video: (headers, tokens, node)."""
    headers, tokens = admin
    node = await video(client, headers, settings)
    return headers, tokens, node


# --- play en de vorm ------------------------------------------------------------------------------


@pytest.mark.anyio
async def test_play_geeft_een_sessie_met_direct_play(client, own):
    headers, tokens, node = own
    out = PlayOut.model_validate(await play(client, headers, node["id"]))
    assert SID.match(out.sid), out.sid
    assert out.mode == "direct" and out.reasons == []
    assert out.urls.model_dump(exclude_none=True) == {"file": "file"}
    assert out.mime == 'video/mp4; codecs="avc1.64001f,mp4a.40.2"'
    assert out.offset == 0 and out.duration_ms == 10000 and out.start_ms == 0
    assert [(a.index, a.codec, a.default) for a in out.tracks.audio] == [(1, "aac", True)]
    assert out.quality.current == "auto" and out.quality.options == ["auto", "source", 480]
    assert (out.expires_at - out.started_at).total_seconds() == 12 * 3600
    s = session_of(out.sid)
    assert s.sha == node["sha"] and s.size == len(node["data"])
    assert s.family == uuid.UUID(family(tokens)) and s.mime == "video/mp4"
    assert s.path is not None and s.path.name == node["sha"]
    # /beheer ziet de sessie, nooit de sid
    [view] = sessions.list()
    assert view.id == s.id and view.mode == "direct" and view.node_id == uuid.UUID(node["id"])
    assert out.sid not in view.model_dump_json()
    # de start ligt in het bestand
    later = await play(client, headers, node["id"], start_ms=99_999)
    assert later["start_ms"] == 9999


@pytest.mark.anyio
async def test_de_sid_heeft_een_nette_naam_van_de_node(client, own, settings):
    headers, _, node = own
    settings.node_name = "Mijn NAS_1 (zolder)"
    out = await play(client, headers, node["id"])
    assert out["sid"].startswith("mijn-nas-1-zolder.") and SID.match(out["sid"])
    assert sessions.node_name("") == "node" and sessions.node_name("...") == "node"
    assert sessions.node_name("Ünï.côde") == "n-c-de"
    assert len(sessions.node_name("a" * 100)) == 63


@pytest.mark.anyio
async def test_play_weigert(client, own, admin, media):
    headers, _, node = own
    path = f"/api/v1/media/{node['id']}/play"
    body = {"profile": profile_json()}
    failed(await client.post(path, json=body), 401, "not_logged_in")
    other = f"/api/v1/media/{uuid.uuid4()}/play"
    failed(await client.post(other, json=body, headers=headers), 404, "not_found")
    failed(await client.post(path, json={}, headers=headers), 422, "invalid_input")
    # een map, een audiospoor of een ondertitel die er niet is: 404
    folder = (await root(client, headers))["folder"]["id"]
    folder_play = f"/api/v1/media/{folder}/play"
    failed(await client.post(folder_play, json=body, headers=headers), 404, "not_found")
    await play(client, headers, node["id"], audio=7, expect=404)
    await play(client, headers, node["id"], subtitle="e3", expect=404)
    await play(client, headers, node["id"], subtitle="x0", expect=404)
    # media uit
    media.enabled = False
    failed(await client.post(path, json=body, headers=headers), 503, "media_off")


@pytest.mark.anyio
async def test_codecs(client, own, settings):
    headers, _, node = own
    r = await client.get(f"/api/v1/media/{node['id']}/codecs", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json() == {"candidates": P.candidates(), "item": ["avc1.64001f", "mp4a.40.2"]}
    # zonder probe: alleen de kandidaten
    r = await put(client, headers, (await root(client, headers))["folder"]["id"], "x.mkv", b"x")
    r = await client.get(f"/api/v1/media/{r.json()['id']}/codecs", headers=headers)
    assert r.json()["item"] == []


@pytest.mark.anyio
async def test_zonder_probe_en_zonder_worker(client, admin, settings, media):
    headers, _ = admin
    parent = (await root(client, headers))["folder"]["id"]
    node = (await put(client, headers, parent, "nieuw.mp4", content("nieuw"))).json()
    # geen probe en geen worker: 503 media_worker_down (de probe kan niet)
    r = await client.post(
        f"/api/v1/media/{node['id']}/play", json={"profile": profile_json()}, headers=headers
    )
    failed(r, 503, "media_worker_down")
    # een probe die het bestand afwees
    sha = await blob_of(settings, node["id"])
    from app.media.types import Probe

    await analysis.save_probe(maker(settings), sha, Probe(state="failed", error="unreadable"))
    await play(client, headers, node["id"], expect=409)
    await analysis.save_probe(maker(settings), sha, Probe(state="failed", error="too_large"))
    out = await play(client, headers, node["id"], expect=415)
    assert out["detail"]["params"]["reasons"] == ["too_large"]


@pytest.mark.anyio
async def test_de_probe_bij_play_binnen_het_budget(client, admin, settings, monkeypatch):
    """Zonder opgeslagen probe probeert play de worker (één keer per sha, ook bij twee
    tegelijk) en slaat hij de uitkomst op; te laat: 409 media_probe_pending met Retry-After."""
    from app.routers import media_play

    headers, _ = admin
    parent = (await root(client, headers))["folder"]["id"]
    node = (await put(client, headers, parent, "laat.mp4", content("laat"))).json()
    sha = await blob_of(settings, node["id"])
    gate = asyncio.Event()
    asked: list[str] = []

    async def probe_blob(_settings, wanted):
        asked.append(wanted)
        await gate.wait()
        found = recorded("h264_aac.mp4")
        found.blob_sha = wanted
        return found

    monkeypatch.setattr(analysis, "probe_blob", probe_blob)
    monkeypatch.setattr(media_play, "PROBE_BUDGET", 0.2)
    first = await play(client, headers, node["id"], expect=409)
    assert first["detail"]["code"] == "media_probe_pending"
    assert first["detail"]["params"]["reason"] == "probe"
    second = await play(client, headers, node["id"], expect=409)
    assert second["detail"]["code"] == "media_probe_pending"
    assert asked == [sha]  # de eerste probe loopt nog: geen tweede
    gate.set()
    await asyncio.wait_for(asyncio.gather(*media_play._probing.values()), 10)
    assert (await analysis.load(maker(settings), sha)) is not None
    out = await play(client, headers, node["id"])
    assert out["mode"] == "direct" and asked == [sha]


@pytest.mark.anyio
async def test_een_mkv_buiten_een_bibliotheek_speelt_als_remux(
    client, admin, settings, monkeypatch
):
    """De keyframes bij play (Keuze 17): zonder index leest play de index meteen, binnen het
    budget, en dan is het remux. De stub van M4 zegt none: transcode met keyframes_missing, en
    zonder M4 is daar geen plek voor (503 media_busy worker). De stroom zelf (streams.open) is
    van M4: met de stub 503 media_off, en de sessie gaat weer dicht."""
    headers, _ = admin
    node = await video(client, headers, settings, "serie.mkv", "vp9_opus.mkv")
    out = await play(client, headers, node["id"], fixture="vp9_opus.mkv", expect=503)
    assert out["detail"]["code"] == "media_busy"
    assert out["detail"]["params"]["reason"] == "worker"
    read: list[str] = []

    async def read_now(sha):
        read.append(sha)
        await store_probe(settings, sha, "vp9_opus.mkv", index=True)
        return "ready"

    monkeypatch.setattr(keyframes, "read_now", read_now)
    out = await play(client, headers, node["id"], fixture="vp9_opus.mkv", expect=503)
    assert out["detail"]["code"] == "media_off"
    assert read == [node["sha"]] and sessions.list() == []
    opened: list[Plan] = []

    async def open_stream(session, plan):
        opened.append(plan)
        return StreamInfo(
            mode="remux", url="master.m3u8", map_url="map.json", duration_ms=5000,
            mime='video/mp4; codecs="vp09.00.31.08,opus"', offset_ms=-1000,
        )  # fmt: skip

    monkeypatch.setattr(streams, "open", open_stream)
    out = await play(client, headers, node["id"], fixture="vp9_opus.mkv")
    assert out["mode"] == "remux" and out["reasons"] == ["container"]
    assert out["urls"]["master"] == "master.m3u8" and out["urls"]["map"] == "map.json"
    assert out["offset"] == -1.0 and [p.mode for p in opened] == ["remux"]
    assert out["mime"] == 'video/mp4; codecs="vp09.00.31.08,opus"'
    assert read == [node["sha"]]  # de index staat er nu: niet nog eens
    failed(await file(client, out["sid"]), 404, "not_found")  # /file is voor direct play


# --- de guard op elke route -----------------------------------------------------------------------


@pytest.mark.anyio
async def test_een_onbekende_sid_op_elke_route(client, own):
    headers, _, node = own
    out = await play(client, headers, node["id"])
    name, _ = out["sid"].split(".", 1)
    for method, path, url in calls():
        for sid in (f"{name}.{'A' * 32}", "s1.geheim-deel", "x" * 300):
            r = await client.request(method, url.replace("s1.geheim-deel", sid))
            assert r.status_code == 410, (method, path, r.status_code)
            if method != "HEAD":
                assert r.json()["detail"]["code"] == "media_session_gone"
    assert (await file(client, out["sid"], "HEAD")).status_code == 200


@pytest.mark.anyio
async def test_stop_laat_een_grafsteen_tot_30_minuten(client, own, clock):
    headers, _, node = own
    out = await play(client, headers, node["id"])
    sid = out["sid"]
    assert sessions.stop(session_of(sid).id, "stopped")
    assert not sessions.stop(session_of(sid).id, "stopped")  # al dicht
    for method in ("GET", "DELETE"):
        r = await client.request(method, f"{STREAM}/{sid}/file" if method == "GET" else
                                 f"{STREAM}/{sid}")  # fmt: skip
        failed(r, 403, "media_access_gone", "stopped")
    assert (await file(client, sid, "HEAD")).status_code == 403
    # de grafsteen blijft na DELETE en in de lijst staat hij niet meer
    assert sessions.list() == []
    clock.advance(29 * 60)
    failed(await file(client, sid), 403, "media_access_gone", "stopped")
    clock.advance(61)
    params = failed(await file(client, sid), 410, "media_session_gone")
    assert "reason" not in params


@pytest.mark.anyio
async def test_sluiten_door_de_speler(client, own):
    headers, _, node = own
    out = await play(client, headers, node["id"])
    r = await client.delete(f"{STREAM}/{out['sid']}")
    assert r.status_code == 204 and r.content == b""
    params = failed(await file(client, out["sid"]), 410, "media_session_gone")
    assert "reason" not in params


# --- intrekken ------------------------------------------------------------------------------------


@pytest.fixture
async def other(client, admin, settings):
    """Een gewone gebruiker met een eigen video: (headers, tokens, user_id, node)."""
    admin_headers, _ = admin
    headers, user_id = await person(client, admin_headers, "bert@example.nl")
    r = await client.post(
        "/api/v1/auth/login", json={"email": "bert@example.nl", "password": "lang-genoeg-123"}
    )
    tokens = r.json()
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    node = await video(client, headers, settings, "bert.mp4")
    return headers, tokens, user_id, node


@pytest.mark.anyio
async def test_blokkeren_sluit_meteen_met_blocked(client, admin, other):
    admin_headers, _ = admin
    headers, _, user_id, node = other
    out = await play(client, headers, node["id"])
    assert (await file(client, out["sid"], "HEAD")).status_code == 200
    r = await client.patch(
        f"/api/v1/users/{user_id}", json={"disabled": True}, headers=admin_headers
    )
    assert r.status_code == 200, r.text
    failed(await file(client, out["sid"]), 403, "media_access_gone", "blocked")
    failed(await file(client, out["sid"]), 403, "media_access_gone", "blocked")


@pytest.mark.anyio
async def test_uitloggen_sluit_die_login_meteen(client, other):
    headers, tokens, _, node = other
    out = await play(client, headers, node["id"])
    second = await play(client, headers, node["id"])
    r = await client.post("/api/v1/auth/logout", json={"refresh_token": tokens["refresh_token"]})
    assert r.status_code == 204
    for sid in (out["sid"], second["sid"]):
        failed(await file(client, sid), 403, "media_access_gone", "revoked")


@pytest.mark.anyio
async def test_een_login_intrekken_laat_de_andere(client, other):
    """Eén login intrekken (de lijst van apparaten) sluit alleen de sessies van die login;
    "overal anders uitloggen" laat de eigen login staan."""
    headers, tokens, _, node = other
    r = await client.post(
        "/api/v1/auth/login", json={"email": "bert@example.nl", "password": "lang-genoeg-123"}
    )
    phone = r.json()
    phone_headers = {"Authorization": f"Bearer {phone['access_token']}"}
    here = await play(client, headers, node["id"])
    there = await play(client, phone_headers, node["id"])
    r = await client.delete(f"/api/v1/auth/sessions/{family(phone)}", headers=headers)
    assert r.status_code == 204, r.text
    failed(await file(client, there["sid"]), 403, "media_access_gone", "revoked")
    assert (await file(client, here["sid"], "HEAD")).status_code == 200
    # opnieuw op de telefoon, en dan vanaf hier overal anders uitloggen
    r = await client.post(
        "/api/v1/auth/login", json={"email": "bert@example.nl", "password": "lang-genoeg-123"}
    )
    phone_headers = {"Authorization": f"Bearer {r.json()['access_token']}"}
    there = await play(client, phone_headers, node["id"])
    r = await client.post("/api/v1/auth/sessions/revoke-others", headers=headers)
    assert r.status_code == 204, r.text
    failed(await file(client, there["sid"]), 403, "media_access_gone", "revoked")
    assert (await file(client, here["sid"], "HEAD")).status_code == 200
    assert family(tokens)  # de eigen login bleef


@pytest.mark.anyio
async def test_2fa_terugzetten_sluit_meteen(client, admin, other):
    admin_headers, _ = admin
    headers, _, user_id, node = other
    out = await play(client, headers, node["id"])
    r = await client.delete(f"/api/v1/users/{user_id}/2fa", headers=admin_headers)
    assert r.status_code == 204, r.text
    failed(await file(client, out["sid"]), 403, "media_access_gone", "revoked")


@pytest.mark.anyio
async def test_de_prullenbak_sluit_meteen(client, own, settings):
    headers, _, node = own
    out = await play(client, headers, node["id"])
    r = await client.delete(f"/api/v1/files/{node['id']}", headers=headers)
    assert r.status_code == 204, r.text
    await settle()
    s = session_of(out["sid"])
    assert s.closed == "revoked"  # de controle liep al, zonder verzoek
    failed(await file(client, out["sid"]), 403, "media_access_gone", "revoked")


@pytest.mark.anyio
async def test_een_map_in_de_prullenbak_bij_het_volgende_verzoek(client, own, settings, clock):
    """Een map erboven: de app weet zonder database niet welke sessies eronder vallen, dus
    vervalt de cache van alle sessies en kijkt het volgende verzoek (niet pas na 15 s)."""
    headers, _, _ = own
    folder = await mkdir(client, headers, (await root(client, headers))["folder"]["id"], "Films")
    node = await video(client, headers, settings, "diep.mp4", parent=folder)
    out = await play(client, headers, node["id"])
    assert (await file(client, out["sid"], "HEAD")).status_code == 200
    r = await client.delete(f"/api/v1/files/{folder}", headers=headers)
    assert r.status_code == 204, r.text
    failed(await file(client, out["sid"]), 403, "media_access_gone", "revoked")


@pytest.mark.anyio
async def test_een_deling_intrekken_binnen_15_s(client, admin, other, settings, clock):
    admin_headers, _ = admin
    headers, _, user_id, _ = other
    node = await video(client, admin_headers, settings, "gedeeld.mp4")
    share = await share_with(client, admin_headers, node["id"], user_id=user_id, permission="read")
    out = await play(client, headers, node["id"])
    assert session_of(out["sid"]).user_id == uuid.UUID(user_id)
    r = await client.delete(f"/api/v1/shares/{share['id']}", headers=admin_headers)
    assert r.status_code == 204, r.text
    # binnen de cache nog wel (hooguit 15 s), daarna niet meer
    clock.advance(10)
    assert (await file(client, out["sid"], "HEAD")).status_code == 200
    clock.advance(5.5)
    failed(await file(client, out["sid"]), 403, "media_access_gone", "revoked")
    # wie geen toegang heeft, kan ook geen nieuwe sessie maken
    await play(client, headers, node["id"], expect=404)


@pytest.mark.anyio
async def test_een_gast_speelt_wat_met_hem_gedeeld_is(client, admin, settings):
    admin_headers, _ = admin
    guest_headers, guest_id = await person(client, admin_headers, "gast@example.nl", "guest")
    node = await video(client, admin_headers, settings, "voor-gast.mp4")
    await play(client, guest_headers, node["id"], expect=404)
    await share_with(client, admin_headers, node["id"], user_id=guest_id, permission="read")
    out = await play(client, guest_headers, node["id"])
    assert (await file(client, out["sid"], "HEAD")).status_code == 200


@pytest.mark.anyio
async def test_een_nieuwe_versie_geeft_replaced(client, own, settings):
    """De sessie pint (sha, grootte): na een nieuwe versie 410 replaced, ook voor een bereik
    daarna; nooit bytes van de nieuwe blob in de oude sessie."""
    headers, _, node = own
    out = await play(client, headers, node["id"])
    sid = out["sid"]
    r = await file(client, sid, Range="bytes=0-99")
    assert r.status_code == 206 and r.content == node["data"][:100]
    new = content("andere inhoud")
    r = await client.put(f"/api/v1/files/{node['id']}/content", content=new, headers=headers)
    assert r.status_code == 200, r.text
    await settle()
    for extra in ({}, {"Range": "bytes=100-199"}):
        r = await file(client, sid, **extra)
        failed(r, 410, "media_session_gone", "replaced")
        assert new[:100] not in r.content
    # een nieuwe sessie speelt de nieuwe inhoud (met zijn eigen probe)
    await store_probe(settings, await blob_of(settings, node["id"]), "h264_aac.mp4")
    fresh = await play(client, headers, node["id"])
    r = await file(client, fresh["sid"], Range="bytes=0-99")
    assert r.status_code == 206 and r.content == new[:100]


@pytest.mark.anyio
async def test_on_discard_sluit_eerst_de_sessies(client, own, monkeypatch):
    headers, _, node = own
    out = await play(client, headers, node["id"])
    s = session_of(out["sid"])
    seen: list[str | None] = []

    async def cleaner(sha):
        seen.append(s.closed)

    monkeypatch.setattr(hooks, "_discard", [])
    hooks.register_discard(cleaner)
    await hooks.on_discard(node["sha"])
    assert seen == ["replaced"]
    failed(await file(client, out["sid"]), 410, "media_session_gone", "replaced")


# --- de losse ondertitels (Keuze 29) --------------------------------------------------------------


async def library_item(settings, owner_id: str, node: dict, root_id: str, subs: list) -> None:
    async with maker(settings)() as session:
        library = MediaLibrary(
            owner_id=uuid.UUID(owner_id), root_id=uuid.UUID(root_id), kind="movies", name="Films"
        )
        session.add(library)
        await session.flush()
        session.add(
            MediaItem(
                node_id=uuid.UUID(node["id"]),
                library_id=library.id,
                kind="movie",
                title="Film",
                sort_title="film",
                subs=subs,
            )
        )
        await session.commit()


@pytest.mark.anyio
async def test_alleen_ondertitels_die_de_kijker_mag_lezen(client, admin, other, settings):
    admin_headers, _ = admin
    bert_headers, _, bert_id, bert_node = other
    me = (await client.get("/api/v1/auth/me", headers=admin_headers)).json()
    top = (await root(client, admin_headers))["folder"]["id"]
    node = await video(client, admin_headers, settings, "met-subs.mp4")
    mine = (await put(client, admin_headers, top, "film.nl.srt", b"1\n")).json()
    gone = (await put(client, admin_headers, top, "film.de.srt", b"2\n")).json()
    await client.delete(f"/api/v1/files/{gone['id']}", headers=admin_headers)
    bert_root = (await root(client, bert_headers))["folder"]["id"]
    foreign = (await put(client, bert_headers, bert_root, "film.en.srt", b"3\n")).json()
    subs = [
        {"node": foreign["id"], "lang": "en"},  # van een ander, niet gedeeld
        {"node": mine["id"], "lang": "nl", "forced": True},
        {"node": gone["id"], "lang": "de"},  # in de prullenbak
        {"node": str(uuid.uuid4()), "lang": "fr"},  # bestaat niet
        {"node": "geen-uuid"},
    ]
    await library_item(settings, me["id"], node, top, subs)
    out = await play(client, admin_headers, node["id"])
    tracks = out["tracks"]["subtitles"]
    assert [(t["id"], t["lang"], t["forced"]) for t in tracks] == [("x0", "nl", True)]
    s = session_of(out["sid"])
    assert sessions.external(s, "x0")["node"] == mine["id"]
    assert sessions.external(s, "x0")["sha"] == await blob_of(settings, mine["id"])
    for track in ("x1", "x99", "e0", "x", foreign["id"]):
        with pytest.raises(Exception) as caught:
            sessions.external(s, track)
        assert getattr(caught.value, "status_code", None) == 404
    # x1 kiezen kan dus niet, x0 wel
    await play(client, admin_headers, node["id"], subtitle="x1", expect=404)
    chosen = await play(client, admin_headers, node["id"], subtitle="x0")
    assert chosen["tracks"]["subtitles"][0]["default"] is True
    # deelt Bert zijn ondertitel met de beheerder, dan staat hij er wel in (vooraan)
    await share_with(client, bert_headers, foreign["id"], user_id=me["id"], permission="read")
    out = await play(client, admin_headers, node["id"])
    assert [t["lang"] for t in out["tracks"]["subtitles"]] == ["en", "nl"]
    assert bert_node  # Bert zelf speelt niets van de beheerder


# --- de grenzen -----------------------------------------------------------------------------------


@pytest.mark.anyio
async def test_8_per_gebruiker_de_oudste_gaat_dicht(client, own, clock):
    headers, _, node = own
    sids = []
    for _ in range(9):
        sids.append((await play(client, headers, node["id"]))["sid"])
        clock.advance(1)
    failed(await file(client, sids[0]), 403, "media_access_gone", "stopped")
    for sid in sids[1:]:
        assert (await file(client, sid, "HEAD")).status_code == 200
    assert len(sessions.list()) == 8


@pytest.mark.anyio
async def test_12_uur_absoluut(client, own, clock):
    headers, _, node = own
    out = await play(client, headers, node["id"])
    for _ in range(12 * 3 - 1):  # elke 20 minuten een verzoek: nooit stil
        clock.advance(20 * 60)
        assert (await file(client, out["sid"], "HEAD")).status_code == 200
    clock.advance(20 * 60)
    params = failed(await file(client, out["sid"]), 410, "media_session_gone")
    assert "reason" not in params


@pytest.mark.anyio
async def test_30_minuten_stil(client, own, clock):
    headers, _, node = own
    out = await play(client, headers, node["id"])
    clock.advance(29 * 60)
    assert (await file(client, out["sid"], "HEAD")).status_code == 200
    clock.advance(30 * 60)
    failed(await file(client, out["sid"]), 410, "media_session_gone")
    assert sessions.list() == []


@pytest.mark.anyio
async def test_replace_sluit_de_oude_in_dezelfde_beurt(client, own, other):
    headers, _, node = own
    bert_headers, _, _, bert_node = other
    old = await play(client, headers, node["id"])
    new = await play(client, headers, node["id"], replace=old["sid"])
    assert new["sid"] != old["sid"]
    failed(await file(client, old["sid"]), 410, "media_session_gone")
    assert (await file(client, new["sid"], "HEAD")).status_code == 200
    # de sid van een ander sluit niets
    bert = await play(client, bert_headers, bert_node["id"])
    await play(client, headers, node["id"], replace=bert["sid"])
    assert (await file(client, bert["sid"], "HEAD")).status_code == 200
    # replace telt niet mee voor de grens: 8 wissels laten 8 sessies over
    current = new["sid"]
    for _ in range(8):
        current = (await play(client, headers, node["id"], replace=current))["sid"]
    assert (await file(client, current, "HEAD")).status_code == 200


@pytest.mark.anyio
async def test_64_in_totaal(client, own, other, monkeypatch):
    headers, _, node = own
    bert_headers, _, _, bert_node = other
    monkeypatch.setattr(sessions, "TOTAL", 3)
    await play(client, headers, node["id"])
    await play(client, headers, node["id"])
    await play(client, bert_headers, bert_node["id"])
    out = await play(client, bert_headers, bert_node["id"], expect=503)
    assert out["detail"]["code"] == "media_busy"
    assert out["detail"]["params"]["reason"] == "cpu"
    assert len(sessions.list()) == 3


@pytest.mark.anyio
async def test_close_all_zonder_grafsteen(client, own):
    headers, _, node = own
    out = await play(client, headers, node["id"])
    await play(client, headers, node["id"])
    assert sessions.close_all() == 2
    assert sessions.list() == [] and sessions.table().by_sid == {}
    params = failed(await file(client, out["sid"]), 410, "media_session_gone")
    assert "reason" not in params


# --- de controle en guarded_body ------------------------------------------------------------------


@pytest.mark.anyio
async def test_gelijktijdige_verzoeken_delen_een_controle(client, own, clock, monkeypatch):
    headers, _, node = own
    out = await play(client, headers, node["id"])
    s = session_of(out["sid"])
    real = sessions._verify
    count = 0

    async def counting(session):
        nonlocal count
        count += 1
        await asyncio.sleep(0.01)
        return await real(session)

    monkeypatch.setattr(sessions, "_verify", counting)
    assert await sessions.check(s) is None and count == 0  # play controleerde net
    clock.advance(16)
    results = await asyncio.gather(*(sessions.check(s) for _ in range(5)))
    assert results == [None] * 5 and count == 1
    assert await sessions.check(s) is None and count == 1  # weer in de cache


async def chunks(n: int, size: int, pause: asyncio.Event | None = None, after: int = 1):
    for i in range(n):
        if pause is not None and i == after:
            await pause.wait()
        yield bytes(size)


@pytest.mark.anyio
async def test_guarded_body_stopt_binnen_een_stuk_na_intrekken(client, own):
    headers, tokens, node = own
    out = await play(client, headers, node["id"])
    s = session_of(out["sid"])
    pause = asyncio.Event()
    body = sessions.guarded_body(s, chunks(10, 1024, pause))
    assert len(await anext(body)) == 1024  # de client leest één stuk en pauzeert
    r = await client.post("/api/v1/auth/logout", json={"refresh_token": tokens["refresh_token"]})
    assert r.status_code == 204
    pause.set()
    with pytest.raises(sessions.StreamCut):
        await anext(body)
    assert s.closed == "revoked"


@pytest.mark.anyio
async def test_guarded_body_kijkt_per_4_mb_en_per_5_s(client, admin, other, settings, clock):
    """Een deling ingetrokken midden in de stroom: bij de controle na 4 MB (of na 5 s) is het
    afgelopen. De cache van 15 s staat hier op 0, zodat de controle echt kijkt."""
    admin_headers, _ = admin
    headers, _, user_id, _ = other
    node = await video(client, admin_headers, settings, "stroom.mp4")
    share = await share_with(client, admin_headers, node["id"], user_id=user_id, permission="read")
    out = await play(client, headers, node["id"])
    s = session_of(out["sid"])
    s.settings = settings.model_copy(update={"media_access_cache": 0.0})
    body = sessions.guarded_body(s, chunks(100, 1024 * 1024))
    got = 0
    for _ in range(3):
        got += len(await anext(body))
    r = await client.delete(f"/api/v1/shares/{share['id']}", headers=admin_headers)
    assert r.status_code == 204, r.text
    clock.advance(1)
    # het vierde stuk gaat nog (3 MB en 0 s sinds de laatste controle)
    got += len(await anext(body))
    # het vijfde komt na 4 MB: controle, de deling is weg
    with pytest.raises(sessions.StreamCut):
        await anext(body)
    assert got == 4 * 1024 * 1024 and s.closed == "revoked"
    # en per 5 s, ook bij kleine stukken
    second = await play(client, admin_headers, node["id"])
    s2 = session_of(second["sid"])
    body = sessions.guarded_body(s2, chunks(100, 10))
    await anext(body)
    sessions.stop(s2.id, "stopped")
    with pytest.raises(sessions.StreamCut):
        await anext(body)


@pytest.mark.anyio
async def test_guarded_body_stopt_als_de_app_stopt(client, own, media):
    """F3: begin_stop sluit elke sessie (close_all), en een controle die dan nog wacht, breekt
    af via runtime.until_stopped. In beide gevallen StreamCut, met de reden."""
    headers, _, node = own
    first = session_of((await play(client, headers, node["id"]))["sid"])
    second = session_of((await play(client, headers, node["id"]))["sid"])
    for s in (first, second):
        s.app = app
    body = sessions.guarded_body(first, chunks(10, 10))
    await anext(body)
    first.stale = True  # de volgende stap controleert, en dat wachten loopt via until_stopped
    media.stopping = True
    with pytest.raises(sessions.StreamCut) as cut:
        await anext(body)
    assert cut.value.reason == "stopping"
    media.stopping = False
    body = sessions.guarded_body(second, chunks(10, 10))
    await anext(body)
    assert sessions.close_all() >= 1
    with pytest.raises(sessions.StreamCut) as cut:
        await anext(body)
    assert cut.value.reason == "gone"


@pytest.mark.anyio
async def test_guarded_body_na_5_s_de_controle(client, own, clock, monkeypatch):
    headers, _, node = own
    out = await play(client, headers, node["id"])
    s = session_of(out["sid"])
    checks = 0
    real = sessions.check

    async def counting(session):
        nonlocal checks
        checks += 1
        return await real(session)

    monkeypatch.setattr(sessions, "check", counting)
    body = sessions.guarded_body(s, chunks(10, 10))
    await anext(body)
    await anext(body)
    assert checks == 0
    clock.advance(5)
    await anext(body)
    assert checks == 1


def test_een_sessie_in_het_geheugen_heeft_geen_sid_in_zijn_repr():
    s = sessions.Session(
        id=uuid.uuid4(), sid="node.geheim", user_id=uuid.uuid4(), sha="a" * 64, size=1,
        plan=Plan(mode="direct"),
    )  # fmt: skip
    assert "settings" not in repr(s) and s.view().reasons == []
