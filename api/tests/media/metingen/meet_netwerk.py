"""Een pytest-plugin voor het onderzoek van Mc direct (docs/fase-5/metingen.md, bevinding 4).

Hij verandert alleen het netwerk van de e2e `media_perf` (PERF_NETWORK in e2e/media_harness.py),
met MEET_DOWN_MBIT en MEET_LATENCY_MS; de test zelf blijft hetzelfde. Vanuit `api/`:

    MEET_DOWN_MBIT=400 E2E_PERF_REPORT_ONLY=1 E2E_PERF_OUT=/tmp/perf-400 \\
      PYTHONPATH=tests/media/metingen uv run --with playwright pytest \\
      ../e2e/test_media_m6_perf.py -m media_perf -k mc_zoeken -p meet_netwerk -rs -s
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

E2E = Path(__file__).resolve().parents[4] / "e2e"


def pytest_configure(config) -> None:
    sys.path.insert(0, str(E2E))
    import media_harness

    if os.environ.get("MEET_DOWN_MBIT"):
        media_harness.PERF_NETWORK["down_mbit"] = float(os.environ["MEET_DOWN_MBIT"])
    if os.environ.get("MEET_LATENCY_MS"):
        media_harness.PERF_NETWORK["latency_ms"] = float(os.environ["MEET_LATENCY_MS"])
    print("PERF_NETWORK", media_harness.PERF_NETWORK)
