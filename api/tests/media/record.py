"""De antwoorden van de echte worker opnemen voor de nep-worker (§3.6).

    cd api && uv run python -m tests.media.record            # alles opnieuw
    cd api && uv run python -m tests.media.record h264_aac.mp4 album/flac/01.flac

Start een lokale worker (echte ffmpeg, PLATFORM_MEDIA_MAX_PROCS 3), maakt de fixtures die er nog
niet zijn, en schrijft:

- `tests/media/recorded/caps.json`: het antwoord op caps (zonder de plaatsen van dat moment);
- `tests/media/recorded/probe/<naam>.json`: per fixture die een bron is het antwoord op probe
  (`{"data": {…}}`), of de nette fout (`{"error": {"code", "detail"}}`). Een `/` in de naam
  (album/flac/01.flac) wordt `__`;
- `tests/media/recorded/probe/_meta.json`: PROBE_VERSION, de versie van ffmpeg en ffprobe, en de
  hash van het recept van elke fixture.

Opnieuw opnemen als PROBE_VERSION stijgt of een recept verandert: test_fake_worker.py faalt dan
met deze opdracht in de melding. Eén ffprobe tegelijk, na elkaar (regel 13).
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

from app.config import Settings
from app.media import analysis, client, local
from app.media.client import WorkerFailed
from app.media.wire import Op
from app.media.worker.args import ProbeArgs
from tests.media import fixtures as F

HERE = Path(__file__).resolve().parent / "recorded"
PROBE_DIR = HERE / "probe"
CAPS_FILE = HERE / "caps.json"
META_FILE = PROBE_DIR / "_meta.json"
# Wat de plaatsen van het moment zijn, niet wat de worker kan
CAPS_VOLATILE = ("in_use", "procs")


def file_name(name: str) -> str:
    return name.replace("/", "__") + ".json"


def name_of(file: Path) -> str:
    return file.name.removesuffix(".json").replace("__", "/")


def sources() -> list[str]:
    """Elke fixture die een bron is, met de nummers van album/ los. Zonder loadtest/ (zwaar),
    de uitvoer van stream en de losse ondertitels (die gaan nooit naar probe)."""
    out: list[str] = []
    for name in F.NAMES:
        if name in F.HEAVY or name in F.WORKER_OUTPUT or name in F.LOOSE_SUBTITLES:
            continue
        if F.RECIPES[name].kind == "dir":
            base = F.make(name)
            out += [f"{name}/{p.relative_to(base)}" for p in sorted(base.rglob("*")) if p.is_file()]
            out = [n for n in out if not n.endswith((".wav", ".png"))]
        else:
            out.append(name)
    return out


def path(name: str) -> Path:
    first, _, rest = name.partition("/")
    base = F.make(first)
    return base / rest if rest else base


def load(name: str) -> dict[str, Any] | None:
    file = PROBE_DIR / file_name(name)
    if not file.exists():
        return None
    return json.loads(file.read_text())


def load_meta() -> dict[str, Any]:
    if not META_FILE.exists():
        return {}
    return json.loads(META_FILE.read_text())


def load_caps() -> dict[str, Any]:
    return json.loads(CAPS_FILE.read_text())


def _write(file: Path, data: dict) -> None:
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(json.dumps(data, indent=1, sort_keys=True, ensure_ascii=False) + "\n")


async def record(names: list[str] | None = None) -> dict[str, str]:
    """Neemt op en geeft per naam "data" of de foutcode."""
    wanted = names or sources()
    for name in wanted:
        path(name)  # eerst alles maken, vóór de worker start
    outcome: dict[str, str] = {}
    with tempfile.TemporaryDirectory(prefix="media-record-") as tmp:
        base = Path(tmp)
        settings = Settings(
            data_dir=base, web_dist=base / "web", jwt_secret="r" * 32, media_max_procs=3
        )
        worker = await local.start(settings, base / "run" / "worker.sock")
        client.bind(worker.socket, worker.key)
        try:
            caps = (await client.caps()).model_dump(mode="json")
            for key in CAPS_VOLATILE:
                caps.pop(key, None)
            _write(CAPS_FILE, caps)
            for name in wanted:
                try:
                    data = await raw_probe(path(name))
                except WorkerFailed as exc:
                    error = {"code": exc.code, "detail": exc.detail}
                    _write(PROBE_DIR / file_name(name), {"error": error})
                    outcome[name] = exc.code
                    continue
                _write(PROBE_DIR / file_name(name), {"data": data})
                outcome[name] = "data"
        finally:
            await local.stop(worker)
            client.bind(None)
    meta = load_meta() if names else {}
    recipes = dict(meta.get("recipes", {}))
    for name in wanted:
        first = name.partition("/")[0]
        recipes[first] = F.recipe_hash(first)
    _write(
        META_FILE,
        {
            "probe_version": analysis.PROBE_VERSION,
            "fixtures_version": F.VERSION,
            "ffmpeg": caps.get("ffmpeg"),
            "ffprobe": caps.get("ffprobe"),
            "recipes": dict(sorted(recipes.items())),
        },
    )
    return outcome


def shape(data: dict) -> dict:
    """Wat bij een andere build van ffmpeg gelijk moet blijven (los van bitrates en tags)."""
    streams = [
        (
            s["type"],
            s["codec"],
            s.get("width"),
            s.get("height"),
            s.get("channels"),
            s.get("sample_rate"),
            s.get("lang"),
            s.get("forced"),
            s.get("hdr"),
        )  # fmt: skip
        for s in data["streams"]
    ]
    return {
        "container": data["container"],
        "streams": streams,
        "chapters": [c.get("title") for c in data.get("chapters", [])],
        "truncated": sorted(data.get("truncated", {})),
    }


def _close(a: int | None, b: int | None, margin: int) -> bool:
    return (a is None) == (b is None) and (a is None or abs(a - b) <= margin)


def differences(name: str, fresh: dict, found: dict) -> list[str]:
    """Waar een verse probe ({"data"} of {"error"}) van de opname afwijkt: de vorm, de duur
    (±60 ms) en per spoor het begin (±30 ms) en het laatste beeld (±60 ms). Leeg: gelijk.
    Voor test_fake_worker.py (ffmpeg 6.1 hier) en tests/media/image_kern.py (7.1 in het image)."""
    if "error" in found or "error" in fresh:
        if fresh.get("error", {}).get("code") != found.get("error", {}).get("code"):
            return [f"{name}: {fresh} tegen {found}"]
        return []
    a, b = fresh["data"], found["data"]
    if shape(a) != shape(b) or not _close(a["duration_ms"], b["duration_ms"], 60):
        return [f"{name}: {shape(a)} tegen {shape(b)}"]
    out = []
    for sa, sb in zip(a["streams"], b["streams"], strict=True):
        if not (
            _close(sa["start_ms"], sb["start_ms"], 30)
            and _close(sa["last_frame_ms"], sb["last_frame_ms"], 60)
        ):
            out.append(f"{name}: tijden van spoor {sa['index']}")
    return out


async def raw_probe(source: Path) -> dict[str, Any]:
    """Het antwoord van de worker zoals het over de socket kwam (de data van result)."""
    async with asyncio.timeout(client.PROBE_TIMEOUT):
        job, _ = await client.call(Op.PROBE, ProbeArgs(), source=source)
        async with job:
            return await job.result()


def main(argv: list[str]) -> int:
    if not F.available():
        print("ffmpeg en ffprobe ontbreken", file=sys.stderr)
        return 2
    outcome = asyncio.run(record(argv or None))
    for name, what in outcome.items():
        print(f"{what:16} {name}")
    print(f"{len(outcome)} opgenomen in {PROBE_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
