"""M5: ondertitels, sprites en hoofdstukken in de browser (fase 5, §2 M5 van het bouwontwerp).

Met media aan en de lokale worker (de fixture media). De speler van M6 is er nog niet: de test
zet zelf een <video> met <track>'s op de adressen van de sessie (dezelfde origin, CSP media-src
'self'), zoals de speler dat straks doet. De bibliotheek en het item schrijft de test in de
database zoals de scanner van M2 dat doet (met subtitles.pair voor de losse bestanden), en de
analyse komt als job met die bibliotheek:

- een webm met een ingebed WebVTT-spoor en hoofdstukken, plus een losse .srt (CP1252) en .ass
  (met \\an8): play geeft de hoofdstukken en het begin van de aftiteling; met drie <track>'s
  ziet de TextTrack-API de cues op de goede tijd en de ASS-cue met line 0, en in de pixels staat
  die bovenaan en de SRT-cue onderaan; elke VTT is text/vtt met nosniff;
- de sprites: de analyse in een bibliotheek maakt ze, play geeft trick, elk adres uit trick.vtt
  laadt via de sessie (ook als <img>), en na de prullenbak 403;
- PGS inbranden gaat via de producer van M4: zonder M4 geeft play 503 media_off en slaat de
  test over (bij het samenvoegen gaat hij aan).
"""

from __future__ import annotations

import hashlib
import io
import json
import sqlite3
import subprocess
import tempfile
import time
import uuid
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest

from media_harness import open_app, run_async
from test_media_m3 import codecs_of, login, play, profile, upload_bytes

STATE_WAIT_S = 60.0
SUBS_WAIT_S = 30.0

FETCH_VTT_JS = """async (url) => {
  // zoals de speler: bij 202 na Retry-After opnieuw
  const end = Date.now() + 30000;
  for (;;) {
    const r = await fetch(url, { cache: "no-store" });
    if (r.status !== 202 || Date.now() > end) {
      return {
        status: r.status,
        type: r.headers.get("content-type"),
        nosniff: r.headers.get("x-content-type-options"),
        cache: r.headers.get("cache-control"),
        text: await r.text(),
      };
    }
    await new Promise((ok) => setTimeout(ok, 1000 * Number(r.headers.get("retry-after") || 1)));
  }
}"""

TRACKS_JS = """({ file, tracks }) => {
  document.getElementById("m5")?.remove();
  const v = document.createElement("video");
  v.id = "m5"; v.muted = true; v.playsInline = true; v.preload = "auto"; v.src = file;
  v.style.width = "640px"; v.style.height = "360px";
  for (const t of tracks) {
    const el = document.createElement("track");
    el.kind = "subtitles"; el.src = t.src; el.srclang = t.lang || ""; el.label = t.id;
    el.id = "t-" + t.id; el.default = false;
    v.append(el);
  }
  document.body.prepend(v);
  for (const el of v.querySelectorAll("track")) el.track.mode = "hidden";  // laden, niet tonen
}"""

CUES_JS = """() => {
  const v = document.getElementById("m5");
  const out = {};
  for (const el of v.querySelectorAll("track")) {
    if (el.readyState !== 2) return null;  // nog niet geladen
    out[el.label] = Array.from(el.track.cues).map((c) => ({
      start: c.startTime, end: c.endTime, text: c.text, line: c.line,
      align: c.align, position: c.position,
    }));
  }
  return out;
}"""

SHOW_JS = """(label) => {
  const v = document.getElementById("m5");
  for (const el of v.querySelectorAll("track")) {
    el.track.mode = el.label === label ? "showing" : "disabled";
  }
}"""

SEEK_JS = """async (t) => {
  const v = document.getElementById("m5");
  const once = (name) => new Promise((ok) => v.addEventListener(name, ok, { once: true }));
  if (v.readyState < 1) await once("loadedmetadata");
  v.pause();
  const seeked = once("seeked");
  v.currentTime = t;
  await seeked;
  await new Promise((ok) => requestAnimationFrame(() => requestAnimationFrame(ok)));
  return v.currentTime;
}"""

IMG_JS = """async (url) => {
  const img = new Image();
  img.src = url;
  try { await img.decode(); } catch (e) { return null; }
  return [img.naturalWidth, img.naturalHeight];
}"""

FETCH_JS = """async (url) => {
  const r = await fetch(url, { cache: "no-store" });
  let detail = null;
  const type = r.headers.get("content-type") || "";
  if (type.startsWith("application/json")) {
    try { detail = (await r.json()).detail ?? null; } catch (e) { /* geen json */ }
  }
  return { status: r.status, type, nosniff: r.headers.get("x-content-type-options"), detail };
}"""


