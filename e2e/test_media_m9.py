"""M9: muziek en gapless in de browser (fase 5, §2 M9 van het bouwontwerp).

Met media aan en de lokale worker (de fixture media). Eén bibliotheek van het soort Muziek per
module (de fixture shelf): het album van tests/media/fixtures.py ("album": drie nummers van 4 s
uit één doorlopende sinus van 440 Hz, per codec een eigen album "Sinus (flac)", "Sinus (opus)",
"Sinus (mp3)" en "Sinus (aac)", albumartiest "Fase Vijf").

- **Gapless (Klaar als):** het album met FLAC en dat met Opus spelen via de knop Afspelen (het
  toetsenbord). Het <audio> van de muziekbalk gaat via een MediaElementAudioSourceNode naar een
  AudioWorklet die elk sample opslaat. Het worklet komt van een adres op dezelfde origin
  (een bestand in de gebouwde web-app tijdens de test, geen blob: of data:; de CSP blijft zoals
  hij is; page.route ziet worklet-modules niet). Daarna per blok van 1024
  samples een kleinste-kwadratenfit van de sinus: de fase per nummer is een rechte lijn, en op
  elke overgang mag de fase niet springen: hooguit 1 sample (|Δθ| / ω ≤ 1). Ook: geen gat (elk
  blok tussen het begin en het eind heeft de volle amplitude), drie keer audio.mp4 via fetch
  (de run van gapless.ts, niet <audio src>), en geen melding "Niet naadloos".
- **MP3 en AAC:** dezelfde meting, alleen vastgelegd (print en record_property), niet geëist.
  MP3 gaat rauw als audio/mpeg (mode sequence, delay = enc_delay). AAC kan Chromium 141 niet
  (415 media_unsupported); Chrome in CI wel.
- **De terugval** (als het AudioWorklet headless niets hoort): `buffered` is één bereik en het
  eind ligt op het sample (de som van samples/rate). Gebruikt de test hem, dan zegt hij dat (een
  waarschuwing en record_property "fallback"); in Chromium 141 lokaal was hij niet nodig.
- **Albums en artiesten met het toetsenbord** (docs/fase-5/toetsenbord-m9.md): de tabs, het
  raster (pijltjes, Home, End, Enter), Afspelen, de lijst van nummers (pijltjes, Enter, Shift+F10),
  de artiest, en de muziekbalk (pauzeren, de rij met Escape terug). De muziek speelt door bij
  navigeren in de app (hetzelfde element, de tijd loopt door).
- **Afspeellijsten met het toetsenbord:** een nieuwe lijst vanuit het menu van een nummer, een
  album erbij, de volgorde met Alt+↓ en Alt+↑, Delete, de naam wijzigen en de lijst
  verwijderen.

Het binnenkomen in een lijst of raster gaat met focus() (één tabstop), zoals in test_media_m2.py.
"""

from __future__ import annotations

import contextlib
import hashlib
import math
import re
import sqlite3
import statistics
import time
import warnings
from collections.abc import Iterator
from contextlib import closing
from typing import Any

import pytest
from playwright.sync_api import expect

from media_harness import open_app, wait_playing
from test_media_m3 import upload_bytes

FREQ = 440.0  # de sinus van het album (tests/media/fixtures.py, _album)
TRACKS = 3
SCAN_S = 90.0
# Een blok voor de fit en de stap ertussen (samples), en de rand rond een overgang die de fit van
# een nummer overslaat (s)
BLOCK = 1024
HOP = 512
EDGE_S = 0.3
MAX_JUMP = 1.0  # samples (Klaar als)
AUDIO = "audio.music-audio"
BAR = "section.music-bar"
PLAY_RE = re.compile(r"/api/v1/media/[^/]+/play$")
# Het worklet staat tijdens de test in de gebouwde web-app (de fixture tap): dezelfde origin, met
# de CSP van de app. page.route kan het niet leveren: Chromium 141 laat het laden van een
# worklet-module (addModule) niet zien aan de onderschepping van Playwright, ook niet aan
# Fetch.enable op de browser (gemeten; m9.md, afwijking).
TAP_PATH = "__e2e-m9/tap.js"

# Het worklet: elk sample van kanaal 0 vanaf het eerste dat niet stil is; een kwantum zonder
# invoer telt als stilte (128 nullen), zodat een gat in de tijd een gat blijft
TAP_JS = """
class Tap extends AudioWorkletProcessor {
  constructor(options) {
    super();
    this.buf = new Float32Array(options.processorOptions.frames);
    this.n = 0;
    this.started = false;
    this.quanta = 0;
    this.empty = 0;
    this.port.onmessage = (e) => {
      if (e.data === "count") this.port.postMessage({ n: this.n });
      if (e.data === "dump") {
        this.port.postMessage({
          n: this.n, quanta: this.quanta, empty: this.empty, rate: sampleRate,
          data: this.buf.slice(0, this.n),
        });
      }
    };
  }
  process(inputs) {
    this.quanta++;
    const input = inputs[0];
    const ch = input && input.length ? input[0] : null;
    if (!ch) {
      this.empty++;
      if (this.started) this.n = Math.min(this.buf.length, this.n + 128);
      return true;
    }
    let from = 0;
    if (!this.started) {
      while (from < ch.length && Math.abs(ch[from]) < 1e-4) from++;
      if (from === ch.length) return true;
      this.started = true;
    }
    const len = Math.min(this.buf.length - this.n, ch.length - from);
    if (len > 0) {
      this.buf.set(ch.subarray(from, from + len), this.n);
      this.n += len;
    }
    return true;
  }
}
registerProcessor("e2e-tap", Tap);
"""

