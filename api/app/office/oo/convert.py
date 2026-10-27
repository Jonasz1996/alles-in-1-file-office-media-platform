"""Conversie naar PDF via POST /converter (Keuze 25, §3.9 #12 en #13).

convert stuurt async: true (met Accept: application/json en ?shardkey=<key>, zie
DocServer.convert en A18) en vraagt daarna elke seconde opnieuw met dezelfde parameters, tot
DS endConvert meldt, hooguit office_convert_timeout seconden. Daarna herschrijft hij fileUrl
naar PLATFORM_OFFICE_URL (urls.rewrite, soort "conv": het pad moet bij onze key horen; de
host uit het antwoord volgen we nooit) en haalt hij de PDF op naar out, hooguit limit bytes.
DS werkt na onze tijdslimiet gewoon door: dezelfde key later opnieuw vragen geeft dan het
resultaat.

Elke fout wordt een ConvertError(code, kind), zoals de naad belooft (engine.Engine.convert):

    code  van           kind           wat de kern doet
    -2    DS (tijd)     new_key        later opnieuw met een nieuwe key (DS bewaart dit antwoord)
    -4    DS (download) new_key
    -3, -7, -9, -10     failed         blijvend mislukt
    -5    DS (wachtwoord) none         dit bestand kan niet
    -1, -6              retry          een gewone poging
    -8    DS (token)    misconfigured  DS eist geen of een ander token
    andere negatieve    retry
    1     onze tijdslimiet             retry (dezelfde key later)
    2     DS antwoordt niet of niet met 200, of niet met JSON
                                       retry
    3     de PDF is groter dan limit   failed
    4     een antwoord zonder bruikbaar fileUrl, of een adres dat niet bij de key past
                                       failed

De positieve codes zijn van ons (de adapter), de negatieve van DS.
"""

from pathlib import Path

import anyio

from app.config import Settings
from app.office import urls
from app.office.engine import ConvertError
from app.office.oo import client

POLL_SECONDS = 1.0

TIMEOUT = 1  # onze eigen tijdslimiet
UNREACHABLE = 2  # geen (bruikbaar) antwoord van DS
TOO_LARGE = 3  # de PDF is te groot
BAD_RESULT = 4  # geen fileUrl, of een adres dat niet bij de key past

KINDS = {
    -1: "retry",
    -2: "new_key",
    -3: "failed",
    -4: "new_key",
    -5: "none",
    -6: "retry",
    -7: "failed",
    -8: "misconfigured",
    -9: "failed",
    -10: "failed",
}


def error_of(code: int) -> ConvertError:
    """De ConvertError voor een foutcode van DS."""
    return ConvertError(code, KINDS.get(code, "retry"))  # type: ignore[arg-type]


async def _ask(server: client.DocServer, params: dict) -> dict:
    try:
        answer = await server.convert(**params)
    except client.DocServerError as exc:
        raise ConvertError(UNREACHABLE, "retry") from exc
    error = answer.get("error")
    if isinstance(error, int) and not isinstance(error, bool) and error != 0:
        raise error_of(error)
    return answer


async def convert(
    settings: Settings, src_url: str, ext: str, key: str, title: str, out: Path, limit: int
) -> None:
    """Zet de bron op src_url (soort ext) om naar PDF in out. ConvertError bij elke fout; dan
    staat out er niet."""
    params = {
        "async": True,
        "filetype": ext,
        "key": key,
        "outputtype": "pdf",
        "title": title,
        "url": src_url,
    }
    server = client.get()
    answer: dict = {}
    with anyio.move_on_after(max(0.0, settings.office_convert_timeout)) as scope:
        while True:
            answer = await _ask(server, params)
            if answer.get("endConvert") is True:
                break
            await anyio.sleep(POLL_SECONDS)
    if scope.cancelled_caught:
        raise ConvertError(TIMEOUT, "retry")
    file_url = answer.get("fileUrl")
    file_type = answer.get("fileType", "pdf")
    if not isinstance(file_url, str) or str(file_type).lower() != "pdf":
        raise ConvertError(BAD_RESULT, "failed")
    try:
        target = urls.rewrite(settings, file_url, key=key, kind="conv")
    except ValueError as exc:
        raise ConvertError(BAD_RESULT, "failed") from exc
    try:
        await server.fetch(target, out, limit)
    except client.TooLarge as exc:
        raise ConvertError(TOO_LARGE, "failed") from exc
    except client.DocServerError as exc:
        raise ConvertError(UNREACHABLE, "retry") from exc
