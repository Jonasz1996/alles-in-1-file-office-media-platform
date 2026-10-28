"""De scanner (Keuze 5): gebeurtenissen via hooks.register_touched, plus een ronde bij de start en
elke 6 uur (jobs media.scan en media.reconcile in de rij media_scan). Stub van M0; M2 bouwt hem."""

from __future__ import annotations

import uuid


def touched(node_ids: list[uuid.UUID], type: str) -> None:
    """De luisteraar voor hooks.touched; de stub meldt zich niet aan."""
    return None