# De AudioContext op de rate van het album, het worklet, en de bron zodra de speler zijn
# <audio> aan <body> hangt (player.ts maakt hem pas bij het eerste spelen)
SETUP_JS = """async ({ url, rate, seconds, selector }) => {
  const ctx = new AudioContext({ sampleRate: rate, latencyHint: "playback" });
  await ctx.audioWorklet.addModule(url);
  const tap = new AudioWorkletNode(ctx, "e2e-tap", {
    numberOfInputs: 1, numberOfOutputs: 1, outputChannelCount: [1],
    processorOptions: { frames: Math.ceil(rate * seconds) },
  });
  tap.connect(ctx.destination);
  const m9 = { ctx, tap, source: null, error: null, element: null };
  window.__m9 = m9;
  const hook = (el) => {
    if (m9.source) return;
    try {
      m9.element = el;
      el.__m9 = true;
      m9.source = ctx.createMediaElementSource(el);
      m9.source.connect(tap);
    } catch (e) {
      m9.error = String(e);
    }
  };
  const found = document.querySelector(selector);
  if (found) hook(found);
  else {
    new MutationObserver((_list, obs) => {
      const el = document.querySelector(selector);
      if (el) { obs.disconnect(); hook(el); }
    }).observe(document.body, { childList: true });
  }
  if (ctx.state !== "running") await ctx.resume();
  return { state: ctx.state, rate: ctx.sampleRate };
}"""

COUNT_JS = """() => new Promise((ok) => {
  const tap = window.__m9.tap;
  tap.port.onmessage = (e) => ok(e.data.n);
  tap.port.postMessage("count");
})"""

# Per blok de fit a·sin(ωm) + b·cos(ωm) (m: de plek in de opname): θ = atan2(b, a), de
# amplitude en wat er overblijft (RMS). Zonder bijwerkingen; alleen de blokken gaan terug
FIT_JS = """async ({ freq, block, hop }) => {
  const m9 = window.__m9;
  const dump = await new Promise((ok) => {
    m9.tap.port.onmessage = (e) => ok(e.data);
    m9.tap.port.postMessage("dump");
  });
  const x = dump.data;
  const w = 2 * Math.PI * freq / dump.rate;
  const blocks = [];
  for (let s = 0; s + block <= dump.n; s += hop) {
    let ss = 0, cc = 0, sc = 0, xs = 0, xc = 0, xx = 0;
    for (let j = 0; j < block; j++) {
      const m = s + j;
      const sn = Math.sin(w * m), cs = Math.cos(w * m), v = x[m];
      ss += sn * sn; cc += cs * cs; sc += sn * cs; xs += v * sn; xc += v * cs; xx += v * v;
    }
    const det = ss * cc - sc * sc;
    const a = (xs * cc - xc * sc) / det;
    const b = (xc * ss - xs * sc) / det;
    const resid = Math.sqrt(Math.max(0, xx - (a * xs + b * xc)) / block);
    blocks.push([s, Math.atan2(b, a), Math.hypot(a, b), resid]);
  }
  let last = -1;
  for (let i = dump.n - 1; i >= 0; i--) if (Math.abs(x[i]) > 1e-3) { last = i; break; }
  return {
    rate: dump.rate, n: dump.n, quanta: dump.quanta, empty: dump.empty, last, blocks,
    error: m9.error,
  };
}"""

BUFFERED_JS = """(sel) => {
  const a = document.querySelector(sel);
  if (!a) return null;
  const r = [];
  for (let i = 0; i < a.buffered.length; i++) r.push([a.buffered.start(i), a.buffered.end(i)]);
  return { ranges: r, src: a.src.slice(0, 5), paused: a.paused, ended: a.ended, t: a.currentTime };
}"""

ENDED_JS = """([sel, total]) => {
  const a = document.querySelector(sel);
  return !!a && (a.ended || (a.paused && a.currentTime >= total - 0.05));
}"""


# --- de bibliotheek -------------------------------------------------------------------------------


def audio_states(media: Any, shas: list[str]) -> dict[str, str | None]:
    """audio_state per inhoud (media_probe, alleen lezen)."""
    uri = f"file:{media.server.data_dir / 'platform.db'}?mode=ro"
    with closing(sqlite3.connect(uri, uri=True, timeout=5)) as conn:
        rows = conn.execute(
            f"SELECT blob_sha, audio_state FROM media_probe WHERE blob_sha IN "
            f"({','.join('?' * len(shas))})",
            shas,
        ).fetchall()
    found = dict(rows)
    return {sha: found.get(sha) for sha in shas}


