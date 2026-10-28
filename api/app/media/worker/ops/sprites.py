"""De opdracht sprites (§3.3). Stub van M0; M5 bouwt hem. Tot dan not_built."""

from __future__ import annotations

from app.media.wire import Code
from app.media.worker.server import Context, OpError


async def run(ctx: Context) -> dict | None:
    raise OpError(Code.NOT_BUILT, "sprites bouwt M5")
