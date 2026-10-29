"""M3: direct play via een echte sessie in de browser (fase 5, §2 M3 van het bouwontwerp).

Met media aan en de lokale worker (de fixture media). De speler van M6 is er nog niet: de test
meet de codecs in de browser zoals de web-app dat straks doet (GET …/codecs, dan
isTypeSupported, canPlayType en decodingInfo), vraagt play via de API, en zet een <video> met
de URL van de sessie in de app (dezelfde origin, CSP media-src 'self'). Daarna wordt de toegang
ingetrokken en stopt de sessie:

- VP9 + Opus (webm): play doet zelf de probe via de worker (geen opgeslagen probe), daarna
  direct play; het bestand naar de prullenbak: 403 revoked, en de <video> laadt niet meer;
- uitloggen van de login die speelt: 403 revoked, de andere login speelt door;
- een nieuwe versie: 410 replaced;
- een publieke link: spelen zonder in te loggen, de link weg: binnen 15 s 403;
- H.264 + AAC (mp4) als de browser het kan (Chromium van Playwright 141 niet; in CI met
  MEDIA_REQUIRE_H264=1 moet het), en blokkeren: 403 blocked.
"""

from __future__ import annotations

import time
from typing import Any

import pytest
from playwright.sync_api import expect

from media_harness import open_app, require_codec, wait_playing

PROBE_WAIT_S = 30.0
ACCESS_CACHE_S = 15.0  # settings.media_access_cache (Keuze 21)

MEASURE_JS = """async (strings) => {
  const v = document.createElement("video");
  const out = {};
  const audio = (s) => /^(mp4a|opus|flac|ac-3|ec-3|mp3)/.test(s);
  for (const s of strings) {
    const types = audio(s)
      ? [`audio/mp4; codecs="${s}"`, `audio/webm; codecs="${s}"`]
      : [`video/mp4; codecs="${s}"`, `video/webm; codecs="${s}"`];
    const mse = typeof MediaSource !== "undefined"
      && types.some((t) => MediaSource.isTypeSupported(t));
    const file = types.some((t) => v.canPlayType(t) !== "");
    const entry = { mse, file };
    if (!audio(s) && navigator.mediaCapabilities) {
      try {
        const info = await navigator.mediaCapabilities.decodingInfo({
          type: "media-source",
          video: {
            contentType: types[0], width: 1280, height: 720, bitrate: 4000000, framerate: 24,
          },
        });
        if (info.supported) { entry.smooth = info.smooth; entry.power = info.powerEfficient; }
      } catch (e) { /* onbekend */ }
    }
    out[s] = entry;
  }
  return out;
}"""

VIDEO_JS = """(url) => {
  document.getElementById("m3")?.remove();
  const v = document.createElement("video");
  v.id = "m3"; v.muted = true; v.playsInline = true; v.src = url;
  document.body.append(v);
  v.play().catch(() => {});
}"""

FETCH_JS = """async (url) => {
  const r = await fetch(url, { headers: { Range: "bytes=0-0" }, cache: "no-store" });
  let detail = null;
  try { detail = (await r.json()).detail; } catch (e) { /* bytes */ }
  return { status: r.status, detail };
}"""


def stream_url(sid: str) -> str:
    return f"/api/v1/stream/{sid}/file"


def profile(page: Any, strings: list[str]) -> dict[str, Any]:
    """Het profiel zoals de web-app het stuurt (Keuze 20, docs/fase-5/clientprofiel.md)."""
    codecs = page.evaluate(MEASURE_JS, list(dict.fromkeys(strings)))
    version = page.context.browser.version if page.context.browser else ""
    return {"v": 1, "base": "web", "client": {"kind": "web", "name": f"Chromium {version}"},
            "codecs": codecs}  # fmt: skip


def codecs_of(media: Any, token: str, node_id: str) -> dict[str, list[str]]:
    r = media.server.request("GET", f"/api/v1/media/{node_id}/codecs", token=token, expect=200)
    return r.json()


def play(media: Any, token: str, node_id: str, body: dict[str, Any], path: str | None = None):
    """POST play; bij 409 media_probe_pending (de probe loopt nog) na Retry-After opnieuw."""
    path = path or f"/api/v1/media/{node_id}/play"
    end = time.monotonic() + PROBE_WAIT_S
    while True:
        r = media.server.request("POST", path, token=token, json=body)
        if r.status_code == 200:
            return r.json()
        detail = r.json().get("detail", {})
        pending = r.status_code == 409 and detail.get("code") == "media_probe_pending"
        assert pending and time.monotonic() < end, (r.status_code, r.text)
        time.sleep(float(r.headers.get("retry-after", "1")))


