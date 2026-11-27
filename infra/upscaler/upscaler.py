#!/usr/bin/env python3
"""De upscaler (fase 11, stap 5c): vergroot films en afleveringen vooraf met AI op de RTX 3060 Ti.

Draait als eigen container op de node met de kaart (jbogaertmedia2) en praat met de app via
/api/v1/upscaler (token van /beheer → Media → AI-upscaling). Alleen de standaardbibliotheek van
Python; ffmpeg (met NVENC), ffprobe, nvidia-smi en realesrgan-ncnn-vulkan staan in de image.

De lus:

1. Elke minuut `claim`. De app geeft alleen werk binnen het venster (standaard :20-:40), met
   `seconds_left` en de VRAM-grens.
2. Het origineel naar /work/<job>/source (hervat met Range als het half binnen is).
3. In stukken van SEGMENT_S seconden, vanaf `segments_done`:
   - genoeg tijd over voor nog een stuk (de duur van het vorige stuk × 1,3), en genoeg vrij
     videogeheugen (`nvidia-smi`)? Anders stoppen tot het volgende venster;
   - ffmpeg pakt de beelden van dat stuk uit (CPU) als PNG;
   - realesrgan-ncnn-vulkan vergroot ze ×2 (GPU), met een tile-grootte naar de VRAM-grens;
   - ffmpeg codeert ze met hevc_nvenc tot seg_NNNNN.mkv en ruimt de PNG's op;
   - `progress` met het aantal klare stukken (de app zegt hoeveel tijd er nog is).
4. Alle stukken klaar: aan elkaar (zonder opnieuw coderen), met de audio en ondertitels van het
   origineel erbij, en als `result` naar de app. Dan /work/<job> weg.

Een fout in een stuk: nog twee keer proberen, dan `fail` met een korte reden.
"""

from __future__ import annotations

import http.client
import json
import math
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

URL = os.environ.get("PLATFORM_URL", "").rstrip("/")
TOKEN = os.environ.get("UPSCALER_TOKEN", "")
WORK = Path(os.environ.get("UPSCALER_WORK", "/work"))
MODEL = os.environ.get("UPSCALER_MODEL", "realesr-animevideov3")
GPU = os.environ.get("UPSCALER_GPU", "0")
SEGMENT_S = 10
POLL_S = 60
SAFETY = 1.3
TRIES = 3


def log(msg: str) -> None:
    print(time.strftime("%Y-%m-%d %H:%M:%S"), msg, flush=True)


# --- pure stukjes (getest in api/tests/arr/test_upscaler_script.py) ----------------------------


def segments(duration_s: float, segment_s: int = SEGMENT_S) -> int:
    return max(1, math.ceil(duration_s / segment_s))


def tile_for(vram_mb: int) -> int:
    """De tile-grootte van realesrgan-ncnn-vulkan voor deze VRAM-grens (0 = automatisch, dat
    neemt te veel)."""
    if vram_mb >= 6144:
        return 400
    if vram_mb >= 4096:
        return 256
    if vram_mb >= 2048:
        return 128
    return 64


def time_enough(seconds_left: float, last_segment_s: float | None) -> bool:
    """Nog een stuk beginnen? Het eerste stuk schatten we op 60 s."""
    need = (last_segment_s if last_segment_s is not None else 60.0) * SAFETY
    return seconds_left >= need


def vram_free_mb(smi_output: str) -> int | None:
    """Uit `nvidia-smi --query-gpu=memory.total,memory.used --format=csv,noheader,nounits`."""
    try:
        total, used = (int(x.strip()) for x in smi_output.strip().splitlines()[0].split(","))
    except (ValueError, IndexError):
        return None
    return total - used


# --- de app -------------------------------------------------------------------------------------


def call(method: str, path: str, body: dict | None = None) -> tuple[int, dict | None]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(URL + path, data=data, method=method)
    req.add_header("Authorization", f"Bearer {TOKEN}")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        return e.code, None


def download(job: str, size: int, dest: Path) -> None:
    have = dest.stat().st_size if dest.exists() else 0
    if have == size:
        return
    req = urllib.request.Request(f"{URL}/api/v1/upscaler/jobs/{job}/source")
    req.add_header("Authorization", f"Bearer {TOKEN}")
    if have:
        req.add_header("Range", f"bytes={have}-")
    with urllib.request.urlopen(req, timeout=120) as r, dest.open("ab" if have else "wb") as f:
        if have and r.status != 206:
            f.truncate(0)
        shutil.copyfileobj(r, f, 4 * 1024 * 1024)


def upload(job: str, path: Path) -> int:
    parts = urlsplit(URL)
    conn_cls = (
        http.client.HTTPSConnection if parts.scheme == "https" else http.client.HTTPConnection
    )
    conn = conn_cls(parts.netloc, timeout=600)
    size = path.stat().st_size
    conn.putrequest("PUT", f"{parts.path}/api/v1/upscaler/jobs/{job}/result")
    conn.putheader("Authorization", f"Bearer {TOKEN}")
    conn.putheader("Content-Type", "video/x-matroska")
    conn.putheader("Content-Length", str(size))
    conn.endheaders()
    with path.open("rb") as f:
        while chunk := f.read(4 * 1024 * 1024):
            conn.send(chunk)
    status = conn.getresponse().status
    conn.close()
    return status


# --- ffmpeg en het model ------------------------------------------------------------------------


def run(cmd: list[str]) -> None:
    done = subprocess.run(cmd, capture_output=True, text=True)
    if done.returncode != 0:
        tail = (done.stderr or "").strip().splitlines()[-1:] or ["?"]
        raise RuntimeError(f"{Path(cmd[0]).name}: {tail[0][:150]}")


