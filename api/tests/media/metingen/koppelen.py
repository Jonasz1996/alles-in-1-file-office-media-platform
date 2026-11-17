"""Keuze 42 op echte namen (integratie punt 9; m8.md open punt 8).

Een corpus van bestandsnamen zoals ze in een bibliotheek staan (koppelen_namen.tsv: scene-namen,
jaren, voorvoegsels als "Night Harbor" naast "Night Harbor Blues", vervolgen, remakes,
buitenlandse titels, getallen, films die niet bij TMDB staan, en afleveringen) met het goede
TMDB-id in de nepdata (koppelen_tmdb.json). Elke naam gaat zoals de scanner hem leest door
names.parse en dan door lookup.identify, met de bibliotheek in nl-NL, tegen de nep-TMDB in het
proces (dezelfde http-client als in de app). Per naam: automatisch goed, automatisch fout, of
Nakijken (en of de goede bij de 5 kandidaten staat). Afleveringen koppelen op de serie (Keuze
42): één beslissing per serie, die telt voor elke aflevering.

Twee manieren van zoeken:
- `nep`: de nep-TMDB zoals hij is: een titel komt terug als elk woord van de vraag erin staat;
- `ruim`: een bovengrens voor wat de echte TMDB teruggeeft (die zoekt ruimer, ook op een deel
  van een woord): elke film of serie met een titel die ≥ 0,5 lijkt op de vraag, met hetzelfde
  filter op het jaar. Zo komen ook "The Hunters" bij "The Hunt" en "Highway 99" bij
  "Highway 9" in de lijst.

Naast de regel zoals hij is (`zoals`) rekent het script twee voorstellen door, zonder de app te
veranderen (de keuze is aan Jonas):
- `breed`: altijd ook zonder jaar en ook in en-US zoeken, en alles samen rangschikken (nu stopt
  lookup.identify na een zekere keuze met jaar of in de taal van de bibliotheek);
- `tekst`: automatisch alleen als de titel zelf ≥ 0,9 lijkt (het jaar en de populariteit niet
  meegeteld); anders Nakijken. Een id uit de naam blijft zeker;
- `breed+tekst`: beide.

    cd api && uv run python -m tests.media.metingen.koppelen --uit /tmp/koppelen.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import tempfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.config import Settings
from app.media import lookup, names
from app.media.providers import http, match, secrets, tmdb
from app.media.providers.base import Candidate
from tests.media.fake_providers import FakeConfig, create_app
from tests.media.fake_providers import kit as fake_kit
from tests.media.fake_providers import tmdb as fake_tmdb
from tests.media.fake_providers.data import Data

HERE = Path(__file__).parent
CORPUS = HERE / "koppelen_namen.tsv"
TMDB_DATA = HERE / "koppelen_tmdb.json"
LANG = "nl-NL"
LOOSE = 0.5
TEXT_MIN = 0.9
RULES = ("zoals", "breed", "tekst", "breed+tekst")


@dataclass
class Row:
    group: str
    kind: str  # film of serie
    expected: int | None
    path: str


def corpus(path: Path = CORPUS) -> list[Row]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        group, kind, expected, name = line.split("\t")
        rows.append(Row(group, kind, None if expected == "-" else int(expected), name))
    return rows


class Loose(tmdb.Tmdb):
    """TMDB met ruimer zoeken (zie boven); details en find gaan naar de nep."""

    def __init__(self, settings: Settings, key: str, data: Data) -> None:
        super().__init__(settings, key)
        self.data = data

    async def search(self, kind: str, title: str, year: int | None, lang: str) -> list[Candidate]:
        movie = kind == "movie"
        items = self.data.movies if movie else self.data.shows
        keys = ("title", "original_title", "release_date") if movie else (
            "name", "original_name", "first_air_date"
        )  # fmt: skip
        found = []
        for item in items.values():
            if year is not None and fake_tmdb.year(item.get(keys[2])) != year:
                continue
            best = max(match.similarity(title, n) for n in fake_tmdb.names(item, *keys[:2]))
            if best >= LOOSE:
                found.append((-float(item.get("popularity", 1.0)), item["id"], item))
        found.sort(key=lambda t: t[:2])
        make = fake_tmdb.movie_result if movie else fake_tmdb.show_result
        out = []
        for _p, _id, item in found[: tmdb.MAX_RESULTS]:
            c = tmdb.candidate(make(item, self._lang(lang)), tmdb.kind_of(kind))
            if c is not None:
                out.append(c)
        return out


def unit_of(row: Row) -> tuple[str, str, int | None, dict[str, str]]:
    """Wat de scanner opzoekt: (soort, titel, jaar, id's); bij een aflevering de serie."""
    if row.kind == "film":
        parsed = names.parse(row.path, "movies")
        return "movie", parsed.title, parsed.year, dict(parsed.ids)
    parsed = names.parse(row.path, "shows")
    return "show", parsed.show or parsed.title, parsed.show_year, dict(parsed.show_ids)


async def identify(provider, kind, title, year, ids, lang, rule: str) -> match.Decision:
    """lookup.identify, of een voorstel (zie boven)."""
    if "breed" in rule and match.direct(ids, kind) is None and " ".join(title.split()):
        found: list[Candidate] = []
        for language in dict.fromkeys((lang, tmdb.FALLBACK_LANG)):
            for y in dict.fromkeys((year, None)):
                found += await provider.search(kind, title, y, language)
        decision = match.decide(title, year, found)
    else:
        decision = await lookup.identify(provider, kind, title, year, ids, lang)
    # een id uit de naam is zeker (Keuze 42), ook als de titel anders is
    sure = match.direct(ids, kind) is not None and decision.confidence >= 1.0
    if "tekst" in rule and decision.state == "matched" and decision.top and not sure:
        first = decision.top[0]
        c = first.candidate
        text = first.score - match.year_bonus(year, c.year) - match.tiebreak(c.popularity)
        if text < TEXT_MIN - 1e-9:
            decision = match.Decision("review", None, decision.confidence, decision.top)
    return decision


def _top(decision: match.Decision) -> list[list[Any]]:
    return [
        [s.candidate.id, s.candidate.title, s.candidate.year, round(s.score, 3)]
        for s in decision.top[:3]
    ]


async def run(
    mode: str, rule: str, rows: list[Row], data: Data, settings: Settings
) -> dict[str, Any]:
    secrets.reset()
    clock = fake_kit.Clock()
    app = create_app(FakeConfig(tmdb_key=fake_kit.KEY, data=data, clock=clock))
    out = fake_kit.outbound(settings, app, clock=clock)
    previous = http.use(out)
    try:
        if mode == "ruim":
            provider: tmdb.Tmdb = Loose(settings, fake_kit.KEY, data)
        else:
            provider = tmdb.Tmdb(settings, fake_kit.KEY)
        decisions: dict[tuple[Any, ...], match.Decision] = {}
        results = []
        for row in rows:
            kind, title, year, ids = unit_of(row)
            key = (kind, match.normalize(title), year, tuple(sorted(ids.items())))
            if key not in decisions:
                decisions[key] = await identify(provider, kind, title, year, ids, LANG, rule)
            decision = decisions[key]
            prefix = "movie:" if kind == "movie" else "tv:"
            want = f"{prefix}{row.expected}" if row.expected is not None else None
            if decision.state == "matched":
                got = decision.choice.id if decision.choice else None
                outcome = "goed" if got == want else "fout"
            else:
                outcome = "nakijken"
            in_top = want is not None and any(s.candidate.id == want for s in decision.top)
            results.append(
                {
                    "groep": row.group,
                    "soort": row.kind,
                    "pad": row.path,
                    "titel": title,
                    "jaar": year,
                    "verwacht": want,
                    "uitkomst": outcome,
                    "gekozen": decision.choice.id if decision.choice else None,
                    "zekerheid": decision.confidence,
                    "goede_in_top5": in_top,
                    "top3": _top(decision),
                }
            )
    finally:
        http.use(previous)
        await out.aclose()
    return {"zoeken": mode, "regel": rule, "namen": results, "samen": summarize(results)}


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    def count(part: list[dict[str, Any]]) -> dict[str, Any]:
        c = Counter(r["uitkomst"] for r in part)
        auto = c["goed"] + c["fout"]
        review = [r for r in part if r["uitkomst"] == "nakijken"]
        return {
            "namen": len(part),
            "automatisch_goed": c["goed"],
            "automatisch_fout": c["fout"],
            "nakijken": c["nakijken"],
            "nakijken_goede_in_top5": sum(1 for r in review if r["goede_in_top5"]),
            "precisie": round(c["goed"] / auto, 3) if auto else None,
        }

    groups = sorted({r["groep"] for r in results})
    return {
        "totaal": count(results),
        "films": count([r for r in results if r["soort"] == "film"]),
        "afleveringen": count([r for r in results if r["soort"] == "serie"]),
        "per_groep": {g: count([r for r in results if r["groep"] == g]) for g in groups},
        "fout": [
            {k: r[k] for k in ("pad", "verwacht", "gekozen", "zekerheid", "top3")}
            for r in results
            if r["uitkomst"] == "fout"
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--uit", type=Path, default=None)
    parser.add_argument("--zoeken", choices=("nep", "ruim", "beide"), default="beide")
    parser.add_argument("--regel", choices=(*RULES, "alle"), default="alle")
    args = parser.parse_args(argv)
    rows = corpus()
    data = Data.from_documents(json.loads(TMDB_DATA.read_text(encoding="utf-8")))
    modes = ("nep", "ruim") if args.zoeken == "beide" else (args.zoeken,)
    with tempfile.TemporaryDirectory(prefix="meet5-koppelen-") as tmp:
        settings = Settings(data_dir=Path(tmp), web_dist=Path(tmp) / "web", jwt_secret="m" * 32)
        report = {"meting": "koppelen", "namen": len(rows), "taal": LANG, "runs": []}
        rules = RULES if args.regel == "alle" else (args.regel,)
        for rule in rules:
            for mode in modes:
                result = asyncio.run(run(mode, rule, rows, data, settings))
                report["runs"].append(result)
                s = result["samen"]
                print(f"regel {rule}, zoeken {mode}: {json.dumps(s['totaal'], ensure_ascii=False)}")
                if rule != "zoals":
                    continue
                for group, part in s["per_groep"].items():
                    print(f"  {group:18} {json.dumps(part, ensure_ascii=False)}")
                for wrong in s["fout"]:
                    print("  FOUT", json.dumps(wrong, ensure_ascii=False))
    if args.uit:
        args.uit.parent.mkdir(parents=True, exist_ok=True)
        args.uit.write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n")
        print("uitkomst:", args.uit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