def login(media: Any, account: Any) -> dict[str, Any]:
    """Een eigen login (een eigen family), los van die van de browser."""
    r = media.server.request(
        "POST",
        "/api/v1/auth/login",
        json={"email": account.email, "password": account.password},
        expect=200,
    )
    return r.json()


def start_video(page: Any, sid: str) -> None:
    page.evaluate(VIDEO_JS, stream_url(sid))
    assert wait_playing(page, 0.5, selector="#m3") >= 0.5


def fetched(page: Any, sid: str) -> dict[str, Any]:
    return page.evaluate(FETCH_JS, stream_url(sid))


def refused(media: Any, page: Any, sid: str, status: int, code: str, reason: str) -> None:
    """De sessie weigert: in de browser (zonder zijn cache), en ook buiten de browser."""
    out = fetched(page, sid)
    assert out["status"] == status, out
    assert out["detail"]["code"] == code and out["detail"]["params"]["reason"] == reason, out
    r = media.server.request("GET", stream_url(sid), headers={"Range": "bytes=0-0"})
    assert r.status_code == status, r.text
    detail = r.json()["detail"]
    assert detail["code"] == code and detail["params"]["reason"] == reason, detail


def video_fails(page: Any, sid: str) -> None:
    """Een nieuwe <video> op de sessie: die geeft niets meer, dus een fout. Met een eigen query
    (de route kijkt er niet naar): anders speelt Chromium een klein bestand dat hij al helemaal
    had uit zijn eigen geheugen, zonder te vragen; een sprong buiten wat hij had, vraagt wel."""
    page.evaluate(VIDEO_JS, f"{stream_url(sid)}?opnieuw={time.monotonic_ns()}")
    page.wait_for_function(
        "() => { const v = document.getElementById('m3'); return v && v.error !== null; }",
        timeout=15_000,
    )


def direct_session(media, page, account, fixture: str, folder: str, token: str) -> tuple:
    """Upload, play (de probe gebeurt bij play), en met de strings van het item opnieuw
    (replace): de sessie zoals de speler hem krijgt."""
    up = media.upload(account, fixture, folder)
    first = codecs_of(media, token, up.id)
    out = play(media, token, up.id, {"profile": profile(page, first["candidates"])})
    found = codecs_of(media, token, up.id)
    assert found["item"], found  # de probe van play staat er nu
    body = {"profile": profile(page, found["candidates"] + found["item"]), "replace": out["sid"]}
    out = play(media, token, up.id, body)
    assert out["mode"] == "direct", out
    assert out["urls"]["file"] == "file" and out["offset"] == 0
    return up, out


@pytest.mark.media
def test_vp9_speelt_via_de_sessie_en_stopt_na_de_prullenbak(media, media_browsers, ffmpeg_count):
    owner = media.user()
    folder = media.folder(owner, "M3 VP9")
    page = media_browsers.context_as(owner).new_page()
    open_app(page)
    token = login(media, owner)["access_token"]
    up, out = direct_session(media, page, owner, "vp9_opus.webm", folder, token)
    assert out["mime"].startswith('video/webm; codecs="vp09.'), out["mime"]
    assert [t["codec"] for t in out["tracks"]["audio"]] == ["opus"]
    start_video(page, out["sid"])
    size = page.evaluate("() => { const v = document.getElementById('m3');"
                         " return [v.videoWidth, v.videoHeight]; }")  # fmt: skip
    assert size == [640, 360]
    # zoeken: een bereik, zoals de <video> dat zelf vraagt
    ranged = fetched(page, out["sid"])
    assert ranged["status"] == 206, ranged
    # naar de prullenbak: de sessie stopt meteen (files.notify → close_node)
    media.server.request("DELETE", f"/api/v1/files/{up.id}", token=token, expect=204)
    refused(media, page, out["sid"], 403, "media_access_gone", "revoked")
    video_fails(page, out["sid"])