def albums_of(media: Any, owner: Any, library_id: str) -> list[dict[str, Any]]:
    r = media.server.call(owner, "GET", f"/api/v1/media/libraries/{library_id}/albums", expect=200)
    return r.json()["items"]


@pytest.fixture(scope="module")
def shelf(media_app: Any) -> dict[str, Any]:
    """Een gebruiker met een bibliotheek Muziek: per codec een map met de drie nummers van het
    album. Wacht tot elk album er is met drie nummers, en audio_info klaar is (de gapless-
    gegevens die play meegeeft)."""
    from tests.media import fixtures

    media_app.ensure_running()
    owner = media_app.user()
    root = media_app.folder(owner, "Muziek")
    source = fixtures.make("album")
    shas: list[str] = []
    for sub, ext in (("flac", "flac"), ("opus", "opus"), ("mp3", "mp3"), ("aac", "m4a")):
        folder = media_app.folder(owner, f"Sinus {sub}", parent=root)
        for n in range(1, TRACKS + 1):
            data = (source / sub / f"{n:02d}.{ext}").read_bytes()
            shas.append(hashlib.sha256(data).hexdigest())
            upload_bytes(media_app, owner, folder, f"{n:02d}.{ext}", data)
    library = media_app.server.call(
        owner, "POST", "/api/v1/media/libraries", json={"root_id": root, "kind": "music"},
        expect=201,
    ).json()  # fmt: skip
    end = time.monotonic() + SCAN_S
    while True:
        albums = albums_of(media_app, owner, library["id"])
        states = audio_states(media_app, shas)
        full = [a for a in albums if a["track_count"] == TRACKS]
        done = all(s in ("ready", "skip", "failed") for s in states.values())
        if len(full) == 4 and done:
            break
        assert time.monotonic() < end, f"na {SCAN_S} s: albums {albums}, audio_info {states}"
        time.sleep(0.25)
    by_title = {a["title"]: a for a in albums}
    return {"owner": owner, "library": library, "albums": by_title, "states": states}


@pytest.fixture
def tap(media: Any) -> Iterator[None]:
    """Het worklet in de web-app van de server (zie TAP_PATH), en na de test weer weg."""
    path = media.server.web_dist / TAP_PATH
    made = not path.parent.exists()
    path.parent.mkdir(exist_ok=True)
    path.write_text(TAP_JS)
    try:
        yield
    finally:
        path.unlink(missing_ok=True)
        if made:
            with contextlib.suppress(OSError):
                path.parent.rmdir()


@pytest.fixture(autouse=True)
def pages_away(media_browsers: Any) -> Iterator[None]:
    """Na elke test elke pagina naar about:blank: pagehide, en de speler sluit zijn sessie (zoals
    in test_media_m6.py)."""
    yield
    for context, _guard in list(media_browsers._open):
        with contextlib.suppress(Exception):
            context.unroute_all(behavior="ignoreErrors")
        for page in context.pages:
            with contextlib.suppress(Exception):
                page.goto("about:blank", timeout=5_000)


# --- de meting ------------------------------------------------------------------------------------


def wrap(x: float) -> float:
    """Een hoek in (−π, π]."""
    return (x + math.pi) % (2 * math.pi) - math.pi


def line(points: list[tuple[float, float]]) -> tuple[float, float]:
    """De rechte lijn y = a + b·x door de punten (kleinste kwadraten)."""
    n = len(points)
    mx = sum(p[0] for p in points) / n
    my = sum(p[1] for p in points) / n
    sxx = sum((p[0] - mx) ** 2 for p in points)
    b = sum((p[0] - mx) * (p[1] - my) for p in points) / sxx if sxx else 0.0
    return my - b * mx, b


