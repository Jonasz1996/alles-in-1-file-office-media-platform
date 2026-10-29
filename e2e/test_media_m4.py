"""M4: remux en transcode via de echte stroom in de browser (fase 5, §2 M4 van het bouwontwerp).

Met media aan en de lokale worker (de fixture media). De speler van M6 is er nog niet: de test
speelt zoals die dat straks doet, met MediaSource in de pagina van de app (dezelfde origin, CSP
media-src 'self' blob:). Eerst map.json, dan init.mp4?g=, dan de segmenten op volgorde in één
SourceBuffer met timestampOffset uit de kaart; zegt een segment X-Init: g en is dat een andere
generatie, dan eerst die init (Keuze 22). Daarna moet de <video> echt spelen.

- een mkv buiten een bibliotheek (VP9 + Opus in Matroska): play doet de probe en leest de
  keyframe-index meteen (Keuze 17), dan remux (reden container) en geen transcode;
- dezelfde via een publieke link, vanuit de publieke pagina (#10): remux mag, transcode niet
  (403 media_public_transcode); de link weg: binnen 15 s geeft de stroom 403 revoked;
- met public_transcode aan: een transcode via de link, hooguit 720, en één per link;
- een transcode op het rooster van 2 s (xvid + mp3): VP9 + Opus in Chromium 141, H.264 + AAC in
  Chrome for Testing (dan trun versie 1, met negatieve compositietijden);
- H.264 + AAC uit HEVC HDR (tonemap) als de browser H.264 via MSE kan (Chromium van Playwright
  141 niet; in CI met MEDIA_REQUIRE_H264=1 moet het);
- #12 van §4.3 zonder de speler (bij het samenvoegen van golf 2, M1 en M4): de worker gedood op
  pid tijdens een transcode; zijn ffmpeg gaat mee, de app start hem opnieuw, en de stroom loopt
  binnen 10 s door.
"""

from __future__ import annotations

import hashlib
import signal
import subprocess
import time
from typing import Any

import pytest

from media_harness import (
    Uploaded,
    comm,
    descendants,
    kill_pid,
    open_app,
    process_table,
    require_codec,
    run_async,
    wait_gone,
    wait_playing,
)
from test_media_m3 import codecs_of, login, padded_webm, play, profile

ACCESS_CACHE_S = 15.0  # settings.media_access_cache (Keuze 21)

MSE_JS = """async (base) => {
  // De speler van M6 in het klein: de kaart, de init, de segmenten op volgorde
  const out = { gens: [], sizes: [], error: null };
  try {
    const r = await fetch(base + "map.json", { cache: "no-store" });
    if (!r.ok) throw new Error(`map.json: ${r.status}`);
    const map = await r.json();
    out.map = map;
    document.getElementById("m4")?.remove();
    const v = document.createElement("video");
    v.id = "m4"; v.muted = true; v.playsInline = true;
    document.body.append(v);
    const ms = new MediaSource();
    v.src = URL.createObjectURL(ms);
    await new Promise((ok) => ms.addEventListener("sourceopen", ok, { once: true }));
    const sb = ms.addSourceBuffer(map.mime);
    sb.timestampOffset = map.offset;
    const append = (data) => new Promise((ok, bad) => {
      sb.addEventListener("updateend", ok, { once: true });
      sb.addEventListener("error", () => bad(new Error("appendBuffer")), { once: true });
      sb.appendBuffer(data);
    });
    const get = async (path) => {
      const res = await fetch(base + path, { cache: "no-store" });
      if (!res.ok) throw new Error(`${path}: ${res.status}`);
      return { gen: Number(res.headers.get("x-init") || 0), data: await res.arrayBuffer() };
    };
    let gen = map.gen;
    await append((await get(map.init)).data);
    const count = Array.isArray(map.segments) ? map.segments.length : map.segments.count;
    for (let n = 0; n < count; n++) {
      const seg = await get(`${n}.m4s`);
      if (seg.gen !== gen) {  // een andere generatie: eerst zijn init
        gen = seg.gen;
        await append((await get(`init.mp4?g=${gen}`)).data);
      }
      out.gens.push(seg.gen);
      out.sizes.push(seg.data.byteLength);
      await append(seg.data);
    }
    ms.endOfStream();
    out.buffered = [];
    for (let i = 0; i < sb.buffered.length; i++) {
      out.buffered.push([sb.buffered.start(i), sb.buffered.end(i)]);
    }
    out.duration = ms.duration;
    v.play().catch(() => {});
  } catch (e) {
    out.error = String(e && e.message || e);
  }
  return out;
}"""

