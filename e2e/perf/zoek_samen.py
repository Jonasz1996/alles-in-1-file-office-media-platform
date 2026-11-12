"""Zoeken naast media en OnlyOffice (fase 6, Z7 en I1; bouwontwerp §4.5, deel B): de tijden, de
CPU en het geheugen in drie stappen, elk zolang de belastingtest van media loopt.

Stappen
1. pauze: het achtergrondwerk staat op pauze (PATCH /admin/search, paused), met een volle rij
   index: n documenten zonder tekst, zoals vóór Z2 (zoek_backfill.zaai; de backfill bij het
   starten zet ze klaar), en sinds I1 ook de foto's en de scans van §4.5 (zie onder). De
   nulmeting. Met --office heeft, zoals op een 0.7.x met OnlyOffice, elk document dat DS omzet
   zijn PDF al (zaai_stap, zaai office_klaar): de inhaalronde van O3 (fase 4) hoort niet bij de
   upgrade naar 0.8.0. Zonder dat zette DS in elke stap, ook de nulmeting, ±2.700 documenten om
   (docx, xlsx, pptx en rtf; I1, M7).
2. achtergrond: dezelfde rij; de pauze gaat eraf zodra de belastingtest loopt (GET
   /admin/media/loadtest zegt running), zodat de index geen voorsprong krijgt. Op dat moment
   leest photometa ook alle foto's opnieuw (POST /admin/search/rebuild hash: GPS, camera en
   pHash). Defer staat aan, zoals standaard (index_defer_transcodes 2): de index en OCR wijken
   voor omzettingen, en tijdens de belastingtest altijd (die zet media.analysis.busy_live op
   minstens 2).
3. zonder_defer: hetzelfde met n nieuwe documenten (weer op pauze tot de belastingtest loopt, en
   weer de herbouw hash), en Defer uit (PLATFORM_INDEX_DEFER_TRANSCODES=1000). Dan beschermen
   alleen nice en de idle IO-klasse. Een rapport.

De last van §4.5 (I1; e2e/perf/zoek_foto.py):
- foto's (--fotos, standaard 5.000; --kort 300): JPEG's van 640 × 480 met EXIF, 85% met een
  plek, 5% bijna-dubbel, elk met zijn voorbeeld van 256, in een eigen map "Foto's". Ze staan er
  als al gelezen (zoek_foto.KLAAR), zodat photometa (die de pauze van de index niet kent) in stap
  1 niets doet; de herbouw hash in stap 2 en 3 laat hem alles opnieuw lezen.
- scans voor X1 (--scans, standaard 500; --kort 20): PDF's zonder tekstlaag, A4 op 300 dpi, op
  0°, 3°, 7°, −5° en 90°, in een eigen map "Scans", één keer (bij stap 1). Hun rijen van ocr
  wachten op de pauze; wat in stap 2 niet af komt, loopt in stap 3 door. Alleen met tesseract
  (nld, eng en osd) en DejaVu Sans: --kort slaat ze anders over en zegt dat (de CI-job
  e2e-media-perf heeft alleen ffmpeg); zonder --kort stopt het script met de reden.
- Nog niet in deze meting (0.8.1): de container analyse (gezichten en beeld, X2 en X3) en de
  beeldvraag.

Tijdens elke stap
- media: `python -m app.cli media-loadtest --sessies S --duur D` tegen de draaiende app (de
  belastingtest van fase 5, in de app: fase a gespreid, fase b tegelijk; korter dan 120 s
  alleen fase b, op 720p en de laagste trede);
- zoeken: 2 vragen per seconde (GET /search, een woord uit de documenten), de p95;
- de app: GET /files/root (de eerste pagina van 200, zoals de web-app een map opent) en GET
  /health elke seconde, de p95;
- geheugen: MemAvailable van de machine, elke seconde, het minimum;
- achtergrondwerk: de rijen van de stappen text en ocr die klaar zijn (zonder skip: wat de stap
  nooit leest, zoals de foto's voor text) en wat photometa nog te doen heeft, aan het begin,
  direct na de herbouw hash, na de belastingtest en aan het eind (en hoeveel foto's een pHash
  hebben); na de belastingtest hoe lang het duurt tot de index (text of ocr) weer leest (poort
  ≤ 90 s);
- de processen: de app en de lokale worker (pid en starttijd) aan het begin en het eind; een
  ander proces of geen proces is een crash of een OOM-kill (die ziet docker inspect niet: ze
  draaien op de host);
- de cores (regel 14 van fase 5): aan het begin van elke stap de lokale worker en zijn kinderen
  op cores 0-2, de grens van de LXC (compose.media-limits.yaml: cpus 3.0, een core vrij voor de
  app); de app, DS en de bewerkers niet. Draai het script zelf dus niet onder taskset: dan deelt
  de app de cores van ffmpeg (zo liep M7 van I1), en dat staat onder "niet in deze meting";
- met --office (de echte DS, E2E_REAL_DS en E2E_REAL_DS_SECRET_FILE; docs/fase-4/e2e.md): B
  bewerkers in OnlyOffice die D seconden typen en dan sluiten (office_editors.one_round, met
  de CallbackClock), in elke stap vanaf het moment dat de belastingtest loopt: de tijd van
  sluiten tot opgeslagen, en de woorden die niet in het bestand kwamen; en de container van DS
  (docker inspect): zijn geheugengrens, OOMKilled, en of hij intussen herstart is.

Poorten (stap 2; stap 3 is een rapport): elke start van media ≤ 5 s en 0 keer haperen (de
poorten van de belastingtest zelf); zoeken p95 ≤ 200 ms; GET /files/root?limit=200 p95 ≤ 200 ms;
GET /health p95 ≤ 50 ms (elk zonder één mislukt verzoek); MemAvailable ≥ 1,5 GiB; geen container
OOMKilled (DS, met een geheugengrens); de app en de worker leven nog (dezelfde processen); de
index leest weer binnen 90 s na de laatste transcode; het opslaan in OnlyOffice ≤ 1,2 × de
nulmeting en 0 verloren woorden (alleen als de ronde volledig liep, in beide stappen naast de
belastingtest: iedereen open, één status 2 per document; onvolledig()). Elke poort zegt
"gehaald", "niet gehaald" of "niet te meten"; zonder --kort telt "niet te meten" als niet gehaald
(ook de poorten van OnlyOffice zonder --office).

Nodig: ffmpeg en ffprobe (de worker en de belastingtest); een gebouwde web-app (cd web && npm
run build: de server serveert web/dist); voor de scans tesseract met nld, eng en osd, en DejaVu
Sans (fonts-dejavu-core); met --office docker en de echte DS (e2e/office_ds.sh start: die zet
--memory alleen als docker info MemoryLimit en CPUCfsQuota true zegt; zonder geheugengrens stopt
het script, want dan zegt OOMKilled niets) en Playwright (uv run --with playwright).

    cd api && uv run python ../e2e/perf/zoek_samen.py --kort
    cd api && E2E_REAL_DS=http://127.0.0.1:18150 E2E_REAL_DS_SECRET_FILE=<map>/jwt.secret \\
      uv run --with playwright python ../e2e/perf/zoek_samen.py --office

--kort (CI, job e2e-media-perf, rapport): 60 s per fase van de belastingtest, 1 sessie, zonder
OnlyOffice, 300 documenten per stap, 300 foto's en (met tesseract) 20 scans. Zonder --kort:
300 s, 3 sessies, 5.000 documenten, 5.000 foto's en 500 scans; met --office ook 5 bewerkers
(docs/runbooks/zoeken.md). Kleiner dan de last van §4.5 (--docs, --fotos of --scans) kan, maar
dan staat dat onder "niet in deze meting".

Aan het eind "=== kort ===" (ook in --kort-uit, voor het einde van het log in CI). In --kort is de
uitkomst altijd 0 (een rapport); zonder --kort 1 als een poort van stap 2 niet gehaald of niet te
meten is.
"""

