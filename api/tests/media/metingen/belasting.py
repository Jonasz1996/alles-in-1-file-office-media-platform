"""Md, Me, Mg en Mh (§4.5): de belastingtest van §4.6 in de app, met wat eromheen gemeten wordt.

De test zelf draait in de app, precies zoals de knop op /beheer en `app.cli media-loadtest`:
POST /api/v1/admin/media/loadtest {streams, seconds, codec}, en dan wachten op het rapport (de
fasen a en b, de grenzen van §4.6 punt 7). Dit script start daarvoor een eigen app (kit.App:
de lokale worker onder taskset -c 0-2, PLATFORM_MEDIA_MAX_PROCS=3) en meet ernaast:

- de CPU en de RSS van de app, de worker en zijn ffmpeg per fase (Mh: de hoogste RSS van de
  worker met zijn kinderen), en de CPU van de rest van de machine (wat er naast liep);
- de API van buitenaf (Mg): elke seconde GET /api/v1/files/root en /api/v1/media/home, de p95
  per fase (het rapport van de app heeft zijn eigen p95 van binnenuit);
- elke 2 s het overzicht van /beheer: de trede en de snelheid per sessie, het budget en of DS
  start (B − 1,0).

Varianten (elk met --herhaal keer):

- standaard: 3 sessies, 300 s; de treden kiest de toelating met de f van de meting (Vraag 3
  verwacht op de LXC 1080 veryfast + 720 superfast + 720 superfast; in de sandbox, met een
  hogere f, wordt het lager: metingen.md, Md);
- Me: --max-transcodes 2 --sessies 2 (1080 veryfast + 1080 superfast);
- --office: met de echte DS 10.0.0.1 (e2e/office_ds.sh, met de grenzen van
  compose.office-limits.yaml: 2 cpu's, 2 GiB), en dan:
  - --docx-elke 30: elke 30 s een nieuwe docx van ±50 pagina's uploaden; de app zet hem om naar
    PDF via DS (Keuze 25), de tijd tot pdf_state ready staat in het rapport;
  - --ds-herstart S: S seconden na het begin van fase a `docker restart` van DS (fase d);
- --fase-c: de spelers buiten de app (`app.cli media-loadtest --extern`), en na --herstart-na s
  de app stoppen op pid en weer starten (fase c).

    cd api && uv run python -m tests.media.metingen.belasting --herhaal 5 --uit /tmp/md.json
    cd api && uv run python -m tests.media.metingen.belasting --fase-c --herhaal 5 --uit …
    cd api && uv run python -m tests.media.metingen.belasting --office --docx-elke 30 --uit …
    cd api && uv run python -m tests.media.metingen.belasting --office --ds-herstart 200 --uit …
    cd api && uv run python -m tests.media.metingen.belasting --sessies 2 --max-transcodes 2 …

De bronnen (3 × 300 s 1080p H.264, ±675 MB) maakt de worker de eerste keer met synth; met
--werkmap blijven ze staan voor een volgende keer.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

import httpx

from tests.media.metingen import kit

OFFICE_DS = kit.REPO / "e2e" / "office_ds.sh"
OFFICE_CONFIG = kit.REPO / "infra" / "single" / "office" / "local-production-linux.json"
DS_NAME = "meet5-ds"
API_PATHS = ("/api/v1/files/root", "/api/v1/media/home")
DOCX_PARAGRAPHS = 2400  # ±50 pagina's (docx_401: 19.240 alinea's is 401 pagina's)


class Probe(threading.Thread):
    """De API van buitenaf (Mg): elke seconde de twee GET's, met de tijd per antwoord."""

    def __init__(self, app: kit.App) -> None:
        super().__init__(daemon=True, name="api")
        self.app = app
        self.times: list[tuple[float, str, float, int]] = []  # (t, pad, ms, status)
        self._halt = threading.Event()

    def run(self) -> None:
        with httpx.Client(base_url=self.app.url, trust_env=False, timeout=10) as http:
            while not self._halt.wait(1.0):
                with contextlib.suppress(Exception):
                    headers = self.app.headers()
                    for path in API_PATHS:
                        t0 = time.monotonic()
                        try:
                            r = http.get(path, headers=headers)
                            status = r.status_code
                        except httpx.HTTPError:
                            status = 0
                        self.times.append((t0, path, (time.monotonic() - t0) * 1000, status))

    def window(self, start: float, end: float) -> dict[str, Any]:
        part = [ms for t, _p, ms, status in self.times if start <= t <= end and status == 200]
        failed = sum(1 for t, _p, _ms, status in self.times if start <= t <= end and status != 200)
        found = kit.p95(part)
        return {
            "n": len(part),
            "p95_ms": round(found, 1) if found is not None else None,
            "max_ms": round(max(part), 1) if part else None,
            "mislukt": failed,
        }

    def stop(self) -> None:
        self._halt.set()
        self.join(timeout=5)


