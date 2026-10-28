"""De bewaking in de worker: VmRSS elke 2 s per kind, voor /beheer (Keuze 12). Stub van M0; M1
bouwt hem."""

from __future__ import annotations


def rss_kb(pid: int) -> int | None:
    """VmRSS uit /proc/<pid>/status, of None."""
    try:
        with open(f"/proc/{pid}/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1])
    except (OSError, ValueError):
        return None
    return None