from __future__ import annotations

import argparse
import contextlib
import functools
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))  # e2e/perf
sys.path.insert(0, str(HERE.parent))  # e2e/
sys.path.insert(0, str(HERE.parents[1] / "api"))  # de app en zijn testhulpen

import zoek_backfill as zb  # noqa: E402
import zoek_foto as zf  # noqa: E402

from conftest import API_DIR, Account, Server, make_admin, percentile  # noqa: E402
from media_harness import (  # noqa: E402
    cmdline,
    descendants,
    media_env,
    process_table,
    set_affinity,
)

API = "/api/v1"
GIB = 1024**3
POORT = {
    "zoeken_p95_ms": 200.0,
    "files_root_p95_ms": 200.0,
    "health_p95_ms": 50.0,
    "mem_min_bytes": 1.5 * GIB,
    "hervat_s": 90.0,
    "opslaan_factor": 1.2,
}
ZONDER_DEFER = "1000"  # zoveel omzettingen tegelijk zijn er nooit: de index wijkt niet
HERVAT_WACHT_S = 180.0
# GET /files/root zoals de web-app een map opent: de eerste pagina (web/src/browser/api.ts
# FIRST_PAGE). Zonder limit kost de lijst van alle 5.000 documenten in de hoofdmap zelf al
# p95 ±320 ms (10.000: ±640 ms), ook zonder last; met limit 200 ±40 ms (I1, M7). Dan meet de
# poort de lengte van de map in plaats van de app naast het achtergrondwerk
EERSTE_PAGINA = 200
# De last van §4.5 in stap 2 (--kort, anders): documenten per stap, foto's en scans
DOCS = (300, 5_000)
FOTOS = (300, 5_000)
SCANS = (20, 500)
STAPPEN = ("text", "ocr")  # de stappen van de analyse die de pauze vasthoudt
# Wat docker info moet zeggen voordat e2e/office_ds.sh DS een geheugengrens geeft
GRENZEN = ("MemoryLimit", "CPUCfsQuota")
# Regel 14 (fase 5): de worker en wat hij start op 3 cores, zoals de LXC hem begrenst
# (compose.media-limits.yaml: cpus 3.0, "houdt een core vrij voor de app"). De app, DS en de
# bewerkers (Chromium) niet. In M7 (I1) liep het hele script onder taskset -c 0-2: dan deelde de
# app de cores van ffmpeg en de bewerkers, en bleef de vierde core bijna leeg; zo staat hij op de
# LXC niet
WORKER_CORES = frozenset({0, 1, 2})
WORKER_WACHT_S = 30.0  # na seeded() start de app de worker opnieuw


@dataclass
class Meter:
    """Tijden in ms per soort, en het geheugen; alles vanuit draden naast de belastingtest."""

    ms: dict[str, list[float]] = field(default_factory=dict)
    fouten: dict[str, int] = field(default_factory=dict)
    mem_min: int | None = None
    stop: threading.Event = field(default_factory=threading.Event)

    def tel(self, soort: str, ms: float | None) -> None:
        if ms is None:
            self.fouten[soort] = self.fouten.get(soort, 0) + 1
        else:
            self.ms.setdefault(soort, []).append(ms)

    def p95(self, soort: str) -> float | None:
        waarde = percentile(self.ms.get(soort, []), 95)
        return None if waarde is None else round(waarde, 1)


def mem_available() -> int:
    for regel in Path("/proc/meminfo").read_text().splitlines():
        if regel.startswith("MemAvailable:"):
            return int(regel.split()[1]) * 1024
    return 0


def _get(http: httpx.Client, path: str, **kw: Any) -> float | None:
    begin = time.perf_counter()
    try:
        r = http.get(path, **kw)
    except httpx.HTTPError:
        return None
    ms = (time.perf_counter() - begin) * 1000
    return ms if r.status_code == 200 else None


def zoekers(
    server: Server, token: Callable[[], str], woorden: list[str], meter: Meter
) -> list[threading.Thread]:
    """2 zoekvragen per seconde; elke seconde /files/root (de eerste pagina, EERSTE_PAGINA) en
    /health; elke seconde het geheugen. token per verzoek (Server.token: hooguit 10 minuten oud,
    het access-token leeft er 15): met één token per stap kreeg in M7 het laatste deel van stap 3
    alleen 401's (375 zoekvragen), en een mislukt verzoek telt niet in de p95 (wel in fouten, en
    dan haalt oordeel() de poort niet)."""
    rng = random.Random(7)

    def kop() -> dict[str, str]:
        return {"Authorization": f"Bearer {token()}"}

    def zoek() -> None:
        with httpx.Client(base_url=server.url, trust_env=False, timeout=30) as http:
            while not meter.stop.wait(0.5):
                q = rng.choice(woorden)
                meter.tel("zoeken", _get(http, f"{API}/search", params={"q": q}, headers=kop()))

    def app() -> None:
        with httpx.Client(base_url=server.url, trust_env=False, timeout=30) as http:
            while not meter.stop.wait(1.0):
                meter.tel("files_root", _get(http, f"{API}/files/root", headers=kop(),
                                             params={"limit": EERSTE_PAGINA}))  # fmt: skip
                meter.tel("health", _get(http, "/health"))
                vrij = mem_available()
                meter.mem_min = vrij if meter.mem_min is None else min(meter.mem_min, vrij)

    draden = [threading.Thread(target=zoek, daemon=True), threading.Thread(target=app, daemon=True)]
    for d in draden:
        d.start()
    return draden


