"""De policy voor publieke links en voor ingelogde kijkers (Keuze 34, 27).

- Ingelogd: alles mag, tot 2160 (de ladder zelf gaat niet boven 1080, Keuze 24).
- Een publieke link: direct en remux; transcode alleen als public_transcode aan staat (Vraag 5,
  standaard uit), en dan hooguit 720. De grenzen per link (2 sessies, 1 transcode, nooit de
  laatste plek) houden sessions.py en de toelating bij.
- HDR live naar SDR gaat zonder hdr_1080 hooguit naar 720 (Keuze 27): `for_source` zet dat
  plafond voor een HDR-bron.
"""

from __future__ import annotations

from app.config import MediaConfig
from app.media.types import Policy, Probe

PUBLIC_MAX_HEIGHT = 720
LOGGED_IN_MAX_HEIGHT = 2160
HDR_MAX_HEIGHT = 720  # zonder hdr_1080 (Keuze 27)
PUBLIC_SESSIONS = 2  # per link (Keuze 34)
PUBLIC_PLAYS_PER_MINUTE = 30  # per IP (Keuze 34)


def policy(config: MediaConfig, *, public: bool) -> Policy:
    """Wat mag: ingelogd alles tot 2160; een publieke link alleen met public_transcode."""
    if public:
        return Policy(public=True, transcode=config.public_transcode, max_height=PUBLIC_MAX_HEIGHT)
    return Policy(public=False, transcode=True, max_height=LOGGED_IN_MAX_HEIGHT)


def for_source(policy: Policy, config: MediaConfig, probe: Probe) -> Policy:
    """De policy voor deze bron: een HDR-bron gaat zonder hdr_1080 live hooguit naar 720."""
    video = probe.video()
    if video is None or video.hdr == "none" or config.hdr_1080:
        return policy
    return policy.model_copy(update={"max_height": min(policy.max_height, HDR_MAX_HEIGHT)})
