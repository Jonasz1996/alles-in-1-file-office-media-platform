"""De adapter voor OnlyOffice Docs 10.0 (Keuze 1 en 3): OnlyOffice bundelt
oo/paths.py en oo/stream.py (O1), oo/editor.py (O2), oo/convert.py (O3) en oo/admin.py
(O0, af). Buiten deze map importeert alleen app/office/engine.py dit.
"""

from collections.abc import Sequence
from pathlib import Path
from typing import Literal

from app.config import Settings
from app.office import urls
from app.office.engine import (
    CallbackEvent,
    Health,
    OpenContext,
    PathClass,
    StreamGuard,
    TokenError,
)
from app.office.oo import admin, client, editor, paths, stream, tokens
from app.office.oo import convert as conversion

EDIT_FORMATS = frozenset({"docx", "xlsx", "pptx"})
CONVERT_FORMATS = frozenset(
    {"docx", "xlsx", "pptx", "odt", "ods", "odp", "doc", "xls", "ppt", "rtf"}
    | {"docm", "xlsm", "pptm"}
)


class OnlyOffice:
    name = "onlyoffice"
    strip_prefix = True
    edit_formats = EDIT_FORMATS
    convert_formats = CONVERT_FORMATS

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def classify(self, method: str, path: str, upgrade: bool) -> PathClass | None:
        return paths.classify(method, path, upgrade)

    def session_key(self, path: str) -> str | None:
        return paths.key_of(path)

    def stream_guard(self, key: str) -> StreamGuard:
        return stream.Guard(self.settings, key)

    def editor_config(self, ctx: OpenContext) -> dict:
        return editor.editor_config(self.settings, ctx)

    def read_callback(self, body: bytes, authorization: str | None) -> CallbackEvent:
        return editor.read_callback(self.settings, body, authorization)

    def callback_reply(self, outcome: str) -> tuple[int, dict]:
        return editor.callback_reply(outcome)

    def check_download(self, authorization: str | None, url: str) -> None:
        tokens.check_download(self.settings, authorization, url)

    async def health(self, deep: bool = True) -> Health:
        """deep=False: alleen /healthcheck. Anders ook version en license (getekend);
        TokenError als DS die met error 6 weigert."""
        if not await admin.healthcheck():
            return Health(ok=False)
        if not deep:
            return Health(ok=True)
        try:
            return Health(ok=True, version=await admin.version(), limit=await admin.license())
        except admin.TokenRejected as exc:
            raise TokenError(str(exc)) from exc

    async def enforces_tokens(self) -> bool:
        return await admin.tokens_enforced()

    async def present(self, key: str) -> list[str] | None:
        return await editor.present(key)

    async def checkpoint(self, key: str, userdata: str) -> Literal["sent", "no_changes", "unknown"]:
        return await editor.checkpoint(key, userdata)

    async def drop(self, key: str, ds_uids: Sequence[str] | None) -> None:
        await editor.drop(key, ds_uids)

    async def convert(
        self, src_url: str, ext: str, key: str, title: str, out: Path, limit: int
    ) -> None:
        await conversion.convert(self.settings, src_url, ext, key, title, out, limit)

    async def forgotten(self) -> list[str]:
        return await admin.forgotten_list()

    async def fetch_forgotten(self, key: str, out: Path, limit: int) -> tuple[str, int]:
        url = urls.rewrite(self.settings, await admin.forgotten_url(key), key=key, kind="forgotten")
        return await client.get().fetch(url, out, limit)

    async def delete_forgotten(self, key: str) -> None:
        await admin.delete_forgotten(key)