def wacht_op_de_belastingtest(server: Server, admin: Account, klaar: threading.Event) -> bool:
    """Tot de belastingtest in de app loopt (state running: de bronnen zijn er, de spelers
    beginnen). False als hij al klaar is of niet binnen 10 minuten begint."""
    einde = time.monotonic() + 600
    while time.monotonic() < einde and not klaar.is_set():
        r = server.call(admin, "GET", f"{API}/admin/media/loadtest", expect=200)
        if r.json().get("state") == "running":
            return True
        time.sleep(0.5)
    return False


def belastingtest(server: Server, sessies: int, duur: int, uit: Path) -> dict[str, Any]:
    """app.cli media-loadtest naast de draaiende app (dezelfde omgeving); de uitkomst."""
    proc = subprocess.run(
        [sys.executable, "-m", "app.cli", "media-loadtest"]
        + ["--sessies", str(sessies), "--duur", str(duur), "--json", str(uit)],
        cwd=API_DIR,
        env=server.environment(),
        capture_output=True,
        text=True,
        timeout=60 * 30 + duur * 4,
    )
    if not uit.is_file():
        return {"passed": False, "fout": (proc.stderr or proc.stdout).strip()[-500:]}
    return json.loads(uit.read_text())


def analyse_stand(server: Server) -> dict[str, dict[str, int]]:
    """De rijen van text en ocr per state."""
    return {step: dict(zb.stand(server.data_dir, step)) for step in STAPPEN}


def klaar(stand: dict[str, int]) -> int:
    return sum(stand.get(s, 0) for s in zb.KLAAR)


def gelezen(stand: dict[str, int]) -> int:
    """klaar() zonder skip: de rijen die de stap echt gelezen heeft. skip is wat hij nooit leest
    (de foto's voor text; voor ocr alles wat geen PDF of afbeelding is) en staat er al vanaf de
    backfill, dus telt dat niet als voortgang (review van I1, breken F3). Alleen voor wat er te
    zien is; het hervatten telt met klaar(), als verschil."""
    return klaar(stand) - stand.get("skip", 0)


def voortgang(
    voor: dict[str, dict[str, int]],
    na_media: dict[str, dict[str, int]],
    na: dict[str, dict[str, int]],
) -> dict[str, Any]:
    """Uit analyse_stand aan het begin, na de belastingtest en aan het eind: hoeveel rijen van
    text gelezen zijn (zonder skip), en de rijen van ocr per state."""
    return {
        "tekst_klaar_voor": gelezen(voor["text"]),
        "tekst_klaar_na_media": gelezen(na_media["text"]),
        "tekst_klaar_na": gelezen(na["text"]),
        "ocr": {"voor": voor["ocr"], "na_media": na_media["ocr"], "na": na["ocr"]},
    }


def klaar_en_open(standen: dict[str, dict[str, int]]) -> tuple[int, int]:
    """(klaar, open) over de stappen samen."""
    return (
        sum(klaar(s) for s in standen.values()),
        sum(s.get("wait", 0) + s.get("busy", 0) for s in standen.values()),
    )


def wacht_tot_de_index_leest(server: Server, vanaf: int) -> float | None:
    """Na de belastingtest: hoe lang tot er meer rijen van text en ocr samen klaar zijn dan vanaf.
    None als er niets meer te lezen was (de rijen al leeg) of het niet binnen de tijd gebeurde."""
    begin = time.monotonic()
    while time.monotonic() - begin < HERVAT_WACHT_S:
        nu, open_ = klaar_en_open(analyse_stand(server))
        if nu > vanaf:
            return round(time.monotonic() - begin, 1)
        if open_ == 0:
            return None
        time.sleep(0.5)
    return None


def foto_stand(server: Server) -> dict[str, int]:
    """Hoe ver photometa is (zoek_foto.foto_stand): te_doen, gehasht, plekken."""
    s = zf.foto_stand(server.data_dir)
    return {k: s[k] for k in ("open", "te_doen", "gehasht", "plekken") if k in s}


def herbouw_hash(server: Server, admin: Account) -> str:
    """POST /admin/search/rebuild hash: photometa leest alle foto's opnieuw. "gestart", of "liep
    nog" (409: de vorige herbouw van photometa is nog niet klaar)."""
    r = server.call(admin, "POST", f"{API}/admin/search/rebuild", json={"what": "hash"},
                    expect=(202, 409))  # fmt: skip
    return "gestart" if r.status_code == 202 else "liep nog"


# --- de processen: de app en de worker op de host, de container van DS ---


def processen(server: Server) -> dict[str, list[int] | None]:
    """De app en de lokale worker als [pid, starttijd] (None: er is er geen). De worker is het
    kind van de app met app.media.worker op zijn opdrachtregel dat zelf geen kind van zo een is
    (een fork erft de opdrachtregel)."""
    uit: dict[str, list[int] | None] = {"app": None, "worker": None}
    proc = server.proc
    if proc is None or proc.poll() is not None:
        return uit
    tabel = process_table()
    if proc.pid not in tabel:
        return uit
    uit["app"] = [proc.pid, tabel[proc.pid][1]]
    workers = {p for p in descendants([proc.pid], tabel) if "app.media.worker" in cmdline(p)}
    eerste = sorted(p for p in workers if tabel.get(p, (0,))[0] not in workers)
    if eerste:
        uit["worker"] = [eerste[0], tabel[eerste[0]][1]]
    return uit


def worker_op_zijn_cores(
    server: Server, wacht_s: float = WORKER_WACHT_S
) -> dict[str, list[int] | None]:
    """Regel 14, aan het begin van elke stap (seeded() start de app en dus de worker opnieuw): de
    lokale worker en al zijn kinderen op WORKER_CORES, of wat deze machine daarvan heeft (zoals
    MediaApp.pin); wat hij daarna start, erft het. Wacht tot de worker er is. Geeft de cores van
    de worker en van de app (None: er is er geen), voor de uitkomst."""
    einde = time.monotonic() + wacht_s
    procs = processen(server)
    while procs["app"] and not procs["worker"] and time.monotonic() < einde:
        time.sleep(0.5)
        procs = processen(server)
    uit: dict[str, list[int] | None] = {"worker": None, "app": None}
    if procs["app"]:
        with contextlib.suppress(OSError):
            uit["app"] = sorted(os.sched_getaffinity(procs["app"][0]))
    if not procs["worker"]:
        return uit
    beschikbaar = os.sched_getaffinity(0)
    cores = (WORKER_CORES & beschikbaar) or beschikbaar
    pid = procs["worker"][0]
    for p in {pid, *descendants([pid], process_table())}:
        set_affinity(p, cores)
    uit["worker"] = sorted(cores)
    return uit


