#!/usr/bin/env python3
"""Zoeken over HTTP op de boom van 100.000 (bouwontwerp fase 6, §4.4): de client-kant.

Alleen de standaardbibliotheek: draait met elke python3, in een ander proces dan de app (de
poort van §4.4 meet bij de client). api/tests/search/test_perf_100k.py maakt de boom, start de
app (python -m app.serve, PLATFORM_SEARCH_TIMING=1) en roept dit script aan; met de hand kan het
ook tegen een app die al draait:

    python3 e2e/perf/zoek_100k.py --url http://127.0.0.1:8080 \\
        --vragen api/tests/search/vragen.tsv --json uit.json

Per rol (--rollen, standaard de zes van §4.4): inloggen, 20 vragen om op te warmen, dan 3 rondes
van de 200 vragen van §4.3, één voor één, met per ronde ook volgende pagina's (offset 30–990),
alleen filters (Keuze 4a) en suggesties. Een p95 is de mediaan van de p95 van de 3 rondes. Uit
Server-Timing de stap snip; uit het antwoord hoe vaak ronde B (ranked false) of C (names_only)
nodig was. Daarna 4 clients tegelijk (elk een eigen proces, elk de vragenlijst) met /health
ernaast. --deel eerste meet alleen de eerste vraag (na een herstart).

Uitvoer: een blok === kort === en met --json alles als JSON. Geen vraag in de uitvoer: alleen
soorten en getallen.
"""

from __future__ import annotations

import argparse
import http.client
import json
import math
import random
import statistics
import subprocess
import sys
import threading
import time
import urllib.parse
from pathlib import Path

ROLLEN = {
    "eigenaar": "b00",
    "ontvanger5": "b01",
    "ontvanger20": "b02",
    "groepslid": "b03",
    "gast": "gast",
    "alles": "alles",
}
TEGELIJK = ("eigenaar", "ontvanger5", "ontvanger20", "groepslid")
DOMEIN = "bench.voorbeeld.nl"
WACHTWOORD = "bench-wachtwoord-lang-genoeg"
# Alleen filters, zonder vraag (Keuze 4a): soorten, datums en beide
FILTERS = [
    {"type": t}
    for t in ("image", "video", "audio", "pdf", "document", "spreadsheet", "presentation", "other")
] + [
    {"from": "2025-11-03"},
    {"to": "2017-12-31"},
    {"from": "2020-01-01", "to": "2020-12-31"},
    {"type": "image", "from": "2025-01-01"},
    {"type": "pdf", "to": "2019-06-30"},
    {"type": "document", "from": "2018-01-01", "to": "2018-03-31"},
    {"type": "image", "from": "2026-11-01"},
    {"type": "audio", "to": "2016-12-31"},
    {"type": "folder"},
    {"type": "image", "from": "2016-11-01", "to": "2016-11-30"},
    {"type": "spreadsheet", "from": "2024-01-01"},
    {"type": "presentation", "to": "2022-01-01"},
]


def pct(values: list[float], q: float) -> float:
    """Het q-percentiel (nearest rank: de waarde op plek ⌈q·n/100⌉); 0 bij geen waarden. Bij 20
    waarden is p95 de 19e, niet de grootste."""
    if not values:
        return 0.0
    s = sorted(values)
    return s[min(len(s) - 1, max(0, math.ceil(q * len(s) / 100) - 1))]


class Klant:
    """Eén HTTP-verbinding (keep-alive), ingelogd als één gebruiker."""

    def __init__(self, url: str) -> None:
        p = urllib.parse.urlsplit(url)
        self.host, self.port = p.hostname or "127.0.0.1", p.port or 80
        self.conn = http.client.HTTPConnection(self.host, self.port, timeout=60)
        self.kop: dict[str, str] = {}

    def vraag(self, methode: str, pad: str, body: dict | None = None):
        data = json.dumps(body).encode() if body is not None else None
        kop = dict(self.kop)
        if data is not None:
            kop["Content-Type"] = "application/json"
        for poging in range(2):
            try:
                begin = time.perf_counter()
                self.conn.request(methode, pad, body=data, headers=kop)
                r = self.conn.getresponse()
                inhoud = r.read()
                ms = (time.perf_counter() - begin) * 1000
                return r.status, r.getheader("Server-Timing") or "", inhoud, ms
            except (ConnectionError, http.client.HTTPException):
                self.conn.close()
                self.conn = http.client.HTTPConnection(self.host, self.port, timeout=60)
                if poging:
                    raise
        raise RuntimeError("onbereikbaar")

    def login(self, email: str, wachtwoord: str) -> None:
        status, _, inhoud, _ = self.vraag(
            "POST", "/api/v1/auth/login", {"email": email, "password": wachtwoord}
        )
        if status != 200:
            raise SystemExit(f"inloggen lukte niet ({status})")
        self.kop = {"Authorization": f"Bearer {json.loads(inhoud)['access_token']}"}

    def get(self, pad: str, params: dict):
        return self.vraag("GET", f"{pad}?{urllib.parse.urlencode(params)}")


