"""Een editor zonder browser: spreekt over de socket hetzelfde als static/editor.js.

Voor test_fake_docserver.py, en voor de Driver van O2 (tests/office_contract.py): direct naar
de nep-DS, of via de tunnel van de app (base met /office, plus de koppen van de browser).

    editor = EditorClient("ws://127.0.0.1:P", config)
    await editor.open()            # CONNECT met het token, auth, documentOpen, Editor.bin
    await editor.type("tekst")     # zijn eigen tekst, live naar de anderen
    await editor.save()            # de knop Opslaan (status 6, forcesavetype 1)
    await editor.reconnect()       # zoals na een breuk: met het sessietoken (Refused met
                                   # code 4008 of 4009 als DS de key dichtdeed of vergat)
    await editor.close()
"""

import asyncio
import contextlib
import json
import time
from typing import Any

import httpx
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed, InvalidStatus

from . import protocol


class Refused(Exception):
    """De socket werd geweigerd: bij de upgrade (status) of na het eerste pakket (code)."""

    def __init__(self, status: int | None = None, code: int | None = None) -> None:
        super().__init__(f"geweigerd: status {status}, code {code}")
        self.status = status
        self.code = code


class EditorClient:
    def __init__(
        self,
        base: str,
        config: dict,
        headers: dict[str, str] | None = None,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        self.base = base.rstrip("/")  # ws://host[:poort][prefix]
        self.config = config
        self.headers = dict(headers or {})
        self.http = http  # om Editor.bin op te halen (anders niet)
        self.session: str | None = None
        self.session_id: str | None = None
        self.index = -1
        self.known_save: float = -1
        self.ws: ClientConnection | None = None
        self.messages: list[dict] = []
        self.frames: list[tuple[str, str]] = []  # (richting, frame), voor de vormtests
        self.content: dict | None = None
        self._new = asyncio.Condition()
        self._reader: asyncio.Task | None = None
        self.reading = False
        self.closed_code: int | None = None

    @property
    def key(self) -> str:
        return self.config["document"]["key"]

    def url(self) -> str:
        key = self.key
        return f"{self.base}/{protocol.V}/doc/{key}/c/?shardkey={key}&EIO=4&transport=websocket"

    def _auth(self, first: bool) -> dict:
        doc = self.config.get("document", {})
        ed = self.config.get("editorConfig", {})
        user = ed.get("user", {})
        data: dict[str, Any] = {
            "type": "auth",
            "docid": doc.get("key"),
            "documentCallbackUrl": ed.get("callbackUrl"),
            "token": "fghhfgsjdgfjs",
            "user": {
                "id": user.get("id"),
                "username": user.get("name"),
                "firstname": None,
                "lastname": None,
                "indexUser": self.index,
            },
            "editorType": 0,
            "lastOtherSaveTime": self.known_save,
            "block": [],
            "sessionId": self.session_id,
            "sessionTimeConnect": None,
            "sessionTimeIdle": 0,
            "documentFormatSave": 65,
            "isCloseCoAuthoring": False,
        }
        if first:
            data["openCmd"] = {
                "c": "open",
                "id": doc.get("key"),
                "userid": user.get("id"),
                "format": doc.get("fileType"),
                "url": doc.get("url"),
                "title": doc.get("title"),
                "nobase64": True,
                "outputformat": 8193,
                "convertToOrigin": ".pdf.xps.oxps.djvu.ofd",
            }
        data.update(
            {
                "lang": ed.get("lang", "en"),
                "mode": ed.get("mode", "edit"),
                "permissions": doc.get("permissions", {}),
                "encrypted": False,
                "IsAnonymousUser": False,
                "timezoneOffset": 0,
                "headingsColor": None,
                "coEditingMode": "fast",
            }
        )
        if first or not self.session:
            data["jwtOpen"] = self.config.get("token")
        else:
            data["jwtSession"] = self.session
        data["time"] = int(time.monotonic() * 1000) % 100000
        data["supportAuthChangesAck"] = True
        return data

    async def _send(self, frame: str) -> None:
        assert self.ws is not None
        self.frames.append(("c2s", frame))
        await self.ws.send(frame)

    async def send_message(self, data: dict) -> None:
        await self._send(protocol.message(data))

    async def _read(self) -> None:
        assert self.ws is not None
        ws = self.ws
        try:
            async for frame in ws:
                if not isinstance(frame, str):
                    continue
                self.frames.append(("s2c", frame))
                kind, data = protocol.parse(frame)
                if kind == "ping":
                    with contextlib.suppress(ConnectionClosed):
                        await self._send("3" + (data or ""))
                    continue
                async with self._new:
                    if kind == "message" and isinstance(data, dict):
                        self.messages.append(data)
                        if data.get("type") == "documentSaved":
                            self.known_save = data.get("time", self.known_save)
                        elif data.get("type") == "refreshToken" and isinstance(
                            data.get("messages"), str
                        ):
                            self.session = data["messages"]
                    elif kind == "connect_error":
                        self.messages.append({"type": "connect_error", **(data or {})})
                    self._new.notify_all()
        except ConnectionClosed:
            pass
        finally:
            self.closed_code = ws.close_code
            async with self._new:
                self.reading = False
                self._new.notify_all()

    async def wait_for(
        self, kind: str | tuple[str, ...], seconds: float = 5.0, after: int = 0
    ) -> dict:
        """Het eerste bericht van deze soort (of een van deze soorten), vanaf index after."""
        kinds = (kind,) if isinstance(kind, str) else kind

        def found() -> dict | None:
            for m in self.messages[after:]:
                if m.get("type") in kinds:
                    return m
            return None

        async with asyncio.timeout(seconds), self._new:
            while (m := found()) is None:
                if not self.reading:
                    raise ConnectionError(f"socket dicht zonder {kind}")
                await self._new.wait()
            return m

    async def _connect(self, session: bool) -> dict:
        """Upgrade, CONNECT, auth; geeft het antwoord op auth."""
        try:
            self.ws = await connect(
                self.url(),
                additional_headers=self.headers,
                compression=None,
                open_timeout=5,
                max_size=None,
            )
        except InvalidStatus as exc:
            raise Refused(status=exc.response.status_code) from exc
        start = len(self.messages)
        opening = await asyncio.wait_for(self.ws.recv(), 5)
        assert isinstance(opening, str)
        self.frames.append(("s2c", opening))
        packet: dict[str, Any] = {"data": self._auth(True)}
        if session and self.session:
            packet["session"] = self.session
        else:
            packet["token"] = self.config.get("token")
        await self._send("40" + json.dumps(packet))
        self.reading = True
        self._reader = asyncio.ensure_future(self._read())
        try:
            await self.wait_for("license", after=start)
        except ConnectionError as exc:
            errors = [m for m in self.messages[start:] if m.get("type") == "connect_error"]
            code = (errors[0].get("data") or {}).get("code") if errors else None
            raise Refused(code=code) from exc
        await self.send_message(self._auth(not (session and self.session)))
        reply = await self.wait_for(("auth", "error"), after=start)
        if reply.get("type") == "error":
            # Bijvoorbeeld 4008 (de key is dicht) of 4009 (DS kent hem niet meer) [gemeten]
            raise Refused(code=reply.get("code"))
        if reply.get("jwt"):
            self.session = reply["jwt"]
        self.session_id = reply.get("sessionId")
        self.index = reply.get("indexUser", self.index)
        return reply

    async def open(self, load: bool = True) -> dict:
        """Opent het document; met load ook Editor.bin (via self.http)."""
        start = len(self.messages)
        reply = await self._connect(session=False)
        opened = await self.wait_for("documentOpen", after=start)
        data = opened.get("data") or {}
        if data.get("status") != "ok":
            raise Refused(code=data.get("data"))
        if load and self.http is not None:
            r = await self.http.get(data["data"]["Editor.bin"])
            r.raise_for_status()
            self.content = r.json()
        return reply

    async def reconnect(self) -> dict:
        """Zoals de editor na een breuk: met het sessietoken."""
        await self.drop_socket()
        return await self._connect(session=True)

    async def drop_socket(self) -> None:
        """De socket weg zonder afmelden (zoals een breuk)."""
        if self.ws is not None:
            await self.ws.close()
        if self._reader is not None:
            with contextlib.suppress(Exception):
                await self._reader
        self.ws = None

    async def type(self, text: str) -> dict:
        start = len(self.messages)
        await self.send_message({"type": "saveChanges", "text": text})
        return await self.wait_for("unSaveLock", after=start)

    async def save(self, seconds: float = 10.0) -> dict:
        start = len(self.messages)
        await self.send_message({"type": "forceSaveStart"})
        return await self.wait_for("forceSave", after=start, seconds=seconds)

    async def close(self) -> None:
        if self.ws is not None:
            with contextlib.suppress(ConnectionClosed):
                await self._send("41")
        await self.drop_socket()

    def texts_of_others(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for m in self.messages:
            if m.get("type") == "saveChanges":
                out[str(m.get("userid"))] = m.get("text", "")
        return out
