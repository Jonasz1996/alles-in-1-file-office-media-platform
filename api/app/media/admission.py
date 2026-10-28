"""Toelating en het CPU-budget (Keuze 26). Stub van M0; M4 bouwt hem.

reserve geeft MediaBusy(reason, retry_after) als het niet past. De stub laat niets toe: zonder
M4 is er geen transcode.
"""

from __future__ import annotations

import uuid

from app.media.errors import MediaBusy
from app.media.types import AdmissionView, BudgetView, Grant, Plan


class Admission:
    """Op MediaState.admission (M4)."""

    def __init__(self) -> None:
        self.grants: dict[uuid.UUID, Grant] = {}


def view(user_id: uuid.UUID, link_id: uuid.UUID | None) -> BudgetView:
    return BudgetView(
        budget=0, in_use=0, places_free=0, user_free=0, reason="worker", retry_after=30
    )


def reserve(plan: Plan, user_id: uuid.UUID, link_id: uuid.UUID | None) -> Grant:
    if plan.mode in ("direct", "remux", "audio"):
        return Grant(user_id=user_id, link_id=link_id, mode=plan.mode, cost=0, places=0)
    raise MediaBusy("worker", 30)


def release(grant: Grant) -> None:
    return None


def snapshot() -> AdmissionView:
    return AdmissionView()