PUBLIC_JS = """async ({ url, key, body }) => {
  // play vanuit de publieke pagina; bij 409 media_probe_pending na Retry-After opnieuw
  const end = Date.now() + 30000;
  for (;;) {
    const r = await fetch(url, {
      method: "POST",
      headers: { Authorization: `Bearer ${key}`, "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await r.json();
    const detail = data.detail ?? null;
    const pending = r.status === 409 && detail && detail.code === "media_probe_pending";
    if (!pending || Date.now() > end) return { status: r.status, data };
    await new Promise((ok) => setTimeout(ok, 1000 * Number(r.headers.get("retry-after") || 1)));
  }
}"""

STATUS_JS = """async (url) => {
  const r = await fetch(url, { cache: "no-store" });
  let detail = null;
  try { detail = (await r.json()).detail; } catch (e) { /* bytes */ }
  return { status: r.status, detail };
}"""


def base_url(sid: str) -> str:
    return f"/api/v1/stream/{sid}/"


def play_mse(page: Any, sid: str) -> dict[str, Any]:
    """Speel de hele stroom via MediaSource; de <video> #m4 speelt daarna."""
    out = page.evaluate(MSE_JS, base_url(sid))
    assert out["error"] is None, out
    assert wait_playing(page, 1.0, selector="#m4") >= 1.0
    return out


def video_size(page: Any) -> list[int]:
    return page.evaluate(
        "() => { const v = document.getElementById('m4'); return [v.videoWidth, v.videoHeight]; }"
    )


def keyframes_state(media: Any, sha: str) -> str | None:
    import contextlib
    import sqlite3

    uri = f"file:{media.server.data_dir / 'platform.db'}?mode=ro"
    with contextlib.closing(sqlite3.connect(uri, uri=True, timeout=5)) as conn:
        row = conn.execute(
            "SELECT keyframes_state FROM media_probe WHERE blob_sha = ?", (sha,)
        ).fetchone()
    return row[0] if row else None


def fresh_mkv(media: Any, owner: Any, folder: str, pad: int) -> Uploaded:
    """vp9_opus.mkv met eigen bytes (een Void-element van pad bytes achteraan het Segment): een
    blob die nog niemand had. Andere tests in dezelfde sessie laden de fixture zelf ook op, en de
    probe en de keyframe-index horen bij de inhoud (blob_sha), niet bij de node."""
    from tests.media import fixtures

    path = fixtures.make("vp9_opus.mkv")
    data = padded_webm(path.read_bytes(), pad)
    r = media.server.call(
        owner, "PUT", f"/api/v1/files/{folder}/upload", params={"name": "film.mkv"},
        content=data, headers={"Content-Length": str(len(data))}, expect=201,
    )  # fmt: skip
    node = r.json()
    return Uploaded(node["id"], node["name"], hashlib.sha256(data).hexdigest(), path, len(data))


def fragments(media: Any, sid: str, n: int) -> list[Any]:
    """Segment n buiten de browser, met zijn init geknipt door de knipper van de app."""
    from app.media import fmp4

    r = media.server.request("GET", f"{base_url(sid)}{n}.m4s", expect=200)
    gen = r.headers["x-init"]
    init = media.server.request("GET", f"{base_url(sid)}init.mp4?g={gen}", expect=200)
    cutter = fmp4.Cutter()
    items = cutter.feed(init.content + r.content)
    cutter.close()
    return [i for i in items if isinstance(i, fmp4.Fragment)]


