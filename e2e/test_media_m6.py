"""M6: de speler zelf in de browser (fase 5, §2 M6 van het bouwontwerp; §4.3 #2–#4, #7, #9, #18,
#19 en #20 voor het deel van de speler).

Met media aan en de lokale worker (de fixture media), tegen de echte routes van M3, M4 en M5
(play, de stroom, de ondertitels, de sprites), nooit fake_stream. De speler meet het profiel
(clientprofiel.md), vraagt play, en speelt direct (het bestand), of via MSE (map.json, de init
per X-Init, de segmenten).

Sinds het samenvoegen van golf 3 is ook de bibliotheek echt (M2): de tests met een serie, delen
of sprites maken een bibliotheek via de API, en de scanner maakt de items uit de namen; /kijken
haalt de titel, de serie en het volgende deel uit getItem en getShow. Een bestand buiten een
bibliotheek heeft geen item (404): dan de naam van het bestand. (Tot golf 3 stond hier een
stand-in voor de routes van M2 in de browser; m6.md, afwijking 1.)

1. direct: vp9_opus.webm; het profiel van de meting, het eerste beeld (requestVideoFrameCallback)
   en zoeken met 5 (50 %);
2. remux: vp9_opus.mkv; zoeken naar 50 % en weer lopen; audiospoor A, B, A (twee wissels tegen de
   echte routes), de positie blijft (±1 s); en een sprong voorbij de cache in een lange remux
   start een nieuwe producent;
3. transcode: hevc10_hdr10.mkv naar 720 (VP9 + Opus in Chromium 141, H.264 in CI): de trede uit
   de opdracht van ffmpeg en de init, de redenen in Info, de SRT als <track> en de ASS-cue met
   \\an8 bovenaan;
4. een ander audiospoor bij een transcode (multi_audio.mkv), met de regel voor geforceerd;
5. kwaliteit 720 naar 480 met het menu: een nieuwe sessie, de positie blijft;
6. de kaart "Volgende aflevering" met de aftelling; Esc annuleert; daarna vanzelf door; cd1 naar
   cd2 zonder kaart;
7. PiP: requestPictureInPicture wordt aangeroepen;
8. alleen met het toetsenbord (docs/fase-5/toetsenbord-m6.md);
9. de app herstart midden in het afspelen, en met outage_s=30: de speler loopt ≤ 10 s na "ready";
10. geen fouten in de console en geen CSP-meldingen: de bewaking van conftest, in elke test;
11. sprites bij hover (review golf 2, K3) en de hoofdstukken in de tijdbalk;
plus de viewer van fase 3: de speler in het venster, en de fallback als play aan het begin niet
lukt; en #19: na 2 haperingen (het netwerk geknepen via CDP) zelf een trede lager.
"""

from __future__ import annotations

import contextlib
import json
import re
import subprocess
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import expect

from media_harness import (
    cmdline,
    codec_supported,
    comm,
    descendants,
    first_frame_ms,
    open_app,
    process_table,
    require_codec,
)
from test_media_m3 import login
from test_media_m5 import library_item, wait_state

PLAYER = ".player"
VIDEO = ".player-video"
H264 = "avc1.640028"
RESTART_LIMIT_S = 10.0  # §2 M6 #9 en §4.3 #18: binnen 10 s weer lopen
SCAN_S = 35.0  # Klaar als van M2: een item verschijnt binnen 35 s (de tik is hier 0,5 s)
PLAY_RE = re.compile(r"/api/v1/media/[^/]+/play$")

STATE_JS = """() => {
  const p = document.querySelector(".player");
  const v = document.querySelector(".player-video");
  if (!p || !v) return null;
  return { state: p.dataset.state, mode: p.dataset.mode || null, t: v.currentTime,
           paused: v.paused, ended: v.ended, w: v.videoWidth, h: v.videoHeight,
           error: v.error ? v.error.code : null, ready: v.readyState };
}"""

# Speelt hij, voorbij t en (optioneel) na een tijd van de klok in de pagina?
PLAYING_PAST_JS = """([t]) => {
  const v = document.querySelector(".player-video");
  const ok = v && !v.error && !v.paused && v.readyState >= 3 && v.currentTime >= t;
  return ok ? v.currentTime : null;
}"""

# De cues van de gekozen <track> (alleen-lezen van de TextTrack-API)
CUES_JS = """(id) => {
  const el = document.querySelector(`.player-video track[data-id="${id}"]`);
  if (!el || !el.track || !el.track.cues) return null;
  return { mode: el.track.mode, ready: el.readyState,
           cues: Array.from(el.track.cues).map((c) => ({ start: c.startTime, end: c.endTime,
             text: c.text, line: c.line, snap: c.snapToLines })) };
}"""

PIP_SPY_JS = """(() => {
  window.__e2ePip = 0;
  HTMLVideoElement.prototype.requestPictureInPicture = function () {
    window.__e2ePip += 1;
    return Promise.resolve({ width: 320, height: 180 });
  };
})();"""


# --- het meeschrijven van play, de segmenten en de encoders ---------------------------------------


class Plays:
    """Elke POST …/play van de pagina, met de body en het antwoord. Via route.fetch: de test heeft
    het antwoord zonder te lezen na een navigatie (de regel van de e2e)."""

    def __init__(self, context: Any) -> None:
        self.calls: list[dict[str, Any]] = []
        self.context = context
        context.route(PLAY_RE, self._play)

    def stop(self) -> None:
        """De route weg, voor een test die van de pagina weggaat. Met een route zet Playwright de
        onderschepping van CDP aan voor elk verzoek van de context, ook voor wat de route niet
        raakt. Een fetch met keepalive bij pagehide (de DELETE van de speler) bleef dan ±1 op 10
        keer hangen tot de context dichtging (gemeten: 13 van 130 met de route, 0 van 120
        zonder of na unroute; een aanroep van Playwright laat hem niet los)."""
        self.context.unroute(PLAY_RE, self._play)

    def _play(self, route: Any) -> None:
        request = route.request
        if request.method != "POST":
            route.fallback()
            return
        response = route.fetch()
        body = json.loads(request.post_data or "{}")
        try:
            out = response.json()
        except Exception:
            out = None
        self.calls.append({"body": body, "status": response.status, "out": out})
        route.fulfill(response=response)

    @property
    def ok(self) -> list[dict[str, Any]]:
        """De geslaagde: body en antwoord."""
        return [c for c in self.calls if c["status"] == 200]

    def wait(self, page: Any, n: int, seconds: float = 30) -> dict[str, Any]:
        """Tot er n geslaagde zijn; geeft de laatste. Met page.wait_for_timeout: dan handelt
        Playwright de routes af (time.sleep niet)."""
        end = time.monotonic() + seconds
        while len(self.ok) < n:
            assert time.monotonic() < end, f"{len(self.ok)} van {n} play: {self.calls[-3:]}"
            page.wait_for_timeout(100)
        return self.ok[n - 1]