def deelt_de_cores_van_de_worker() -> str | None:
    """Zonder --kort: draait het script zelf (en dus de app) alleen op de cores van de worker
    terwijl de machine er meer heeft (taskset rond het script, zoals in M7)? Dan staat dat onder
    "niet in deze meting": de poorten zijn dan strenger dan op de LXC, niet zachter."""
    eigen = os.sched_getaffinity(0)
    if eigen <= WORKER_CORES and (os.cpu_count() or 0) > len(WORKER_CORES):
        return (f"een core voor de app (regel 14): het script draait op cores {cores_tekst(eigen)}"
                f" van {os.cpu_count()}, dus de app deelt de cores van de worker")  # fmt: skip
    return None


def cores_tekst(cores: Any) -> str:
    """{0, 1, 2} → "0-2"; [0, 2] → "0,2"; None → "-"."""
    if not cores:
        return "-"
    rij = sorted(cores)
    if rij == list(range(rij[0], rij[-1] + 1)) and len(rij) > 1:
        return f"{rij[0]}-{rij[-1]}"
    return ",".join(str(c) for c in rij)


def overleefd(voor: dict[str, Any] | None, na: dict[str, Any] | None) -> tuple[bool | None, str]:
    """Leven de app en de worker van het begin van de stap aan het eind nog, als dezelfde
    processen? None als ze er bij het begin niet (allebei) waren."""
    voor, na = voor or {}, na or {}
    if not voor.get("app"):
        return None, "geen app bij het begin"
    if not voor.get("worker"):
        return None, "geen lokale worker bij het begin (E2E_MEDIA_WORKER: buiten de app)"
    weg = [naam for naam in ("app", "worker") if list(na.get(naam) or []) != list(voor[naam])]
    if weg:
        return False, f"niet meer hetzelfde proces: {', '.join(weg)}"
    return True, "dezelfde processen"


def ds_lees(info: dict[str, Any]) -> dict[str, Any]:
    """Uit docker inspect van de container van DS: de geheugengrens (bytes, 0 = geen),
    OOMKilled, de status, de herstarts en wanneer hij startte."""
    host, state = info.get("HostConfig") or {}, info.get("State") or {}
    return {
        "geheugen": int(host.get("Memory") or 0),
        "oom": bool(state.get("OOMKilled")),
        "status": state.get("Status"),
        "herstarts": int(info.get("RestartCount") or 0),
        "gestart": state.get("StartedAt"),
    }


def ds_toestand(naam: str | None) -> dict[str, Any] | None:
    """ds_lees van de container naam nu; None zonder naam of zonder antwoord van docker."""
    if not naam:
        return None
    try:
        raw = subprocess.run(["docker", "inspect", naam], capture_output=True, text=True,
                             timeout=20, check=True).stdout  # fmt: skip
        return ds_lees(json.loads(raw)[0])
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        return None


def docker_grenzen() -> dict[str, bool] | None:
    """Wat deze docker-host kan (docker info: MemoryLimit en CPUCfsQuota): e2e/office_ds.sh zet
    --memory alleen als ze allebei true zijn. None als docker niet antwoordt."""
    formaat = " ".join(f"{{{{.{k}}}}}" for k in GRENZEN)
    try:
        out = subprocess.run(["docker", "info", "--format", formaat],
                             capture_output=True, text=True, timeout=20, check=True)  # fmt: skip
    except (OSError, subprocess.SubprocessError):
        return None
    return grenzen_lees(out.stdout)


def grenzen_lees(tekst: str) -> dict[str, bool] | None:
    """De uitvoer van docker info --format '{{.MemoryLimit}} {{.CPUCfsQuota}}'; None als die er
    anders uitziet."""
    delen = tekst.split()
    if len(delen) != len(GRENZEN):
        return None
    return {k: d == "true" for k, d in zip(GRENZEN, delen, strict=True)}


def zonder_grens(host: dict[str, bool] | None) -> str:
    """Waarom OOMKilled van DS niets zegt als hij geen geheugengrens heeft, en wat eraan te doen
    is (host: docker_grenzen())."""
    tekst = "de container van DS heeft geen geheugengrens (HostConfig.Memory 0)"
    mist = [k for k in GRENZEN if host is not None and not host.get(k)]
    if "MemoryLimit" in mist:
        return tekst + "; deze docker-host kan er geen zetten (docker info: MemoryLimit false)"
    if mist:
        # Zoals office_ds.sh: zonder CPUCfsQuota ook geen --memory (review van I1, regels F4)
        return tekst + (
            f"; e2e/office_ds.sh zet --memory alleen als MemoryLimit en CPUCfsQuota true zijn, en "
            f"deze docker-host zegt {mist[0]} false: start DS met de hand met --memory 2g "
            f"--memory-swap 2g"
        )
    return tekst + "; start hem met e2e/office_ds.sh (--memory 2g)"


def ds_oordeel(ds: dict[str, Any] | None) -> tuple[bool | None, str]:
    """De poort "geen container OOMKilled" voor DS: ds is {"voor", "na", "host"} (None zonder
    --office). Alleen met een geheugengrens zegt OOMKilled iets; een herstart wist hem."""
    if not ds:
        return None, "zonder OnlyOffice"
    voor, na = ds.get("voor"), ds.get("na")
    if not voor or not na:
        return None, "docker inspect gaf niets"
    if voor["geheugen"] <= 0:
        return None, zonder_grens(ds.get("host"))
    herstart = (na["gestart"], na["herstarts"]) != (voor["gestart"], voor["herstarts"])
    if na["status"] != "running" or herstart:
        return False, f"DS is gestopt of herstart ({na['status']}, {na['herstarts']} herstarts)"
    if na["oom"]:
        return False, "DS OOMKilled"
    return True, f"OOMKilled false, grens {voor['geheugen'] // 2**20} MiB"


def pauze(server: Server, admin: Account, aan: bool) -> None:
    r = server.call(admin, "PATCH", f"{API}/admin/search", json={"paused": aan}, expect=200)
    assert r.json()["paused"] is aan


@dataclass
class Opzet:
    server: Server
    admin: Account
    office: Any = None  # office_bench.Bench met --office
    squad: Any = None
    ds_name: str | None = None
    url: str | None = None
    fotos: int = 0  # foto's in de map "Foto's" (dan de herbouw hash in stap 2 en 3)
    ds_host: dict[str, bool] | None = None  # docker_grenzen(), met --office


def pauze_eraf(opzet: Opzet) -> dict[str, Any] | None:
    """Het begin van stap 2 en 3, zodra de belastingtest loopt: de pauze eraf, en met foto's de
    herbouw hash. photometa kent de pauze niet: pas nu, net als de index, leest hij alle foto's
    opnieuw. Hoeveel hij dan te doen heeft, staat in de uitkomst (de peiling voor de stap ziet
    0)."""
    pauze(opzet.server, opzet.admin, False)
    if not opzet.fotos:
        return None
    herbouw = herbouw_hash(opzet.server, opzet.admin)
    return {"herbouw_hash": herbouw, "fotos_te_doen": foto_stand(opzet.server).get("te_doen")}