def close_session(media: Any, token: str, sid: str) -> None:
    media.server.request("DELETE", f"/api/v1/stream/{sid}", token=token, expect=204)


def public_link(media: Any, token: str, folder: str) -> tuple[dict[str, Any], dict[str, Any]]:
    made = media.server.request(
        "POST", f"/api/v1/files/{folder}/links", token=token, json={"permission": "read"},
        expect=201,
    ).json()  # fmt: skip
    opened = media.server.request(
        "POST", "/api/v1/public/open", json={"token": made["link"]["url"].removeprefix("/s/")},
        expect=200,
    ).json()  # fmt: skip
    return made, opened


def public_play(page: Any, key: str, node: str, body: dict[str, Any]) -> dict[str, Any]:
    """play vanuit de publieke pagina (de sleutel van de link, geen login)."""
    url = f"/api/v1/public/media/{node}/play"
    return page.evaluate(PUBLIC_JS, {"url": url, "key": key, "body": body})


def set_public_transcode(media: Any, on: bool) -> None:
    """public_transcode in de tabel setting (zoals /beheer van M10 het straks doet), en de app
    opnieuw gestart: MediaPolicy leest de instelling één keer en houdt hem in het geheugen."""
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app import db
    from app.config import MediaConfig
    from app.media.state import MediaPolicy

    settings = media.settings()

    async def go() -> None:
        engine = db.get_engine(settings)
        try:
            async with async_sessionmaker(engine)() as session:
                await MediaPolicy().save(session, MediaConfig(public_transcode=on))
        finally:
            await engine.dispose()
            db._engine.cache_clear()

    run_async(go)
    media.restart()


@pytest.mark.media
def test_een_mkv_buiten_een_bibliotheek_speelt_als_remux(media, media_browsers, ffmpeg_count):
    """Geen bibliotheek en geen opgeslagen probe: play doet de probe en leest de keyframe-index
    in hetzelfde budget, en kiest dan remux (alleen de container past niet), geen transcode."""
    owner = media.user()
    folder = media.folder(owner, "M4 remux")
    page = media_browsers.context_as(owner).new_page()
    open_app(page)
    token = login(media, owner)["access_token"]
    up = fresh_mkv(media, owner, folder, 4096 + 1)
    assert keyframes_state(media, up.sha) is None  # nog niets: geen bibliotheek, geen scan
    first = codecs_of(media, token, up.id)
    out = play(media, token, up.id, {"profile": profile(page, first["candidates"])})
    found = codecs_of(media, token, up.id)
    body = {"profile": profile(page, found["candidates"] + found["item"]), "replace": out["sid"]}
    out = play(media, token, up.id, body)
    assert out["mode"] == "remux" and out["reasons"] == ["container"], out
    assert keyframes_state(media, up.sha) == "ready"
    assert out["urls"] == {**out["urls"], "map": "map.json", "master": "master.m3u8"}, out
    assert out["mime"].startswith('video/mp4; codecs="vp09.'), out["mime"]
    got = play_mse(page, out["sid"])
    # remux: [begin, duur] per segment uit de index (een keyframe na elke 6 s), één generatie
    spans = got["map"]["segments"]
    assert isinstance(spans, list) and spans[0][0] == 0, got
    assert sum(d for _b, d in spans) >= 5900, got
    assert got["map"]["offset"] == -1.0 and set(got["gens"]) == {1}, got
    assert got["buffered"][0][0] < 0.1 and got["buffered"][-1][1] > 5.5, got
    assert video_size(page) == [640, 360]
    # de playlist voor een native speler (Safari) staat er ook
    master = media.server.request("GET", f"{base_url(out['sid'])}master.m3u8", expect=200)
    assert master.text.startswith("#EXTM3U") and "v.m3u8" in master.text
    close_session(media, token, out["sid"])
    gone = page.evaluate(STATUS_JS, f"{base_url(out['sid'])}map.json")
    assert gone["status"] == 410, gone