class Segments:
    """De antwoorden op segmenten (*.m4s): het nummer en X-Init (alleen de koppen)."""

    def __init__(self, page: Any) -> None:
        self.seen: list[tuple[str, int, str | None]] = []
        page.on("response", self._response)

    def _response(self, response: Any) -> None:
        m = re.search(r"/api/v1/stream/([^/]+)/(\d+)\.m4s$", urlsplit(response.url).path)
        if m and response.status == 200:
            self.seen.append((m.group(1), int(m.group(2)), response.headers.get("x-init")))


class Encoders:
    """De opdrachtregels van elke ffmpeg in de boom van de app (een eigen draad, elke 0,1 s): de
    trede (de maat in het filter, en bij H.264 -preset) staat daar. admission.snapshot() leeft in
    het proces van de app en zegt de trede niet (een Grant heeft alleen de kosten); m6.md,
    afwijking 3."""

    def __init__(self, media: Any) -> None:
        self.media = media
        self.lines: dict[int, str] = {}
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="m6-encoders", daemon=True)

    def __enter__(self) -> Encoders:
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._stop.set()
        self._thread.join(timeout=5)

    def _run(self) -> None:
        while not self._stop.wait(0.1):
            pid = self.media.pid
            if not pid:
                continue
            table = process_table()
            for child in descendants([pid], table):
                if child not in self.lines and comm(child) == "ffmpeg":
                    line = cmdline(child)
                    if line:
                        self.lines[child] = line

    def streams(self) -> list[str]:
        """De ffmpeg van de stroom (met -f mp4 naar pipe:1), niet de probe of de previews."""
        return [line for line in self.lines.values() if "frag_keyframe" in line or "-f mp4" in line]


# --- hulpjes -----------------------------------------------------------------------------------


def state(page: Any) -> dict[str, Any]:
    out = page.evaluate(STATE_JS)
    assert out is not None, "geen speler"
    return out


def watch(page: Any, node_id: str, start: float | None = None) -> None:
    """/kijken/:node openen (na open_app: het verversen van het token is dan klaar)."""
    page.goto(f"/kijken/{node_id}" + (f"?t={start:g}" if start else ""))
    expect(page.locator(PLAYER)).to_be_visible()


def playing_past(page: Any, t: float, timeout_ms: float = 20_000) -> float:
    handle = page.wait_for_function(PLAYING_PAST_JS, arg=[t], timeout=timeout_ms)
    return float(handle.json_value())


def ready_state(page: Any, mode: str | None = None, timeout_ms: float = 30_000) -> None:
    expect(page.locator(PLAYER)).to_have_attribute("data-state", "ready", timeout=timeout_ms)
    if mode:
        expect(page.locator(PLAYER)).to_have_attribute("data-mode", mode)


def pause_at(page: Any, t: float) -> float:
    """Spelen tot voorbij t, dan pauzeren (K); geeft de positie."""
    playing_past(page, t)
    page.keyboard.press("k")
    page.wait_for_function("() => document.querySelector('.player-video').paused")
    return float(state(page)["t"])


def choose(page: Any, menu: str, name: Any, nth: int = 0) -> None:
    """Een keuze in een menu van de speler met de muis (de knop, dan het menu-item)."""
    page.locator(PLAYER).hover()
    page.get_by_role("button", name=menu, exact=True).click()
    m = page.get_by_role("menu", name=menu)
    expect(m).to_be_visible()
    m.get_by_role("menuitemradio", name=name).nth(nth).click()
    expect(m).to_be_hidden()


def scanned(
    media: Any, owner: Any, root_id: str, kind: str, nodes: list[str]
) -> dict[str, dict[str, Any]]:
    """Een bibliotheek op root_id via de API van M2; wacht tot de scanner (tik 0,5 s in het
    harnas) voor elk van de nodes een item maakte. Geeft {node_id: Item}, opnieuw gelezen als ze
    er allemaal zijn (de delen van een film rekent M2 bij het lezen uit)."""
    media.server.call(
        owner, "POST", "/api/v1/media/libraries", json={"root_id": root_id, "kind": kind},
        expect=201,
    )  # fmt: skip
    end = time.monotonic() + SCAN_S
    seen: set[str] = set()
    while len(seen) < len(nodes):
        assert time.monotonic() < end, f"na {SCAN_S} s {len(seen)} van {len(nodes)} items"
        for node in nodes:
            if node not in seen:
                r = media.server.call(owner, "GET", f"/api/v1/media/items/{node}")
                if r.status_code == 200:
                    seen.add(node)
        time.sleep(0.25)
    return {
        node: media.server.call(owner, "GET", f"/api/v1/media/items/{node}", expect=200).json()
        for node in nodes
    }


@pytest.fixture(autouse=True)
def pages_away(media_browsers: Any) -> Iterator[None]:
    """Na elke test elke pagina naar about:blank: pagehide, en de speler sluit zijn sessie. Zonder
    dat blijft een sessie (en haar plaats bij de toelating) leven tot de server haar opruimt, en
    krijgt een volgende test met een transcode 503 media_busy (3 plaatsen in het harnas). De
    contexts zelf sluit media_browsers daarna (Browsers._open: de open contexts)."""
    yield
    for context, _guard in list(media_browsers._open):
        # Eerst de routes weg (Plays): anders blijft de DELETE met keepalive soms hangen tot de
        # context dicht is (Plays.stop)
        with contextlib.suppress(Exception):
            context.unroute_all(behavior="ignoreErrors")
        for page in context.pages:
            with contextlib.suppress(Exception):
                page.goto("about:blank", timeout=5_000)


def setup(media: Any, media_browsers: Any, name: str) -> tuple[Any, str, Any, Any]:
    """Een gebruiker, een map (geen bibliotheek), een context en een pagina waarop de app open
    is. Geeft (owner, map, context, page)."""
    owner = media.user()
    folder = media.folder(owner, name)
    context = media_browsers.context_as(owner)
    page = context.new_page()
    open_app(page)
    return owner, folder, context, page


TRICK_JS = """async (url) => {
  const r = await fetch(url, { cache: "no-store" });
  return { status: r.status, text: await r.text() };
}"""


