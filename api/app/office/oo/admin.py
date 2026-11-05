"""Beheercommando's van OnlyOffice via DocServer.command (O0, af).

Fouten van /command: 0 goed, 1 key onbekend, 4 geen wijzigingen, 5 fout commando, 6 fout
token. Een ander antwoord dan verwacht geeft DocServerError; error 6 op een getekend commando
TokenRejected (het office-geheim klopt niet met JWT_SECRET van DS). fonts_pending gebruikt geen
/command, maar twee HEAD's op statische bestanden.
"""

import secrets

from app.office import runtime, urls
from app.office.oo.client import DocServerError, get

# version zonder token en een conversie zonder token: zo weigert een DS die tokens eist
NO_TOKEN_COMMAND = 6
NO_TOKEN_CONVERT = -8

# Statische bestanden van DS (nginx, met Last-Modified): api.js schrijft
# documentserver-flush-cache bij elke start en na de fontbouw opnieuw; de fontbouw schrijft
# fonts_thumbnail.png elke keer opnieuw (AllFonts.js niet als de fonts gelijk bleven [gemeten])
API_JS = "/web-apps/apps/api/documents/api.js"
FONTS_THUMBNAIL = "/sdkjs/common/Images/fonts_thumbnail.png"


async def fonts_pending() -> bool:
    """Komt de font-herstart van DS nog (spike, extra bevinding 1)?

    Het startscript van DS schrijft eerst api.js (flush-cache) en bouwt dan de fonts
    (GENERATE_FONTS staat standaard aan, en een nieuwe container bouwt altijd): allfontsgen
    met fonts_thumbnail.png binnen een seconde, dan minuten aan thema's en caches, dan
    supervisorctl restart van docservice en de converter (open sessies zijn dan weg, zonder
    callback en zonder forgotten file), en pas dan opnieuw api.js. Tot die herstart voorbij is,
    is fonts_thumbnail.png er dus niet (niet in het image) of niet ouder dan api.js [gemeten,
    10.0.0.1, nieuwe container: api.js 17:55:05, de herstart 17:57:00-02, api.js 17:57:02;
    dezelfde container opnieuw gestart: api.js en fonts_thumbnail.png 18:00:14, de herstart
    18:02:06-08, api.js 18:02:08]. Zonder fontbouw (GENERATE_FONTS=false) is api.js nieuwer.
    Zonder api.js (of zijn Last-Modified) is het niet te zien: False. Het plaatje zonder
    Last-Modified telt als nog niet gebouwd (health.py wacht dan hooguit SETTLING_SECONDS)."""
    server = get()
    api = await server.modified(API_JS)
    if api is None:
        return False
    fonts = await server.modified(FONTS_THUMBNAIL)
    return fonts is None or fonts >= api


class TokenRejected(DocServerError):
    """DS weigerde ons getekende commando (error 6): het geheim klopt niet."""


def _ok(answer: dict, what: str) -> dict:
    error = answer.get("error")
    if error == NO_TOKEN_COMMAND:
        raise TokenRejected(f"{what}: error {error} (DS weigert ons token)")
    if error != 0:
        raise DocServerError(f"{what}: error {error}")
    return answer


async def healthcheck() -> bool:
    return await get().healthy()


async def version() -> str:
    answer = _ok(await get().command("version"), "version")
    value = answer.get("version")
    if not isinstance(value, str):
        raise DocServerError("version: geen versie")
    return value


async def license() -> int | None:
    """Het aantal gelijktijdige verbindingen volgens de licentie (2147483647 = geen limiet)."""
    answer = _ok(await get().command("license"), "license")
    connections = (answer.get("license") or {}).get("connections")
    return connections if isinstance(connections, int) else None


async def forgotten_list() -> list[str]:
    answer = _ok(await get().command("getForgottenList"), "getForgottenList")
    keys = answer.get("keys") or []
    return [k for k in keys if isinstance(k, str)]


async def forgotten_url(key: str) -> str:
    """Het adres van een forgotten file, zoals DS het geeft (nog niet herschreven)."""
    answer = _ok(await get().command("getForgotten", key=key), "getForgotten")
    url = answer.get("url")
    if not isinstance(url, str):
        raise DocServerError("getForgotten: geen url")
    return url


async def delete_forgotten(key: str) -> None:
    _ok(await get().command("deleteForgotten", key=key), "deleteForgotten")


async def tokens_enforced() -> bool:
    """Eist DS een token (Keuze 28)? version zonder token moet error 6 geven, en een
    conversie zonder token -8. De conversie heeft een key die nooit terugkomt en een bron
    die niet bestaat: komt DS toch tot ophalen, dan weigert onze interne app."""
    server = get()
    answer = await server.command("version", signed=False)
    if answer.get("error") != NO_TOKEN_COMMAND:
        return False
    params = {
        "async": False,
        "filetype": "docx",
        "outputtype": "pdf",
        "key": f"tokencheck-{secrets.token_hex(8)}",
        "title": "tokencheck.docx",
        "url": urls.internal_url(runtime.settings(), "/internal/office/src/0"),
    }
    answer = await server.convert(signed=False, **params)
    return answer.get("error") == NO_TOKEN_CONVERT
