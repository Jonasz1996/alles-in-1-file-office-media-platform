"""De beslissing (Keuze 19): een pure functie met vaste regels. Stub van M0; M3 bouwt de regels."""

from __future__ import annotations

from app.media import errors as media_errors
from app.media.types import BudgetView, Choice, ClientProfile, Plan, Policy, Probe


def decide(
    probe: Probe, profile: ClientProfile, choice: Choice, policy: Policy, budget: BudgetView
) -> Plan:
    raise media_errors.off()