def trick_cues(page: Any, sid: str) -> list[dict[str, Any]]:
    """De cues van trick.vtt van de sessie: begin, eind, tegel en vak (xywh)."""
    got = page.evaluate(TRICK_JS, f"/api/v1/stream/{sid}/trick.vtt")
    assert got["status"] == 200, got

    def secs(stamp: str) -> float:
        h, m, rest = stamp.split(":")
        return int(h) * 3600 + int(m) * 60 + float(rest)

    cues = []
    lines = got["text"].split("\n")
    for i, line in enumerate(lines):
        if "-->" in line:
            a, b = (x.strip() for x in line.split("-->"))
            tile, _, box = lines[i + 1].partition("#xywh=")
            cues.append({"start": secs(a), "end": secs(b), "tile": tile,
                         "xywh": [int(v) for v in box.split(",")]})  # fmt: skip
    return cues


def loop_mkv(tmp: Path, times: int, fixture: str = "vp9_opus.mkv") -> Path:
    """Een fixture times keer achter elkaar (-c copy, geen codering): een lange bron voor de
    sprong voorbij de cache, de herstart en het haperen. Eén ffmpeg, buiten de app."""
    from tests.media import fixtures

    out = tmp / f"lang-{fixture}"
    subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-y", "-stream_loop", str(times - 1),
         "-i", str(fixtures.make(fixture)), "-map", "0", "-c", "copy", str(out)],
        check=True, timeout=60,
    )  # fmt: skip
    return out


def leave(page: Any, ffmpeg_count: Any) -> None:
    """Weg van /kijken in de app (Esc): de speler sluit zijn sessie (DELETE), de producent stopt,
    en er loopt geen ffmpeg meer (anders wacht die tot hij zelf stil valt)."""
    page.keyboard.press("Escape")
    expect(page).not_to_have_url(re.compile("/kijken/"))
    assert not ffmpeg_count.wait_zero(15), "ffmpeg loopt nog na het sluiten van de sessie"


def upload_path(media: Any, owner: Any, folder: str, path: Path) -> str:
    size = path.stat().st_size
    with open(path, "rb") as body:
        r = media.server.call(
            owner, "PUT", f"/api/v1/files/{folder}/upload", params={"name": path.name},
            content=body, headers={"Content-Length": str(size)}, expect=201,
        )  # fmt: skip
    return r.json()["id"]


# --- 1. direct ---------------------------------------------------------------------------------


@pytest.mark.media
def test_direct_het_profiel_het_eerste_beeld_en_zoeken(media, media_browsers, ffmpeg_count):
    owner, folder, context, page = setup(media, media_browsers, "M6 direct")
    plays = Plays(context)
    up = media.upload(owner, "vp9_opus.webm", folder)
    media.analyse(up.sha)  # de probe vooraf: dan geeft …/codecs de echte strings van het item
    media.wait_probe(up.sha)
    watch(page, up.id)
    took = first_frame_ms(page, VIDEO)
    ready_state(page, "direct")
    assert took < 15_000, took
    first = plays.wait(page, 1)
    profile = first["body"]["profile"]
    # De meting van clientprofiel.md: de vorm, de grens van 64 en de strings van het item
    assert profile["v"] == 1 and profile["base"] == "web", profile
    assert profile["client"]["kind"] == "web" and profile["client"]["name"].startswith("Chrom")
    assert profile["engines"]["mse"] is True, profile["engines"]
    assert 0 < len(profile["codecs"]) <= 64, len(profile["codecs"])
    assert profile["codecs"]["opus"]["mse"] is True, profile["codecs"]
    assert profile["codecs"]["vp09.00.40.08"]["mse"] is True, profile["codecs"]
    screen = page.evaluate("() => Math.round(screen.height * devicePixelRatio)")
    assert profile["max"]["height"] == screen, (profile["max"], screen)
    found = media.server.call(owner, "GET", f"/api/v1/media/{up.id}/codecs", expect=200).json()
    assert found["item"] and set(found["item"]) <= set(profile["codecs"]), (found, profile)
    for s in found["item"]:
        assert profile["codecs"][s]["file"] is True, (s, profile["codecs"][s])
    assert "replace" not in first["body"] and "start_ms" not in first["body"], first["body"]
    out = first["out"]
    assert out["mode"] == "direct" and out["urls"]["file"] == "file", out
    s = state(page)
    assert [s["w"], s["h"]] == [640, 360], s
    expect(page.locator(".player-title")).to_have_text("vp9_opus.webm")
    expect(page).to_have_title(re.compile("vp9_opus.webm"))
    # Zoeken met 5: naar 50 % (3 s) en weer lopen, zonder nieuwe sessie (direct zoekt zelf)
    playing_past(page, 0.3)
    page.keyboard.press("5")
    assert playing_past(page, 3.1) >= 3.1
    assert len(plays.ok) == 1, plays.calls


# --- 2. remux, audiospoor A, B, A, en een sprong voorbij de cache ---------------------------------


@pytest.mark.media
def test_remux_zoeken_en_audiospoor_a_b_a(media, media_browsers, ffmpeg_count):
    owner, folder, context, page = setup(media, media_browsers, "M6 remux")
    plays = Plays(context)
    segments = Segments(page)
    up = media.upload(owner, "vp9_opus.mkv", folder)
    watch(page, up.id)
    ready_state(page, "remux")
    out = plays.wait(page, 1)["out"]
    assert out["reasons"] == ["container"], out
    assert [a["lang"] for a in out["tracks"]["audio"]] == ["nld", "eng"], out["tracks"]
    playing_past(page, 0.5)
    # Zoeken naar 50 %: weer lopen voorbij 3 s, in dezelfde sessie
    page.keyboard.press("5")
    playing_past(page, 3.1)
    assert len(plays.ok) == 1
    # A, B, A: elke wissel een nieuwe sessie met replace, op de positie van nu
    page.keyboard.press("Home")
    at = pause_at(page, 1.0)
    audio = [a["index"] for a in out["tracks"]["audio"]]
    sids = [out["sid"]]
    for n, want in ((2, audio[1]), (3, audio[0])):
        page.keyboard.press("a")
        call = plays.wait(page, n)
        assert call["body"]["replace"] == sids[-1], call["body"]
        assert call["body"].get("audio") == want, call["body"]
        assert abs(call["body"].get("start_ms", 0) / 1000 - at) <= 1.0, (call["body"], at)
        sids.append(call["out"]["sid"])
        ready_state(page, "remux")
        page.wait_for_function("() => document.querySelector('.player-video').readyState >= 2")
        assert abs(state(page)["t"] - at) <= 1.0, (state(page), at)
    expect(page.locator(".sr-only[role=status]")).to_contain_text("Audiospoor")
    page.keyboard.press("k")
    playing_past(page, at + 0.5)
    # Elke sessie kreeg zijn segmenten; X-Init is per sessie een sleutel (hier steeds 1)
    by_sid = {sid: [g for s, _n, g in segments.seen if s == sid] for sid in sids}
    assert all(by_sid[sid] for sid in sids[1:]), {k[:8]: v for k, v in by_sid.items()}
    assert {g for gens in by_sid.values() for g in gens} == {"1"}, by_sid