# --- wat M2 straks doet -----------------------------------------------------------------------


def library_item(
    media: Any, owner_id: str, root_id: str, node_id: str, title: str, subs: list[dict]
) -> str:
    """Een bibliotheek (films) op root_id en het item voor node_id, met de losse ondertitels
    zoals de scanner ze vindt; daarna de analyse als job met die bibliotheek. Geeft de id van
    de bibliotheek."""
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app import db, jobs
    from app.media import analysis
    from app.models import MediaItem, MediaLibrary, Node

    settings = media.settings()

    async def go() -> str:
        engine = db.get_engine(settings)
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                library = MediaLibrary(
                    owner_id=uuid.UUID(owner_id), root_id=uuid.UUID(root_id), kind="movies",
                    name=f"Films {title}",
                )  # fmt: skip
                session.add(library)
                await session.flush()
                session.add(
                    MediaItem(
                        node_id=uuid.UUID(node_id),
                        library_id=library.id,
                        kind="movie",
                        title=title,
                        sort_title=title.lower(),
                        subs=subs,
                    )  # fmt: skip
                )
                node = await session.get(Node, uuid.UUID(node_id))
                assert node is not None and node.blob_id
                payload = {"sha": node.blob_id, "library_id": str(library.id)}
                await jobs.enqueue(
                    session, analysis.JOB, payload, key=f"{analysis.JOB}:{node.blob_id}"
                )
                await session.commit()
                return str(library.id)
        finally:
            await engine.dispose()
            db._engine.cache_clear()

    return run_async(go)


def loose_subs(video: str, uploaded: dict[str, str]) -> list[dict]:
    """De lijst subs van het item: subtitles.pair (M5) plus de node, zoals M2 hem schrijft."""
    from app.media import subtitles

    found = subtitles.pair([video], list(uploaded))[video]
    return [
        {"node": uploaded[s["name"]], "lang": s["lang"], "forced": s["forced"],
         "sdh": s["sdh"], "default": s["default"]}
        for s in found
    ]  # fmt: skip


def probe_state(media: Any, sha: str) -> dict[str, Any] | None:
    uri = f"file:{media.server.data_dir / 'platform.db'}?mode=ro"
    with closing(sqlite3.connect(uri, uri=True, timeout=5)) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT state, start_ms, subs_state, trick_state, trick FROM media_probe"
            " WHERE blob_sha = ?",
            (sha,),
        ).fetchone()
    if row is None:
        return None
    out = dict(row)
    out["trick"] = json.loads(out["trick"]) if out["trick"] else None
    return out


def wait_state(media: Any, sha: str, field: str, seconds: float = STATE_WAIT_S) -> dict[str, Any]:
    end = time.monotonic() + seconds
    row = None
    while time.monotonic() < end:
        row = probe_state(media, sha)
        if row is not None and row[field] in ("ready", "failed"):
            return row
        time.sleep(0.25)
    raise AssertionError(f"{field} van {sha[:12]} niet klaar binnen {seconds} s: {row}")


def session_play(media: Any, page: Any, token: str, node_id: str, **extra: Any) -> dict:
    """play met het profiel van de browser, met de strings van het item."""
    first = codecs_of(media, token, node_id)
    found = codecs_of(media, token, node_id)
    body = {"profile": profile(page, first["candidates"] + found["item"]), **extra}
    return play(media, token, node_id, body)


CHAPTERS = [(0, 2000, "Intro"), (2000, 4000, "De film"), (4000, 6000, "Aftiteling")]


def webm_with_webvtt(tmp: Path) -> Path:
    """vp9_opus.webm met een ingebed WebVTT-spoor (nld) en hoofdstukken (de laatste heet
    Aftiteling): zo speelt hij direct en heeft hij een tekstspoor e2. Eén ffmpeg, buiten de
    app."""
    from tests.media import fixtures as F

    srt = tmp / "in.srt"
    srt.write_text(F.SRT_NL, encoding="utf-8")
    meta = tmp / "ch.ffmeta"
    meta.write_text(F.chapters_meta(CHAPTERS), encoding="utf-8")
    out = tmp / "film.webm"
    subprocess.run(
        [*F.FFMPEG, "-i", str(F.make("vp9_opus.webm")), "-i", str(srt), "-i", str(meta),
         "-map", "0", "-map", "1", "-map_chapters", "2", "-c", "copy", "-c:s", "webvtt",
         "-metadata:s:s:0", "language=nld", str(out)],
        check=True, timeout=60, stdin=subprocess.DEVNULL,
    )  # fmt: skip
    return out