def _timing(kop: str) -> dict[str, float]:
    uit = {}
    for deel in kop.split(","):
        naam, _, rest = deel.strip().partition(";dur=")
        if naam and rest:
            uit[naam] = float(rest)
    return uit


def lees_vragen(pad: Path) -> list[tuple[str, str]]:
    regels = pad.read_text(encoding="utf-8").splitlines()
    return [tuple(r.split("\t", 1)) for r in regels if r and not r.startswith("#")]


def zoek(k: Klant, params: dict, maat: dict) -> None:
    status, timing, inhoud, ms = k.get("/api/v1/search", params)
    maat["ms"].append(ms)
    if status != 200:
        maat["fout"][str(status)] = maat["fout"].get(str(status), 0) + 1
        return
    snip = _timing(timing).get("snip")
    if snip is not None:
        maat["snip"].append(snip)
    page = json.loads(inhoud)
    if page.get("names_only"):
        maat["C"] += 1
    elif page.get("partial") and not page.get("ranked"):
        maat["B"] += 1


def _leeg() -> dict:
    return {"ms": [], "snip": [], "B": 0, "C": 0, "fout": {}}


def rol(url: str, naam: str, email: str, wachtwoord: str, vragen, rondes: int, warm: int) -> dict:
    k = Klant(url)
    k.login(email, wachtwoord)
    for _, v in vragen[:warm]:
        zoek(k, {"q": v}, _leeg())
    rng = random.Random(f"volgende:{naam}")
    uit: dict[str, list] = {s: [] for s in ("zoeken", "volgende", "filters", "suggesties")}
    for _ in range(rondes):
        z, n, f, s = _leeg(), _leeg(), _leeg(), _leeg()
        for _, v in vragen:
            zoek(k, {"q": v}, z)
        for _, v in vragen[::4]:
            zoek(k, {"q": v, "offset": 30 * rng.randint(1, 33)}, n)
        for params in FILTERS:
            zoek(k, params, f)
        for _, v in vragen:
            status, _, _, ms = k.get("/api/v1/search/suggest", {"q": v})
            s["ms"].append(ms)
            if status != 200:
                s["fout"][str(status)] = s["fout"].get(str(status), 0) + 1
        for soort, m in (("zoeken", z), ("volgende", n), ("filters", f), ("suggesties", s)):
            uit[soort].append(m)
    return {soort: samenvatting(rondes_) for soort, rondes_ in uit.items()}


def samenvatting(rondes: list[dict]) -> dict:
    """p95 en p99: de mediaan over de rondes; max over alles; B, C en fouten opgeteld."""
    fout: dict[str, int] = {}
    for r in rondes:
        for k, v in r["fout"].items():
            fout[k] = fout.get(k, 0) + v
    alle = [ms for r in rondes for ms in r["ms"]]
    return {
        "n": len(alle),
        "p50": statistics.median(alle) if alle else 0.0,
        "p95": statistics.median(pct(r["ms"], 95) for r in rondes),
        "p99": statistics.median(pct(r["ms"], 99) for r in rondes),
        "max": max(alle, default=0.0),
        "p95_rondes": [pct(r["ms"], 95) for r in rondes],
        "snip_p95": statistics.median(pct(r["snip"], 95) for r in rondes),
        "snip_max": max((s for r in rondes for s in r["snip"]), default=0.0),
        "B": sum(r["B"] for r in rondes),
        "C": sum(r["C"] for r in rondes),
        "fout": fout,
    }


def klant(args) -> int:
    """Eén client van de 4 tegelijk: de vragenlijst één keer; de tijden als JSON op stdout."""
    vragen = lees_vragen(args.vragen)
    k = Klant(args.url)
    k.login(f"{ROLLEN[args.rol]}@{args.domein}", args.wachtwoord)
    m = _leeg()
    print("klaar", flush=True)
    sys.stdin.readline()  # wacht tot alle 4 ingelogd zijn
    for _, v in vragen:
        zoek(k, {"q": v}, m)
    print(json.dumps(m), flush=True)
    return 0


