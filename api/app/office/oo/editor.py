"""De editorconfig en de callbacks van OnlyOffice (Keuze 11 en 15), en de commando's info,
forcesave en drop.

editor_config: de config van §2 O2, getekend met tokens.browser_token (exp 2 uur). Uit de
spike: geen close.text (DS kent zelf een tekst per lang, A15), wel suggestFeature: false (A16);
uiTheme "theme-dark" voor donker en geen sleutel voor licht (stap 6).
read_callback: een callback lezen tot een CallbackEvent, alleen uit een geldig token
(tokens.callback_data), met mark = lastsave (A9: hele seconden, de kern neemt aan bij >=).
callback_reply: saved | same | copy | ignored -> (200, {"error": 0}); refused -> (200,
{"error": 1}); retry -> (503, {}).
present, checkpoint en drop: info, forcesave en drop via DocServer.command.
"""

import json
from collections.abc import Sequence
from typing import Literal
from urllib.parse import urlsplit

from app.config import Settings
from app.office import keys
from app.office.engine import CallbackEvent, OpenContext, TokenError
from app.office.oo import tokens
from app.office.oo.client import DocServerError, get

DOCUMENT_TYPES = {"docx": "word", "xlsx": "cell", "pptx": "slide"}
MAX_TITLE = 128
MAX_MARK = 40
# Hoe groot een callback mag zijn (gemeten: een paar KB; history kan groeien)
MAX_BODY = 1024 * 1024

KINDS: dict[int, str] = {
    1: "presence",
    2: "final",
    3: "failed",
    4: "closed",
    6: "checkpoint",
    7: "checkpoint_failed",
}
# Fouten van /command (oo/admin.py)
CMD_OK, CMD_KEY, CMD_NO_CHANGES = 0, 1, 4
BY_BUTTON = 1  # forcesavetype van de knop Opslaan [gemeten, spike stap 3]


def editor_config(settings: Settings, ctx: OpenContext) -> dict:
    customization: dict = {
        "autosave": True,
        "forcesave": True,
        "feedback": False,
        "help": False,
        "plugins": False,
        "macros": False,
        "macrosMode": "disable",
        "suggestFeature": False,
        "close": {"visible": True},
    }
    if ctx.close_text:
        customization["close"]["text"] = ctx.close_text
    if ctx.theme == "dark":
        customization["uiTheme"] = "theme-dark"
    config = {
        "documentType": DOCUMENT_TYPES.get(ctx.file_type, "word"),
        "type": "mobile" if ctx.mobile else "desktop",
        "width": "100%",
        "height": "100%",
        "document": {
            "fileType": ctx.file_type,
            "key": ctx.key,
            "title": ctx.title[:MAX_TITLE],
            "url": ctx.doc_url,
            "permissions": {
                "edit": True,
                "download": True,
                "print": True,
                "comment": True,
                "review": True,
                "copy": True,
                "fillForms": True,
            },
        },
        "editorConfig": {
            "mode": "edit",
            "lang": ctx.lang,
            "region": ctx.region,
            "callbackUrl": ctx.callback_url,
            "user": {"id": ctx.ds_uid, "name": ctx.user_name[:MAX_TITLE]},
            "coEditing": {"mode": "fast", "change": True},
            "customization": customization,
        },
    }
    config["token"] = tokens.browser_token(settings, config)
    return config


def _path_only(url: object) -> str | None:
    """Alleen pad en query: de host van DS (die van de browser) volgen we nooit (Keuze 13)."""
    if not isinstance(url, str) or not url:
        return None
    parts = urlsplit(url)
    if not parts.path:
        return None
    return parts.path + (f"?{parts.query}" if parts.query else "")


def _ids(values: object) -> list[str]:
    return [v for v in values if isinstance(v, str) and v] if isinstance(values, list) else []


def _editors(fields: dict) -> list[str]:
    """De bewerkers, de laatste eerst: history.changes van achter naar voren, dan actions."""
    found: list[str] = []
    history = fields.get("history")
    changes = history.get("changes") if isinstance(history, dict) else None
    if isinstance(changes, list):
        for change in reversed(changes):
            user = change.get("user") if isinstance(change, dict) else None
            uid = user.get("id") if isinstance(user, dict) else None
            if isinstance(uid, str) and uid and uid not in found:
                found.append(uid)
    actions = fields.get("actions")
    if isinstance(actions, list):
        for action in actions:
            uid = action.get("userid") if isinstance(action, dict) else None
            if isinstance(uid, str) and uid and uid not in found:
                found.append(uid)
    return found


def read_callback(settings: Settings, body: bytes, authorization: str | None) -> CallbackEvent:
    if len(body) > MAX_BODY:
        raise TokenError("te groot")
    try:
        data = json.loads(body or b"{}")
    except ValueError as exc:
        raise TokenError("geen JSON") from exc
    # Alleen wat in het token staat telt; losse velden in de body nooit
    fields = tokens.callback_data(settings, data, authorization)
    key = fields.get("key")
    if not isinstance(key, str) or not keys.valid(key):
        raise TokenError("geen geldige key")
    status = fields.get("status")
    kind = KINDS.get(status, "other") if isinstance(status, int) else "other"
    mark = fields.get("lastsave")
    filetype = fields.get("filetype")
    return CallbackEvent(
        kind=kind,  # type: ignore[arg-type]
        key=key,
        url=_path_only(fields.get("url")),
        connected=_ids(fields.get("users")),
        editors=_editors(fields),
        explicit=fields.get("forcesavetype") == BY_BUTTON,
        filetype=filetype if isinstance(filetype, str) else None,
        mark=mark if isinstance(mark, str) and 0 < len(mark) <= MAX_MARK else None,
    )


def callback_reply(outcome: str) -> tuple[int, dict]:
    if outcome in ("saved", "same", "copy", "ignored"):
        return 200, {"error": 0}
    if outcome == "refused":
        return 200, {"error": 1}
    if outcome == "retry":
        return 503, {}
    raise ValueError(f"onbekende uitkomst {outcome}")


async def present(key: str) -> list[str] | None:
    """info: wie DS op deze key kent ([] als DS de key dichtdeed), of None als DS hem niet
    (meer) kent. Let op: elke info laat DS een callback sturen (status 1, of 4 op een dichte
    key) [gemeten]."""
    answer = await get().command("info", key=key)
    error = answer.get("error")
    if error == CMD_KEY:
        return None
    if error != CMD_OK:
        raise DocServerError(f"info: error {error}")
    return _ids(answer.get("users"))


async def checkpoint(key: str, userdata: str) -> Literal["sent", "no_changes", "unknown"]:
    """forcesave: "sent" (status 6 volgt), "no_changes" (error 4) of "unknown" (error 1)."""
    answer = await get().command("forcesave", key=key, userdata=userdata)
    error = answer.get("error")
    if error == CMD_OK:
        return "sent"
    if error == CMD_NO_CHANGES:
        return "no_changes"
    if error == CMD_KEY:
        return "unknown"
    raise DocServerError(f"forcesave: error {error}")


async def drop(key: str, ds_uids: Sequence[str] | None) -> None:
    """drop: deze gebruikers (None: iedereen) uit de editor. Het antwoord zegt niets (A19: ook
    een onbekende key geeft error 0)."""
    params: dict = {"key": key}
    if ds_uids is not None:
        params["users"] = list(ds_uids)
    await get().command("drop", **params)