@pytest.mark.media
def test_een_publieke_link_speelt_remux_en_transcode_niet(media, media_browsers, ffmpeg_count):
    """#10: via een publieke link, vanuit de publieke pagina zonder in te loggen. De mkv buiten
    een bibliotheek speelt als remux (de keyframes bij play), een bron die omzetten nodig heeft
    geeft 403 media_public_transcode, en de link weg: binnen 15 s (de cache van de controle)
    geeft de stroom 403 revoked."""
    owner = media.user()
    folder = media.folder(owner, "M4 link")
    token = login(media, owner)["access_token"]
    mkv = fresh_mkv(media, owner, folder, 4096 + 2)
    hevc = media.upload(owner, "hevc10_hdr10.mkv", folder)
    assert keyframes_state(media, mkv.sha) is None
    made, opened = public_link(media, token, folder)
    page = media_browsers.new_context().new_page()
    page.goto(made["link"]["url"])  # de publieke pagina: niet ingelogd
    # een publieke speler kent alleen de vaste kandidaten (…/codecs vraagt een login)
    body = {"profile": profile(page, codecs_of(media, token, mkv.id)["candidates"])}
    out = public_play(page, opened["key"], mkv.id, body)
    assert out["status"] == 200, out
    assert out["data"]["mode"] == "remux" and out["data"]["reasons"] == ["container"], out
    assert keyframes_state(media, mkv.sha) == "ready"
    sid = out["data"]["sid"]
    got = play_mse(page, sid)
    assert set(got["gens"]) == {1} and got["buffered"][-1][1] > 5.5, got
    # omzetten mag niet via een link (public_transcode staat standaard uit)
    refused = public_play(page, opened["key"], hevc.id, body)
    assert refused["status"] == 403, refused
    assert refused["data"]["detail"]["code"] == "media_public_transcode", refused
    # de link weg: binnen de cache van de controle stopt de stroom
    media.server.request("DELETE", f"/api/v1/shares/{made['id']}", token=token, expect=204)
    deadline = time.monotonic() + ACCESS_CACHE_S + 5
    while True:
        status = page.evaluate(STATUS_JS, f"{base_url(sid)}0.m4s")
        if status["status"] == 403:
            break
        assert time.monotonic() < deadline, f"de stroom speelt na 20 s nog: {status}"
        page.wait_for_timeout(500)
    assert status["detail"]["code"] == "media_access_gone", status
    assert status["detail"]["params"]["reason"] == "revoked", status


@pytest.mark.media
def test_publiek_omzetten_als_het_aan_staat(media, media_browsers, ffmpeg_count):
    """public_transcode aan: een transcode via de link mag, hooguit 720, en één per link; een
    tweede kijker op dezelfde link krijgt 503 media_busy (reason link) met Retry-After."""
    set_public_transcode(media, True)
    try:
        owner = media.user()
        folder = media.folder(owner, "M4 link omzetten")
        token = login(media, owner)["access_token"]
        up = media.upload(owner, "xvid_mp3.avi", folder)
        made, opened = public_link(media, token, folder)
        page = media_browsers.new_context().new_page()
        page.goto(made["link"]["url"])
        body = {"profile": profile(page, codecs_of(media, token, up.id)["candidates"])}
        out = public_play(page, opened["key"], up.id, body)
        assert out["status"] == 200, out
        assert out["data"]["mode"] == "transcode", out
        options = out["data"]["quality"]["options"]
        assert all(q <= 720 for q in options if isinstance(q, int)), options
        got = play_mse(page, out["data"]["sid"])
        assert isinstance(got["map"]["segments"], dict), got  # het rooster
        assert video_size(page)[1] <= 720
        # een tweede kijker op dezelfde link: de plaats van de link is bezet
        other = media_browsers.new_context().new_page()
        other.goto(made["link"]["url"])
        again = media.server.request(
            "POST", "/api/v1/public/open",
            json={"token": made["link"]["url"].removeprefix("/s/")}, expect=200,
        ).json()  # fmt: skip
        busy = public_play(other, again["key"], up.id, body)
        assert busy["status"] == 503, busy
        assert busy["data"]["detail"]["code"] == "media_busy", busy
        assert busy["data"]["detail"]["params"]["reason"] == "link", busy
    finally:
        set_public_transcode(media, False)