def analyse(fit: dict[str, Any], tracks: list[dict[str, Any]]) -> dict[str, Any]:
    """De sprongen in de fase op de overgangen, in samples van de opname. tracks: de gapless-
    gegevens per nummer ({rate, samples}). De opname begint bij het eerste sample dat niet stil
    is: sample 1 van het eerste nummer (sample 0 van een sinus is 0)."""
    rate = fit["rate"]
    w = 2 * math.pi * FREQ / rate
    blocks = fit["blocks"]
    assert blocks, f"het worklet hoorde niets: {fit | {'blocks': []}}"
    ref = statistics.median(b[2] for b in blocks if b[2] > 0.5 * max(x[2] for x in blocks))
    full = [b[2] > 0.8 * ref and b[3] < 0.2 * ref for b in blocks]
    first = full.index(True)
    last = len(full) - 1 - full[::-1].index(True)
    holes = [blocks[i][0] for i in range(first, last + 1) if not full[i]]

    # De overgangen in de opname (sample 0 van het eerste nummer ligt op −1)
    bounds = [-1.0]
    for t in tracks:
        bounds.append(bounds[-1] + t["samples"] / t["rate"] * rate)
    edge = EDGE_S * rate
    fits: list[tuple[float, float]] = []
    spread = 0.0
    for k in range(len(tracks)):
        lo, hi = bounds[k] + edge, bounds[k + 1] - edge
        inside = [b for b in blocks if b[0] >= lo and b[0] + BLOCK <= hi and b[2] > 0.8 * ref]
        assert len(inside) >= 4, f"nummer {k + 1}: te weinig blokken ({len(inside)})"
        # De fase uitvouwen (de stappen tussen blokken zijn klein) en een lijn erdoor
        points: list[tuple[float, float]] = []
        theta = inside[0][1]
        for i, b in enumerate(inside):
            if i:
                theta += wrap(b[1] - inside[i - 1][1])
            points.append((b[0] + BLOCK / 2, theta))
        a0, b0 = line(points)
        spread = max(spread, max(abs(y - (a0 + b0 * x)) for x, y in points) / w)
        fits.append((a0, b0))
    jumps = []
    for k in range(1, len(tracks)):
        at = bounds[k]
        before = fits[k - 1][0] + fits[k - 1][1] * at
        after = fits[k][0] + fits[k][1] * at
        jumps.append(wrap(after - before) / w)
    # Wat er rond de overgangen overblijft na de fit (een knik in een blok), als deel van de
    # amplitude: alleen ter informatie
    near = [
        b[3] / ref
        for b in blocks
        for at in bounds[1:-1]
        if b[0] <= at <= b[0] + BLOCK and b[2] > 0.5 * ref
    ]
    expected = bounds[-1] - bounds[0]
    return {
        "rate": rate,
        "jumps": [round(j, 4) for j in jumps],
        "spread": round(spread, 4),
        "holes": holes[:5],
        "span_s": round((blocks[last][0] + BLOCK - blocks[first][0]) / rate, 3),
        "total_s": round(expected / rate, 3),
        # Het laatste sample dat klinkt, tegen het verwachte eind (samples; ter informatie)
        "end_error": round(fit["last"] - (bounds[-1] - 1), 1) if fit["last"] >= 0 else None,
        "residual_near": round(max(near), 5) if near else None,
        "empty_quanta": fit["empty"],
    }


def gapless_rows(media: Any, shas: list[str]) -> list[dict[str, Any]]:
    """De gapless-gegevens (media_probe.audio) per nummer, in de volgorde van shas."""
    import json

    uri = f"file:{media.server.data_dir / 'platform.db'}?mode=ro"
    with closing(sqlite3.connect(uri, uri=True, timeout=5)) as conn:
        rows = dict(
            conn.execute(
                f"SELECT blob_sha, audio FROM media_probe WHERE blob_sha IN "
                f"({','.join('?' * len(shas))})",
                shas,
            ).fetchall()
        )
    out = []
    for sha in shas:
        raw = rows.get(sha)
        out.append(json.loads(raw) if isinstance(raw, str) else raw)
    return out


def track_shas(sub: str, ext: str) -> list[str]:
    from tests.media import fixtures

    source = fixtures.make("album")
    return [
        hashlib.sha256((source / sub / f"{n:02d}.{ext}").read_bytes()).hexdigest()
        for n in range(1, TRACKS + 1)
    ]


def play_album(
    media: Any, media_browsers: Any, shelf: dict[str, Any], sub: str, ext: str
) -> dict[str, Any]:
    """Speelt het album van sub met de knop Afspelen en het worklet ertussen; geeft wat play
    zei, de fetches van audio.mp4 (of file) en de meting (of de reden dat er niets klonk)."""
    album = shelf["albums"][f"Sinus ({sub})"]
    rows = gapless_rows(media, track_shas(sub, ext))
    total = sum(r["samples"] / r["rate"] for r in rows if r) if all(rows) else 12.0
    rate = rows[0]["rate"] if rows[0] else 44100
    # Zonder service worker: het worklet komt zo zeker van de server (de precache kent het niet)
    context = media_browsers.context_as(shelf["owner"], service_workers="block")
    page = context.new_page()
    fetched: list[tuple[str, int, str]] = []

    def seen(response: Any) -> None:
        path = response.url.split("?")[0]
        if "/api/v1/stream/" in path and path.endswith(("/audio.mp4", "/file")):
            kind = response.request.resource_type
            fetched.append((path.rsplit("/", 1)[1], response.status, kind))

    page.on("response", seen)
    open_app(page)
    page.goto(f"/muziek/album/{album['id']}")
    expect(page.get_by_role("heading", name=f"Sinus ({sub})", level=1)).to_be_visible()

    ctx = page.evaluate(
        SETUP_JS, {"url": f"/{TAP_PATH}", "rate": rate, "seconds": total + 4, "selector": AUDIO}
    )
    assert ctx["state"] == "running", ctx

    play = page.locator("[data-play-album]")
    play.focus()
    with page.expect_response(PLAY_RE) as got:
        page.keyboard.press("Enter")
    response = got.value
    status = response.status
    out = response.json() if status == 200 else response.json().get("detail")
    result: dict[str, Any] = {"status": status, "play": out, "rows": rows}
    if status != 200:
        expect(page.locator(".music-bar-error")).to_be_visible()
        result["measured"] = None
        return result

    expect(page.locator(BAR)).to_be_visible()
    wait_playing(page, 0.2, AUDIO)
    # Alles in de buffer (20 s vooruit laden; het album is korter)
    page.wait_for_timeout(1500)
    result["buffered"] = page.evaluate(BUFFERED_JS, AUDIO)
    result["gaps_note"] = page.get_by_text("Niet naadloos").count()
    page.wait_for_function(ENDED_JS, arg=[AUDIO, total], timeout=(total + 20) * 1000)
    page.wait_for_timeout(300)
    count = page.evaluate(COUNT_JS)
    result["fetched"] = fetched
    if count < rate:  # het worklet hoorde (bijna) niets: de terugval
        result["measured"] = None
        result["fallback"] = True
        return result
    fit = page.evaluate(FIT_JS, {"freq": FREQ, "block": BLOCK, "hop": HOP})
    result["measured"] = analyse(fit, rows)
    return result


