"""De kern van de worker in het echte image (job media-image, bouwontwerp fase 5 §4.8).

    docker run --rm --network none ... -v "$PWD/api/tests:/srv/tests:ro" platform:ci \\
        python3 -I /srv/tests/media/image_kern.py [--alles] [--ffmpeg 7.]

In het image staat de app in /srv/app; met api/tests op /srv/tests is de indeling dezelfde als
in api/ (zo draait het script hier ook: `cd api && uv run python -I tests/media/image_kern.py
--ffmpeg 6.`). Alleen wat het image heeft (geen pytest):

1. de fixtures van KERN, gemaakt in het image met tests/media/fixtures.py (in /tmp, één ffmpeg
   tegelijk); met --alles ook elke andere bron waar een opname van is;
2. de lokale worker van M0 (app.media.local), met PLATFORM_MEDIA_MAX_PROCS 3;
3. caps: PROTOCOL, de versie van ffmpeg (--ffmpeg, standaard 7.), niets dat §3.3 nodig heeft
   ontbreekt, en lavfi (spike punt 7: in 7.1 alleen onder -devices);
4. probe via de worker: elke fixture van KERN is ready, met de container en de sporen van de
   opname; broken.mp4 en random.mkv geven hun nette fout;
5. na het stoppen geen ffmpeg of ffprobe meer in de container;
6. met --alles: elke opname (tests/media/recorded/probe) tegen een verse probe, alleen als
   rapport. De opnames zijn van ffmpeg 6.1.1 (de sandbox); een verschil op 7.1 faalt hier niet,
   maar staat in de uitvoer ("opnames"). Dan opnieuw opnemen (record.py), of het verschil in
   m0.md zetten.

Uitvoer: JSON op stdout, het verloop op stderr. Exitcode 1 als een harde controle faalt.
M1 neemt de afscherming over met `--selftest` en infra/single/tests/test_media_container.sh.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
import tempfile
import time
import traceback
from pathlib import Path

sys.dont_write_bytecode = True  # api/tests is in het image alleen te lezen
ROOT = Path(__file__).resolve().parents[2]  # api/ hier, /srv in het image
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import __version__  # noqa: E402
from app.config import Settings  # noqa: E402
from app.media import client, local, wire  # noqa: E402
from app.media.client import WorkerFailed  # noqa: E402
from tests.media import fixtures as F  # noqa: E402
from tests.media import record  # noqa: E402

KERN = ("h264_aac.mp4", "vp9_opus.mkv", "hevc10_hdr10.mkv", "interlaced.ts")
NETTE_FOUTEN = {"broken.mp4": "unreadable", "random.mkv": "unknown_format"}


def log(text: str) -> None:
    print(text, file=sys.stderr, flush=True)


class Uitkomst:
    def __init__(self) -> None:
        self.checks: list[dict] = []

    def check(self, naam: str, ok: bool, verwacht, gemeten) -> bool:
        self.checks.append({"naam": naam, "ok": bool(ok), "verwacht": verwacht, "gemeten": gemeten})
        log(f"  [{'goed' if ok else 'FOUT'}] {naam}: {gemeten}")
        return bool(ok)

    def ok(self) -> bool:
        return bool(self.checks) and all(c["ok"] for c in self.checks)


def sporen(data: dict) -> list[tuple[str, str]]:
    return [(s["type"], s["codec"]) for s in data.get("streams", [])]


async def verse_probe(name: str) -> dict:
    try:
        return {"data": await record.raw_probe(record.path(name))}
    except WorkerFailed as exc:
        return {"error": {"code": exc.code}}


def ffmpeg_processen() -> list[int]:
    """De pids van ffmpeg en ffprobe in deze container (via /proc)."""
    found = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            comm = (entry / "comm").read_text().strip()
        except OSError:
            continue
        if comm in ("ffmpeg", "ffprobe"):
            found.append(int(entry.name))
    return found


async def run(args: argparse.Namespace, uit: Uitkomst) -> dict:
    out: dict = {"app": __version__, "python": sys.version.split()[0], "cpus": os.cpu_count()}
    base = Path(tempfile.mkdtemp(prefix="media-kern-"))
    F.CACHE = base / "fixtures"  # niet in /srv/.pytest_cache: dat is van het image
    try:
        namen = list(KERN) + list(NETTE_FOUTEN)
        if args.alles:
            namen += [n for n in record.sources() if n not in namen]
        log(f"== fixtures maken ({len(namen)}), één ffmpeg tegelijk")
        t0 = time.monotonic()
        for name in namen:
            record.path(name)
        out["fixtures_s"] = round(time.monotonic() - t0, 1)
        log(f"   klaar in {out['fixtures_s']} s")

        settings = Settings(
            _env_file=None,
            data_dir=base / "data",
            web_dist=base / "web",
            jwt_secret="k" * 32,
            media_enabled=True,
            media_max_procs=3,
        )
        log("== de lokale worker (M0) starten")
        worker = await local.start(settings, base / "run" / "worker.sock")
        client.bind(worker.socket)
        try:
            caps = await client.caps()
            out["caps"] = caps.model_dump(mode="json", exclude={"encoders", "filters"})
            log("== caps")
            uit.check(
                "caps: PROTOCOL", caps.protocol == wire.PROTOCOL, wire.PROTOCOL, caps.protocol
            )
            uit.check(
                f"caps: ffmpeg {args.ffmpeg}x",
                (caps.ffmpeg or "").startswith(args.ffmpeg)
                and (caps.ffprobe or "").startswith(args.ffmpeg),
                f"{args.ffmpeg}…",
                {"ffmpeg": caps.ffmpeg, "ffprobe": caps.ffprobe},
            )
            uit.check("caps: niets ontbreekt (§3.3)", caps.missing == [], [], caps.missing)
            uit.check(
                "caps: lavfi (spike punt 7) en de witte lijst",
                "lavfi" in caps.demuxers and {"matroska", "mov", "mpegts"} <= set(caps.demuxers),
                "lavfi, matroska, mov, mpegts",
                caps.demuxers,
            )
            uit.check(
                "caps: lokaal, zonder afscherming, 3 processen",
                caps.mode == "local" and caps.sandbox == "off" and caps.max_procs == 3,
                "local, off, 3",
                [caps.mode, caps.sandbox, caps.max_procs],
            )
            log("== probe op fixtures uit dit image")
            probes: dict = {}
            for name in KERN:
                fresh = await verse_probe(name)
                found = record.load(name) or {}
                data, ref = fresh.get("data") or {}, found.get("data") or {}
                probes[name] = {
                    "container": data.get("container"),
                    "duration_ms": data.get("duration_ms"),
                    "sporen": sporen(data),
                    "fout": fresh.get("error"),
                }
                uit.check(
                    f"probe {name}",
                    data.get("state") == "ready"
                    and data.get("container") == ref.get("container")
                    and sporen(data) == sporen(ref),
                    {"state": "ready", "container": ref.get("container"), "sporen": sporen(ref)},
                    {"state": data.get("state"), **probes[name]},
                )
            for name, code in NETTE_FOUTEN.items():
                fresh = await verse_probe(name)
                probes[name] = fresh
                got = (fresh.get("error") or {}).get("code")
                uit.check(f"probe {name}: nette fout", got == code, code, got)
            out["probes"] = probes
            if args.alles:
                log("== alle opnames tegen een verse probe (alleen een rapport)")
                anders: list[str] = []
                bronnen = record.sources()
                for name in bronnen:
                    found = record.load(name)
                    if found is None:
                        anders.append(f"{name}: geen opname")
                        continue
                    anders += record.differences(name, await verse_probe(name), found)
                out["opnames"] = {
                    "van": {k: record.load_meta().get(k) for k in ("ffmpeg", "ffprobe")},
                    "bronnen": len(bronnen),
                    "anders": anders,
                }
                log(f"   {len(bronnen) - len(anders)} van {len(bronnen)} gelijk")
                for line in anders:
                    log(f"   ANDERS {line}")
        finally:
            await local.stop(worker)
            client.bind(None)
        over: list[int] = []
        for _ in range(20):  # de kinderen zijn bij stop al gestopt; even ruimte voor waitpid
            over = ffmpeg_processen()
            if not over:
                break
            await asyncio.sleep(0.1)
        uit.check("na het stoppen: geen ffmpeg of ffprobe meer", not over, [], over)
    finally:
        shutil.rmtree(base, ignore_errors=True)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--alles", action="store_true", help="ook elke opname als rapport")
    ap.add_argument("--ffmpeg", default="7.", help="het begin van de verwachte versie")
    args = ap.parse_args()
    uit = Uitkomst()
    t0 = time.monotonic()
    try:
        out = asyncio.run(run(args, uit))
    except Exception:
        out = {"crash": traceback.format_exc()[-4000:]}
        log(out["crash"])
    out["checks"] = uit.checks
    out["ok"] = uit.ok() and "crash" not in out
    out["duur_s"] = round(time.monotonic() - t0, 1)
    print(json.dumps(out, ensure_ascii=False, indent=1, default=str))
    log(f"== {'goed' if out['ok'] else 'FOUT'}: {sum(c['ok'] for c in uit.checks)} van "
        f"{len(uit.checks)} controles in {out['duur_s']} s")  # fmt: skip
    return 0 if out["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
