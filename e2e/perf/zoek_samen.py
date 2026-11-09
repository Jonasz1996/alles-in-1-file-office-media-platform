"""Zoeken naast media en OnlyOffice (fase 6, Z7; bouwontwerp §4.5, deel B): de tijden, de CPU en
het geheugen in drie stappen, elk zolang de belastingtest van media loopt.

Stappen
1. pauze: het achtergrondwerk staat op pauze (PATCH /admin/search, paused), met een volle rij
   index: n documenten zonder tekst, zoals vóór Z2 (zoek_backfill.zaai; de backfill bij het
   starten zet ze klaar). De nulmeting.
2. achtergrond: dezelfde rij; de pauze gaat eraf zodra de belastingtest loopt (GET
   /admin/media/loadtest zegt running), zodat de index geen voorsprong krijgt. Defer staat aan,
   zoals standaard (index_defer_transcodes 2): de index wijkt voor omzettingen, en tijdens de
   belastingtest altijd (die zet media.analysis.busy_live op minstens 2).
3. zonder_defer: hetzelfde met n nieuwe documenten (weer op pauze tot de belastingtest loopt), en
   Defer uit (PLATFORM_INDEX_DEFER_TRANSCODES=1000). Dan beschermen alleen nice en de idle
   IO-klasse. Een rapport.

Tijdens elke stap
- media: `python -m app.cli media-loadtest --sessies S --duur D` tegen de draaiende app (de
  belastingtest van fase 5, in de app: fase a gespreid, fase b tegelijk; korter dan 120 s
  alleen fase b, op 720p en de laagste trede);
- zoeken: 2 vragen per seconde (GET /search, een woord uit de documenten), de p95;
- de app: GET /files/root en GET /health elke seconde, de p95;
- geheugen: MemAvailable van de machine, elke seconde, het minimum;
- achtergrondwerk: de rijen van de stap text die klaar zijn, aan het begin en het eind, en na de
  belastingtest hoe lang het duurt tot de index weer leest (poort ≤ 90 s);
- met --office (de echte DS, E2E_REAL_DS en E2E_REAL_DS_SECRET_FILE; docs/fase-4/e2e.md): B
  bewerkers in OnlyOffice die D seconden typen en dan sluiten (office_editors.one_round, met
  de CallbackClock): de tijd van sluiten tot opgeslagen, en de woorden die niet in het bestand
  kwamen.

Poorten (stap 2; stap 3 is een rapport): elke start van media ≤ 5 s en 0 keer haperen (de
poorten van de belastingtest zelf); zoeken p95 ≤ 200 ms; GET /files/root p95 ≤ 200 ms; GET /health
p95 ≤ 50 ms; MemAvailable ≥ 1,5 GiB; de index leest weer binnen 90 s na de laatste transcode; met
--office het opslaan ≤ 1,2 × de nulmeting en 0 verloren woorden.

    cd api && uv run python ../e2e/perf/zoek_samen.py --kort
    cd api && E2E_REAL_DS=http://127.0.0.1:18150 E2E_REAL_DS_SECRET_FILE=<map>/jwt.secret \\
      uv run --with playwright python ../e2e/perf/zoek_samen.py --office

--kort (CI, job e2e-media-perf, rapport): 60 s per fase van de belastingtest, 1 sessie, zonder
OnlyOffice, 300 documenten per stap. Zonder --kort: 300 s, 3 sessies, 5.000 documenten; met
--office ook 5 bewerkers (docs/runbooks/zoeken.md). Nog niet in deze meting: foto's voor
photometa, scans voor OCR (X1) en de container analyse (X2, X3); zie docs/fase-6/z7.md, Open.

Aan het eind "=== kort ===" (ook in --kort-uit, voor het einde van het log in CI). In --kort is de
uitkomst altijd 0 (een rapport); zonder --kort 1 als een poort van stap 2 niet gehaald is.
"""

from __future__ import annotations

import argparse
import contextlib
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

