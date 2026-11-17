"""app.cli media-status [--wait N] [--sessions] (M10, Keuze 47).

collect geeft een dict met enabled, worker, ok (alles goed, of media staat uit), caps, lines
(wat de cli print, in het Nederlands) en exit (de exitcode). De eerste regel blijft zoals het
harnas en de job media-image hem lezen: "Media: aan (afgeschermd, ffmpeg 7.1.5)", "Media: uit",
of "Media: aan, maar de worker is niet bereikbaar (…)". Daarna, als het zo is: een GPU die de
worker ziet (nooit een eis, en nog niet in gebruik), wat ontbreekt, en met --sessions wie er nu
kijkt.

- wait: hooguit zoveel seconden wachten tot de worker er is (na de start, Keuze 47). Met wait is
  de exitcode altijd 0, ook bij een probleem: install.sh print dan de regels en gaat door (het
  stopt nooit door media). Zonder wait: 0 als alles goed is, anders 1.
- sessions: de sessies staan in het geheugen van de app, niet in de database. De cli vraagt ze
  daarom aan de draaiende app (GET /api/v1/admin/media op 127.0.0.1), met een kort access-token
  van een beheerder dat hij zelf tekent (hij draait in dezelfde container, met hetzelfde
  geheim). Dat token komt nooit in een regel of een log. Lukt het niet (de app draait niet,
  geen beheerder), dan zegt een regel dat, en is het geen fout: vóór de eerste installatie kijkt
  er ook niemand.
"""

from __future__ import annotations

import asyncio
import re
import time
import uuid
from typing import Any

from app.config import Settings

RETRY = 0.5  # seconden tussen twee pogingen zolang wait loopt
# Stuurtekens (C0, DEL, C1) worden een vraagteken: een titel of naam komt zo in de terminal van
# root (install.sh, watchers) en mag daar niets doen (OSC 52, de cursor verplaatsen, een regel
# wissen). Review golf 5, S1.
CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")
SESSIONS_TIMEOUT = 5.0
# Alleen een geslaagde zelftest (M1) is afgeschermd; zonder is het geen bewijs (review S6)
SANDBOX_TEXT = {
    "on": "afgeschermd",
    "off": "lokaal, zonder afscherming",
    "unchecked": "afscherming niet gecontroleerd",
}


async def _caps(settings: Settings, wait: float) -> tuple[Any, str]:
    """(WorkerCaps of None, de reden als het niet lukte); probeert opnieuw tot wait om is."""
    from app.media import client

    client.bind(settings.media_socket, settings.media_key())
    problem = settings.media_problem()
    if problem is not None:
        return None, problem
    end = time.monotonic() + max(0.0, wait)
    while True:
        try:
            return await client.caps(), ""
        except (client.WorkerDown, client.WorkerBusy, client.WorkerFailed, TimeoutError) as exc:
            reason = str(exc)[:200] or exc.__class__.__name__
        if time.monotonic() + RETRY > end:
            return None, reason
        await asyncio.sleep(RETRY)


async def admin_token(settings: Settings, minutes: int = 5) -> str | None:
    """Een kort access-token van de eerste actieve beheerder, voor de cli die de draaiende app
    iets vraagt (de sessies, de belastingtest met --extern). Een eigen login-familie die nergens
    in de database staat: er valt niets in te trekken, en het verloopt vanzelf. None zonder
    beheerder."""
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.db import get_engine
    from app.models import User
    from app.security import _encode

    maker = async_sessionmaker(get_engine(settings), expire_on_commit=False)
    async with maker() as session:
        user = await session.scalar(
            select(User)
            .where(User.role == "admin", User.disabled_at.is_(None))
            .order_by(User.created_at)
            .limit(1)
        )
    if user is None:
        return None
    claims = {"sub": str(user.id), "role": user.role, "typ": "access", "fam": str(uuid.uuid4())}
    return _encode(settings, claims, minutes)


def app_url(settings: Settings) -> str:
    return f"http://127.0.0.1:{settings.http_port}"


