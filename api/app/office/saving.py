"""Opslaan uit een callback (Keuze 16 en 17). O0-stub; O2 maakt het af.

store(row, tmp, sha, size, event) -> "saved" | "same" | "copy" | "ignored" | "retry" |
"refused": het recept van replace_content met place_file(..., exact=True, squash=...), los
van OnlyOffice.
"""

from pathlib import Path

from app.models import OfficeSession
from app.office.engine import CallbackEvent


async def store(row: OfficeSession, tmp: Path, sha: str, size: int, event: CallbackEvent) -> str:
    raise NotImplementedError("O2")