from conftest import API_DIR, Account, Server, make_admin, percentile  # noqa: E402
from media_harness import media_env  # noqa: E402

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


def zoekers(server: Server, token: str, woorden: list[str], meter: Meter) -> list[threading.Thread]:
    """2 zoekvragen per seconde; elke seconde /files/root en /health; elke seconde het geheugen."""
    headers = {"Authorization": f"Bearer {token}"}
    rng = random.Random(7)

    def zoek() -> None:
        with httpx.Client(base_url=server.url, trust_env=False, timeout=30) as http:
            while not meter.stop.wait(0.5):
                q = rng.choice(woorden)
                meter.tel("zoeken", _get(http, f"{API}/search", params={"q": q}, headers=headers))

    def app() -> None:
        with httpx.Client(base_url=server.url, trust_env=False, timeout=30) as http:
            while not meter.stop.wait(1.0):
                meter.tel("files_root", _get(http, f"{API}/files/root", headers=headers))
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


def tekst_stand(server: Server) -> dict[str, int]:
    return dict(zb.stand(server.data_dir))


def klaar(stand: dict[str, int]) -> int:
    return sum(stand.get(s, 0) for s in zb.KLAAR)


def wacht_tot_de_index_leest(server: Server, vanaf: int) -> float | None:
    """Na de belastingtest: hoe lang tot er meer rijen van de stap text klaar zijn dan vanaf.
    None als er niets meer te lezen was (de rij al leeg) of het niet binnen de tijd gebeurde."""
    begin = time.monotonic()
    while time.monotonic() - begin < HERVAT_WACHT_S:
        stand = tekst_stand(server)
        open_ = stand.get("wait", 0) + stand.get("busy", 0)
        if klaar(stand) > vanaf:
            return round(time.monotonic() - begin, 1)
        if open_ == 0:
            return None
        time.sleep(0.5)
    return None


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


