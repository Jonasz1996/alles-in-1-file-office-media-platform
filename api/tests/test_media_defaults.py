"""De standaarden van media op de metingen (integratie deel 3; §5 stap 4 van het ontwerp: "het
budget en de standaarden van MediaConfig op de metingen gezet").

Elke waarde hier staat in docs/fase-5/integratie.md, "Standaarden", met de oude waarde, de nieuwe
en de meting die haar draagt (docs/fase-5/metingen.md). Een andere waarde hier hoort een andere
regel daar te krijgen, met een meting; daarom staan ze hier vast. De machine is de LXC van Jonas:
4 cores, 16 GB, geen GPU, en de worker krijgt er 3 (compose.media-limits.yaml).

- MediaConfig (/beheer), de omgeving (Settings), de plaatsen van de worker en de grenzen van de
  container;
- het aandeel van subs/ en trick/ in de cache (m10.md "Naar de integratie" punt 2);
- de regel van Keuze 26 bij de standaarden: tot welke `f` de eerste kijker met 3 plaatsen nog
  past (het runbook van media noemt die grens);
- met media uit doet geen van deze standaarden iets: de sweep van de cache draait dan niet.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.config import MediaConfig, Settings
from app.media import admission, cache, decide, runtime
from app.media.state import MediaPolicy, MediaState
from app.media.worker.server import Config, WorkClass

ROOT = Path(__file__).resolve().parents[2]
GiB = 1024**3


def test_mediaconfig_de_standaarden_op_beheer():
    config = MediaConfig()
    assert config.model_dump() == {
        "enabled": True,
        "cpu_budget": 3.0,  # de 3 cores van de worker (Md, Mg)
        "max_transcodes": 3,  # Vraag 3 (Md fase b 5 van 5)
        "max_user_transcodes": 2,
        "public_transcode": False,  # Vraag 5
        "hdr_1080": False,  # Keuze 27 (Mf)
        "cache_gb": 20,  # Keuze 31 (Md: 28 MB per minuut)
        "musicbrainz": False,  # de beslissingskaarten: providers uit
        "bench": None,
    }
    # de grenzen van het ontwerp blijven
    fields = MediaConfig.model_fields
    bounds = {
        name: {
            type(m).__name__: getattr(m, attr)
            for m in fields[name].metadata
            for attr in ("ge", "le")
            if hasattr(m, attr)
        }
        for name in ("cpu_budget", "max_transcodes", "max_user_transcodes", "cache_gb")
    }
    assert bounds == {
        "cpu_budget": {"Ge": 0.5, "Le": 16},
        "max_transcodes": {"Ge": 0, "Le": 16},
        "max_user_transcodes": {"Ge": 1, "Le": 16},
        "cache_gb": {"Ge": 1, "Le": 10_000},
    }


def test_de_omgeving_en_de_worker():
    fields = Settings.model_fields
    defaults = {
        name: fields[name].default
        for name in (
            "media_enabled",
            "media_max_transcodes",
            "media_job_memory_mb",
            "media_job_workers",
            "media_max_procs",
            "media_segment_wait",
        )
    }
    assert defaults == {
        "media_enabled": False,  # zonder de vlag geen worker en 503 media_off
        "media_max_transcodes": 3,  # het plafond; /beheer kan alleen lager
        "media_job_memory_mb": 1200,  # Mh: ±200 MB per ffmpeg
        "media_job_workers": 1,
        "media_max_procs": 10,  # Md: nooit meer dan 3 ffmpeg tegelijk voor 3 transcodes
        "media_segment_wait": 20,
    }
    worker = Config(socket=Path("/nergens"))
    assert (worker.max_procs, worker.max_transcodes, worker.memory_mb) == (10, 3, 1200)
    assert worker.limits() == {
        WorkClass.TRANSCODE: 3,
        WorkClass.REMUX: 6,
        WorkClass.BACKGROUND: 1,
        WorkClass.SHORT: 2,
    }


def test_het_budget_past_bij_de_cores_van_de_container():
    """cpu_budget is wat de worker aan cores krijgt (Keuze 26); de container houdt een core vrij
    voor de app en DS (regel 14), en 3 GB is ruim boven Mh (ten hoogste 793 MB)."""
    yaml = pytest.importorskip("yaml")
    text = (ROOT / "infra" / "single" / "compose.media-limits.yaml").read_text(encoding="utf-8")
    media = yaml.safe_load(text)["services"]["media"]
    assert media["cpus"] == MediaConfig().cpu_budget == 3.0
    assert media["mem_limit"] == media["memswap_limit"] == "3g"


def test_subs_en_trick_samen_een_kwart_van_de_cache():
    """Punt 2 van "Naar de integratie": standaard 5 GiB, los van de sessies en audio/ (die
    houden cache_gb samen, Keuze 31)."""
    assert cache.DERIVED == ("subs", "trick")
    assert cache.limit_bytes(MediaConfig().cache_gb) // cache.DERIVED_SHARE == 5 * GiB


@pytest.mark.parametrize(
    ("f", "budget", "places", "first_fits"),
    [
        (1.0, 3.0, 3, True),  # de referentie-CPU
        (1.40, 3.0, 3, True),  # de hoogste f van de sandbox (Md)
        (1.60, 3.0, 3, True),
        (1.61, 3.0, 3, False),  # daarboven past met 3 plaatsen geen kijker meer
        (1.61, 3.0, 2, True),  # met 2 plaatsen wel (het runbook: Omzettingen tegelijk 2)
        (2.41, 3.0, 2, True),
        (2.42, 3.0, 2, False),
        (1.07, 2.0, 3, True),  # DS start: B − 1,0 (bevinding 7)
        (1.08, 2.0, 3, False),
    ],
)
def test_tot_welke_f_de_eerste_kijker_past(f, budget, places, first_fits):
    """De regel van Keuze 26 met de standaarden: in gebruik + kosten(480 veryfast) + de reserve
    voor de andere vrije plaatsen ≤ 0,9 × B. Met f_dec = f, zoals de bench meestal geeft."""
    from app.media.types import BudgetView, Plan, Rung

    plan = Plan(
        mode="transcode",
        reasons=["video_codec"],
        video=Rung(height=480, preset="veryfast", codec="h264"),
        audio="copy",
        cost=decide.reserve_cost(f, f),
        places=1,
    )
    view = BudgetView(
        budget=budget,
        in_use=0.0,
        places_free=places,
        user_free=MediaConfig().max_user_transcodes,
        factor=f,
        factor_dec=f,
    )
    assert admission.fits(plan, view, public=False) is first_fits


@pytest.mark.anyio
async def test_met_media_uit_doet_de_sweep_niets(monkeypatch, tmp_path):
    """Een andere standaard verandert niets met media uit: de housekeeping media.sweep (en dus
    ook de grens van subs/ en trick/) draait dan niet."""
    from fastapi import FastAPI

    called: list[object] = []

    async def sweep(settings) -> None:
        called.append(settings)

    monkeypatch.setattr(cache, "sweep", sweep)
    settings = Settings(data_dir=tmp_path)
    app = FastAPI()
    app.state.media_policy = MediaPolicy(MediaConfig())
    try:
        for enabled, config, runs in (
            (False, MediaConfig(), False),  # de vlag in de omgeving uit
            (True, MediaConfig(enabled=False), False),  # uitgezet op /beheer
            (True, MediaConfig(), True),
        ):
            app.state.media = MediaState(enabled=enabled)
            app.state.media_policy = MediaPolicy(config)
            runtime.bind(app, settings)
            called.clear()
            await runtime._sweep(settings)
            assert bool(called) is runs, (enabled, config.enabled)
    finally:
        runtime.unbind()