@pytest.mark.media
def test_uitloggen_stopt_alleen_die_login(media, media_browsers, ffmpeg_count):
    owner = media.user()
    folder = media.folder(owner, "M3 login")
    page = media_browsers.context_as(owner).new_page()
    open_app(page)
    phone = login(media, owner)
    laptop = login(media, owner)
    up, out = direct_session(media, page, owner, "vp9_opus.webm", folder, phone["access_token"])
    found = codecs_of(media, laptop["access_token"], up.id)
    body = {"profile": profile(page, found["candidates"] + found["item"])}
    other = play(media, laptop["access_token"], up.id, body)
    assert other["mode"] == "direct", other
    start_video(page, out["sid"])
    media.server.request(
        "POST", "/api/v1/auth/logout", json={"refresh_token": phone["refresh_token"]}, expect=204
    )
    refused(media, page, out["sid"], 403, "media_access_gone", "revoked")
    video_fails(page, out["sid"])
    # de andere login speelt door
    start_video(page, other["sid"])
    assert fetched(page, other["sid"])["status"] == 206


@pytest.mark.media
def test_een_nieuwe_versie_geeft_replaced(media, media_browsers, ffmpeg_count):
    owner = media.user()
    folder = media.folder(owner, "M3 versie")
    page = media_browsers.context_as(owner).new_page()
    open_app(page)
    token = login(media, owner)["access_token"]
    up, out = direct_session(media, page, owner, "vp9_opus.webm", folder, token)
    start_video(page, out["sid"])
    from tests.media import fixtures

    data = fixtures.make("vp9_opus.mkv").read_bytes()
    media.server.request(
        "PUT", f"/api/v1/files/{up.id}/content", token=token, content=data, expect=200
    )
    out2 = fetched(page, out["sid"])
    assert out2["status"] == 410, out2
    assert out2["detail"] == {
        "message": out2["detail"]["message"],
        "code": "media_session_gone",
        "params": {"reason": "replaced"},
    }


@pytest.mark.media
def test_een_publieke_link_speelt_zonder_inloggen(media, media_browsers, ffmpeg_count):
    owner = media.user()
    folder = media.folder(owner, "M3 link")
    token = login(media, owner)["access_token"]
    up = media.upload(owner, "vp9_opus.webm", folder)
    made = media.server.request(
        "POST", f"/api/v1/files/{folder}/links", token=token, json={"permission": "read"},
        expect=201,
    ).json()  # fmt: skip
    opened = media.server.request(
        "POST", "/api/v1/public/open", json={"token": made["link"]["url"].removeprefix("/s/")},
        expect=200,
    ).json()  # fmt: skip
    page = media_browsers.new_context().new_page()
    page.goto(made["link"]["url"])  # de publieke pagina: niet ingelogd
    # een publieke speler kent alleen de vaste kandidaten (…/codecs vraagt een login)
    body = {"profile": profile(page, codecs_of(media, token, up.id)["candidates"])}
    out = play(media, opened["key"], up.id, body, path=f"/api/v1/public/media/{up.id}/play")
    assert out["mode"] == "direct", out
    start_video(page, out["sid"])
    media.server.request("DELETE", f"/api/v1/shares/{made['id']}", token=token, expect=204)
    # de cache van de controle is hooguit 15 s: tot dan kan het nog spelen
    deadline = time.monotonic() + ACCESS_CACHE_S + 5
    while fetched(page, out["sid"])["status"] != 403:
        assert time.monotonic() < deadline, "de sessie speelt na 20 s nog"
        page.wait_for_timeout(500)
    refused(media, page, out["sid"], 403, "media_access_gone", "revoked")


@pytest.mark.media
def test_h264_speelt_via_de_sessie_en_stopt_na_blokkeren(media, media_browsers, ffmpeg_count):
    owner = media.user()
    page = media_browsers.context_as(owner).new_page()
    open_app(page)
    require_codec(page, 'video/mp4; codecs="avc1.64001f"', "native")
    require_codec(page, 'audio/mp4; codecs="mp4a.40.2"', "native")
    folder = media.folder(owner, "M3 H264")
    token = login(media, owner)["access_token"]
    _up, out = direct_session(media, page, owner, "h264_aac.mp4", folder, token)
    assert out["mime"] == 'video/mp4; codecs="avc1.64001f,mp4a.40.2"', out["mime"]
    start_video(page, out["sid"])
    media.server.call(
        media.admin, "PATCH", f"/api/v1/users/{owner.id}", json={"disabled": True}, expect=200
    )
    refused(media, page, out["sid"], 403, "media_access_gone", "blocked")
    expect(page.locator("#m3")).to_have_count(1)