@pytest.mark.media
def test_een_transcode_speelt_op_het_rooster(media, media_browsers, ffmpeg_count):
    """xvid + mp3: alleen met omzetten. Het rooster van 2 s in map.json, elk segment met een
    keyframe vooraan; VP9 + Opus in Chromium 141, H.264 + AAC waar de browser dat kan (dan trun
    versie 1: de B-frames hebben negatieve compositietijden)."""
    owner = media.user()
    folder = media.folder(owner, "M4 transcode")
    page = media_browsers.context_as(owner).new_page()
    open_app(page)
    token = login(media, owner)["access_token"]
    up = media.upload(owner, "xvid_mp3.avi", folder)
    first = codecs_of(media, token, up.id)
    out = play(media, token, up.id, {"profile": profile(page, first["candidates"])})
    assert out["mode"] == "transcode" and "video_codec" in out["reasons"], out
    got = play_mse(page, out["sid"])
    grid = got["map"]["segments"]
    assert grid["grid_ms"] == 2000 and grid["count"] == len(got["sizes"]), got
    assert set(got["gens"]) == {1}, got
    h264 = "avc1." in got["map"]["mime"]
    for n in range(grid["count"]):
        frags = [f for f in fragments(media, out["sid"], n) if f.video_start_ms is not None]
        assert frags and frags[0].sync, (n, frags)
        if h264:
            assert {f.trun_version for f in frags} == {1}, (n, frags)
    close_session(media, token, out["sid"])


@pytest.mark.media
def test_h264_uit_hevc_hdr_via_mse(media, media_browsers, ffmpeg_count):
    """HEVC 10-bit HDR10: omzetten naar H.264 + AAC met tonemap, als de browser H.264 en AAC
    via MSE kan. Lokaal (Chromium 141) overgeslagen; in CI (MEDIA_REQUIRE_H264=1) moet het."""
    owner = media.user()
    page = media_browsers.context_as(owner).new_page()
    open_app(page)
    require_codec(page, 'video/mp4; codecs="avc1.64001f"', "mse")
    require_codec(page, 'audio/mp4; codecs="mp4a.40.2"', "mse")
    folder = media.folder(owner, "M4 H264")
    token = login(media, owner)["access_token"]
    up = media.upload(owner, "hevc10_hdr10.mkv", folder)
    first = codecs_of(media, token, up.id)
    out = play(media, token, up.id, {"profile": profile(page, first["candidates"])})
    found = codecs_of(media, token, up.id)
    body = {"profile": profile(page, found["candidates"] + found["item"]), "replace": out["sid"]}
    out = play(media, token, up.id, body)
    assert out["mode"] == "transcode" and "hdr" in out["reasons"], out
    got = play_mse(page, out["sid"])
    assert got["map"]["mime"].startswith('video/mp4; codecs="avc1.'), got["map"]
    assert "mp4a.40.2" in got["map"]["mime"], got["map"]
    assert video_size(page) == [1280, 720]
    frags = [f for f in fragments(media, out["sid"], 0) if f.video_start_ms is not None]
    assert frags and frags[0].sync and {f.trun_version for f in frags} == {1}, frags
    close_session(media, token, out["sid"])