def bewerkers(opzet: Opzet, n: int, seconden: float, ronde: int) -> dict[str, Any]:
    """B bewerkers (5 documenten met elk n // 5, minstens 1) die typen en sluiten, zoals
    office_editors.one_round; zonder het wachten op een stille machine, want hier loopt de rest
    juist."""
    import office_bench as ob
    import office_editors as oe

    docs = min(5, n)
    per_doc = max(1, n // docs)
    if opzet.squad is None:
        opzet.squad = ob.team(opzet.office, {}, members=docs * per_doc - 1)
    stil = ob.wait_quiet
    ob.wait_quiet = lambda **_kw: {"quiet": False}  # type: ignore[assignment]
    try:
        r = oe.one_round(opzet.office, opzet.squad, docs, per_doc, seconden, ronde,
                         opzet.ds_name or "", opzet.url or "")  # fmt: skip
    finally:
        ob.wait_quiet = stil  # type: ignore[assignment]
    callbacks = [s for p in r["per_doc_results"] for s in p["callback_s"]]
    return {
        "bewerkers": r["editors"],
        "open": r["opened"],
        "opslaan_s": r["save_s"],
        "callback_p95_s": percentile(callbacks, 95),
        "getypt": r["typed"],
        "opgeslagen": r["saved"],
        "verloren": r["lost_saving"] + (r["typed"] - r["saved"]),
    }


def stap(
    opzet: Opzet,
    naam: str,
    args: argparse.Namespace,
    woorden: list[str],
    werk: Path,
    ronde: int,
    begin: Callable[[], None] | None = None,
) -> dict[str, Any]:
    """Eén stap: de belastingtest, met daarnaast de zoekers (en met --office de bewerkers). begin
    (de pauze eraf) gaat pas als de belastingtest loopt."""
    server, admin = opzet.server, opzet.admin
    meter = Meter()
    token = server.token(admin)
    voor = tekst_stand(server)
    print(f"[{naam}] begin: stap text {voor}", flush=True)
    draden = zoekers(server, token, woorden, meter)
    office: dict[str, Any] | None = None
    office_draad = None
    if opzet.office is not None:
        uitkomst: dict[str, Any] = {}

        def typ() -> None:
            uitkomst.update(bewerkers(opzet, args.bewerkers, float(args.duur), ronde))

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
    begonnen = None
    if begin is not None:
        begonnen = wacht_op_de_belastingtest(server, admin, media_klaar)
        begin()
    media_draad.join()
    media_s = round(time.monotonic() - klok, 1)
    na_media = klaar(tekst_stand(server))
    meter.stop.set()
    for d in draden:
        d.join(timeout=35)
    if office_draad is not None:
        office_draad.join(timeout=60 * 15)
        office = uitkomst or {"fout": "de bewerkers kwamen niet terug"}
    hervat = wacht_tot_de_index_leest(server, na_media) if naam != "pauze" else None
    na = tekst_stand(server)
    uit = {
        "stap": naam,
        "media_s": media_s,
        "pauze_eraf_tijdens_media": begonnen,
        "media": {
            "passed": media.get("passed"),
            "samenvatting": media.get("summary") or [media.get("fout", "")],
            "api_p95_ms": media.get("api_p95_ms"),
        },
        "zoeken_p95_ms": meter.p95("zoeken"),
        "zoeken_n": len(meter.ms.get("zoeken", [])),
        "files_root_p95_ms": meter.p95("files_root"),
        "health_p95_ms": meter.p95("health"),
        "fouten": meter.fouten,
        "mem_min_mb": None if meter.mem_min is None else meter.mem_min // 2**20,
        "mem_min_bytes": meter.mem_min,
        "tekst_klaar_voor": klaar(voor),
        "tekst_klaar_na_media": na_media,
        "tekst_klaar_na": klaar(na),
        "hervat_s": hervat,
        "office": office,
    }
    print(f"[{naam}] {json.dumps(uit, ensure_ascii=False)}", flush=True)
    return uit


def oordeel(stap2: dict[str, Any], nul: dict[str, Any]) -> list[tuple[str, bool | None]]:
    """De poorten van stap 2: (regel, gehaald); None als er niets te meten was."""

    def onder(waarde: float | None, grens: float) -> bool | None:
        return None if waarde is None else waarde <= grens

    def boven(waarde: float | None, grens: float) -> bool | None:
        return None if waarde is None else waarde >= grens

    out: list[tuple[str, bool | None]] = [
        ("media: elke start ≤ 5 s, 0 keer haperen", stap2["media"]["passed"]),
    ]
    for sleutel, regel in (
        ("zoeken_p95_ms", "zoeken p95 ≤ {:g} ms"),
        ("files_root_p95_ms", "GET /files/root p95 ≤ {:g} ms"),
        ("health_p95_ms", "GET /health p95 ≤ {:g} ms"),
    ):
        out.append((regel.format(POORT[sleutel]), onder(stap2[sleutel], POORT[sleutel])))
    out.append(("MemAvailable ≥ 1,5 GiB", boven(stap2["mem_min_bytes"], POORT["mem_min_bytes"])))
    regel = f"de index leest weer ≤ {POORT['hervat_s']:g} s na de laatste transcode"
    out.append((regel, onder(stap2["hervat_s"], POORT["hervat_s"])))
    office, basis = stap2.get("office"), nul.get("office")
    if office and basis and "opslaan_s" in office and "opslaan_s" in basis:
        grens = POORT["opslaan_factor"] * max(basis["opslaan_s"], 0.1)
        out.append((f"OnlyOffice: opslaan ≤ 1,2 × de nulmeting ({grens:.1f} s)",
                    office["opslaan_s"] <= grens))  # fmt: skip
        out.append(("OnlyOffice: 0 verloren woorden", office["verloren"] == 0))
    return out


def kort_blok(uitkomst: dict[str, Any]) -> list[str]:
    kop = f"zoek_samen ({uitkomst['soort']}): {uitkomst['opzet']}; {uitkomst['duur_s']} s in totaal"
    regels = ["=== kort ===", kop]
    for s in uitkomst["stappen"]:
        # De fasen die de korte belastingtest niet draait, laten we weg: dat staat in de opzet
        media = "; ".join(r for r in s["media"]["samenvatting"] if "niet gedraaid" not in r)
        hervat = "-" if s["hervat_s"] is None else f"{s['hervat_s']} s"
        regels.append(
            f"{s['stap']}: zoeken p95 {s['zoeken_p95_ms']} ms ({s['zoeken_n']} vragen), "
            f"/files/root p95 {s['files_root_p95_ms']} ms, /health p95 {s['health_p95_ms']} ms, "
            f"MemAvailable min {s['mem_min_mb']} MB, tekst klaar {s['tekst_klaar_voor']} → "
            f"{s['tekst_klaar_na_media']} (na media) → {s['tekst_klaar_na']}, hervat {hervat}; "
            f"media: {media}"
        )
        if s.get("office"):
            regels.append(f"{s['stap']}: OnlyOffice {json.dumps(s['office'], ensure_ascii=False)}")
        if s["fouten"]:
            regels.append(f"{s['stap']}: mislukte verzoeken {s['fouten']}")
    for regel, ok in uitkomst["poorten"]:
        woord = "niet te meten" if ok is None else ("gehaald" if ok else "NIET gehaald")
        regels.append(f"poort (stap achtergrond): {regel}: {woord}")
    regels.append("uitkomst: " + ("rapport" if uitkomst["soort"] == "kort" else (
        "geslaagd" if uitkomst["geslaagd"] else "niet geslaagd")))  # fmt: skip
    return regels


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--kort", action="store_true", help="60 s, 1 sessie, zonder OnlyOffice")
    parser.add_argument("--office", action="store_true", help="met de echte DS (E2E_REAL_DS)")
    parser.add_argument("--duur", type=int, help="seconden per fase van de belastingtest")
    parser.add_argument("--sessies", type=int)
    parser.add_argument("--docs", type=int, help="documenten in de rij index, per stap")
    parser.add_argument("--bewerkers", type=int, default=5)
    parser.add_argument("--json", type=Path, help="de uitkomst ook naar dit bestand")
    parser.add_argument("--kort-uit", type=Path, help="het blok === kort === ook naar dit bestand")
    args = parser.parse_args(argv)
    if args.kort and args.office:
        parser.error("--kort is zonder OnlyOffice")
    args.duur = args.duur or (60 if args.kort else 300)
    args.sessies = args.sessies or (1 if args.kort else 3)
    args.docs = args.docs or (300 if args.kort else 5_000)
    if not zb.media_beschikbaar():
        raise SystemExit("zoek_samen heeft ffmpeg en ffprobe nodig (de belastingtest van media)")

    soort = "kort" if args.kort else ("office" if args.office else "zonder office")
    opzet_tekst = f"{args.sessies} sessie(s) media, {args.duur} s per fase, {args.docs} documenten"
    opzet_tekst += " per stap" + (f", {args.bewerkers} bewerkers" if args.office else "")
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

        def met(docs: list[zb.Document], extra: dict[str, str]) -> Callable[[Path], None]:
            def zaai(data_dir: Path) -> None:
                server.extra_env.update(extra)
                zb.zaai(data_dir, admin.id, admin.root_id or "", docs)

            return zaai

        def eraf() -> None:
            pauze(server, admin, False)

        # 1. pauze: de rij index vol, maar stil (de pauze staat in Setting search, dus ook na
        # de herstart van seeded; de backfill bij het starten zet de rijen klaar)
        pauze(server, admin, True)
        server.seeded(met(eerste, {}))
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
    geslaagd = all(ok is not False for _, ok in poorten)
    uitkomst = {
        "soort": soort,
        "opzet": opzet_tekst,
        "cores": os.cpu_count(),
        "poort": POORT,
        "stappen": stappen,
        "poorten": poorten,
        "geslaagd": geslaagd,
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
    return 0 if args.kort or geslaagd else 1


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        sys.exit(main())