class Overview(threading.Thread):
    """Elke 2 s /beheer → Media (en Office): de trede en de snelheid per sessie, het budget."""

    def __init__(self, app: kit.App, office: bool) -> None:
        super().__init__(daemon=True, name="overzicht")
        self.app = app
        self.office = office
        self.rows: list[dict[str, Any]] = []
        self._halt = threading.Event()

    def run(self) -> None:
        while not self._halt.wait(2.0):
            with contextlib.suppress(Exception):
                found = self.app.call("GET", "/api/v1/admin/media").json()
                row: dict[str, Any] = {
                    "t": time.monotonic(),
                    "budget": found.get("budget"),
                    "sessions": [
                        {
                            "id": s.get("id"),
                            "title": s.get("title"),
                            "mode": s.get("mode"),
                            "rung": s.get("rung"),
                            "speed": s.get("speed"),
                        }
                        for s in found.get("sessions") or []
                    ],
                }
                if self.office:
                    with contextlib.suppress(Exception):
                        row["office"] = (
                            self.app.call("GET", "/api/v1/admin/office").json().get("state")
                        )
                self.rows.append(row)

    def window(self, start: float, end: float) -> dict[str, Any]:
        part = [r for r in self.rows if start <= r["t"] <= end]
        rungs: dict[str, dict[str, Any]] = {}
        speeds: dict[str, list[float]] = {}
        for r in part:
            for s in r["sessions"]:
                if s.get("mode") != "transcode":
                    continue
                rung = s.get("rung") or {}
                key = str(s.get("id"))
                rungs.setdefault(key, {"bron": s.get("title"), **rung})
                if s.get("speed") is not None:
                    speeds.setdefault(key, []).append(float(s["speed"]))
        for key, row in rungs.items():
            values = speeds.get(key) or []
            row["snelheid_max"] = round(max(values), 2) if values else None
        office = [r.get("office") for r in part if "office" in r]
        starting = [r for r in part if (r.get("budget") or {}).get("ds_starting")]
        return {
            "treden": list(rungs.values()),
            "budget_totaal": sorted({(r.get("budget") or {}).get("total") for r in part} - {None}),
            "ds_starting_s": len(starting) * 2,
            "office": _runs(office),
        }

    def trace(self, start: float, end: float) -> list[list[Any]]:
        """[s na start, budget in gebruik, budget totaal, ds_starting, office, sessies] per rij."""
        out = []
        for r in self.rows:
            if start <= r["t"] <= end:
                b = r.get("budget") or {}
                out.append([
                    round(r["t"] - start, 1), b.get("used"), b.get("total"),
                    b.get("ds_starting"), r.get("office"), len(r["sessions"]),
                ])  # fmt: skip
        return out

    def stop(self) -> None:
        self._halt.set()
        self.join(timeout=5)


def _runs(values: list[Any]) -> list[list[Any]]:
    """[waarde, aantal] achter elkaar: down ×5, starting ×40, ok ×20."""
    out: list[list[Any]] = []
    for v in values:
        if out and out[-1][0] == v:
            out[-1][1] += 1
        else:
            out.append([v, 1])
    return out