def diff_box(before: bytes, after: bytes) -> tuple[int, int, int, int] | None:
    """Het vak waar twee schermafdrukken (PNG) verschillen, of None."""
    from PIL import Image, ImageChops

    a = Image.open(io.BytesIO(before)).convert("RGB")
    b = Image.open(io.BytesIO(after)).convert("RGB")
    diff = ImageChops.difference(a, b).convert("L").point(lambda v: 255 if v > 40 else 0)
    return diff.getbbox()


def shot(page: Any) -> bytes:
    return page.locator("#m5").screenshot()


def png_height(png: bytes) -> int:
    from PIL import Image

    return Image.open(io.BytesIO(png)).height


# --- de tests -------------------------------------------------------------------------------------


@pytest.mark.media
def test_ondertitels_als_track_en_ass_bovenaan(media, media_browsers, ffmpeg_count):
    from tests.media import fixtures as F

    owner = media.user()
    folder = media.folder(owner, "M5 film")
    page = media_browsers.context_as(owner).new_page()
    page.set_viewport_size({"width": 1000, "height": 700})
    open_app(page)
    token = login(media, owner)["access_token"]
    with tempfile.TemporaryDirectory(prefix="m5-") as tmp:
        data = webm_with_webvtt(Path(tmp)).read_bytes()
    video = upload_bytes(media, owner, folder, "film.webm", data)
    loose = {
        "film.nl.srt": F.make("nl.srt").read_bytes(),
        "film.en.ass": F.make("en.ass").read_bytes(),
        "film.sup": b"PG\x00",  # een beeldondertitel: die koppelen we niet
    }
    uploaded = {
        name: upload_bytes(media, owner, folder, name, body) for name, body in loose.items()
    }
    subs = loose_subs("film.webm", uploaded)
    assert [s["lang"] for s in subs] == ["eng", "nld"]
    library_item(media, owner.id, folder, video, "Film", subs)
    out = session_play(media, page, token, video)
    assert out["mode"] == "direct", out
    tracks = {t["id"]: t for t in out["tracks"]["subtitles"]}
    assert set(tracks) == {"e2", "x0", "x1"}, tracks
    assert tracks["e2"]["lang"] == "nld" and tracks["x0"]["lang"] == "eng"
    sid = out["sid"]
    base = f"/api/v1/stream/{sid}"
    # elke VTT: via de sessie, text/vtt met nosniff (202 met Retry-After zolang hij gemaakt wordt)
    texts = {}
    for track in tracks:
        got = page.evaluate(FETCH_VTT_JS, f"{base}/subs/{track}.vtt")
        assert got["status"] == 200, (track, got)
        assert got["type"] == "text/vtt; charset=utf-8" and got["nosniff"] == "nosniff", got
        assert got["cache"] == "private, max-age=3600", got
        texts[track] = got["text"]
    assert "Café, één, ë en ü" in texts["x1"]
    assert " line:0\nBovenaan in beeld" in texts["x0"]
    # drie <track>'s; de TextTrack-API ziet de cues
    elements = [
        {"id": i, "src": f"{base}/subs/{i}.vtt", "lang": t["lang"]} for i, t in tracks.items()
    ]
    page.evaluate(TRACKS_JS, {"file": f"{base}/file", "tracks": elements})
    cues = page.wait_for_function(CUES_JS, timeout=15_000).json_value()
    ass, srt, inner = cues["x0"], cues["x1"], cues["e2"]
    assert [c["text"] for c in ass] == ["Bovenaan in beeld", "Onderaan, gewoon"]
    assert ass[0]["line"] == 0 and ass[1]["line"] == "auto"
    assert (srt[0]["start"], srt[0]["end"], srt[0]["text"]) == (1.0, 2.0, "Café, één, ë en ü")
    row = probe_state(media, hashlib.sha256(data).hexdigest())
    assert row is not None and row["state"] == "ready", row
    shift = -(row["start_ms"] or 0) / 1000
    assert [c["text"] for c in inner] == ["Hallo, dit is de eerste regel.", "En dit is de tweede."]
    assert abs(inner[0]["start"] - (1.0 + shift)) < 0.002, inner[0]
    # de hoofdstukken en het begin van de aftiteling (in mediatijd, zoals de cues)
    assert [c["title"] for c in out["chapters"]] == [t for _, _, t in CHAPTERS]
    assert out["credits_ms"] == 4000 + round(shift * 1000), out["credits_ms"]
    # de pixels: op 1,5 s staat de ASS-cue bovenaan, de SRT-cue (op 1,5 s ook) onderaan
    assert abs(page.evaluate(SEEK_JS, 1.5) - 1.5) < 0.1
    page.evaluate(SHOW_JS, "")  # alles uit
    page.wait_for_timeout(200)
    bare = shot(page)
    height = png_height(bare)
    for label, top in (("x0", True), ("x1", False)):
        page.evaluate(SHOW_JS, label)
        page.wait_for_timeout(300)
        box = diff_box(bare, shot(page))
        assert box is not None, f"geen ondertitel te zien voor {label}"
        middle = (box[1] + box[3]) / 2
        if top:
            assert middle < height / 3, (label, box, height)
        else:
            assert middle > height * 2 / 3, (label, box, height)
    page.evaluate(SHOW_JS, "")
    # de staat in de sessie gaat mee: play ziet ze nu als ready
    again = session_play(media, page, token, video, replace=sid)
    assert {t["id"]: t["state"] for t in again["tracks"]["subtitles"]}["e2"] == "ready"