def tegelijk(args) -> dict:
    procs = [
        subprocess.Popen(
            [
                sys.executable,
                "-I",
                __file__,
                "--deel",
                "klant",
                "--rol",
                r,
                "--url",
                args.url,
                "--vragen",
                str(args.vragen),
                "--domein",
                args.domein,
                "--wachtwoord",
                args.wachtwoord,
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
        )  # fmt: skip
        for r in TEGELIJK
    ]
    for p in procs:
        assert p.stdout is not None and p.stdout.readline().strip() == "klaar"
    health: list[float] = []
    stop = threading.Event()

    def kijk() -> None:
        k = Klant(args.url)
        while not stop.is_set():
            _, _, _, ms = k.vraag("GET", "/health")
            health.append(ms)
            time.sleep(0.05)

    t = threading.Thread(target=kijk, daemon=True)
    t.start()
    for p in procs:
        assert p.stdin is not None
        p.stdin.write("start\n")
        p.stdin.flush()
    metingen = []
    for p in procs:
        uit, _ = p.communicate(timeout=1800)
        metingen.append(json.loads(uit.strip().splitlines()[-1]))
    stop.set()
    t.join(timeout=5)
    alle = [ms for m in metingen for ms in m["ms"]]
    fout: dict[str, int] = {}
    for m in metingen:
        for k, v in m["fout"].items():
            fout[k] = fout.get(k, 0) + v
    return {
        "n": len(alle),
        "p50": statistics.median(alle),
        "p95": pct(alle, 95),
        "p99": pct(alle, 99),
        "max": max(alle),
        "fout": fout,
        "health_n": len(health),
        "health_p95": pct(health, 95),
        "health_max": max(health, default=0.0),
    }


def eerste(args) -> dict:
    vragen = lees_vragen(args.vragen)
    k = Klant(args.url)
    k.login(f"{ROLLEN['eigenaar']}@{args.domein}", args.wachtwoord)
    m = _leeg()
    zoek(k, {"q": vragen[0][1]}, m)
    return {"ms": m["ms"][0], "fout": m["fout"]}


def kort(uit: dict) -> str:
    regels = ["=== kort ===", "zoek_100k: p95 (mediaan van 3 rondes), p99, max in ms"]
    for naam, r in uit.get("rollen", {}).items():
        z, v, f, s = r["zoeken"], r["volgende"], r["filters"], r["suggesties"]
        regels.append(
            f"{naam}: zoeken p95 {z['p95']:.1f} p99 {z['p99']:.1f} max {z['max']:.1f} "
            f"(B {z['B']}, C {z['C']}); volgende p95 {v['p95']:.1f}; filters p95 "
            f"{f['p95']:.1f}; suggesties p95 {s['p95']:.1f}; snip p95 {z['snip_p95']:.1f} "
            f"max {z['snip_max']:.1f}"
        )
    if "tegelijk" in uit:
        t = uit["tegelijk"]
        regels.append(
            f"4 tegelijk: p95 {t['p95']:.1f} p99 {t['p99']:.1f} max {t['max']:.1f}; "
            f"/health p95 {t['health_p95']:.1f} max {t['health_max']:.1f} (n {t['health_n']})"
        )
    if "eerste" in uit:
        regels.append(f"eerste vraag: {uit['eerste']['ms']:.1f} ms")
    return "\n".join(regels)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", required=True)
    parser.add_argument("--vragen", type=Path, required=True, help="tests/search/vragen.tsv")
    parser.add_argument("--domein", default=DOMEIN)
    parser.add_argument("--wachtwoord", default=WACHTWOORD)
    parser.add_argument("--rollen", default=",".join(ROLLEN), help="komma's; standaard alle zes")
    parser.add_argument("--rondes", type=int, default=3)
    parser.add_argument("--opwarmen", type=int, default=20)
    parser.add_argument("--deel", choices=("alles", "eerste", "klant"), default="alles")
    parser.add_argument("--rol", choices=tuple(ROLLEN), help="alleen met --deel klant")
    parser.add_argument("--json", type=Path)
    args = parser.parse_args(argv)
    if args.deel == "klant":
        return klant(args)
    uit: dict = {}
    if args.deel == "eerste":
        uit["eerste"] = eerste(args)
    else:
        vragen = lees_vragen(args.vragen)
        uit["vragen"] = len(vragen)
        uit["rollen"] = {}
        for naam in args.rollen.split(","):
            email = f"{ROLLEN[naam]}@{args.domein}"
            begin = time.monotonic()
            uit["rollen"][naam] = rol(
                args.url, naam, email, args.wachtwoord, vragen, args.rondes, args.opwarmen
            )
            uit["rollen"][naam]["seconden"] = time.monotonic() - begin
            print(f"{naam}: {uit['rollen'][naam]['seconden']:.0f} s", file=sys.stderr, flush=True)
        uit["tegelijk"] = tegelijk(args)
    if args.json:
        args.json.write_text(json.dumps(uit, indent=2) + "\n")
    print(kort(uit))
    return 0


if __name__ == "__main__":
    sys.exit(main())