class Docx(threading.Thread):
    """Elke every s een nieuwe docx van ±50 pagina's; de app zet hem om naar PDF (via DS)."""

    def __init__(self, app: kit.App, every: float) -> None:
        super().__init__(daemon=True, name="docx")
        self.app = app
        self.every = every
        self.done: list[dict[str, Any]] = []
        self._halt = threading.Event()
        self._seed = int(time.time())
        sys.path.insert(0, str(kit.REPO / "e2e" / "perf"))
        import office_files  # noqa: PLC0415 - uit e2e/perf, de generator van fase 4

        self.make = office_files.docx_401
        self.folder = self.app.mkdir(self.app.root_folder(), f"Docx {self._seed}")

    def run(self) -> None:
        while not self._halt.is_set():
            began = time.monotonic()
            self._seed += 1
            data, _info = self.make(self._seed, DOCX_PARAGRAPHS)
            entry: dict[str, Any] = {"t": began, "bytes": len(data)}
            try:
                node = self.app.upload_bytes(self.folder, f"verslag-{self._seed}.docx", data)
                entry["node"] = node
                entry |= self._wait_pdf(node, began)
            except Exception as exc:  # noqa: BLE001 - alleen voor het rapport
                entry["fout"] = str(exc)[:200]
            self.done.append(entry)
            self._halt.wait(max(0.0, self.every - (time.monotonic() - began)))

    def _wait_pdf(self, node: str, began: float) -> dict[str, Any]:
        """Tot pdf_state ready of failed in de database (alleen lezen), hooguit 120 s."""
        db = self.app.data_dir / "platform.db"
        end = began + 120
        state = None
        while time.monotonic() < end and not self._halt.is_set():
            with contextlib.closing(sqlite3.connect(f"file:{db}?mode=ro", uri=True)) as con:
                query = (
                    "SELECT b.pdf_state FROM node n JOIN blob b ON b.sha256 = n.blob_id "
                    "WHERE n.id = ?"
                )
                row = con.execute(query, (node.replace("-", ""),)).fetchone()
                if row is None:
                    row = con.execute(query, (node,)).fetchone()
            state = row[0] if row else None
            if state in ("ready", "failed", "none", "unsafe"):
                return {"pdf": state, "seconden": round(time.monotonic() - began, 2)}
            time.sleep(0.5)
        return {"pdf": state or "?", "seconden": None}

    def window(self, start: float, end: float) -> dict[str, Any]:
        part = [d for d in self.done if start <= d["t"] <= end]
        return {
            "geupload": len(part),
            "klaar": sum(1 for d in part if d.get("pdf") == "ready"),
            "seconden": kit.spread(d.get("seconden") for d in part if d.get("pdf") == "ready"),
            "anders": [{k: v for k, v in d.items() if k in ("pdf", "fout")} for d in part
                       if d.get("pdf") != "ready"],
        }  # fmt: skip

    def stop(self) -> None:
        self._halt.set()
        self.join(timeout=150)


# --- DS -----------------------------------------------------------------------------------------


def ds_start(work: Path, port: int) -> kit.Office:
    env = {
        **os.environ,
        "OFFICE_DS_NAME": DS_NAME,
        "OFFICE_DS_PORT": str(port),
        "OFFICE_DS_DIR": str(work / "ds"),
        "OFFICE_DS_CONFIG": str(OFFICE_CONFIG),
    }
    subprocess.run(["bash", str(OFFICE_DS), "start"], env=env, check=True)
    subprocess.run(["bash", str(OFFICE_DS), "wait"], env=env, check=True)
    secret = (work / "ds" / "jwt.secret").read_text().strip()
    return kit.Office(url=f"http://127.0.0.1:{port}", secret=secret, container=DS_NAME)


def ds_stop(work: Path) -> None:
    env = {**os.environ, "OFFICE_DS_NAME": DS_NAME,
           "OFFICE_DS_DIR": str(work / "ds")}  # fmt: skip
    subprocess.run(["bash", str(OFFICE_DS), "stop"], env=env, check=False)


def ds_memory() -> int | None:
    """Het geheugen van de container van DS (docker stats), in MB."""
    out = subprocess.run(
        ["docker", "stats", "--no-stream", "--format", "{{.MemUsage}}", DS_NAME],
        capture_output=True, text=True, check=False,
    ).stdout.strip()  # fmt: skip
    if not out:
        return None
    used = out.split("/")[0].strip()
    for unit, factor in (("GiB", 1024), ("MiB", 1), ("KiB", 1 / 1024)):
        if used.endswith(unit):
            return round(float(used[: -len(unit)]) * factor)
    return None