@dataclass
class Probe:
    duration: float
    fps: str


def probe(source: Path) -> Probe:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=avg_frame_rate:format=duration", "-of", "json", str(source)],
        capture_output=True, text=True, check=True,
    ).stdout  # fmt: skip
    data = json.loads(out)
    fps = data["streams"][0].get("avg_frame_rate") or "24000/1001"
    return Probe(float(data["format"]["duration"]), fps if fps != "0/0" else "24000/1001")


def free_vram() -> int | None:
    try:
        out = subprocess.run(
            ["nvidia-smi", f"--id={GPU}", "--query-gpu=memory.total,memory.used",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, check=True,
        ).stdout  # fmt: skip
    except (OSError, subprocess.CalledProcessError):
        return None
    return vram_free_mb(out)


def do_segment(source: Path, folder: Path, n: int, info: Probe, vram_mb: int) -> None:
    frames, big = folder / "frames", folder / "big"
    for d in (frames, big):
        shutil.rmtree(d, ignore_errors=True)
        d.mkdir(parents=True)
    run(["ffmpeg", "-nostdin", "-v", "error", "-ss", str(n * SEGMENT_S), "-t", str(SEGMENT_S),
         "-i", str(source), "-map", "0:v:0", "-vf", f"fps={info.fps}", "-compression_level", "1",
         str(frames / "%06d.png")])  # fmt: skip
    if not any(frames.iterdir()):
        return  # na het laatste beeld: leeg stuk
    run(["realesrgan-ncnn-vulkan", "-i", str(frames), "-o", str(big), "-n", MODEL, "-s", "2",
         "-t", str(tile_for(vram_mb)), "-g", GPU, "-f", "png"])  # fmt: skip
    tmp = folder / f"seg_{n:05d}.part.mkv"
    run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-framerate", info.fps,
         "-i", str(big / "%06d.png"), "-c:v", "hevc_nvenc", "-preset", "p5", "-rc", "vbr",
         "-cq", "20", "-pix_fmt", "p010le", str(tmp)])  # fmt: skip
    tmp.rename(folder / f"seg_{n:05d}.mkv")
    shutil.rmtree(frames, ignore_errors=True)
    shutil.rmtree(big, ignore_errors=True)


def finish(source: Path, folder: Path, total: int) -> Path:
    listing = folder / "list.txt"
    names = [
        f"file 'seg_{n:05d}.mkv'" for n in range(total) if (folder / f"seg_{n:05d}.mkv").exists()
    ]
    listing.write_text("\n".join(names) + "\n")
    video = folder / "video.mkv"
    run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "concat", "-safe", "0",
         "-i", str(listing), "-c", "copy", str(video)])  # fmt: skip
    result = folder / "result.mkv"
    run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(video), "-i", str(source),
         "-map", "0:v:0", "-map", "1:a?", "-map", "1:s?", "-c", "copy", str(result)])  # fmt: skip
    return result


# --- de lus -------------------------------------------------------------------------------------


def work(claim: dict) -> None:
    job = claim["id"]
    folder = WORK / job
    folder.mkdir(parents=True, exist_ok=True)
    source = folder / "source"
    download(job, int(claim["size"]), source)
    info = probe(source)
    total = segments(info.duration)
    left = float(claim["seconds_left"])
    started = time.monotonic()
    last: float | None = None
    n = int(claim["segments_done"])
    log(f"{claim['name']}: stuk {n} van {total}, {int(left)} s in het venster")
    while n < total:
        remaining = left - (time.monotonic() - started)
        if not time_enough(remaining, last):
            log("het venster gaat dicht: verder in het volgende")
            return
        free = free_vram()
        if free is not None and free < int(claim["vram_mb"]):
            log(f"te weinig vrij videogeheugen ({free} MB): later verder")
            return
        t0 = time.monotonic()
        for attempt in range(TRIES):
            try:
                do_segment(source, folder, n, info, int(claim["vram_mb"]))
                break
            except RuntimeError as exc:
                if attempt == TRIES - 1:
                    call("POST", f"/api/v1/upscaler/jobs/{job}/fail", {"reason": str(exc)[:200]})
                    shutil.rmtree(folder, ignore_errors=True)
                    return
        last = time.monotonic() - t0
        n += 1
        status, body = call(
            "POST",
            f"/api/v1/upscaler/jobs/{job}/progress",
            {"segments_done": n, "segments_total": total},
        )
        if status == 409:
            log("geannuleerd")
            shutil.rmtree(folder, ignore_errors=True)
            return
        if body is not None:
            left, started = float(body["seconds_left"]), time.monotonic()
    result = finish(source, folder, total)
    status = upload(job, result)
    if status == 204:
        log(f"{claim['name']}: klaar")
        shutil.rmtree(folder, ignore_errors=True)
    else:
        log(f"het resultaat kwam niet aan ({status}); volgende ronde opnieuw")


def main() -> int:
    if not URL or not TOKEN:
        log("PLATFORM_URL en UPSCALER_TOKEN ontbreken")
        return 2
    WORK.mkdir(parents=True, exist_ok=True)
    while True:
        try:
            status, claim = call("POST", "/api/v1/upscaler/claim")
            if status == 200 and claim:
                work(claim)
            elif status == 401:
                log("de app weigert het token")
        except Exception as exc:  # noqa: BLE001 - de volgende ronde probeert het opnieuw
            log(f"fout: {exc}")
        time.sleep(POLL_S)


if __name__ == "__main__":
    sys.exit(main())
