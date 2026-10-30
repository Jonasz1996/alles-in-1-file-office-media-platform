"""Hoofdstukken en de aftiteling (Keuze 30, M5): app/media/chapters.py.

De titels, de aftiteling in het Nederlands en het Engels (alleen in de tweede helft), geen
hoofdstukken, en de echte probe van chapters.mkv (marker media).
"""

from __future__ import annotations

import pytest

from app.media import chapters
from app.media.types import Chapter, Probe


def probe(titles: list[tuple[int, int, str | None]], duration: int | None = 60_000) -> Probe:
    return Probe(
        duration_ms=duration,
        chapters=[Chapter(start_ms=s, end_ms=e, title=t) for s, e, t in titles],
    )


@pytest.mark.parametrize(
    "title",
    [
        "Credits",
        "End Credits",
        "end-credits",
        "Closing credits",
        "ENDING CREDITS",
        "Aftiteling",
        "De aftiteling",
        "Begin aftiteling",
        "Eindgeneriek",
        "Générique",
        "Generiek",
        "Outro",
        "Ending",
        "Post-credits scene",
        "End titles",
    ],
)
def test_aftiteling(title: str) -> None:
    assert chapters.is_credits(title)
    found = probe([(0, 40_000, "Begin"), (40_000, 60_000, title)])
    assert chapters.credits_ms(found) == 40_000


@pytest.mark.parametrize(
    "title",
    [
        None,
        "",
        "Intro",
        "Opening Credits",
        "Opening",
        "Begingeneriek",
        "A Happy Ending",
        "Hoofdstuk 12",
        "Creditcard",
        "Endgame",
    ],
)
def test_geen_aftiteling(title: str | None) -> None:
    assert not chapters.is_credits(title)
    assert chapters.credits_ms(probe([(0, 40_000, "Begin"), (40_000, 60_000, title)])) is None


def test_alleen_in_de_tweede_helft() -> None:
    # Een "Credits" vooraan is de begintitel
    assert chapters.credits_ms(probe([(0, 5_000, "Credits"), (5_000, 60_000, "Film")])) is None
    found = probe([(0, 5_000, "Credits"), (5_000, 50_000, "Film"), (50_000, 60_000, "Credits")])
    assert chapters.credits_ms(found) == 50_000


def test_zonder_duur_telt_het_eind_van_de_hoofdstukken() -> None:
    found = probe([(0, 40_000, "Film"), (40_000, 60_000, "Outro")], duration=None)
    assert chapters.credits_ms(found) == 40_000


def test_de_eerste_wint() -> None:
    found = probe(
        [(0, 30_000, "Film"), (45_000, 50_000, "Post-credits"), (35_000, 45_000, "Credits")]
    )
    assert chapters.credits_ms(found) == 35_000


def test_geen_hoofdstukken() -> None:
    assert chapters.credits_ms(Probe()) is None
    assert chapters.items(Probe()) == []


def test_items_op_volgorde_en_opgeschoond() -> None:
    found = probe(
        [
            (20_000, 30_000, "  Twee  "),
            (0, 20_000, "x" * 300),
            (30_000, 30_000, "Nul lang"),
            (70_000, 80_000, "Na het eind"),
            (50_000, 90_000, "Over het eind"),
            (-5, 0, "Negatief"),
        ]
    )
    items = chapters.items(found)
    assert [(c.start_ms, c.end_ms) for c in items] == [
        (0, 20_000),
        (20_000, 30_000),
        (50_000, 60_000),
    ]
    assert len(items[0].title or "") == chapters.TITLE_MAX
    assert items[1].title == "Twee"


@pytest.fixture(scope="module")
def anyio_backend():
    return "asyncio"


@pytest.fixture(scope="module")
async def worker(tmp_path_factory):
    from app.config import Settings
    from app.media import client, local

    base = tmp_path_factory.mktemp("chapters-worker")
    settings = Settings(data_dir=base, web_dist=base / "web", jwt_secret="t" * 32)
    w = await local.start(settings, base / "run" / "worker.sock")
    client.bind(w.socket, w.key)
    try:
        yield w
    finally:
        await local.stop(w)
        client.bind(None)


@pytest.mark.media
@pytest.mark.anyio
async def test_de_probe_van_chapters_mkv(worker) -> None:
    from app.media import client
    from tests.media import fixtures as F

    found = await client.probe(F.make("chapters.mkv"))
    titles = [c.title for c in chapters.items(found)]
    assert titles == ["Intro", "Deel 1", "End Credits"]
    # De probe geeft mediatijd: de bron begint hier op S = start_ms (negatief door de priming van
    # de audio), dus het hoofdstuk van 24 s in de bron staat op 24 s - S
    assert chapters.credits_ms(found) == 24_000 - (found.start_ms or 0)