def wait_office(app: kit.App, wanted: str = "ok", seconds: float = 600) -> str:
    end = time.monotonic() + seconds
    state = "?"
    while time.monotonic() < end:
        with contextlib.suppress(Exception):
            state = app.call("GET", "/api/v1/admin/office").json().get("state", "?")
            if state == wanted:
                return state
        time.sleep(2)
    return state


# --- één run ------------------------------------------------------------------------------------


def run_inside(app: kit.App, args, sampler, probe, overview, docx) -> dict[str, Any]:
    """Eén belastingtest in de app (fase a en b), met de fasen op de klok van dit script."""
    body = {"streams": args.sessies, "seconds": args.duur, "codec": args.codec}
    began = time.monotonic()
    app.call("POST", "/api/v1/admin/media/loadtest", json=body, expect=202)
    phases: dict[str, float] = {}
    restart: dict[str, Any] = {}
    ds_mem: list[int] = []
    while True:
        time.sleep(1.0)
        found = app.call("GET", "/api/v1/admin/media/loadtest").json()
        phase = found.get("phase")
        now = time.monotonic()
        if phase and phase not in phases:
            phases[phase] = now
            sampler.mark(f"fase {phase}")
            kit.log(f"fase {phase}")
        if args.ds_herstart is not None and "a" in phases and not restart:
            if now - phases["a"] >= args.ds_herstart:
                restart = {"t": now, "na_begin_fase_a_s": round(now - phases["a"], 1)}
                kit.log("DS herstarten (docker restart)")
                subprocess.run(["docker", "restart", DS_NAME], capture_output=True, check=False)
                restart["herstart_klaar_s"] = round(time.monotonic() - now, 1)
        if args.office and int(now) % 10 == 0:
            mem = ds_memory()
            if mem is not None:
                ds_mem.append(mem)
        if found.get("state") in ("done", "failed", "stopped"):
            report = found.get("report") or {}
            break
        if now - began > 60 * 30 + args.duur * 4:
            report = {"error": "te lang"}
            break
    end = time.monotonic()
    windows: dict[str, Any] = {}
    names = sorted(phases, key=phases.get)
    for i, name in enumerate(names):
        start = phases[name]
        stop = phases[names[i + 1]] if i + 1 < len(names) else end
        windows[name] = {
            "processen": sampler.window(start, stop),
            "api_buiten": probe.window(start, stop),
            "overzicht": overview.window(start, stop),
        }
        if docx is not None:
            windows[name]["docx"] = docx.window(start, stop)
    out = {
        "rapport": report,
        "fasen": windows,
        "duur_s": round(end - began, 1),
        "bronnen_s": round(phases.get(names[0], end) - began, 1) if names else None,
    }
    if restart:
        out["ds_herstart"] = {k: v for k, v in restart.items() if k != "t"}
        out["ds_herstart"]["office_na"] = overview.window(restart["t"], end)["office"]
        out["ds_herstart"]["verloop"] = overview.trace(restart["t"] - 4, end)
    if ds_mem:
        out["ds_geheugen_mb_max"] = max(ds_mem)
    return out