def fallback_ok(result: dict[str, Any]) -> None:
    """De terugval van Klaar als: één bereik in buffered, en het eind op het sample (de som van
    samples/rate; Chromium rekent in µs, dus ±1 µs)."""
    rows = result["rows"]
    total = sum(r["samples"] / r["rate"] for r in rows)
    ranges = result["buffered"]["ranges"]
    assert len(ranges) == 1, f"buffered is niet één bereik: {ranges}"
    assert abs(ranges[0][1] - total) <= max(1 / rows[0]["rate"], 2e-6), (ranges, total)


# --- gapless --------------------------------------------------------------------------------------


@pytest.mark.media
@pytest.mark.parametrize(("sub", "ext"), [("flac", "flac"), ("opus", "opus")])
def test_gapless_de_fase_springt_niet(
    media, media_browsers, ffmpeg_count, shelf, tap, sub, ext, record_property
):
    result = play_album(media, media_browsers, shelf, sub, ext)
    assert result["status"] == 200, result
    play = result["play"]
    assert play["mode"] == "audio" and play["urls"].get("audio") == "audio.mp4", play
    assert 'codecs="flac"' in play["mime"], play  # Opus wordt FLAC (S8)
    assert play["gapless"] and play["gapless"]["delay"] == 0, play
    assert all(r and r["samples"] > 0 for r in result["rows"]), result["rows"]
    # De run van gapless.ts: drie keer audio.mp4 via fetch, geen <audio src>
    fetches = [f for f in result["fetched"] if f[0] == "audio.mp4"]
    assert [f[1] for f in fetches] == [200] * TRACKS, result["fetched"]
    assert {f[2] for f in fetches} == {"fetch"}, result["fetched"]
    assert result["gaps_note"] == 0
    # Het eind van de buffer op het sample (ook als het worklet werkt: goedkoop en exact)
    fallback_ok(result)
    record_property(f"gapless_{sub}", result.get("measured"))
    print(f"\ngapless {sub}: {result.get('measured')} buffered={result['buffered']}")
    if result.get("fallback"):
        warnings.warn(
            f"gapless {sub}: het AudioWorklet hoorde niets; alleen de terugval (buffered)",
            stacklevel=1,
        )
        record_property("fallback", True)
        return
    m = result["measured"]
    assert not m["holes"], f"stilte in het album: {m}"
    assert m["span_s"] >= m["total_s"] - 2 * EDGE_S, m
    assert m["spread"] <= MAX_JUMP, f"de fase binnen een nummer is geen lijn: {m}"
    assert all(abs(j) <= MAX_JUMP for j in m["jumps"]), f"de fase springt: {m}"


@pytest.mark.media
@pytest.mark.parametrize(("sub", "ext"), [("mp3", "mp3"), ("aac", "m4a")])
def test_gapless_mp3_en_aac_vastgelegd(
    media, media_browsers, ffmpeg_count, shelf, tap, sub, ext, record_property
):
    """Niet geëist (Klaar als): wat er gebeurt, staat in de uitvoer en in m9.md."""
    result = play_album(media, media_browsers, shelf, sub, ext)
    play = result["play"]
    summary = {
        "status": result["status"],
        "mode": play.get("mode") if result["status"] == 200 else play,
        "mime": play.get("mime") if result["status"] == 200 else None,
        "gapless": play.get("gapless") if result["status"] == 200 else None,
        "fetched": result.get("fetched"),
        "buffered": result.get("buffered"),
        "measured": result.get("measured"),
        "fallback": result.get("fallback", False),
    }
    record_property(f"gapless_{sub}", summary)
    print(f"\ngapless {sub} (vastgelegd): {summary}")


# --- het toetsenbord ------------------------------------------------------------------------------


def focused_text(page: Any) -> str:
    return page.evaluate(
        "() => (document.activeElement && (document.activeElement.getAttribute('aria-label')"
        " || document.activeElement.textContent)) || ''"
    )


def is_focused(page: Any, selector: str) -> bool:
    return page.evaluate(
        "(s) => !!document.activeElement && document.activeElement.matches(s)", selector
    )