def segment_after_retry(media: Any, sid: str, n: int, seconds: float) -> tuple[float, list[Any]]:
    """Segment n zoals een speler het vraagt: bij 503 na Retry-After opnieuw. Geeft hoe lang het
    duurde en wat er tussendoor kwam (status en code)."""
    began = time.monotonic()
    seen: list[Any] = []
    while True:
        r = media.server.request("GET", f"{base_url(sid)}{n}.m4s")
        if r.status_code == 200:
            return time.monotonic() - began, seen
        seen.append((r.status_code, r.json().get("detail", {}).get("code")))
        assert r.status_code == 503 and time.monotonic() - began < seconds, seen
        time.sleep(float(r.headers.get("retry-after", "1")))


@pytest.mark.media
def test_de_worker_gedood_tijdens_een_transcode(media, media_browsers, ffmpeg_count, tmp_path):
    """#12 van §4.3 zonder de speler (M1 en M4, bij het samenvoegen van golf 2): een bron van
    120 s xvid + mp3 (alleen met omzetten). De speler haalt de eerste 20 s; de producent loopt
    door (tot 60 s voor de speler). Dan de worker gedood met SIGKILL op pid: zijn ffmpeg gaat mee
    (PDEATHSIG), wat in de cache staat komt meteen, en een segment voorbij wat er al was komt
    binnen 10 s na het doden (503 media_worker_down tot de worker terug is, dan een nieuwe run).
    Daarna nooit meer dan 3 ffmpeg en geen over (ffmpeg_count)."""
    src = tmp_path / "lang.avi"
    subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-y",
         "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=25:duration=120",
         "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=120",
         "-c:v", "mpeg4", "-vtag", "XVID", "-q:v", "5", "-g", "50",
         "-c:a", "libmp3lame", "-b:a", "128k", "-threads", "3", str(src)],
        check=True, timeout=120,
    )  # fmt: skip
    owner = media.user()
    folder = media.folder(owner, "M4 worker weg")
    page = media_browsers.context_as(owner).new_page()
    open_app(page)
    token = login(media, owner)["access_token"]
    data = src.read_bytes()
    node = media.server.call(
        owner, "PUT", f"/api/v1/files/{folder}/upload", params={"name": "lang.avi"},
        content=data, headers={"Content-Length": str(len(data))}, expect=201,
    ).json()  # fmt: skip
    src.unlink()
    first = codecs_of(media, token, node["id"])
    out = play(media, token, node["id"], {"profile": profile(page, first["candidates"])})
    assert out["mode"] == "transcode" and "video_codec" in out["reasons"], out
    sid = out["sid"]
    for n in range(10):  # de speler: de eerste 20 s
        media.server.request("GET", f"{base_url(sid)}{n}.m4s", expect=200)

    old = media.worker_pid()
    assert old is not None
    table = process_table()
    running = [(p, table[p][1]) for p in descendants([old], table) if comm(p) == "ffmpeg"]
    assert running, "geen ffmpeg in de worker: de producent liep al niet meer"
    killed = time.monotonic()
    assert kill_pid(old, table[old][1], signal.SIGKILL)
    assert wait_gone([(old, table[old][1])]) == []
    assert wait_gone(running) == [], running  # ffmpeg ging mee met de worker

    took, _ = segment_after_retry(media, sid, 5, 1.0)  # al in de cache: zonder worker
    assert took < 1.0, took
    took, seen = segment_after_retry(media, sid, 50, 10.0)  # 100 s: daar was niets
    assert time.monotonic() - killed <= 10.0, (took, seen)
    assert {code for _status, code in seen} <= {"media_worker_down", "media_busy"}, seen
    media.server.request("GET", f"{base_url(sid)}51.m4s", expect=200)  # de nieuwe run loopt
    assert media.worker_pid() not in (None, old)
    close_session(media, token, sid)
