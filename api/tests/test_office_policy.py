"""De regels van bewerken als tabellen (app/office/policy.py): versies (Keuze 17), wie opslaat
(Keuze 16, stap 6), waarheen (stap 5 tot 8), een callback op een dichte sessie (Keuze 15) en de
naam van een herstelkopie."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.models import OfficeSession
from app.office import policy
from app.office.engine import CallbackEvent
from app.office.state import OfficeConfig

T0 = datetime(2026, 10, 27, 9, 0, tzinfo=UTC)
NAIVE = T0.replace(tzinfo=None)
CFG = OfficeConfig()


def row(**fields) -> OfficeSession:
    base = {
        "key": "k-1",
        "checkpoint": None,
        "versioned_at": None,
        "versions_made": 0,
        "state": "open",
        "closed_reason": None,
    }
    return OfficeSession(**{**base, **fields})


# --- make_version ---


@pytest.mark.parametrize(
    ("fields", "current", "minutes", "want"),
    [
        # De eerste opslag van de sessie: de inhoud van vóór de sessie wordt een versie
        ({}, "a", 0, True),
        # De huidige inhoud komt niet van deze sessie (upload of terugzetten ertussen)
        ({"checkpoint": "b", "versions_made": 1, "versioned_at": T0}, "a", 1, True),
        # Ook na 10 versies blijft inhoud van buiten altijd staan
        ({"checkpoint": "b", "versions_made": 10, "versioned_at": T0}, "a", 1, True),
        # De vorige tussenstand van deze sessie, binnen 30 minuten: vervangen
        ({"checkpoint": "a", "versions_made": 1, "versioned_at": T0}, "a", 29, False),
        # Na 30 minuten: wel een versie
        ({"checkpoint": "a", "versions_made": 1, "versioned_at": T0}, "a", 30, True),
        ({"checkpoint": "a", "versions_made": 1, "versioned_at": T0}, "a", 31, True),
        # Hooguit 10 per sessie
        ({"checkpoint": "a", "versions_made": 10, "versioned_at": T0}, "a", 600, False),
        # Een tussenstand zonder versie-tijd: een versie
        ({"checkpoint": "a", "versions_made": 1}, "a", 0, True),
        # Een tijd zonder tijdzone (SQLite) telt als UTC
        ({"checkpoint": "a", "versions_made": 1, "versioned_at": NAIVE}, "a", 31, True),
    ],
)  # fmt: skip
def test_make_version(fields, current, minutes, want):
    assert policy.make_version(row(**fields), current, T0 + timedelta(minutes=minutes), CFG) is want


def test_make_version_volgt_version_minutes():
    r = row(checkpoint="a", versions_made=1, versioned_at=T0)
    cfg = OfficeConfig(version_minutes=5)
    assert policy.make_version(r, "a", T0 + timedelta(minutes=5), cfg)
    assert not policy.make_version(r, "a", T0 + timedelta(minutes=4), cfg)


def test_25_tussenstanden_met_31_minuten_hooguit_10_versies():
    """25 tussenstanden, elk 31 minuten na de vorige: hooguit 10 versies, en de eerste daarvan
    is de inhoud van vóór de sessie (die blijft dus staan, met MAX_VERSIONS = 20)."""
    r = row()
    current = "voor"
    kept: list[str] = []
    now = T0
    for n in range(25):
        now += timedelta(minutes=31)
        new = f"stand-{n}"
        if policy.make_version(r, current, now, CFG):
            kept.append(current)
            r.versions_made += 1
            r.versioned_at = now
        r.checkpoint = new
        current = new
    assert len(kept) == 10 == r.versions_made
    assert kept[0] == "voor"


# --- choose_actor ---

A, B, C = (uuid.uuid4() for _ in range(3))
MEMBERS = {"ua": str(A), "ub": str(B), "uc": str(C)}


@pytest.mark.parametrize(
    ("history", "actions", "banned", "writers", "want"),
    [
        # De laatste bewerker eerst
        (["ub", "ua"], [], [], {A, B, C}, B),
        # Hij heeft geen recht meer: de volgende uit history
        (["ub", "ua"], [], [], {A, C}, A),
        # Niemand uit history: actions
        ([], ["uc"], [], {A, B, C}, C),
        # Banned telt nooit, ook al heeft hij nog recht
        (["ub"], ["ub"], ["ub"], {A, B}, A),
        # De rest van members
        (["onbekend"], [], [], {C}, C),
        # Niemand meer: None (herstelkopie)
        (["ua", "ub"], ["uc"], [], set(), None),
        # Alleen een onbekende: geen lid, dus niet
        (["vreemd"], ["vreemd"], [], {A, B, C}, A),
    ],
)
def test_choose_actor(history, actions, banned, writers, want):
    assert policy.choose_actor(history, actions, MEMBERS, banned, writers.__contains__) == want


def test_choose_actor_zonder_leden_of_met_een_kapotte_id():
    assert policy.choose_actor(["ua"], [], {}, [], lambda _: True) is None
    assert policy.choose_actor(["ua"], [], {"ua": "geen-id"}, [], lambda _: True) is None


# --- target ---


@pytest.mark.parametrize(
    ("node", "owner", "actor", "want"),
    [
        (True, True, A, "original"),
        (True, True, None, "copy"),
        (False, True, A, "nothing"),
        (True, False, A, "nothing"),
        (False, False, None, "nothing"),
    ],
)
def test_target(node, owner, actor, want):
    assert policy.target(node, owner, actor) == want


# --- late: de tabel van dichte sessies (Keuze 15) ---

URL = "/cache/files/data/k-1_1/output.docx/output.docx?md5=x&expires=1"


@pytest.mark.parametrize("reason", ["saved", "no_changes", "copy", "refused", "lost", "gone"])
def test_late_status_1_altijd_drop(reason):
    r = row(state="closed", closed_reason=reason)
    assert policy.late(r, CallbackEvent("presence", "k-1", connected=["ua"])) == "drop"
    # Status 1 zonder iemand (de laatste ging weg): niets
    assert policy.late(r, CallbackEvent("presence", "k-1", connected=[])) == "ignore"


@pytest.mark.parametrize(
    ("reason", "kind", "want"),
    [
        *[(r, k, "ignore") for r in ("saved", "no_changes", "copy", "refused", "gone")
          for k in ("final", "failed", "checkpoint")],
        ("lost", "final", "save"),
        ("lost", "failed", "save"),
        ("lost", "checkpoint", "save"),
    ],
)  # fmt: skip
def test_late_opslag(reason, kind, want):
    r = row(state="closed", closed_reason=reason)
    assert policy.late(r, CallbackEvent(kind, "k-1", url=URL)) == want


@pytest.mark.parametrize("reason", ["saved", "no_changes", "copy", "refused", "lost", "gone"])
def test_late_status_4_en_zonder_adres(reason):
    r = row(state="closed", closed_reason=reason)
    assert policy.late(r, CallbackEvent("closed", "k-1")) == "ignore"
    assert policy.late(r, CallbackEvent("final", "k-1", url=None)) == "ignore"
    assert policy.late(r, CallbackEvent("checkpoint_failed", "k-1")) == "ignore"


# --- recovery_name ---

WHEN = datetime(2026, 10, 27, 14, 5)


@pytest.mark.parametrize(
    ("name", "locale", "want"),
    [
        ("verslag.docx", "nl", "verslag (hersteld 2026-10-27 14.05).docx"),
        ("verslag.docx", None, "verslag (hersteld 2026-10-27 14.05).docx"),
        ("report.xlsx", "en", "report (recovered 2026-10-27 14.05).xlsx"),
        ("report.xlsx", "en-GB", "report (recovered 2026-10-27 14.05).xlsx"),
        ("plan.pptx", "de", "plan (hersteld 2026-10-27 14.05).pptx"),
        ("zonder-punt", "nl", "zonder-punt (hersteld 2026-10-27 14.05)"),
        (".docx", "nl", ".docx (hersteld 2026-10-27 14.05)"),
        ("a.b.docx", "nl", "a.b (hersteld 2026-10-27 14.05).docx"),
    ],
)
def test_recovery_name(name, locale, want):
    assert policy.recovery_name(name, WHEN, locale) == want


def test_recovery_name_lange_naam_past():
    name = "x" * 300 + ".docx"
    out = policy.recovery_name(name, WHEN, "nl")
    assert len(out) <= 255
    assert out.endswith(" (hersteld 2026-10-27 14.05).docx")
    assert ":" not in out  # mag niet op Windows