def tab_to(page: Any, selector: str, presses: int = 40, back: bool = False) -> None:
    """Tab (of Shift+Tab) tot het element van selector de focus heeft."""
    for _ in range(presses):
        page.keyboard.press("Shift+Tab" if back else "Tab")
        if is_focused(page, selector):
            return
    raise AssertionError(f"{selector} niet bereikt met Tab; focus op {focused_text(page)!r}")


def menu_to(page: Any, label: str, presses: int = 20) -> None:
    """In een open menu ↓ tot het item met deze naam de focus heeft."""
    for _ in range(presses):
        if focused_text(page).strip() == label:
            return
        page.keyboard.press("ArrowDown")
    raise AssertionError(f"{label} niet gevonden in het menu; focus op {focused_text(page)!r}")


def toast(page: Any, text: str) -> None:
    expect(page.locator(".toast-message", has_text=text).last).to_be_visible()


def playlist(media: Any, owner: Any, playlist_id: str) -> dict[str, Any]:
    return media.server.call(
        owner, "GET", f"/api/v1/media/playlists/{playlist_id}", expect=200
    ).json()


def wait_order(media: Any, owner: Any, playlist_id: str, titles: list[str]) -> None:
    end = time.monotonic() + 10
    found: list[str] = []
    while time.monotonic() < end:
        found = [i["track"]["title"] for i in playlist(media, owner, playlist_id)["items"]]
        if found == titles:
            return
        time.sleep(0.1)
    raise AssertionError(f"volgorde {found}, verwacht {titles}")


def bar_title(page: Any) -> str:
    return page.locator(f"{BAR} .music-bar-title").inner_text()