@pytest.mark.media
def test_sprites_via_de_sessie_en_403_na_de_prullenbak(media, media_browsers, ffmpeg_count):
    owner = media.user()
    folder = media.folder(owner, "M5 sprites")
    page = media_browsers.context_as(owner).new_page()
    open_app(page)
    token = login(media, owner)["access_token"]
    up = media.upload(owner, "vp9_opus.webm", folder)
    library_item(media, owner.id, folder, up.id, "Sprites", [])
    row = wait_state(media, up.sha, "trick_state")
    assert row["trick_state"] == "ready", row
    assert row["trick"] == {"interval": 10, "w": 160, "h": 90, "cols": 10, "rows": 10,
                            "count": 1, "tiles": 1}  # fmt: skip
    out = session_play(media, page, token, up.id)
    assert out["mode"] == "direct", out
    assert out["trick"] == {"url": "trick.vtt", "interval_s": 10.0, "width": 160, "height": 90}
    base = f"/api/v1/stream/{out['sid']}"
    got = page.evaluate(FETCH_VTT_JS, f"{base}/trick.vtt")
    assert got["status"] == 200 and got["type"] == "text/vtt; charset=utf-8", got
    assert got["nosniff"] == "nosniff", got
    addresses = [line for line in got["text"].split("\n") if line.startswith("trick/")]
    assert addresses == ["trick/0.jpg#xywh=0,0,160,90"], got["text"]
    for address in addresses:
        url = f"{base}/{address.split('#')[0]}"
        tile = page.evaluate(FETCH_JS, url)
        assert tile["status"] == 200 and tile["type"] == "image/jpeg", tile
        assert tile["nosniff"] == "nosniff", tile
        assert page.evaluate(IMG_JS, url) == [1600, 900]
    # naar de prullenbak: de sessie stopt meteen, ook voor de sprites
    media.server.request("DELETE", f"/api/v1/files/{up.id}", token=token, expect=204)
    for rest in ("trick.vtt", "trick/0.jpg"):
        refused = page.evaluate(FETCH_JS, f"{base}/{rest}")
        assert refused["status"] == 403, refused
        assert refused["detail"]["code"] == "media_access_gone", refused
        assert refused["detail"]["params"]["reason"] == "revoked", refused
    assert page.evaluate(IMG_JS, f"{base}/trick/0.jpg?opnieuw=1") is None


@pytest.mark.media
def test_pgs_inbranden_via_de_producer(media, media_browsers, ffmpeg_count):
    """Keuze 29 en S9: een beeldondertitel kan alleen ingebrand, en dat doet de producer van M4
    (VideoEncode.burn_index). M5 geeft het spoor met staat burn; play met dat spoor vraagt
    transcode. Zonder M4 is daar geen stroom (503 media_off): dan slaat deze test over."""
    owner = media.user()
    folder = media.folder(owner, "M5 PGS")
    page = media_browsers.context_as(owner).new_page()
    open_app(page)
    token = login(media, owner)["access_token"]
    up = media.upload(owner, "pgs.mkv", folder)
    first = codecs_of(media, token, up.id)
    body = {"profile": profile(page, first["candidates"]), "subtitle": "e1"}
    r = media.server.request("POST", f"/api/v1/media/{up.id}/play", token=token, json=body)
    if r.status_code == 409:  # de probe loopt nog
        time.sleep(float(r.headers.get("retry-after", "2")))
        r = media.server.request("POST", f"/api/v1/media/{up.id}/play", token=token, json=body)
    detail = r.json().get("detail") if r.status_code != 200 else None
    if r.status_code == 503 and detail and detail.get("code") in ("media_off", "media_busy"):
        pytest.skip("PGS inbranden gaat via de producer van M4; die is er in deze tak nog niet")
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["mode"] == "transcode", out
    burn = [t for t in out["tracks"]["subtitles"] if t["id"] == "e1"]
    assert burn and burn[0]["state"] == "burn" and burn[0]["default"], out["tracks"]