@pytest.mark.media
def test_een_sprong_voorbij_de_cache_start_een_nieuwe_producent(
    media, media_browsers, ffmpeg_count, tmp_path
):
    owner, folder, context, page = setup(media, media_browsers, "M6 cache")
    plays = Plays(context)
    # 300 s; de producent loopt hooguit 60 s voor (LEAD_MS). De sprong gaat naar het midden: de
    # nieuwe producent heeft dan meer dan 60 s voor zich, wacht op de tegendruk en leeft tot de
    # sessie dicht is. Eerst ging de sprong naar 90 % van 150 s; de producent van de laatste 15 s
    # was dan in ±90 ms klaar, en Encoders (elke 0,1 s) zag hem soms niet.
    path = loop_mkv(tmp_path, 50)
    node = upload_path(media, owner, folder, path)
    path.unlink()
    with Encoders(media) as enc:
        watch(page, node)
        ready_state(page, "remux")
        out = plays.wait(page, 1)["out"]
        assert out["duration_ms"] >= 290_000, out["duration_ms"]
        playing_past(page, 1.0)
        before = set(enc.lines)
        page.keyboard.press("5")  # 50 %: 150 s, ver voorbij wat de producent maakte
        target = out["duration_ms"] / 1000 * 0.5
        assert playing_past(page, target + 0.5) >= target
        page.wait_for_timeout(300)
        new = [line for pid, line in enc.lines.items() if pid not in before]
    assert len(plays.ok) == 1, plays.calls  # dezelfde sessie
    assert any("-ss" in line for line in new), (new, list(enc.lines.values()))
    leave(page, ffmpeg_count)


# --- 3. transcode naar 720 ------------------------------------------------------------------------


def expected_codec(page: Any) -> str:
    """Wat de server kiest: H.264 als de browser het via MSE kan (CI), anders VP9."""
    return "avc1" if codec_supported(page, H264, "mse") else "vp09"


@pytest.mark.media
def test_transcode_hdr_naar_720_met_ondertitels(media, media_browsers, ffmpeg_count):
    owner, folder, context, page = setup(media, media_browsers, "M6 transcode")
    plays = Plays(context)
    up = media.upload(owner, "hevc10_hdr10.mkv", folder)
    codec = expected_codec(page)
    with Encoders(media) as enc:
        watch(page, up.id)
        ready_state(page, "transcode")
        out = plays.wait(page, 1)["out"]
        playing_past(page, 0.5)
    assert {"video_codec", "hdr"} <= set(out["reasons"]), out["reasons"]
    # Een bron van 720: "Origineel" is 720, daaronder 480
    assert out["quality"]["current"] == "auto", out["quality"]
    assert out["quality"]["options"] == ["auto", "source", 480], out["quality"]
    assert codec in out["mime"], out["mime"]
    s = state(page)
    assert s["h"] == 720, s
    # De trede: 720 (de maat in het filter van de tonemap), bij H.264 ook -preset veryfast
    lines = enc.streams()
    assert lines, list(enc.lines.values())
    assert any("h=720" in line for line in lines), lines
    if codec == "avc1":
        assert any("-preset veryfast" in line for line in lines), lines
    # Info: de redenen in woorden
    page.keyboard.press("i")
    info = page.get_by_role("region", name="Info")
    expect(info).to_be_visible()
    expect(info).to_contain_text("Omgezet")
    expect(info).to_contain_text("De video is HDR en het scherm niet")
    expect(info).to_contain_text("1280×720")
    page.keyboard.press("Escape")
    expect(info).to_be_hidden()
    # De ondertitels: de SRT als <track> (subs/{id}.vtt, bij 202 wachten), de ASS met \an8 boven
    subs = out["tracks"]["subtitles"]
    assert len(subs) == 2 and all(x["state"] != "burn" for x in subs), subs
    for n, sub in enumerate(subs):
        page.keyboard.press("Home")
        choose(page, "Ondertitels", re.compile(r"^Nederlands"), nth=n)  # beide zijn Nederlands
        page.wait_for_function(
            "(id) => { const el = document.querySelector(`.player-video track[data-id='${id}']`);"
            " return el && el.track.mode === 'showing' && el.track.cues"
            " && el.track.cues.length > 0; }",
            arg=sub["id"],
            timeout=30_000,
        )
        got = page.evaluate(CUES_JS, sub["id"])
        assert got["mode"] == "showing" and got["cues"], got
        # De fixture: eerst de SRT (alles onderaan, line auto), dan de ASS, met één cue met \an8
        # (line 0: bovenaan)
        lines = {c["text"]: c["line"] for c in got["cues"]}
        if n == 0:
            assert set(lines.values()) == {"auto"}, got
        else:
            assert lines["Bovenaan in beeld"] == 0 and lines["Onderaan, gewoon"] == "auto", got
    # Uit: geen track meer zichtbaar
    page.keyboard.press("c")
    page.wait_for_function(
        "() => Array.from(document.querySelectorAll('.player-video track')).every("
        "(t) => t.track.mode !== 'showing')"
    )


@pytest.mark.media
def test_transcode_naar_h264_in_ci(media, media_browsers, ffmpeg_count):
    """§2 M6 #3: in CI (Chrome for Testing) naar H.264; anders overgeslagen met de reden, en met
    MEDIA_REQUIRE_H264=1 faalt hij."""
    owner, folder, context, page = setup(media, media_browsers, "M6 h264")
    require_codec(page, H264, "mse")
    plays = Plays(context)
    up = media.upload(owner, "hevc10_hdr10.mkv", folder)
    with Encoders(media) as enc:
        watch(page, up.id)
        ready_state(page, "transcode")
        out = plays.wait(page, 1)["out"]
        playing_past(page, 0.5)
    assert out["mime"].startswith('video/mp4; codecs="avc1.'), out["mime"]
    assert state(page)["h"] == 720
    lines = enc.streams()
    assert any("libx264" in x and "-preset veryfast" in x for x in lines), lines


# --- 4. een ander audiospoor bij een transcode --------------------------------------------------


