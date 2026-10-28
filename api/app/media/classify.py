"""Wat een bestand in een bibliotheek is: film, aflevering, extra, nummer (Keuze 8). Stub van M0;
M2 bouwt hem."""

from __future__ import annotations

from typing import Literal

Kind = Literal["movie", "episode", "extra", "video", "track", "other"]


def classify(library_kind: str, path_parts: list[str], mime: str | None) -> Kind:
    return "other"