async def watching(settings: Settings, base: str | None = None) -> tuple[list[dict] | None, str]:
    """(de open sessies volgens de draaiende app, of None met de reden)."""
    import httpx

    try:
        token = await admin_token(settings)
    except Exception as exc:  # geen database (nog niet geïnstalleerd)
        return None, f"geen database ({exc.__class__.__name__})"
    if token is None:
        return None, "er is nog geen beheerder"
    url = (base or app_url(settings)) + "/api/v1/admin/media"
    try:
        async with httpx.AsyncClient(timeout=SESSIONS_TIMEOUT) as http:
            r = await http.get(url, headers={"Authorization": f"Bearer {token}"})
    except httpx.HTTPError as exc:
        return None, f"de app antwoordt niet ({exc.__class__.__name__})"
    if r.status_code != 200:
        return None, f"de app gaf {r.status_code}"
    return list(r.json().get("sessions") or []), ""


def safe(text: object) -> str:
    """Tekst voor de terminal: elk stuurteken wordt "?"."""
    return CONTROL.sub("?", str(text))


def session_line(s: dict) -> str:
    who = safe((s.get("user") or {}).get("display_name") or "?")
    what = safe(s.get("title") or "?")
    mode = safe(s.get("mode") or "?")
    rung = s.get("rung") or {}
    if mode == "transcode" and rung.get("height"):
        mode = f"transcode {rung['height']}p"
    return f"  {who}: {what} ({mode})"


def sessions_lines(found: list[dict] | None, reason: str) -> list[str]:
    if found is None:
        return [f"Kijkers: onbekend ({reason})"]
    if not found:
        return ["Kijkers: niemand"]
    who = "kijkt nu 1 persoon" if len(found) == 1 else f"kijken nu {len(found)} mensen"
    lines = [
        f"Let op: er {who}. Bij het bijwerken staat de stroom tot ±60 s stil; de speler"
        " probeert het zelf opnieuw."
    ]
    return lines + [session_line(s) for s in found]


async def collect(settings: Settings, *, sessions: bool = False, wait: float = 0) -> dict[str, Any]:
    """De staat van media voor de cli."""
    from app.media.admin import gpu_text

    if not settings.media_enabled:
        return {"enabled": False, "worker": "off", "ok": True, "lines": ["Media: uit"], "exit": 0}
    caps, reason = await _caps(settings, wait)
    if caps is None:
        out: dict[str, Any] = {
            "enabled": True,
            "worker": "down",
            "ok": False,
            "caps": None,
            "lines": [f"Media: aan, maar de worker is niet bereikbaar ({reason})"],
        }
    else:
        missing = list(caps.missing)
        where = SANDBOX_TEXT.get(caps.sandbox, SANDBOX_TEXT["unchecked"])
        lines = [f"Media: aan ({where}, ffmpeg {caps.ffmpeg or 'ontbreekt'})"]
        gpu = gpu_text(caps)
        if gpu != "geen":  # media gebruikt hem (nog) niet en heeft hem nooit nodig
            lines.append(f"GPU: {gpu} (niet in gebruik)")
        if missing:
            lines.append("Let op: de worker mist " + ", ".join(missing))
        out = {
            "enabled": True,
            "worker": "ffmpeg_missing" if caps.ffmpeg is None or missing else "ok",
            "ok": caps.ffmpeg is not None and not missing,
            "caps": caps.model_dump(mode="json"),
            "lines": lines,
        }
    if sessions:
        found, why = await watching(settings)
        out["sessions"] = found
        out["lines"] = [*out["lines"], *sessions_lines(found, why)]
    # Wat de worker of de app terugzei, gaat ook naar de terminal: zonder stuurtekens
    out["lines"] = [safe(line) for line in out["lines"]]
    # Met wait (na de start in install.sh) nooit een fout: de regels zeggen wat er is
    out["exit"] = 0 if wait > 0 or out["ok"] else 1
    return out