@pytest.mark.media
def test_een_ander_audiospoor_bij_een_transcode(media, media_browsers, ffmpeg_count):
    owner, folder, context, page = setup(media, media_browsers, "M6 audio")
    plays = Plays(context)
    up = media.upload(owner, "multi_audio.mkv", folder)
    watch(page, up.id)
    ready_state(page)
    first = plays.wait(page, 1)["out"]
    mode = "remux" if codec_supported(page, H264, "mse") else "transcode"
    assert first["mode"] == mode, first
    # De regel voor geforceerd: het geforceerde Nederlandse spoor staat aan bij Nederlands geluid
    forced = [s for s in first["tracks"]["subtitles"] if s["forced"]]
    assert len(forced) == 1, first["tracks"]
    page.wait_for_function(
        "(id) => { const el = document.querySelector(`.player-video track[data-id='${id}']`);"
        " return el && el.track.mode === 'showing'; }",
        arg=forced[0]["id"],
        timeout=30_000,
    )
    at = pause_at(page, 1.5)
    choose(page, "Audiospoor", re.compile(r"^Engels"))
    call = plays.wait(page, 2)
    assert call["body"]["replace"] == first["sid"], call["body"]
    eng = next(a for a in first["tracks"]["audio"] if a["lang"] == "eng")
    assert call["body"]["audio"] == eng["index"], call["body"]
    ready_state(page)
    page.wait_for_function("() => document.querySelector('.player-video').readyState >= 2")
    assert abs(state(page)["t"] - at) <= 1.0, (state(page), at)
    # Engels geluid: het Nederlandse geforceerde spoor gaat uit
    page.wait_for_function(
        "() => Array.from(document.querySelectorAll('.player-video track')).every("
        "(t) => t.track.mode !== 'showing')"
    )
    page.keyboard.press("k")
    playing_past(page, at + 0.5)


# --- 5. kwaliteit 720 naar 480 --------------------------------------------------------------------


@pytest.mark.media
def test_kwaliteit_720_naar_480(media, media_browsers, ffmpeg_count):
    owner, folder, context, page = setup(media, media_browsers, "M6 kwaliteit")
    plays = Plays(context)
    up = media.upload(owner, "hevc10_hdr10.mkv", folder)
    watch(page, up.id)
    ready_state(page, "transcode")
    first = plays.wait(page, 1)["out"]
    assert 480 in first["quality"]["options"], first["quality"]
    at = pause_at(page, 1.5)
    assert state(page)["h"] == 720
    choose(page, "Kwaliteit", "480p")
    call = plays.wait(page, 2)
    assert call["body"]["quality"] == 480 and call["body"]["replace"] == first["sid"], call
    assert call["out"]["quality"]["current"] == 480, call["out"]["quality"]
    ready_state(page, "transcode")
    page.wait_for_function("() => document.querySelector('.player-video').videoHeight === 480")
    assert abs(state(page)["t"] - at) <= 1.0, (state(page), at)
    expect(page.locator(".sr-only[role=status]")).to_have_text("Kwaliteit: 480p")
    page.keyboard.press("k")
    playing_past(page, at + 0.5)


# --- 6. de volgende aflevering en het volgende deel --------------------------------------------


@pytest.mark.media
def test_de_kaart_volgende_aflevering(media, media_browsers, ffmpeg_count):
    """Een echte bibliotheek met series (M2): de scanner maakt de serie en haar afleveringen uit de
    namen, en /kijken haalt de titel en de volgende aflevering uit getItem en getShow."""
    owner = media.user()
    root = media.folder(owner, "M6 series")
    season = media.folder(owner, "Season 1", media.folder(owner, "De serie", root))
    one = media.upload(owner, "chapters.mkv", season, name="De serie S01E01 Het begin.mkv")
    two = media.upload(owner, "vp9_opus.webm", season, name="De serie S01E02 Het vervolg.webm")
    found = scanned(media, owner, root, "shows", [one.id, two.id])
    got = [(found[x]["season"], found[x]["episode"], found[x]["title"]) for x in (one.id, two.id)]
    assert got == [(1, 1, "Het begin"), (1, 2, "Het vervolg")], found
    show_id = found[one.id]["show_id"]
    assert show_id and found[two.id]["show_id"] == show_id, found
    context = media_browsers.context_as(owner)
    plays = Plays(context)
    page = context.new_page()
    open_app(page)
    watch(page, one.id, start=21)
    ready_state(page)
    out = plays.wait(page, 1)["out"]
    assert abs(out["credits_ms"] - 24_000) < 100, out["credits_ms"]  # "End Credits" op 24 s
    assert plays.ok[0]["body"]["start_ms"] == 21_000
    expect(page.locator(".player-title")).to_have_text("Het begin")
    # Bij het begin van de aftiteling: de kaart, met de aftelling
    next_card = page.get_by_role("region", name="Volgende aflevering")
    expect(next_card).to_be_visible(timeout=20_000)
    expect(next_card).to_contain_text("S1 · A2")
    expect(next_card).to_contain_text("Het vervolg")
    expect(next_card.locator(".player-next-count")).to_have_text(
        re.compile(r"Begint over (10|9|8) seconden")
    )
    count = next_card.locator(".player-next-count").inner_text()
    expect(next_card.locator(".player-next-count")).not_to_have_text(count)
    # Esc annuleert: de kaart weg, en na het einde blijft hij hier
    page.keyboard.press("Escape")
    expect(next_card).to_be_hidden()
    page.wait_for_function("() => document.querySelector('.player-video').ended", timeout=20_000)
    page.wait_for_timeout(1500)
    assert urlsplit(page.url).path == f"/kijken/{one.id}", page.url
    # Terug van voor de aftiteling (7: 70 %, 21 s; de kaart mag dan weer komen) en spelen: nu
    # loopt de aftelling af en gaat hij vanzelf door
    page.keyboard.press("7")
    page.keyboard.press("k")
    playing_past(page, 21.5)
    expect(next_card).to_be_visible(timeout=20_000)
    expect(page).to_have_url(re.compile(f"/kijken/{two.id}$"), timeout=25_000)
    expect(page.locator(".player-title")).to_have_text("Het vervolg")
    ready_state(page, "direct")
    playing_past(page, 0.3)
    # N (volgende) bestaat hier niet meer (de laatste aflevering): niets gebeurt
    page.keyboard.press("n")
    page.wait_for_timeout(500)
    assert urlsplit(page.url).path == f"/kijken/{two.id}"