def run_external(app: kit.App, args, sampler, probe) -> dict[str, Any]:
    """Fase c: de spelers buiten de app (app.cli media-loadtest --extern), en na herstart_na s
    de app stoppen op pid en weer starten (zoals een update)."""
    cmd = [
        sys.executable, "-m", "app.cli", "media-loadtest", "--extern", app.url,
        "--sessies", str(args.sessies), "--duur", str(args.duur), "--codec", args.codec,
    ]  # fmt: skip
    began = sampler.mark("fase c")
    proc = subprocess.Popen(
        cmd, cwd=kit.API_DIR, env=app.environment(), stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True,
    )  # fmt: skip
    time.sleep(args.herstart_na)
    kit.log("de app herstarten (stoppen op pid, weer starten)")
    stopped = time.monotonic()
    ready = app.restart(outage_s=args.uitval)
    kit.log(f"de app is er weer na {ready - stopped:.1f} s")
    out, err = proc.communicate(timeout=args.duur * 3 + 600)
    end = time.monotonic()
    try:
        report = json.loads(out[out.index("{") :])
    except ValueError:
        report = {"error": "geen JSON", "stdout": out[-2000:], "stderr": err[-2000:]}
    return {
        "rapport": report,
        "herstart": {
            "na_s": round(stopped - began, 1),
            "weg_tot_ready_s": round(ready - stopped, 2),
        },
        "fasen": {
            "c": {
                "processen": sampler.window(ready, end),
                "api_buiten": probe.window(ready, end),
            }
        },
        "duur_s": round(end - began, 1),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--herhaal", type=int, default=1)
    parser.add_argument("--sessies", type=int, default=3)
    parser.add_argument("--duur", type=int, default=300)
    parser.add_argument("--codec", choices=("h264", "vp9"), default="h264")
    parser.add_argument("--max-transcodes", type=int, default=3)
    parser.add_argument("--office", action="store_true")
    parser.add_argument("--ds-poort", type=int, default=18190)
    parser.add_argument("--docx-elke", type=float, default=None)
    parser.add_argument("--ds-herstart", type=float, default=None)
    parser.add_argument("--fase-c", action="store_true")
    parser.add_argument("--herstart-na", type=float, default=60.0)
    parser.add_argument("--uitval", type=float, default=0.0)
    parser.add_argument("--werkmap", type=Path, default=None)
    parser.add_argument("--uit", type=Path, default=None)
    parser.add_argument("--rust", type=float, default=0.6, help="cores: eerst wachten tot stil")
    args = parser.parse_args(argv)
    if (args.docx_elke or args.ds_herstart is not None) and not args.office:
        parser.error("--docx-elke en --ds-herstart vragen --office")

    keep = args.werkmap is not None
    work = args.werkmap or Path(tempfile.mkdtemp(prefix="meet5-belasting-"))
    work.mkdir(parents=True, exist_ok=True)
    dog = kit.Watchdog()
    dog.start()
    office = None
    app = None
    result: dict[str, Any] = {
        "meting": "belasting",
        "omstandigheden": kit.conditions(),
        "opties": {k: (str(v) if isinstance(v, Path) else v) for k, v in vars(args).items()},
        "runs": [],
    }
    try:
        if args.office:
            dog.containers.append(DS_NAME)
            office = ds_start(work, args.ds_poort)
        app = kit.App(work, office=office)
        dog.watch(lambda: app.pid if app is not None else None)
        app.start()
        if office is not None:
            result["office_bij_de_start"] = wait_office(app)
        result["rust_voor_bench"] = kit.quiet(args.rust, 900)
        result["bench"] = app.bench()
        kit.log("bench:", result["bench"])
        # altijd zetten: de instellingen staan in de database van --werkmap, en een vorige
        # variant (Me: 2) mag niet blijven hangen
        app.config(max_transcodes=args.max_transcodes)
        result["config"] = app.call("GET", "/api/v1/admin/media").json().get("config")
        for i in range(args.herhaal):
            if dog.tripped:
                break
            rest = kit.quiet(args.rust)
            if office is not None:
                wait_office(app)
            kit.log(f"run {i + 1} van {args.herhaal} (de machine: {rest} cores bezig)")
            sampler = kit.Sampler(app)
            probe = Probe(app)
            overview = Overview(app, office is not None)
            for t in (sampler, probe, overview):
                t.start()
            docx = Docx(app, args.docx_elke) if args.docx_elke else None
            if docx is not None:
                docx.start()
            try:
                if args.fase_c:
                    run = run_external(app, args, sampler, probe)
                else:
                    run = run_inside(app, args, sampler, probe, overview, docx)
            finally:
                for t in (sampler, probe, overview):
                    t.stop()
                if docx is not None:
                    docx.stop()
            run["rust_voor_cores"] = rest
            run["ffmpeg_max"] = sampler.max_ffmpeg
            result["runs"].append(run)
            for line in (run.get("rapport") or {}).get("summary") or []:
                kit.log(line)
            kit.write_json(args.uit, result)
    finally:
        dog.stop()
        result["waakhond"] = dog.summary()
        if app is not None:
            app.close()
        if office is not None:
            ds_stop(work)
        kit.write_json(args.uit, result)
        if not keep:
            shutil.rmtree(work, ignore_errors=True)
    return 0 if not dog.tripped else 99


if __name__ == "__main__":
    raise SystemExit(main())
