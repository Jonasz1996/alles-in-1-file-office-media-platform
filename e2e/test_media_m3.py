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
- een deling aan een ander weg terwijl die kijkt: een lezing van /file die nog loopt, breekt
  af na hooguit 15 s en één stuk; uitloggen breekt hem meteen af (review golf 1, K5; #10 en
  #11 van de e2e-tabel);
- een publieke link op een bron die alleen met omzetten kan: 403 media_public_transcode, ook
  gevraagd vanuit de publieke pagina in de browser;
- H.264 + AAC (mp4) als de browser het kan (Chromium van Playwright 141 niet; in CI met
  MEDIA_REQUIRE_H264=1 moet het), en blokkeren: 403 blocked.
"""

from __future__ import annotations

import socket
import time
from typing import Any
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import expect

from media_harness import open_app, require_codec, wait_playing

PROBE_WAIT_S = 30.0
ACCESS_CACHE_S = 15.0  # settings.media_access_cache (Keuze 21)
PIECE_S = 5.0  # sessions.CHECK_SECONDS: guarded_body kijkt elke 5 s (of 4 MB) opnieuw
PAD = 48 << 20  # opvulling: ruim meer dan de buffers van de kernel en de browser samen

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
            contentType: types[0], width: 1920, height: 1080, bitrate: 8000000, framerate: 24,
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


PUBLIC_PLAY_JS = """async ({ url, key, body }) => {
  // play vanuit de publieke pagina; bij 409 media_probe_pending na Retry-After opnieuw
  const end = Date.now() + 30000;
  for (;;) {
    const r = await fetch(url, {
      method: "POST",
      headers: { Authorization: `Bearer ${key}`, "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const detail = (await r.json()).detail ?? null;
    const pending = r.status === 409 && detail && detail.code === "media_probe_pending";
    if (!pending || Date.now() > end) return { status: r.status, detail };
    await new Promise((ok) => setTimeout(ok, 1000 * Number(r.headers.get("retry-after") || 1)));
  }
}"""


def padded_webm(data: bytes, extra: int) -> bytes:
    """data (een webm waarvan het Segment tot het einde loopt) met een Void-element (ID 0xEC,
    opvulling van EBML) van extra bytes aan het einde van het Segment. Een geldig bestand dat
    groter is dan wat de buffers vasthouden; de probe en de speler slaan de opvulling over."""

    def vint(pos: int) -> tuple[int, int]:
        n = 9 - data[pos].bit_length()
        return n, int.from_bytes(data[pos : pos + n], "big") & ((1 << (7 * n)) - 1)

    assert data[:4] == b"\x1a\x45\xdf\xa3", "geen EBML"
    n, size = vint(4)
    seg = 4 + n + size
    assert data[seg : seg + 4] == b"\x18\x53\x80\x67", "geen Segment"
    n, size = vint(seg + 4)
    start = seg + 4 + n
    assert start + size == len(data), "het Segment loopt niet tot het einde"
    void = b"\xec" + ((1 << 56) | (extra - 9)).to_bytes(8, "big") + bytes(extra - 9)
    head = ((1 << (7 * n)) | (size + extra)).to_bytes(n, "big")
    return data[: seg + 4] + head + data[start:] + void


class SlowRead:
    """Een lezing van /file die loopt, buiten de browser. In de browser kan dat hier niet:
    Chromium las de body van een fetch helemaal van de socket, ook toen de pagina na 1,2 MB
    stopte met lezen (ss gaf lege rijen; de server was al klaar). Hier een socket met een vaste,
    kleine ontvangstbuffer (SO_RCVBUF zet de autotuning van de kernel uit): eerst 1 MB, dan
    stil, zodat de server op de verbinding wacht en de rest nog moet sturen."""

    def __init__(self, base_url: str, path: str) -> None:
        url = urlsplit(base_url)
        host, port = url.hostname or "127.0.0.1", url.port or 80
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 64 * 1024)
        self.sock.settimeout(10)
        self.sock.connect((host, port))
        ask = f"GET {path} HTTP/1.1\r\nHost: {host}:{port}\r\nConnection: close\r\n\r\n"
        self.sock.sendall(ask.encode())
        head = b""
        while b"\r\n\r\n" not in head:
            part = self.sock.recv(4096)
            assert part, "geen antwoord"
            head += part
        head, _, rest = head.partition(b"\r\n\r\n")
        lines = head.decode("latin-1").split("\r\n")
        self.status = int(lines[0].split()[1])
        fields = dict(line.lower().split(": ", 1) for line in lines[1:] if ": " in line)
        self.size = int(fields["content-length"])
        self.bytes = len(rest)

    def read(self, limit: int | None = None, timeout: float = 10) -> bool:
        """Lees tot limit bytes, of tot het einde. False als de verbinding eerder dichtging."""
        self.sock.settimeout(timeout)
        while limit is None or self.bytes < limit:
            try:
                part = self.sock.recv(1 << 16)
            except ConnectionResetError:
                return False
            if not part:
                return self.bytes >= self.size
            self.bytes += len(part)
        return True

    def close(self) -> None:
        self.sock.close()


def upload_bytes(media: Any, account: Any, parent: str, name: str, data: bytes) -> str:
    r = media.server.call(
        account,
        "PUT",
        f"/api/v1/files/{parent}/upload",
        params={"name": name},
        content=data,
        headers={"Content-Length": str(len(data))},
        expect=201,
    )
    return r.json()["id"]


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


def worker_busy(status: int, detail: dict[str, Any]) -> bool:
    """503 media_busy met reden worker: geen plaats in de worker (Keuze 12; de plaatsen "kort"
    delen probe, keyframes en de previews van net geüploade video's). De speler probeert het na
    Retry-After opnieuw (§2 M6); de andere redenen (cpu, disk, user, link) zijn een antwoord."""
    params = detail.get("params") or {}
    return status == 503 and detail.get("code") == "media_busy" and params.get("reason") == "worker"


def play(media: Any, token: str, node_id: str, body: dict[str, Any], path: str | None = None):
    """POST play zoals de speler: bij 409 media_probe_pending (de probe loopt nog) en bij 503
    media_busy met reden worker na Retry-After opnieuw."""
    path = path or f"/api/v1/media/{node_id}/play"
    end = time.monotonic() + PROBE_WAIT_S
    while True:
        r = media.server.request("POST", path, token=token, json=body)
        if r.status_code == 200:
            return r.json()
        detail = r.json().get("detail", {})
        pending = r.status_code == 409 and detail.get("code") == "media_probe_pending"
        again = pending or worker_busy(r.status_code, detail)
        assert again and time.monotonic() < end, (r.status_code, r.text)
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
def test_een_deling_intrekken_breekt_een_lopende_lezing_af(media, media_browsers, ffmpeg_count):
    """#11, en #10 voor direct play: Bob kijkt naar een video die Anna met hem deelt, en een
    lezing van /file loopt nog. Anna haalt de deling weg. Na hooguit 15 s (de cache van de
    controle) is het eerstvolgende stuk het laatste: de lezing breekt af, korter dan
    Content-Length. Daarna geeft een nieuw verzoek 403 revoked (ook in de browser), en een
    nieuwe <video> laadt niet meer."""
    from tests.media import fixtures

    anna = media.user()
    bob = media.user()
    folder = media.folder(anna, "M3 deling")
    data = padded_webm(fixtures.make("vp9_opus.webm").read_bytes(), PAD)
    node = upload_bytes(media, anna, folder, "groot.webm", data)
    share = media.server.call(
        anna, "POST", f"/api/v1/files/{folder}/shares",
        json={"user_id": bob.id, "permission": "read"}, expect=201,
    ).json()  # fmt: skip
    page = media_browsers.context_as(bob).new_page()
    open_app(page)
    token = login(media, bob)["access_token"]
    first = codecs_of(media, token, node)
    out = play(media, token, node, {"profile": profile(page, first["candidates"])})
    found = codecs_of(media, token, node)
    body = {"profile": profile(page, found["candidates"] + found["item"]), "replace": out["sid"]}
    out = play(media, token, node, body)
    assert out["mode"] == "direct", out
    start_video(page, out["sid"])
    reading = SlowRead(media.server.url, stream_url(out["sid"]))
    try:
        assert reading.status == 200 and reading.size == len(data)
        assert reading.read(1 << 20)
        media.server.call(anna, "DELETE", f"/api/v1/shares/{share['id']}", expect=204)
        revoked = time.monotonic()
        # tot de cache verloopt mag het nog; daarna lezen zo snel als het kan
        time.sleep(ACCESS_CACHE_S + 1)
        assert not reading.read(timeout=PIECE_S + 5), "de lezing liep tot het einde"
        took = time.monotonic() - revoked
    finally:
        reading.close()
    # wat er kwam: de eerste MB, wat de buffers hielden en hooguit één stuk; nooit alles
    assert reading.bytes < len(data) - PAD // 2, reading.bytes
    assert took <= ACCESS_CACHE_S + 1 + PIECE_S + 5, took
    refused(media, page, out["sid"], 403, "media_access_gone", "revoked")
    video_fails(page, out["sid"])


@pytest.mark.media
def test_uitloggen_breekt_een_lopende_lezing_meteen_af(media, media_browsers, ffmpeg_count):
    """#11: uitloggen sluit de sessies van die login meteen (close_login), ook midden in een
    /file-stream: het eerstvolgende stuk komt niet meer, zonder te wachten op de cache."""
    from tests.media import fixtures

    owner = media.user()
    folder = media.folder(owner, "M3 lezing")
    data = padded_webm(fixtures.make("vp9_opus.webm").read_bytes(), PAD)
    node = upload_bytes(media, owner, folder, "groot.webm", data)
    page = media_browsers.context_as(owner).new_page()
    open_app(page)
    phone = login(media, owner)
    token = phone["access_token"]
    first = codecs_of(media, token, node)
    out = play(media, token, node, {"profile": profile(page, first["candidates"])})
    found = codecs_of(media, token, node)
    body = {"profile": profile(page, found["candidates"] + found["item"]), "replace": out["sid"]}
    out = play(media, token, node, body)
    assert out["mode"] == "direct", out
    reading = SlowRead(media.server.url, stream_url(out["sid"]))
    try:
        assert reading.status == 200 and reading.read(1 << 20)
        media.server.request(
            "POST", "/api/v1/auth/logout", json={"refresh_token": phone["refresh_token"]},
            expect=204,
        )  # fmt: skip
        out_at = time.monotonic()
        assert not reading.read(timeout=PIECE_S + 5), "de lezing liep tot het einde"
        took = time.monotonic() - out_at
    finally:
        reading.close()
    assert reading.bytes < len(data) - PAD // 2, reading.bytes
    assert took < 2, took  # meteen: niet pas bij de controle van na 5 s
    refused(media, page, out["sid"], 403, "media_access_gone", "revoked")


@pytest.mark.media
def test_publiek_omzetten_geeft_403_ook_in_de_browser(media, media_browsers, ffmpeg_count):
    """#10: een bron die alleen met omzetten kan (xvid + mp3), via een publieke link zonder
    public_transcode: play vanuit de publieke pagina geeft 403 media_public_transcode."""
    owner = media.user()
    folder = media.folder(owner, "M3 link omzetten")
    token = login(media, owner)["access_token"]
    up = media.upload(owner, "xvid_mp3.avi", folder)
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
    body = {"profile": profile(page, codecs_of(media, token, up.id)["candidates"])}
    url = f"/api/v1/public/media/{up.id}/play"
    out = page.evaluate(PUBLIC_PLAY_JS, {"url": url, "key": opened["key"], "body": body})
    assert out["status"] == 403, out
    assert out["detail"]["code"] == "media_public_transcode", out


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