def start(base: Path, env: dict[str, str], office: bool) -> Opzet:
    if not office:
        server = Server(base / "data", env=env)
        server.start()
        return Opzet(server, make_admin(server))
    import office_bench as ob

    url, secret = ob.need_real_ds()
    ds_name = ob.ds_container(url).get("name")
    if not ds_name:
        raise SystemExit("de container van DS is niet te vinden (docker ps op de poort)")
    bench = ob.start_office(base, url, secret, clock=True)
    bench.server.extra_env.update(env)
    bench.server.restart()
    return Opzet(bench.server, bench.office.admin, office=bench, ds_name=ds_name, url=url)


def bewerkers(
    opzet: Opzet, n: int, seconden: float, ronde: int, start: threading.Event | None = None
) -> dict[str, Any]:
    """B bewerkers (5 documenten met elk n // 5, minstens 1) die typen en sluiten, zoals
    office_editors.one_round; zonder het wachten op een stille machine of een lege wachtrij
    (quiet=False), want hier loopt de rest juist. Met dat wachten begonnen ze in de eerste volle
    meting (M7) in geen enkele stap: in stap 1 zette DS nog de documenten van zaai om, in stap 2
    en 3 is de rij nooit leeg, en make_docs gaf na 300 s op.

    start: pas typen als dit gezet is (stap: de belastingtest loopt), zodat de bewerkers in elke
    stap op hetzelfde moment van de belastingtest opslaan; het team maken gaat ervoor. Wat
    office_editors.check als fout ziet (niet iedereen open, geen of twee keer status 2 per
    document, te weinig geheugen) komt mee; oordeel() rekent het (onvolledig()). Te weinig
    geheugen is hier de poort van §4.5 (MemAvailable < 1,5 GiB), niet LOW_FREE van fase 4 (3 GiB):
    daaronder stoppen openen en typen."""
    import office_bench as ob
    import office_editors as oe

    docs = min(5, n)
    per_doc = max(1, n // docs)
    if opzet.squad is None:
        opzet.squad = ob.team(opzet.office, {}, members=docs * per_doc - 1)
    if start is not None:
        start.wait()
    r = oe.one_round(opzet.office, opzet.squad, docs, per_doc, seconden, ronde,
                     opzet.ds_name or "", opzet.url or "", quiet=False,
                     min_free=int(POORT["mem_min_bytes"]))  # fmt: skip
    callbacks = [s for p in r["per_doc_results"] for s in p["callback_s"]]
    return {
        "bewerkers": r["editors"],
        "open": r["opened"],
        "opslaan_s": r["save_s"],
        "callback_p95_s": percentile(callbacks, 95),
        "getypt": r["typed"],
        "opgeslagen": r["saved"],
        "verloren": r["lost_saving"] + (r["typed"] - r["saved"]),
        "status2": [p["status2"] for p in r["per_doc_results"]],
        "weinig_geheugen": bool(r["low_memory"]),
        "fouten_open": [str(f)[:200] for f in r["open_errors"][:3]],
    }


def onvolledig(office: dict[str, Any] | None) -> str | None:
    """Waarom de ronde van de bewerkers niet telt (None: ze telt), zoals office_editors.check:
    de belastingtest liep niet (stap: na_de_start_van_media), niet iedereen open, een document
    zonder (of met twee keer) status 2, niets getypt, of te weinig geheugen (dan stopten openen
    en typen eerder). Zonder dat haalde een ronde waarin niemand opende de poorten: opslaan ±0 s
    en 0 verloren woorden (review van de herstelling van M7)."""
    if not office or "fout" in office:
        return (office or {}).get("fout", "geen bewerkers")
    if office.get("na_de_start_van_media") is not True:
        return "de belastingtest liep niet: de bewerkers typten zonder media"
    if not office.get("bewerkers") or office.get("open") != office.get("bewerkers"):
        return f"{office.get('open')} van {office.get('bewerkers')} bewerkers open"
    status2 = office.get("status2")
    if not status2 or any(s != 1 for s in status2):
        return f"status 2 per document: {status2}"
    if not office.get("getypt"):
        return "niets getypt"
    if office.get("weinig_geheugen") is not False:  # niet gemeten telt als te weinig
        return "te weinig geheugen vrij (MemAvailable < 1,5 GiB): de bewerkers stopten eerder"
    return None


def stap(
    opzet: Opzet,
    naam: str,
    args: argparse.Namespace,
    woorden: list[str],
    werk: Path,
    ronde: int,
    begin: Callable[[], dict[str, Any] | None] | None = None,
) -> dict[str, Any]:
    """Eén stap: de belastingtest, met daarnaast de zoekers (en met --office de bewerkers). begin
    (de pauze eraf, de herbouw hash) gaat pas als de belastingtest loopt; wat het teruggeeft,
    komt onder "begin" in de uitkomst."""
    server, admin = opzet.server, opzet.admin
    meter = Meter()
    voor = analyse_stand(server)
    fotos_voor = foto_stand(server) if opzet.fotos else None
    cores = worker_op_zijn_cores(server)
    procs_voor = processen(server)
    ds_voor = ds_toestand(opzet.ds_name) if opzet.office is not None else None
    print(f"[{naam}] begin: {voor}, foto's {fotos_voor}, processen {procs_voor}, cores {cores}",
          flush=True)  # fmt: skip
    draden = zoekers(server, lambda: server.token(admin), woorden, meter)
    office: dict[str, Any] | None = None
    office_draad = None
    # De bewerkers typen pas als de belastingtest loopt: in stap 1 maakt hij eerst zijn bronnen
    # (±2 minuten; daarna liggen ze er), dus zonder dit sloegen ze daar op een ander moment van
    # de belastingtest op dan in stap 2 (review van de herstelling van M7)
    media_loopt = threading.Event()
    if opzet.office is not None:
        uitkomst: dict[str, Any] = {}

        def typ() -> None:
            try:
                uitkomst.update(
                    bewerkers(opzet, args.bewerkers, float(args.duur), ronde, start=media_loopt)
                )
            except Exception as exc:  # in de uitkomst, niet alleen op stderr
                uitkomst["fout"] = f"{type(exc).__name__}: {exc}"[:300]

        office_draad = threading.Thread(target=typ, daemon=True)
        office_draad.start()
    klok = time.monotonic()
    media: dict[str, Any] = {}
    media_klaar = threading.Event()

    def last() -> None:
        try:
            media.update(
                belastingtest(server, args.sessies, args.duur, werk / f"media-{naam}.json")
            )
        finally:
            media_klaar.set()

    media_draad = threading.Thread(target=last, daemon=True)
    media_draad.start()
    begonnen = loopt = None
    bij_begin: dict[str, Any] | None = None
    if begin is not None or office_draad is not None:
        loopt = wacht_op_de_belastingtest(server, admin, media_klaar)
        media_loopt.set()  # ook als hij niet kwam: dan haalt media zijn poort niet
    if begin is not None:
        begonnen = loopt
        bij_begin = begin()
    media_draad.join()
    media_s = round(time.monotonic() - klok, 1)
    na_media = analyse_stand(server)
    fotos_na_media = foto_stand(server) if opzet.fotos else None
    meter.stop.set()
    for d in draden:
        d.join(timeout=35)
    if office_draad is not None:
        office_draad.join(timeout=60 * 15)
        if office_draad.is_alive():
            office = {"fout": "de bewerkers kwamen na 15 minuten niet terug"}
        else:
            office = uitkomst or {"fout": "de bewerkers gaven niets terug"}
        office["na_de_start_van_media"] = loopt
    hervat = None
    if naam != "pauze":
        hervat = wacht_tot_de_index_leest(server, klaar_en_open(na_media)[0])
    na = analyse_stand(server)
    fotos_na = foto_stand(server) if opzet.fotos else None
    fotos = None
    if fotos_voor is not None:
        fotos = {"voor": fotos_voor, "na_media": fotos_na_media, "na": fotos_na}
    ds = None
    if opzet.office is not None:
        ds = {"voor": ds_voor, "na": ds_toestand(opzet.ds_name), "host": opzet.ds_host}
    uit = {
        "stap": naam,
        "media_s": media_s,
        "pauze_eraf_tijdens_media": begonnen,
        "begin": bij_begin,
        "media": {
            "passed": media.get("passed"),
            "samenvatting": media.get("summary") or [media.get("fout", "")],
            "api_p95_ms": media.get("api_p95_ms"),
            # per fase de spelers (start, seek, haperen, herstarts) en de worker: alleen om te
            # lezen, zodat haperen naast de seeks en het werk ernaast te leggen is (I1, M7)
            "fasen": media.get("phases"),
            "worker_cpu_cores": media.get("worker_cpu_cores"),
            "ffmpeg_max": media.get("ffmpeg_max"),
        },
        "zoeken_p95_ms": meter.p95("zoeken"),
        "zoeken_n": len(meter.ms.get("zoeken", [])),
        "files_root_p95_ms": meter.p95("files_root"),
        "health_p95_ms": meter.p95("health"),
        "fouten": meter.fouten,
        "mem_min_mb": None if meter.mem_min is None else meter.mem_min // 2**20,
        "mem_min_bytes": meter.mem_min,
        **voortgang(voor, na_media, na),
        "fotos": fotos,
        "hervat_s": hervat,
        "processen": {"voor": procs_voor, "na": processen(server)},
        "cores": cores,
        "ds": ds,
        "office": office,
    }
    print(f"[{naam}] {json.dumps(uit, ensure_ascii=False)}", flush=True)
    return uit


def oordeel(stap2: dict[str, Any], nul: dict[str, Any]) -> list[tuple[str, bool | None]]:
    """De poorten van stap 2: (regel, gehaald); None als er niets te meten was (de regel zegt
    dan waarom). Altijd dezelfde poorten, ook zonder OnlyOffice: zie geslaagd()."""

    def onder(waarde: float | None, grens: float) -> bool | None:
        return None if waarde is None else waarde <= grens

    def boven(waarde: float | None, grens: float) -> bool | None:
        return None if waarde is None else waarde >= grens

    out: list[tuple[str, bool | None]] = [
        ("media: elke start ≤ 5 s, 0 keer haperen", stap2["media"]["passed"]),
    ]
    # Een mislukt verzoek (geen 200, of na 30 s geen antwoord) zit niet in de p95; elke mislukte
    # telt daarom als niet gehaald, anders mat de poort alleen wat lukte (M7: 401's in stap 3)
    fouten = stap2.get("fouten") or {}
    for sleutel, soort, regel in (
        ("zoeken_p95_ms", "zoeken", "zoeken p95 ≤ {:g} ms"),
        (
            "files_root_p95_ms",
            "files_root",
            "GET /files/root?limit=" + str(EERSTE_PAGINA) + " p95 ≤ {:g} ms",
        ),
        ("health_p95_ms", "health", "GET /health p95 ≤ {:g} ms"),
    ):
        regel = regel.format(POORT[sleutel])
        if fouten.get(soort):
            out.append((f"{regel} ({fouten[soort]} mislukt)", False))
        else:
            out.append((regel, onder(stap2[sleutel], POORT[sleutel])))
    out.append(("MemAvailable ≥ 1,5 GiB", boven(stap2["mem_min_bytes"], POORT["mem_min_bytes"])))
    ok, waarom = ds_oordeel(stap2.get("ds"))
    out.append((f"geen container OOMKilled (DS: {waarom})", ok))
    procs = stap2.get("processen") or {}
    ok, waarom = overleefd(procs.get("voor"), procs.get("na"))
    out.append((f"de app en de worker leven nog, geen crash of OOM-kill ({waarom})", ok))
    regel = f"de index leest weer ≤ {POORT['hervat_s']:g} s na de laatste transcode"
    if stap2["hervat_s"] is None:
        regel += " (er kwam binnen de tijd geen rij van text of ocr klaar)"
    out.append((regel, onder(stap2["hervat_s"], POORT["hervat_s"])))
    office, basis = stap2.get("office"), nul.get("office")
    # Een ronde die niet volledig liep (onvolledig()), telt niet: in stap 2 is dat niet gehaald,
    # in de nulmeting is er dan niets om mee te vergelijken
    mis2, mis1 = onvolledig(office), onvolledig(basis)
    regel = "OnlyOffice: opslaan ≤ 1,2 × de nulmeting"
    if office and basis and "opslaan_s" in office and "opslaan_s" in basis:
        grens = POORT["opslaan_factor"] * max(basis["opslaan_s"], 0.1)
        if mis2:
            out.append((f"{regel} ({grens:.1f} s; stap 2: {mis2})", False))
        elif mis1:
            out.append((f"{regel} (de nulmeting: {mis1})", None))
        else:
            out.append((f"{regel} ({grens:.1f} s)", office["opslaan_s"] <= grens))
    else:
        waarom = "zonder OnlyOffice" if not office and not basis else "geen opslaantijd gemeten"
        out.append((f"{regel} ({waarom})", None))
    if office and "verloren" in office:
        if mis2:
            out.append((f"OnlyOffice: 0 verloren woorden (stap 2: {mis2})", False))
        else:
            out.append(("OnlyOffice: 0 verloren woorden", office["verloren"] == 0))
    else:
        waarom = "zonder OnlyOffice" if not office else office.get("fout", "niet gemeten")
        out.append((f"OnlyOffice: 0 verloren woorden ({waarom})", None))
    return out


def geslaagd(poorten: list[tuple[str, bool | None]]) -> bool:
    """Elke poort gehaald; een poort die niet te meten was (None) telt als niet gehaald."""
    return all(ok is True for _, ok in poorten)


def woord(ok: bool | None) -> str:
    return "niet te meten" if ok is None else ("gehaald" if ok else "niet gehaald")


def kort_blok(uitkomst: dict[str, Any]) -> list[str]:
    kop = f"zoek_samen ({uitkomst['soort']}): {uitkomst['opzet']}; {uitkomst['duur_s']} s in totaal"
    regels = ["=== kort ===", kop]
    regels += [f"niet in deze meting: {wat}" for wat in uitkomst.get("overgeslagen", [])]
    for s in uitkomst["stappen"]:
        # De fasen die de korte belastingtest niet draait, laten we weg: dat staat in de opzet
        media = "; ".join(r for r in s["media"]["samenvatting"] if "niet gedraaid" not in r)
        hervat = "-" if s["hervat_s"] is None else f"{s['hervat_s']} s"
        regels.append(
            f"{s['stap']}: zoeken p95 {s['zoeken_p95_ms']} ms ({s['zoeken_n']} vragen), "
            f"/files/root?limit={EERSTE_PAGINA} p95 {s['files_root_p95_ms']} ms, "
            f"/health p95 {s['health_p95_ms']} ms, "
            f"MemAvailable min {s['mem_min_mb']} MB, tekst klaar {s['tekst_klaar_voor']} → "
            f"{s['tekst_klaar_na_media']} (na media) → {s['tekst_klaar_na']}, hervat {hervat}; "
            f"media: {media}"
        )
        regels.append(f"{s['stap']}: {achtergrond_regel(s)}")
        if s.get("office"):
            regels.append(f"{s['stap']}: OnlyOffice {json.dumps(s['office'], ensure_ascii=False)}")
        if s["fouten"]:
            regels.append(f"{s['stap']}: mislukte verzoeken {s['fouten']}")
    for regel, ok in uitkomst["poorten"]:
        regels.append(f"poort (stap achtergrond): {regel}: {woord(ok)}")
    regels.append("uitkomst: " + ("rapport" if uitkomst["soort"] == "kort" else (
        "geslaagd" if uitkomst["geslaagd"] else "niet geslaagd")))  # fmt: skip
    return regels


def achtergrond_regel(s: dict[str, Any]) -> str:
    """OCR, photometa, de processen en DS van één stap, op één regel."""
    ocr = s.get("ocr") or {}
    delen = [
        f"ocr klaar {gelezen(ocr.get('voor') or {})} → {gelezen(ocr.get('na_media') or {})} "
        f"(na media) → {gelezen(ocr.get('na') or {})}, open "
        f"{klaar_en_open({'ocr': ocr.get('na') or {}})[1]}"
    ]
    fotos = s.get("fotos")
    if fotos:
        te_doen = [str((fotos.get(k) or {}).get("te_doen")) for k in ("voor", "na_media", "na")]
        begin = s.get("begin") or {}
        if begin.get("herbouw_hash"):
            # wat photometa direct na de herbouw te doen had, tussen voor en na media in
            te_doen.insert(1, f"{begin.get('fotos_te_doen', '?')} (herbouw hash "
                              f"{begin['herbouw_hash']})")  # fmt: skip
        te_doen[-2] += " (na media)"
        gehasht = (fotos.get("na") or {}).get("gehasht")
        delen.append(f"photometa te doen {' → '.join(te_doen)}"
                     + ("" if gehasht is None else f", gehasht {gehasht}"))  # fmt: skip
    procs = s.get("processen") or {}
    delen.append(f"processen: {overleefd(procs.get('voor'), procs.get('na'))[1]}")
    if s.get("cores") is not None:
        cores = s["cores"]
        delen.append(f"cores: worker {cores_tekst(cores.get('worker'))}, "
                     f"app {cores_tekst(cores.get('app'))}")  # fmt: skip
    if s.get("ds") is not None:
        delen.append(f"DS: {ds_oordeel(s['ds'])[1]}")
    return "; ".join(delen)


def kleiner_dan_45(args: argparse.Namespace) -> list[str]:
    """Zonder --kort: wat er minder is dan de last van §4.5 (bouwontwerp: 5.000 documenten,
    5.000 foto's en 500 scans voor X1), voor "niet in deze meting" in het blok kort en de JSON.
    De poorten zeggen daar niets over: met --docs, --fotos of --scans kleiner kan de uitkomst nog
    "geslaagd" zijn, maar dan niet voor de last van §4.5 (review van I1, regels F3)."""
    if args.kort:
        return []
    uit = []
    for wat, n, vol in (
        ("documenten per stap", args.docs, DOCS[1]),
        ("foto's", args.fotos, FOTOS[1]),
        ("scans voor X1", args.scans, SCANS[1]),
    ):
        if n < vol:
            uit.append(f"de last van §4.5: {n:,} {wat} in plaats van {vol:,}".replace(",", "."))
    return uit


def zaai_stap(
    data_dir: Path,
    admin: Account,
    docs: list[zb.Document],
    args: argparse.Namespace,
    opzet: Opzet,
    last: bool,
) -> None:
    """Wat seeded() schrijft voor stap 1 (last: ook de foto's en de scans) en voor stap 3: de
    documenten in de hoofdmap. Met --office zoals op een 0.7.x met OnlyOffice: elk document dat
    DS omzet, heeft zijn PDF al (zaai office_klaar); zonder dat zette DS in elke stap, ook de
    nulmeting, ±2.700 documenten om (docx, xlsx, pptx en rtf; I1, M7)."""
    eigenaar, root = admin.id, admin.root_id or ""
    zb.zaai(data_dir, eigenaar, root, docs, office_klaar=bool(args.office))
    if not last:
        return
    klok = time.monotonic()
    if args.fotos:
        teller = zf.zaai(data_dir, eigenaar, root, zf.fotos(args.fotos), zf.KLAAR)
        opzet.fotos = teller["fotos"]
        print(f"{teller['fotos']} foto's ({teller['met_gps']} met een plek) gemaakt "
              f"in {time.monotonic() - klok:.1f} s", flush=True)  # fmt: skip
    if args.scans:
        klok = time.monotonic()
        n = zf.zaai_documenten(data_dir, eigenaar, root, zf.scans(args.scans), "Scans")
        print(f"{n} scans gemaakt in {time.monotonic() - klok:.1f} s", flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--kort", action="store_true", help="60 s, 1 sessie, zonder OnlyOffice")
    parser.add_argument("--office", action="store_true", help="met de echte DS (E2E_REAL_DS)")
    parser.add_argument("--duur", type=int, help="seconden per fase van de belastingtest")
    parser.add_argument("--sessies", type=int)
    parser.add_argument("--docs", type=int, help="documenten in de rij index, per stap")
    parser.add_argument("--fotos", type=int, help="foto's (standaard 300 met --kort, anders 5.000)")
    parser.add_argument(
        "--scans", type=int, help="scans voor X1 (standaard 20 met --kort, anders 500)"
    )
    parser.add_argument("--bewerkers", type=int, default=5)
    parser.add_argument("--json", type=Path, help="de uitkomst ook naar dit bestand")
    parser.add_argument("--kort-uit", type=Path, help="het blok === kort === ook naar dit bestand")
    args = parser.parse_args(argv)
    if args.kort and args.office:
        parser.error("--kort is zonder OnlyOffice")
    args.duur = args.duur or (60 if args.kort else 300)
    args.sessies = args.sessies or (1 if args.kort else 3)
    args.docs = args.docs or DOCS[0 if args.kort else 1]
    args.fotos = FOTOS[0 if args.kort else 1] if args.fotos is None else args.fotos
    args.scans = SCANS[0 if args.kort else 1] if args.scans is None else args.scans
    if not zb.media_beschikbaar():
        raise SystemExit("zoek_samen heeft ffmpeg en ffprobe nodig (de belastingtest van media)")
    overgeslagen = kleiner_dan_45(args)
    gedeeld = None if args.kort else deelt_de_cores_van_de_worker()
    if gedeeld:
        overgeslagen.append(gedeeld)
    for wat in overgeslagen:
        print(f"zoek_samen: niet in deze meting: {wat}", flush=True)
    reden = zf.scan_reden() if args.scans else None
    if reden and not args.kort:
        raise SystemExit(f"zoek_samen zonder --kort heeft de {args.scans} scans van X1 nodig: "
                         f"{reden} (of --scans 0, dan is het niet de last van §4.5)")  # fmt: skip
    if reden:
        overgeslagen.append(f"de {args.scans} scans voor X1 ({reden})")
        print(f"zoek_samen: de scans voor X1 overgeslagen: {reden}", flush=True)
        args.scans = 0

    soort = "kort" if args.kort else ("office" if args.office else "zonder office")
    opzet_tekst = f"{args.sessies} sessie(s) media, {args.duur} s per fase, {args.docs} documenten"
    opzet_tekst += " per stap" + (f", {args.bewerkers} bewerkers" if args.office else "")
    opzet_tekst += f", {args.fotos} foto's, {args.scans} scans"
    # De belastingtest in de app draait fase a en b (korter dan 120 s alleen b); fase c alleen met
    # spelers buiten de app, fase d met de hand (runbook media.md). kort_blok laat die twee weg.
    opzet_tekst += "; media fase b" if args.duur < 120 else "; media fase a en b"
    print(f"zoek_samen: {opzet_tekst}", flush=True)
    begonnen = klok = time.monotonic()
    # één reeks van 2n (elk document uniek, ook tussen de stappen), in twee helften
    alle = zb.documenten(2 * args.docs, zaad=61)
    eerste, tweede = alle[: args.docs], alle[args.docs :]
    woorden = [f"backfillwoord{i}" for i in range(2 * args.docs)]
    print(f"documenten gemaakt in {time.monotonic() - klok:.1f} s", flush=True)

    base = Path(tempfile.mkdtemp(prefix="zoek-samen-"))
    werk = base / "uit"
    werk.mkdir()
    env = {**media_env(None), "PLATFORM_MEDIA_LOADTEST": "1", "PLATFORM_SEARCH_INDEX": "aan"}
    opzet: Opzet | None = None
    stappen = []
    try:
        opzet = start(base, env, args.office)
        server, admin = opzet.server, opzet.admin
        if opzet.office is not None:
            opzet.ds_host = docker_grenzen()
            ds = ds_toestand(opzet.ds_name)
            print(f"zoek_samen: DS {opzet.ds_name}: {ds}", flush=True)
            if ds is None or ds["geheugen"] <= 0:
                # Zonder grens zegt OOMKilled niets (review van het plan van I1)
                raise SystemExit("zoek_samen --office: " + (
                    "docker inspect van DS gaf niets" if ds is None else zonder_grens(
                        opzet.ds_host)))  # fmt: skip

        def met(
            docs: list[zb.Document], extra: dict[str, str], last: bool = False
        ) -> Callable[[Path], None]:
            def zaai(data_dir: Path) -> None:
                server.extra_env.update(extra)
                zaai_stap(data_dir, admin, docs, args, opzet, last)

            return zaai

        eraf = functools.partial(pauze_eraf, opzet)
        # 1. pauze: de rij index vol, maar stil (de pauze staat in Setting search, dus ook na
        # de herstart van seeded; de backfill bij het starten zet de rijen klaar). De foto's
        # staan er als gelezen: photometa heeft niets te doen tot de herbouw van stap 2
        pauze(server, admin, True)
        server.seeded(met(eerste, {}, last=True))
        stappen.append(stap(opzet, "pauze", args, woorden, werk, 1))
        # 2. achtergrond: dezelfde rij, de pauze eraf zodra media loopt; Defer zoals standaard
        stappen.append(stap(opzet, "achtergrond", args, woorden, werk, 2, begin=eraf))
        # 3. zonder Defer: nieuwe documenten, de index wijkt niet meer voor omzettingen
        pauze(server, admin, True)
        server.seeded(met(tweede, {"PLATFORM_INDEX_DEFER_TRANSCODES": ZONDER_DEFER}))
        stappen.append(stap(opzet, "zonder_defer", args, woorden, werk, 3, begin=eraf))
    finally:
        if opzet is not None:
            if opzet.office is not None:
                opzet.office.stop()
            else:
                opzet.server.stop()
        shutil.rmtree(base, ignore_errors=True)

    poorten = oordeel(stappen[1], stappen[0])
    gehaald = geslaagd(poorten)
    uitkomst = {
        "soort": soort,
        "opzet": opzet_tekst,
        "cores": os.cpu_count(),
        "poort": POORT,
        "overgeslagen": overgeslagen,
        "stappen": stappen,
        "poorten": poorten,
        "geslaagd": gehaald,
        "duur_s": round(time.monotonic() - begonnen),
    }
    out = os.environ.get("E2E_PERF_OUT")
    paden = [args.json] if args.json else []
    if out:
        paden.append(Path(out) / "zoek-samen.json")
    for pad in paden:
        pad.parent.mkdir(parents=True, exist_ok=True)
        pad.write_text(json.dumps(uitkomst, indent=2, ensure_ascii=False) + "\n")
    blok = kort_blok(uitkomst)
    print("\n".join(blok), flush=True)
    if args.kort_uit:
        args.kort_uit.parent.mkdir(parents=True, exist_ok=True)
        args.kort_uit.write_text("\n".join(blok) + "\n")
    return 0 if args.kort or gehaald else 1


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        sys.exit(main())
