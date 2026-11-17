"""app.cli analyse-status [--wait N] (fase 6, X2a; Keuze 59, §3.11).

collect(settings, wait) kijkt met monitor.check (één keer caps), en met wait hooguit zoveel
seconden opnieuw (elke seconde) tot de stand ok is. lines(found) is wat de cli print:

- de eerste regel blijft zoals A0 hem maakte en install.sh hem leest: "Analyse: ok", of
  "Analyse: <stand> (<reden in het Nederlands>)";
- met caps daarna per model of het klopt, de grenzen van de container en de opdrachten die de
  worker kent (state.view: dezelfde stand als /beheer).

De exitcode: 0 bij ok en off, en altijd 0 met wait (install.sh gaat dan door; hij stopt nooit
door de analyse); anders 1. Nooit de sleutel, nooit een bericht van de worker: de namen in caps
zijn door wire nagekeken (MODEL_NAME, OP_NAME).
"""

from __future__ import annotations

import asyncio

from app.analyse import monitor, state
from app.config import Settings

RETRY = 1.0  # seconden tussen twee pogingen zolang wait loopt
MODEL_TEXT = {
    "ok": "klopt",
    "ontbreekt": "ontbreekt",
    "anders": "klopt niet (een andere grootte of sha256, of geen gewoon bestand)",
}


def lines(found: monitor.AnalyseStatus) -> list[str]:
    problem = monitor.analyse_problem(found)
    out = [f"Analyse: {found.state}" + (f" ({problem})" if problem else "")]
    if found.caps is None:
        return out
    shown = state.view(found)
    for model, caps in zip(shown.models, found.caps.models, strict=True):
        out.append(f"  model {model.name} (versie {model.version}): {MODEL_TEXT[caps.state]}")
    if shown.limits is None:
        out.append("  grenzen: geen (de container heeft geen cgroup-grenzen)")
    else:
        out.append(f"  grenzen: {shown.limits.cpus:g} cpus, {shown.limits.mem_mb} MB geheugen")
    out.append("  opdrachten: " + ", ".join(found.caps.built))
    return out


async def check(settings: Settings, wait: float = 0) -> monitor.AnalyseStatus:
    loop = asyncio.get_running_loop()
    end = loop.time() + max(wait, 0)
    while True:
        found = await monitor.check(settings)
        if found.state in ("ok", "off") or loop.time() >= end:
            return found
        await asyncio.sleep(min(RETRY, max(end - loop.time(), 0)))


def exit_code(found: monitor.AnalyseStatus, wait: float) -> int:
    return 0 if wait > 0 or found.state in ("ok", "off") else 1


async def collect(settings: Settings, wait: float = 0) -> tuple[list[str], int]:
    found = await check(settings, wait)
    return lines(found), exit_code(found, wait)
