"""Beheercommando's van OnlyOffice via DocServer.command (O0, af).

Fouten van /command: 0 goed, 1 key onbekend, 4 geen wijzigingen, 5 fout commando, 6 fout
token. Een ander antwoord dan verwacht geeft DocServerError.
"""

import secrets

from app.office import runtime, urls
from app.office.oo.client import DocServerError, get

# version zonder token en een conversie zonder token: zo weigert een DS die tokens eist
NO_TOKEN_COMMAND = 6
NO_TOKEN_CONVERT = -8


def _ok(answer: dict, what: str) -> dict:
    if answer.get("error") != 0:
        raise DocServerError(f"{what}: error {answer.get('error')}")
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
