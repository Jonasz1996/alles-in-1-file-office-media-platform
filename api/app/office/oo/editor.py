"""De editorconfig en de callbacks van OnlyOffice (Keuze 11 en 15). O0-stub; O2 maakt het af.

editor_config: de config van §2 O2, getekend met tokens.browser_token (exp 2 uur).
read_callback: een callback lezen tot een CallbackEvent, alleen uit een geldig token
(tokens.callback_data), met mark = lastsave.
callback_reply: saved | same | copy | ignored -> (200, {"error": 0}); refused -> (200,
{"error": 1}); retry -> (503, {}).
present, checkpoint en drop: de commando's info, forcesave en drop.
"""

from collections.abc import Sequence
from typing import Literal

from app.config import Settings
from app.office.engine import CallbackEvent, OpenContext


def editor_config(settings: Settings, ctx: OpenContext) -> dict:
    raise NotImplementedError("O2")


def read_callback(settings: Settings, body: bytes, authorization: str | None) -> CallbackEvent:
    raise NotImplementedError("O2")


def callback_reply(outcome: str) -> tuple[int, dict]:
    raise NotImplementedError("O2")


async def present(key: str) -> list[str] | None:
    raise NotImplementedError("O2")


async def checkpoint(key: str, userdata: str) -> Literal["sent", "no_changes", "unknown"]:
    raise NotImplementedError("O2")


async def drop(key: str, ds_uids: Sequence[str] | None) -> None:
    raise NotImplementedError("O2")