@pytest.mark.media
def test_cd1_gaat_vanzelf_naar_cd2(media, media_browsers, ffmpeg_count):
    """Een film in delen in een echte bibliotheek (M2): het item noemt beide delen."""
    owner = media.user()
    folder = media.folder(owner, "M6 delen")
    one = media.upload(owner, "vp9_opus.webm", folder, name="De film cd1.webm")
    two = media.upload(owner, "vp9_opus.webm", folder, name="De film cd2.webm")
    found = scanned(media, owner, folder, "movies", [one.id, two.id])
    parts = [{"node_id": one.id, "part": 1}, {"node_id": two.id, "part": 2}]
    assert found[one.id]["parts"] == parts and found[one.id]["title"] == "De film", found
    context = media_browsers.context_as(owner)
    page = context.new_page()
    open_app(page)
    watch(page, one.id, start=4)
    ready_state(page, "direct")
    expect(page.locator(".player-title")).to_have_text("De film")
    # Geen kaart bij een deel: na het einde meteen het volgende deel
    expect(page).to_have_url(re.compile(f"/kijken/{two.id}$"), timeout=20_000)
    assert page.get_by_role("region", name="Volgende aflevering").count() == 0
    ready_state(page, "direct")
    playing_past(page, 0.3)


# --- 7. PiP --------------------------------------------------------------------------------------


@pytest.mark.media
def test_pip_vraagt_beeld_in_beeld(media, media_browsers, ffmpeg_count):
    owner, folder, context, page = setup(media, media_browsers, "M6 pip")
    context.add_init_script(PIP_SPY_JS)
    up = media.upload(owner, "vp9_opus.webm", folder)
    watch(page, up.id)
    ready_state(page, "direct")
    playing_past(page, 0.2)
    page.keyboard.press("p")
    page.wait_for_function("() => window.__e2ePip === 1")
    page.locator(PLAYER).hover()
    page.get_by_role("button", name="Beeld-in-beeld").click()
    page.wait_for_function("() => window.__e2ePip === 2")


# --- 8. alleen met het toetsenbord ---------------------------------------------------------------


def focused(page: Any) -> str:
    return page.evaluate(
        "() => { const a = document.activeElement;"
        " return a ? (a.getAttribute('aria-label') || a.getAttribute('role') || a.tagName) : ''; }"
    )


def tab_to(page: Any, label: str, limit: int = 30) -> None:
    for _ in range(limit):
        page.keyboard.press("Tab")
        if focused(page) == label:
            return
    raise AssertionError(f"met Tab niet bij {label!r} (nu {focused(page)!r})")


@pytest.mark.media
def test_alleen_met_het_toetsenbord(media, media_browsers, ffmpeg_count):
    """De checklist van docs/fase-5/toetsenbord-m6.md, op /kijken (remux: alle menu's)."""
    owner, folder, context, page = setup(media, media_browsers, "M6 toetsen")
    plays = Plays(context)
    up = media.upload(owner, "vp9_opus.mkv", folder)
    watch(page, up.id)
    ready_state(page, "remux")
    out = plays.wait(page, 1)["out"]
    playing_past(page, 0.3)
    video = page.locator(VIDEO)
    # Spatie en K: pauze en weer spelen
    page.keyboard.press(" ")
    page.wait_for_function("() => document.querySelector('.player-video').paused")
    page.keyboard.press("k")
    playing_past(page, 0.5)
    # J en L: 10 s (de video is 6 s: naar het begin en het eind), ← en → 5 s
    page.keyboard.press("Home")
    page.keyboard.press("l")
    page.wait_for_function("() => document.querySelector('.player-video').currentTime >= 5")
    page.keyboard.press("j")
    page.wait_for_function("() => document.querySelector('.player-video').currentTime < 1.5")
    page.keyboard.press("ArrowRight")
    page.wait_for_function("() => document.querySelector('.player-video').currentTime >= 4.5")
    page.keyboard.press("ArrowLeft")
    page.wait_for_function("() => document.querySelector('.player-video').currentTime < 2")
    # ↑ ↓ volume, M geluid uit
    volume = video.evaluate("v => v.volume")
    page.keyboard.press("ArrowDown")
    page.wait_for_function(f"() => document.querySelector('.player-video').volume < {volume}")
    page.keyboard.press("ArrowUp")
    page.wait_for_function(f"() => document.querySelector('.player-video').volume >= {volume}")
    page.keyboard.press("m")
    page.wait_for_function("() => document.querySelector('.player-video').muted")
    page.keyboard.press("m")
    page.wait_for_function("() => !document.querySelector('.player-video').muted")
    # I: Info, Esc sluit hem
    page.keyboard.press("i")
    expect(page.get_by_role("region", name="Info")).to_be_visible()
    page.keyboard.press("Escape")
    expect(page.get_by_role("region", name="Info")).to_be_hidden()
    # F: volledig scherm op de container, Esc eruit
    page.keyboard.press("f")
    page.wait_for_function("() => document.fullscreenElement?.classList.contains('player')")
    page.keyboard.press("Escape")
    page.wait_for_function("() => document.fullscreenElement === null")
    # A: het volgende audiospoor (een nieuwe sessie)
    page.keyboard.press("a")
    call = plays.wait(page, 2)
    assert call["body"]["audio"] == out["tracks"]["audio"][1]["index"], call["body"]
    ready_state(page, "remux")
    # Met Tab door de bediening, en het menu Kwaliteit is er niet (remux: één keuze); Audiospoor
    # met Enter, ↓ en Enter
    page.locator(".player-title").focus()
    tab_to(page, "Audiospoor")
    page.keyboard.press("Enter")
    menu = page.get_by_role("menu", name="Audiospoor")
    expect(menu).to_be_visible()
    expect(menu.get_by_role("menuitemradio", checked=True)).to_be_focused()
    page.keyboard.press("ArrowDown")
    expect(menu.get_by_role("menuitemradio", name=re.compile("^Nederlands"))).to_be_focused()
    page.keyboard.press("Escape")  # Esc sluit het menu (en de focus terug op de knop)
    expect(menu).to_be_hidden()
    expect(page.get_by_role("button", name="Audiospoor")).to_be_focused()
    page.keyboard.press("Enter")
    page.keyboard.press("ArrowDown")
    page.keyboard.press("Enter")
    plays.wait(page, 3)
    ready_state(page, "remux")
    # De tijdbalk: Tab erheen, → en ← zoeken 5 s, Home en End
    page.locator(".player-title").focus()
    tab_to(page, "Tijdbalk")
    page.keyboard.press("End")
    page.wait_for_function("() => document.querySelector('.player-video').currentTime >= 5")
    page.keyboard.press("Home")
    page.wait_for_function("() => document.querySelector('.player-video').currentTime < 1")
    # Esc op /kijken: terug (geen bibliotheek: naar /media of de vorige pagina)
    page.keyboard.press("Escape")
    expect(page).not_to_have_url(re.compile("/kijken/"))
    assert len(plays.ok) == 3