@pytest.mark.media
def test_albums_artiesten_en_de_muziekbalk_met_het_toetsenbord(
    media, media_browsers, ffmpeg_count, shelf
):
    page = media_browsers.context_as(shelf["owner"]).new_page()
    open_app(page)
    page.goto("/muziek")
    expect(page.get_by_role("heading", name="Muziek", level=1)).to_be_visible()

    # De tabs: Albums is gekozen; het raster van albums (vier, op titel)
    tabs = page.get_by_role("tablist", name="Muziek")
    expect(tabs.get_by_role("tab", name="Albums")).to_have_attribute("aria-selected", "true")
    cards = page.locator(".music-grid [data-index]")
    expect(cards).to_have_count(4)
    expect(cards.first).to_contain_text("Sinus (aac)")
    expect(cards.first).to_contain_text("Fase Vijf · 2025")
    page.locator('.music-grid [data-index][tabindex="0"]').focus()
    page.keyboard.press("End")
    expect(cards.nth(3)).to_be_focused()
    page.keyboard.press("Home")
    expect(cards.first).to_be_focused()
    page.keyboard.press("ArrowRight")
    expect(cards.nth(1)).to_be_focused()
    expect(cards.nth(1)).to_contain_text("Sinus (flac)")
    page.keyboard.press("Enter")
    expect(page).to_have_url(re.compile(r"/muziek/album/"))
    expect(page.get_by_role("heading", name="Sinus (flac)", level=1)).to_be_visible()

    # Afspelen: de balk verschijnt en speelt nummer 1
    tab_to(page, "[data-play-album]")
    page.keyboard.press("Enter")
    expect(page.locator(BAR)).to_be_visible()
    wait_playing(page, 0.2, AUDIO)
    expect(page.locator(f"{BAR} .music-bar-title")).to_have_text("Nummer 1")
    page.evaluate("(s) => { document.querySelector(s).__m9mark = 1; }", AUDIO)

    # De lijst van nummers: ↓ en Enter speelt nummer 2; Shift+F10 opent het menu van de regel
    rows = page.locator(".music-tracks [data-index]")
    expect(rows).to_have_count(TRACKS)
    page.locator('.music-tracks [data-index][tabindex="0"]').focus()
    page.keyboard.press("ArrowDown")
    expect(rows.nth(1)).to_be_focused()
    page.keyboard.press("Enter")
    expect(page.locator(f"{BAR} .music-bar-title")).to_have_text("Nummer 2")
    expect(rows.nth(1)).to_have_attribute("aria-current", "true")
    page.keyboard.press("Shift+F10")
    menu = page.get_by_role("menu", name="Meer voor Nummer 2")
    expect(menu).to_be_visible()
    menu_to(page, "Aan de rij toevoegen")
    page.keyboard.press("Enter")
    toast(page, "1 nummer in de rij gezet")
    # Het menu zet de focus terug op zijn knop in de regel
    expect(page.get_by_role("button", name="Meer voor Nummer 2")).to_be_focused()

    # De artiest: de link in de kop, dan zijn pagina met het raster van zijn albums
    artist = page.locator(".music-head-artist").get_by_role("link", name="Fase Vijf")
    expect(artist).to_be_visible()
    artist.focus()
    page.keyboard.press("Enter")
    expect(page.get_by_role("heading", name="Fase Vijf", level=1)).to_be_visible()
    expect(page.locator(".music-grid [data-index]")).to_have_count(4)

    # De muziek speelt door bij navigeren: hetzelfde element, de tijd loopt verder
    before = page.evaluate("(s) => document.querySelector(s).currentTime", AUDIO)
    nav = page.get_by_role("navigation", name="Hoofdmenu")
    nav.get_by_role("link", name="Media", exact=True).focus()
    page.keyboard.press("Enter")
    expect(page).to_have_url(re.compile(r"/media$"))
    nav.get_by_role("link", name="Muziek", exact=True).focus()
    page.keyboard.press("Enter")
    expect(page).to_have_url(re.compile(r"/muziek$"))
    expect(page.locator(BAR)).to_be_visible()
    page.wait_for_function(
        """([s, t]) => { const a = document.querySelector(s);
          return !!a && a.__m9mark === 1 && !a.paused && a.currentTime > t + 0.3; }""",
        arg=[AUDIO, before],
    )
    assert page.locator(AUDIO).count() == 1

    # De tab Artiesten met → (de URL volgt), Enter op de artiest
    page.get_by_role("tab", name="Albums").focus()
    page.keyboard.press("ArrowRight")
    expect(page.get_by_role("tab", name="Artiesten")).to_have_attribute("aria-selected", "true")
    expect(page).to_have_url(re.compile(r"[?&]tab=artiesten"))
    links = page.locator(".music-list [data-index]")
    expect(links).to_have_count(1)
    expect(links.first).to_contain_text("Fase Vijf")
    page.locator('.music-list [data-index][tabindex="0"]').focus()
    page.keyboard.press("Enter")
    expect(page.get_by_role("heading", name="Fase Vijf", level=1)).to_be_visible()

    # De balk: pauzeren met Enter op de knop, de rij open en met Escape dicht (focus terug)
    toggle = page.locator(f'{BAR} [data-music="toggle"]')
    toggle.focus()
    expect(toggle).to_have_accessible_name("Pauzeren")
    page.keyboard.press("Enter")
    page.wait_for_function("(s) => document.querySelector(s).paused", arg=AUDIO)
    expect(toggle).to_have_accessible_name("Afspelen")
    queue_button = page.locator(f'{BAR} [data-music="queue"]')
    tab_to(page, f'{BAR} [data-music="queue"]')
    page.keyboard.press("Enter")
    panel = page.get_by_role("region", name="De rij")
    expect(panel).to_be_visible()
    # Nummer 2 speelt, daarna nummer 3 en het toegevoegde nummer 2
    expect(panel.locator(".music-queue-title")).to_have_text(["Nummer 2", "Nummer 3", "Nummer 2"])
    tab_to(page, f"{BAR} .music-queue-play")
    expect(panel.get_by_role("button", name="Nummer 2 afspelen").first).to_be_focused()
    page.keyboard.press("Escape")
    expect(panel).to_have_count(0)
    expect(queue_button).to_be_focused()
    # Volgende met het toetsenbord; dan de speler sluiten: de balk weg, het element stil
    next_button = page.locator(f'{BAR} [data-music="next"]')
    next_button.focus()
    page.keyboard.press("Enter")
    expect(page.locator(f"{BAR} .music-bar-title")).to_have_text("Nummer 3")
    close = page.locator(f'{BAR} [data-music="close"]')
    close.focus()
    page.keyboard.press("Enter")
    expect(page.locator(BAR)).to_have_count(0)
    page.wait_for_function(
        "(s) => { const a = document.querySelector(s); return !a || a.paused; }", arg=AUDIO
    )


