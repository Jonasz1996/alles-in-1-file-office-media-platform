"""app.cli media-status [--wait N] [--sessions]. Stub van M0; M10 bouwt het."""

from __future__ import annotations

from typing import Any

from app.config import Settings


async def collect(settings: Settings, *, sessions: bool = False) -> dict[str, Any]:
    """De staat van media voor de cli. De stub zegt alleen of media aan staat."""
    return {"enabled": settings.media_enabled, "worker": "unknown"}