# --- 9. de app herstart ---------------------------------------------------------------------------


def restart_case(
    media: Any, media_browsers: Any, ffmpeg_count: Any, tmp: Path, outage_s: float
) -> dict[str, Any]:
    owner, folder, context, page = setup(media, media_browsers, f"M6 herstart {outage_s:g}")
    plays = Plays(context)
    path = loop_mkv(tmp, 25)
    node = upload_path(media, owner, folder, path)
    path.unlink()
    watch(page, node)
    ready_state(page, "remux")
    first = plays.wait(page, 1)["out"]
    playing_past(page, 2.0)
    media.restart(outage_s=outage_s)
    ready = time.monotonic()
    at_ready = float(state(page)["t"])
    # Binnen 10 s na "ready": een nieuwe sessie op de positie (replace), en hij loopt weer
    call = plays.wait(page, 2, seconds=RESTART_LIMIT_S + 5)
    playing_past(page, at_ready + 0.5, timeout_ms=(RESTART_LIMIT_S + 5) * 1000)
    took = time.monotonic() - ready
    assert call["body"]["replace"] == first["sid"], call["body"]
    assert abs(call["body"].get("start_ms", 0) / 1000 - at_ready) <= 3.0, (call["body"], at_ready)
    assert state(page)["state"] == "ready"
    assert took <= RESTART_LIMIT_S, took
    leave(page, ffmpeg_count)
    return {"seconden": round(took, 2), "positie": at_ready}


@pytest.mark.media
def test_de_app_herstart_midden_in_het_afspelen(media, media_browsers, ffmpeg_count, tmp_path):
    restart_case(media, media_browsers, ffmpeg_count, tmp_path, 0)


@pytest.mark.media
def test_de_app_dertig_seconden_weg(media, media_browsers, ffmpeg_count, tmp_path):
    restart_case(media, media_browsers, ffmpeg_count, tmp_path, 30)


# --- 11. sprites bij hover en de hoofdstukken ---------------------------------------------------


@pytest.mark.media
def test_sprites_bij_hover_en_hoofdstukken(media, media_browsers, ffmpeg_count):
    owner, folder, context, page = setup(media, media_browsers, "M6 sprites")
    plays = Plays(context)
    up = media.upload(owner, "chapters.mkv", folder)
    library_item(media, owner, folder, up.id, [])
    row = wait_state(media, up.sha, "trick_state")
    assert row["trick_state"] == "ready" and row["trick"]["count"] >= 2, row
    watch(page, up.id)
    ready_state(page)
    out = plays.wait(page, 1)["out"]
    assert out["trick"]["url"] == "trick.vtt", out["trick"]
    assert [c["title"] for c in out["chapters"]] == ["Intro", "Deel 1", "End Credits"]
    cues = trick_cues(page, out["sid"])
    assert len(cues) == row["trick"]["count"], cues
    # Streepjes bij de hoofdstukken (niet bij 0)
    expect(page.locator(".player-seek-mark")).to_have_count(2)
    track = page.locator(".player-seek-track")
    box = track.bounding_box()
    assert box
    duration = out["duration_ms"] / 1000
    for at, chapter in ((3.0, "Intro"), (15.0, "Deel 1"), (25.5, "End Credits")):
        cue = next(c for c in cues if c["start"] <= at < c["end"])
        page.mouse.move(box["x"] + box["width"] * at / duration, box["y"] + box["height"] / 2)
        sprite = page.locator(".player-seek-sprite")
        expect(sprite).to_have_attribute("data-start", f"{cue['start']:g}")
        style = sprite.get_attribute("style") or ""
        x, y, w, h = cue["xywh"]
        pos = " ".join(f"-{v}px" if v else "0px" for v in (x, y))
        assert f"background-position: {pos}" in style, (style, cue)
        assert f"width: {w}px" in style and f"height: {h}px" in style, (style, cue)
        assert f"/api/v1/stream/{out['sid']}/{cue['tile']}" in style, (style, cue)
        expect(page.locator(".player-seek-chapter")).to_have_text(chapter)
        expect(page.locator(".player-seek-time")).to_have_text(re.compile(r"^0:\d\d$"))
        shown = page.locator(".player-seek-time").inner_text()
        assert abs(int(shown[2:]) - at) <= 1, (shown, at)
    # Het plaatje laadt (CSP img-src 'self'): de tegel van de sessie
    url = re.search(r'url\("([^"]+)"\)', style)
    assert url, style
    size = page.evaluate(
        "(u) => new Promise((ok) => { const i = new Image(); i.onload = () =>"
        " ok([i.naturalWidth, i.naturalHeight]); i.onerror = () => ok(null); i.src = u; })",
        url.group(1),
    )
    assert size == [1600, 900], size
    # Van de tijdbalk af: geen voorbeeld meer
    page.mouse.move(box["x"] + box["width"] / 2, box["y"] - 200)
    expect(page.locator(".player-seek-preview")).to_be_hidden()
    # PageDown: naar het volgende hoofdstuk
    page.keyboard.press("Home")
    page.keyboard.press("PageDown")
    page.wait_for_function("() => document.querySelector('.player-video').currentTime >= 4.9")


# --- de viewer van fase 3 -----------------------------------------------------------------------


@pytest.mark.media
def test_de_speler_in_de_viewer_en_de_fallback(media, media_browsers, ffmpeg_count):
    owner, folder, context, page = setup(media, media_browsers, "M6 viewer")
    plays = Plays(context)
    good = media.upload(owner, "vp9_opus.webm", folder)
    page.goto(f"/map/{folder}?bestand={good.id}")
    viewer = page.get_by_role("dialog", name="vp9_opus.webm", exact=True)
    expect(viewer).to_be_visible()
    player = viewer.locator(".player.player-viewer")
    expect(player).to_have_attribute("data-state", "ready", timeout=30_000)
    expect(player).to_have_attribute("data-mode", "direct")
    playing_past(page, 0.3)
    # In de viewer: Spatie en K van de speler; geen titelbalk van /kijken
    assert viewer.locator(".player-title").count() == 0
    page.keyboard.press("k")
    page.wait_for_function("() => document.querySelector('.player-video').paused")
    page.keyboard.press(" ")
    playing_past(page, 0.5)
    # Esc sluit de viewer (niet van de speler), en de sessie gaat dicht
    sid = plays.ok[0]["out"]["sid"]
    page.keyboard.press("Escape")
    expect(viewer).to_be_hidden()
    end = time.monotonic() + 5
    while True:
        r = media.server.request(
            "GET", f"/api/v1/stream/{sid}/file", headers={"Range": "bytes=0-0"}
        )
        if r.status_code == 410 or time.monotonic() > end:
            break
        page.wait_for_timeout(200)
    assert r.status_code == 410, r.text