@pytest.mark.media
def test_afspeellijsten_met_het_toetsenbord(media, media_browsers, ffmpeg_count, shelf):
    owner = shelf["owner"]
    album = shelf["albums"]["Sinus (opus)"]
    page = media_browsers.context_as(owner).new_page()
    open_app(page)
    page.goto(f"/muziek/album/{album['id']}")
    expect(page.get_by_role("heading", name="Sinus (opus)", level=1)).to_be_visible()

    # Een nieuwe afspeellijst vanuit het menu van nummer 3: het submenu met →
    rows = page.locator(".music-tracks [data-index]")
    expect(rows).to_have_count(TRACKS)
    page.locator('.music-tracks [data-index][tabindex="0"]').focus()
    page.keyboard.press("End")
    expect(rows.nth(2)).to_be_focused()
    page.keyboard.press("Shift+F10")
    expect(page.get_by_role("menu", name="Meer voor Nummer 3")).to_be_visible()
    menu_to(page, "Aan afspeellijst toevoegen")
    page.keyboard.press("ArrowRight")
    expect(page.get_by_role("menu", name="Aan afspeellijst toevoegen")).to_be_visible()
    menu_to(page, "Nieuwe afspeellijst…")
    page.keyboard.press("Enter")
    dialog = page.get_by_role("dialog", name="Nieuwe afspeellijst")
    expect(dialog).to_be_visible()
    name = dialog.get_by_label("Naam", exact=True)
    expect(name).to_be_focused()
    page.keyboard.type("Avond")
    page.keyboard.press("Enter")
    toast(page, "Afspeellijst Avond gemaakt")
    toast(page, "1 nummer toegevoegd aan Avond")
    lists = media.server.call(owner, "GET", "/api/v1/media/playlists", expect=200).json()
    avond = next(p for p in lists if p["name"] == "Avond")

    # Het hele album erbij via het menu in de kop (de lijst staat nu in het submenu)
    tab_to(page, 'button[aria-label="Meer voor Sinus (opus)"]', back=True)
    page.keyboard.press("Enter")
    expect(page.get_by_role("menu", name="Meer voor Sinus (opus)")).to_be_visible()
    menu_to(page, "Aan afspeellijst toevoegen")
    page.keyboard.press("Enter")
    menu_to(page, "Avond")
    page.keyboard.press("Enter")
    toast(page, "3 nummers toegevoegd aan Avond")
    wait_order(media, owner, avond["id"], ["Nummer 3", "Nummer 1", "Nummer 2", "Nummer 3"])

    # Naar de tab Afspeellijsten (End in de tabs), Enter op de lijst
    page.goto("/muziek")
    page.get_by_role("tab", name="Albums").focus()
    page.keyboard.press("End")
    expect(page.get_by_role("tab", name="Afspeellijsten")).to_have_attribute(
        "aria-selected", "true"
    )
    links = page.locator(".music-list [data-index]")
    expect(links).to_have_count(1)
    expect(links.first).to_contain_text("4 nummers")
    page.locator('.music-list [data-index][tabindex="0"]').focus()
    page.keyboard.press("Enter")
    expect(page.get_by_role("heading", name="Avond", level=1)).to_be_visible()

    # De volgorde: Alt+↓ en Alt+↑ op een regel (de focus gaat mee), Delete haalt eruit
    rows = page.locator(".music-tracks [data-index]")
    expect(rows).to_have_count(4)
    page.locator('.music-tracks [data-index][tabindex="0"]').focus()
    page.keyboard.press("Alt+ArrowDown")
    wait_order(media, owner, avond["id"], ["Nummer 1", "Nummer 3", "Nummer 2", "Nummer 3"])
    expect(rows.nth(1)).to_be_focused()
    page.keyboard.press("Alt+ArrowDown")
    wait_order(media, owner, avond["id"], ["Nummer 1", "Nummer 2", "Nummer 3", "Nummer 3"])
    expect(rows.nth(2)).to_be_focused()
    page.keyboard.press("Alt+ArrowUp")
    wait_order(media, owner, avond["id"], ["Nummer 1", "Nummer 3", "Nummer 2", "Nummer 3"])
    page.keyboard.press("End")
    expect(rows.nth(3)).to_be_focused()
    page.keyboard.press("Delete")
    toast(page, "Nummer 3 uit de lijst gehaald")
    wait_order(media, owner, avond["id"], ["Nummer 1", "Nummer 3", "Nummer 2"])
    expect(rows).to_have_count(3)
    expect(rows.nth(2)).to_be_focused()

    # Afspelen vanaf de lijst: de rij is de lijst
    tab_to(page, "[data-play-list]", back=True)
    page.keyboard.press("Enter")
    expect(page.locator(BAR)).to_be_visible()
    wait_playing(page, 0.1, AUDIO)
    expect(page.locator(f"{BAR} .music-bar-title")).to_have_text("Nummer 1")

    # De naam wijzigen en de lijst verwijderen via het menu in de kop
    tab_to(page, 'button[aria-label="Meer voor Avond"]')
    page.keyboard.press("Enter")
    menu_to(page, "Naam wijzigen")
    page.keyboard.press("Enter")
    dialog = page.get_by_role("dialog", name="Naam wijzigen")
    expect(dialog.get_by_label("Naam", exact=True)).to_be_focused()
    page.keyboard.press("Control+A")
    page.keyboard.type("Nacht")
    page.keyboard.press("Enter")
    toast(page, "De afspeellijst heet nu Nacht")
    expect(page.get_by_role("heading", name="Nacht", level=1)).to_be_visible()
    tab_to(page, 'button[aria-label="Meer voor Nacht"]', back=True)
    page.keyboard.press("Enter")
    menu_to(page, "Verwijderen")
    page.keyboard.press("Enter")
    confirm = page.get_by_role("dialog", name="Afspeellijst verwijderen?")
    expect(confirm.get_by_role("button", name="Annuleren")).to_be_focused()  # eerst veilig
    page.keyboard.press("Tab")
    expect(confirm.get_by_role("button", name="Verwijderen")).to_be_focused()
    page.keyboard.press("Enter")
    toast(page, "Afspeellijst Nacht verwijderd")
    expect(page).to_have_url(re.compile(r"/muziek\?tab=lijsten"))
    media.server.call(owner, "GET", f"/api/v1/media/playlists/{avond['id']}", expect=404)
    # De muziek speelt door (de rij is los van de lijst)
    expect(page.locator(BAR)).to_be_visible()
    assert not page.evaluate("(s) => document.querySelector(s).paused", AUDIO)
