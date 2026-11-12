"""Mo (§4.5): 10.000 namen ontleden. Grens ≤ 0,5 s.

De namen: de 10.000 van de fuzz-test van M2 (fuzz_names met dezelfde vaste seed, scene-namen,
afleveringen, muziek en rommel; test_media_names.py), elk door names.parse met de soort waarvoor
ze gemaakt zijn (aflevering: shows, anders movies), op één core van de worker. Eerst één ronde
om op te warmen (imports, regex), dan --herhaal keer de tijd, elke keer zonder wat de parser
onthoudt (de cache van _movie_core leeg, zoals de test).

    cd api && uv run python -m tests.media.metingen.namen --herhaal 5 --uit /tmp/mo.json
"""

from __future__ import annotations

import argparse
import os
import time
from pathlib import Path
from typing import Any

from tests.media.metingen import kit

LIMIT_S = 0.5
SEED = 20261029


def names_10000() -> list[tuple[str, str]]:
    from tests.test_media_names import fuzz_names

    return [
        (name, "shows" if episode else "movies") for name, *_, episode in fuzz_names(SEED, 10_000)
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--herhaal", type=int, default=5)
    parser.add_argument("--uit", type=Path, default=None)
    args = parser.parse_args(argv)
    from app.media import names

    os.sched_setaffinity(0, {min(kit.CPUS)})
    cases = names_10000()
    rest = kit.quiet(0.8, 120)
    for path, kind in cases:  # opwarmen
        names.parse(path, kind)
    times = []
    for _ in range(args.herhaal):
        names._movie_core.cache_clear()
        began = time.perf_counter()
        for path, kind in cases:
            names.parse(path, kind)
        times.append(time.perf_counter() - began)
        kit.log(f"{len(cases)} namen: {times[-1]:.3f} s")
    result: dict[str, Any] = {
        "meting": "namen",
        "omstandigheden": kit.conditions(),
        "namen": len(cases),
        "soorten": sorted({k for _p, k in cases}),
        "seconden": kit.spread(times),
        "grens_s": LIMIT_S,
        "rust_voor_cores": rest,
    }
    kit.write_json(args.uit, result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