@pytest.mark.media
def test_de_viewer_valt_terug_als_play_nee_zegt(media, media_browsers, ffmpeg_count):
    owner, folder, context, page = setup(media, media_browsers, "M6 fallback")
    plays = Plays(context)
    bad = media.upload(owner, "broken.mp4", folder, name="kapot.mp4")
    page.goto(f"/map/{folder}?bestand={bad.id}")
    viewer = page.get_by_role("dialog", name="kapot.mp4", exact=True)
    expect(viewer).to_be_visible()
    # play zei nee aan het begin: de <video> van fase 3, met zijn eigen melding. Die <video>
    # probeert het bestand zelf en geeft dan de melding van de viewer (de <video> is dan weg;
    # hoe snel hangt van de browser af, dus de test kijkt naar de melding)
    expect(viewer.locator(".player")).to_have_count(0, timeout=30_000)
    expect(viewer.get_by_role("alert")).to_have_text(
        "Deze browser kan dit formaat niet afspelen", timeout=15_000
    )
    assert plays.calls and plays.calls[-1]["status"] != 200, plays.calls


@pytest.mark.media
def test_weg_van_de_pagina_sluit_de_sessie(media, media_browsers, ffmpeg_count):
    """pagehide (een ander adres, het tabblad dicht): de speler sluit zijn sessie meteen
    (DELETE met keepalive), zodat de plaats bij de toelating vrij is en de producent stopt."""
    owner, folder, context, page = setup(media, media_browsers, "M6 weg")
    plays = Plays(context)
    up = media.upload(owner, "vp9_opus.mkv", folder)
    watch(page, up.id)
    ready_state(page, "remux")
    playing_past(page, 0.5)
    sid = plays.ok[0]["out"]["sid"]
    plays.stop()  # zonder route: de keepalive-DELETE gaat meteen de deur uit (Plays.stop)
    page.goto("about:blank")
    end = time.monotonic() + 5
    while True:
        r = media.server.request("GET", f"/api/v1/stream/{sid}/map.json")
        if r.status_code == 410 or time.monotonic() > end:
            break
        time.sleep(0.1)
    assert r.status_code == 410, (r.status_code, r.text)
    assert not ffmpeg_count.wait_zero(15), "de producent loopt nog na pagehide"


@pytest.mark.media
def test_403_de_speler_stopt_en_zegt_waarom(media, media_browsers, ffmpeg_count, tmp_path):
    """Het bestand naar de prullenbak terwijl het speelt: 403 media_access_gone (revoked). De
    speler stopt, zegt waarom, en vraagt nooit een nieuwe sessie. Remux van 150 s: de speler
    vraagt elke 2 s een segment, dus hij merkt het meteen (een klein direct bestand heeft
    Chromium al helemaal in het geheugen en speelt door, m3.md)."""
    owner, folder, context, page = setup(media, media_browsers, "M6 ingetrokken")
    plays = Plays(context)
    path = loop_mkv(tmp_path, 25)
    node = upload_path(media, owner, folder, path)
    path.unlink()
    watch(page, node)
    ready_state(page, "remux")
    playing_past(page, 1.0)
    token = login(media, owner)["access_token"]
    media.server.request("DELETE", f"/api/v1/files/{node}", token=token, expect=204)
    expect(page.locator(PLAYER)).to_have_attribute("data-state", "stopped", timeout=20_000)
    expect(page.locator(".player-overlay[role=alert]")).to_have_text(
        "Je hebt geen toegang meer tot deze video."
    )
    assert page.evaluate("() => document.querySelector('.player-video').paused")
    # Geen nieuwe sessie, ook niet na een tijdje
    page.wait_for_timeout(3000)
    assert len(plays.calls) == 1, plays.calls
    assert not ffmpeg_count.wait_zero(15), "de producent loopt nog na 403"


# --- #19: na 2 haperingen zelf een trede lager ----------------------------------------------------


@pytest.mark.media
def test_na_twee_haperingen_een_trede_lager(media, media_browsers, ffmpeg_count, tmp_path):
    """§4.3 #19: de verbinding geknepen via CDP; na 2 haperingen binnen een minuut zelf een
    trede lager (een nieuwe sessie met quality 480), en de speler zegt het."""
    owner, folder, context, page = setup(media, media_browsers, "M6 haperen")
    plays = Plays(context)
    sizes: list[int] = []

    def size(response: Any) -> None:
        if response.url.endswith(".m4s") and response.status == 200:
            sizes.append(int(response.headers.get("content-length") or 0))

    page.on("response", size)
    path = loop_mkv(tmp_path, 10, "hevc10_hdr10.mkv")  # 60 s HEVC: een transcode
    node = upload_path(media, owner, folder, path)
    path.unlink()
    watch(page, node)
    ready_state(page, "transcode")
    first = plays.wait(page, 1)["out"]
    assert first["quality"]["current"] == "auto"
    playing_past(page, 0.5)
    # Knijpen tot een segment (2 s beeld) ongeveer 4 s laden kost: dan hapert elk segment, en
    # vallen de twee haperingen binnen een halve minuut. De maat van een segment verschilt per
    # encoder (VP9 hier, H.264 in CI), dus de snelheid volgt het eerste segment.
    assert sizes and max(sizes) > 0, sizes
    rate = max(20_000, max(sizes) // 4)
    cdp = context.new_cdp_session(page)
    cdp.send("Network.enable")
    slow = {"offline": False, "latency": 100, "downloadThroughput": rate, "uploadThroughput": rate}
    cdp.send("Network.emulateNetworkConditions", slow)
    # Een sprong naar wat er nog niet is: elk segment komt nu te laat
    page.keyboard.press("6")
    call = plays.wait(page, 2, seconds=60)
    fast = {"offline": False, "latency": 0, "downloadThroughput": -1, "uploadThroughput": -1}
    cdp.send("Network.emulateNetworkConditions", fast)
    assert call["body"]["quality"] == 480 and call["body"]["replace"] == first["sid"], call
    expect(page.locator(".sr-only[role=status]")).to_have_text(
        "Het haperde: kwaliteit verlaagd naar 480p", timeout=20_000
    )
    page.wait_for_function("() => document.querySelector('.player-video').videoHeight === 480")
    leave(page, ffmpeg_count)
