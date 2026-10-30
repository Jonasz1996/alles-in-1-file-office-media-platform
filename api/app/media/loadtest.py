"""De belastingtest in de app (§4.6): bronnen via synth, sessies via LoadtestFile. Stub van M0;
M10 bouwt hem."""

from __future__ import annotations

from typing import Any, Literal

from app.config import Settings
from app.media import errors as media_errors


async def run_cli(
    settings: Settings,
    *,
    streams: int = 3,
    seconds: int = 300,
    codec: Literal["h264", "vp9"] = "h264",
    external: str | None = None,
) -> dict[str, Any]:
    """Voor app.cli media-loadtest: draait de test (of met external de spelers buiten de app,
    tegen dat adres) en geeft de uitkomst als dict (zoals LoadtestView). De stub: media_off."""
    raise media_errors.off()
