"""De policy voor publieke links (Keuze 34). Stub van M0; M3 bouwt hem."""

from __future__ import annotations

from app.config import MediaConfig
from app.media.types import Policy

PUBLIC_MAX_HEIGHT = 720


def policy(config: MediaConfig, *, public: bool) -> Policy:
    """Wat mag: ingelogd alles tot 2160; een publieke link alleen met public_transcode."""
    if public:
        return Policy(public=True, transcode=config.public_transcode, max_height=PUBLIC_MAX_HEIGHT)
    return Policy(public=False, transcode=True, max_height=2160)
