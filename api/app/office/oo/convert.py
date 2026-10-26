"""Conversie naar PDF via POST /converter (Keuze 25). O0-stub; O3 maakt het af.

convert: async true en Accept: application/json, elke seconde opnieuw tot endConvert,
hooguit office_convert_timeout; fileUrl herschrijven (urls.rewrite, kind "conv") en ophalen
naar out, hooguit limit bytes. De foutcodes van DS worden ConvertError(code, kind).
"""

from pathlib import Path

from app.config import Settings


async def convert(
    settings: Settings, src_url: str, ext: str, key: str, title: str, out: Path, limit: int
) -> None:
    raise NotImplementedError("O3")
