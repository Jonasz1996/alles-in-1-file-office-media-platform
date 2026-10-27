"""De nep-DS: een kleine ASGI-app die het protocol van DS 10.0 spreekt zoals gemeten (§3.6).

De kern (O0): tokens, callbacks 1, 2, 4 en 6, /command (info, forcesave, drop, version,
license), de editor (api.js, de editorpagina en de socket), secure links onder /cache/files,
en /__fake/* om hem te sturen. Twee delen komen later en haken in zonder deze module te
veranderen: forgotten files (forgotten.py, O2) en conversie (convert.py en pdf.py, O3). Elk
deel heeft een functie install(fake, app); create_app roept die aan als de module er is.

Haken voor die delen (en voor tests):
- fake.commands: c -> async handler(params) -> antwoord (een dict);
- fake.convert: async handler(request, params) -> Response, voor /converter;
- fake.on_lost: async hook(doc, fields) als een opslag (status 2) de app niet bereikt
  (429 of 5xx na alle pogingen, of een antwoord {"error": 1}): dan hoort hij bij de
  forgotten files;
- fake.on_reset: hook(reden) bij kill, stop en restart (en de font-herstart: kill).

De fonts (spike, extra bevinding 1): api.js en fonts_thumbnail.png hebben een Last-Modified
zoals bij nginx van DS. Standaard is de nep een DS die al lang draait (api.js nieuwer).
/__fake/fonts {"pending": true} is een DS vlak na de start (allebei net geschreven, de
font-herstart komt nog); {"pending": false} is die herstart zelf: alle sessies weg zonder
callback (zoals kill, maar hij blijft op), daarna een nieuwe api.js. restart is een herstart
zonder fontbouw (zoals GENERATE_FONTS=false): alleen een nieuwe api.js.

In pytest gaat de nep met httpx.ASGITransport, of als echte server (uvicorn) voor de socket.
Callbacks en downloads gaan naar de app met config.transport (in pytest een ASGITransport naar
de interne app), anders over het netwerk.

Wat de nep zelf verzint, omdat de echte binnenin anders werkt (de eerste pakketten en de
velden van callbacks, downloads en commando's zijn wel zoals gemeten):
- de tekst: elke gebruiker heeft zijn eigen tekst (c2s "saveChanges" {text}); de anderen
  krijgen hem live (s2c "saveChanges" {userid, name, text}); een opgeslagen docx heeft de
  alinea's van het origineel en dan één alinea per gebruiker;
- Editor.bin is JSON (de alinea's en de teksten), op een echte secure link;
- "documentSaved" {time} naar iedereen na een geslaagde opslag; een editor die opnieuw
  verbindt met een oudere lastOtherSaveTime, of opent op een key die al is opgeslagen
  (status 2), krijgt "refreshFile" en meldt onRequestRefreshFile (M17). Herverbinden (met
  het sessietoken) op een key die DS al dichtdeed, geeft wel zoals gemeten "refreshToken" en
  error 4008 "Update Version error"; op een key die DS niet (meer) kent, bijvoorbeeld na een
  herstart, "refreshToken" en error 4009 "Other error" (spike stap 14 en 15). De nep-editor
  meldt dan onRequestRefreshFile, zoals de echte;
- de knop Opslaan stuurt "forceSaveStart"; het antwoord is "forceSave" {messages: {ended,
  success}};
- drop stuurt "drop" en sluit de socket; de nep-editor verbindt dan niet zelf opnieuw;
- onError -20 als het token niet klopt, -21 als het verlopen is (DS: VKeyEncrypt en
  KeyExpire), -4 als het document niet op te halen is.
"""

import asyncio
import contextlib
import hashlib
import importlib
import json
import logging
import re
import secrets
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from email.utils import formatdate
from pathlib import Path
from typing import Any

import httpx
import jwt
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, Response

from . import docx, protocol

log = logging.getLogger("fake_docserver")

STATIC = Path(__file__).parent / "static"
EDITORS = ("documenteditor", "spreadsheeteditor", "presentationeditor")
PREFIX_RE = re.compile(r"^(?:/[A-Za-z0-9._~-]+)*$")
HOST_RE = re.compile(r"^[A-Za-z0-9.\-]+(?::\d{1,5})?$|^\[[0-9A-Fa-f:.]+\](?::\d{1,5})?$")
RECEIVE_SECONDS = 10.0
# Zoveel dichte keys onthoudt de nep (de echte kent ze nog lang na het sluiten [gemeten])
DONE_KEYS = 10_000
# Herverbinden op een dichte of onbekende key [gemeten, spike stap 14 en 15]
UPDATE_VERSION = (4008, "Update Version error")
OTHER_ERROR = (4009, "Other error")
# Wat nooit in de log komt
SECRET_QUERY = re.compile(r"((?:^|[?&])(?:md5|token)=)[^&]*")


def _query_safe(text: str) -> str:
    return SECRET_QUERY.sub(r"\1…", text)


@dataclass
class FakeConfig:
    secret: str  # het office-geheim (PLATFORM_OFFICE_SECRET)
    link_secret: str | None = None  # vast linkgeheim; None = willekeurig, nieuw bij restart
    time_scale: float = 1.0  # maal de pauzes tussen pogingen
    save_delay: float = 5.0  # na de laatste sluiter tot status 2 (of 4)
    retries: tuple[float, ...] = (1.0, 2.0, 4.0)  # pauzes bij 429 en 5xx
    connections: int = protocol.UNLIMITED  # wat command license zegt
    transport: httpx.AsyncBaseTransport | None = None  # naar de app: callbacks en downloads


@dataclass(eq=False)
class Conn:
    ws: WebSocket
    key: str
    sid: str
    user: dict
    index: int
    mode: str
    base: str  # http(s)://host + prefix van de browser, voor de links
    known_save: float = -1  # lastOtherSaveTime van de editor (ms)


@dataclass(eq=False)
class Doc:
    key: str
    url: str
    callback: str
    filetype: str
    title: str
    permissions: dict
    base: str
    original: bytes = b""
    paragraphs: list[str] = field(default_factory=list)
    texts: dict[str, str] = field(default_factory=dict)  # DS-gebruiker -> zijn tekst
    names: dict[str, str] = field(default_factory=dict)
    changed_at: dict[str, float] = field(default_factory=dict)
    conns: list[Conn] = field(default_factory=list)
    dirty: bool = False  # wijzigingen sinds de laatste geslaagde opslag
    ever: bool = False  # ooit iets veranderd
    last_change: float = 0.0
    last_editor: str | None = None
    last_save: float = -1  # ms van de laatste geslaagde opslag
    open_error: int | None = None
    opened: asyncio.Event = field(default_factory=asyncio.Event)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)  # callbacks in volgorde
    closing: asyncio.Task | None = None
    dropping: bool = False
    finished: bool = False
    next_index: int = 1

    def users(self) -> list[str]:
        seen: list[str] = []
        for c in self.conns:
            if c.user["id"] not in seen:
                seen.append(c.user["id"])
        return seen


class FakeDocServer:
    def __init__(self, config: FakeConfig) -> None:
        self.config = config
        self.secret = config.secret
        self.link_secret = config.link_secret or secrets.token_urlsafe(16)
        self.docs: dict[str, Doc] = {}
        self.saved: dict[str, float] = {}  # keys met een geaccepteerde status 2
        # Keys die DS dichtdeed (status 2 of 4) en nog kent: info geeft dan error 0 zonder
        # gebruikers (en een status 4), forcesave error 4 [gemeten]. Niet na een opslag die
        # de app niet bereikte (forgotten file: error 1, zoals de echte), en niet na een kill
        # of restart (een leeg geheugen)
        self.done: dict[str, Doc] = {}
        self.files: dict[str, dict[str, bytes]] = {}  # "<key>_<n>" -> {naam: inhoud}
        self.entries: list[dict] = []
        self.up = True
        self.starting = False
        # Wanneer api.js (flush-cache) en fonts_thumbnail.png (de fontbouw) geschreven zijn
        self.flushed = time.time()
        self.fonts_built = self.flushed - 3600
        self.jwt = True
        self.connections = config.connections
        self.counter = 1000 + secrets.randbelow(8000)
        self.tasks: set[asyncio.Task] = set()
        self.commands: dict[str, Callable[[dict], Awaitable[dict]]] = {
            "info": self._info,
            "forcesave": self._forcesave,
            "drop": self._drop,
            "version": self._version,
            "license": self._license,
        }
        self.convert: Callable[[Request, dict], Awaitable[Response]] | None = None
        self.on_lost: list[Callable[[Doc, dict], Awaitable[None]]] = []
        self.on_reset: list[Callable[[str], None]] = []
        self._http: httpx.AsyncClient | None = None

    # --- algemeen ---

    @property
    def http(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(
                transport=self.config.transport,
                trust_env=False,
                follow_redirects=False,
                timeout=httpx.Timeout(30.0),
            )
        return self._http

    async def aclose(self) -> None:
        for task in list(self.tasks):
            task.cancel()
        for task in list(self.tasks):
            with contextlib.suppress(BaseException):
                await task
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    def spawn(self, coro: Awaitable) -> asyncio.Task:
        task = asyncio.ensure_future(coro)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return task

    def record(self, kind: str, **fields: Any) -> None:
        """Eén regel in de log (zonder geheimen: geen tokens, geen md5)."""
        entry = {"t": time.time(), "kind": kind, **fields}
        self.entries.append(entry)
        if len(self.entries) > 5000:
            del self.entries[:1000]

    def remember(self, doc: Doc) -> None:
        self.done.pop(doc.key, None)
        self.done[doc.key] = doc
        while len(self.done) > DONE_KEYS:
            del self.done[next(iter(self.done))]

    def next_part(self, key: str) -> str:
        self.counter += 1
        return f"{key}_{self.counter}"

    # --- tokens ---

    def _params(self, body: dict, authorization: str | None, command: bool) -> dict | None:
        """De parameters uit het token (body of header); None als er geen geldig token is.
        Met jwt-off neemt hij alles aan."""
        if not self.jwt:
            return {k: v for k, v in body.items() if k != "token"}
        token = body.get("token")
        try:
            if isinstance(token, str) and token:
                claims = protocol.decode(self.secret, token)
                params = {k: v for k, v in claims.items() if k not in ("iat", "exp")}
            else:
                header = protocol.bearer(authorization)
                if header is None:
                    return None
                claims = protocol.decode(self.secret, header)
                params = claims.get("payload")
                if not isinstance(params, dict):
                    return None
        except jwt.PyJWTError:
            return None
        if command and not isinstance(params.get("c"), str):
            return None  # bijvoorbeeld een config-token: error 6, zoals de echte
        return params

    def _open_claims(self, key: str, token: Any, session: Any) -> tuple[dict | None, int | None]:
        """(claims, None) of (None, foutcode) voor het token van CONNECT of auth."""
        if not self.jwt:
            raw = token if isinstance(token, str) else session
            if isinstance(raw, str):
                with contextlib.suppress(jwt.PyJWTError):
                    claims = jwt.decode(raw, options={"verify_signature": False})
                    if isinstance(claims, dict):
                        return claims, None
            return {}, None
        if isinstance(token, str) and token:
            secret, raw = self.secret, token
        elif isinstance(session, str) and session:
            secret, raw = protocol.session_secret(self.secret), session
        else:
            return None, protocol.ERR_TOKEN
        try:
            claims = protocol.decode(secret, raw)
        except jwt.ExpiredSignatureError:
            return None, protocol.ERR_TOKEN_EXPIRED
        except jwt.PyJWTError:
            return None, protocol.ERR_TOKEN
        document = claims.get("document")
        if not isinstance(document, dict) or document.get("key") != key:
            return None, protocol.ERR_TOKEN
        return claims, None

    # --- callbacks en downloads ---

    async def post_callback(self, doc: Doc, fields: dict) -> bool:
        """Eén callback, met de pogingen van DS: bij 429, 5xx of geen verbinding opnieuw na
        1, 2 en 4 s (maal time_scale); {"error": 1} of een ander antwoord niet opnieuw."""
        body, authorization = protocol.callback_request(self.secret, fields)
        waits = list(self.config.retries)
        while True:
            status: int | None = None
            answer: Any = None
            try:
                r = await self.http.post(
                    doc.callback,
                    content=json.dumps(body).encode(),
                    headers={
                        "Authorization": authorization,
                        "Content-Type": "application/json",
                        "User-Agent": "DocumentServer",
                    },
                )
                status = r.status_code
                with contextlib.suppress(ValueError):
                    answer = r.json()
            except httpx.HTTPError as exc:
                answer = type(exc).__name__
            self.record(
                "callback",
                key=doc.key,
                status=fields.get("status"),
                http=status,
                answer=answer,
                fields=sorted(fields),
            )
            if status == 200 and isinstance(answer, dict) and answer.get("error") == 0:
                return True
            if status is not None and status != 429 and status < 500:
                return False
            if not waits:
                return False
            await asyncio.sleep(waits.pop(0) * self.config.time_scale)

    async def send(self, doc: Doc, fields: dict) -> bool:
        async with doc.lock:
            return await self.post_callback(doc, fields)

    async def download(self, doc: Doc) -> int | None:
        """Haalt document.url op met het download-JWT; None als het lukte, anders -4."""
        token = protocol.download_token(self.secret, doc.url)
        headers = {**protocol.DOWNLOAD_HEADERS, "Authorization": f"Bearer {token}"}
        try:
            r = await self.http.get(doc.url, headers=headers)
        except httpx.HTTPError as exc:
            self.record("download", key=doc.key, http=None, error=type(exc).__name__)
            return protocol.ERR_DOWNLOAD
        digest = r.headers.get("repr-digest")
        ok = r.status_code == 200 and protocol.repr_digest_ok(digest, r.content)
        self.record(
            "download", key=doc.key, http=r.status_code, bytes=len(r.content), digest=digest
        )
        if not ok:
            return protocol.ERR_DOWNLOAD
        doc.original = r.content
        doc.paragraphs = docx.paragraphs(r.content) if doc.filetype == "docx" else []
        return None

    # --- opslaan ---

    def output(self, doc: Doc) -> bytes:
        texts = [doc.texts[u] for u in doc.texts if doc.texts[u]]
        if doc.filetype == "docx":
            return docx.make_docx(doc.paragraphs + texts)
        if doc.filetype in ("xlsx", "pptx") and doc.original:
            with contextlib.suppress(Exception):
                return docx.with_text(doc.original, "\n".join(texts))
        return doc.original

    def store(self, doc: Doc) -> tuple[str, str, list[dict]]:
        """Zet de uitvoer klaar onder /cache/files/data/<key>_<n>/; (url, changesurl,
        history.changes)."""
        part = self.next_part(doc.key)
        name = f"output.{doc.filetype}"
        content = self.output(doc)
        changes = [
            {
                "documentSha256": hashlib.sha256(doc.original).hexdigest(),
                "created": protocol.created(doc.changed_at[u]),
                "user": {"id": u, "name": doc.names.get(u, u)},
            }
            for u in sorted(doc.changed_at, key=lambda u: doc.changed_at[u])
        ]
        self.files[part] = {name: content, "changes.zip": docx.changes_zip(changes)}
        url = protocol.secure_link(
            doc.base, self.link_secret, f"/cache/files/data/{part}/{name}/{name}", doc.key, name
        )
        changesurl = protocol.secure_link(
            doc.base,
            self.link_secret,
            f"/cache/files/data/{part}/changes.zip/changes.zip",
            doc.key,
            "changes.zip",
        )
        return url, changesurl, changes

    async def forcesave(
        self, doc: Doc, kind: int, user: str | None = None, userdata: str | None = None
    ) -> bool:
        """Status 6. Zonder wijzigingen sinds de laatste opslag: niets (en True)."""
        async with doc.lock:
            if not doc.dirty or doc.finished:
                return True
            doc.dirty = False
            url, changesurl, changes = self.store(doc)
            fields = protocol.saved(
                doc.key,
                protocol.FORCESAVE,
                url=url,
                changesurl=changesurl,
                changes=changes,
                users=[doc.last_editor] if doc.last_editor else [],
                lastsave_at=doc.last_change,
                filetype=doc.filetype,
                action=(protocol.FORCESAVE_BUTTON, user) if kind == protocol.BY_BUTTON else None,
                forcesavetype=kind,
                userdata=userdata,
            )
            ok = await self.post_callback(doc, fields)
            if ok:
                doc.last_save = time.time() * 1000
                await self.broadcast(doc, {"type": "documentSaved", "time": int(doc.last_save)})
            else:
                doc.dirty = True  # blijft open: de laatste opslag neemt het mee
            return ok

    async def finish(self, doc: Doc, action: tuple[int, str] | None) -> None:
        """De laatste is weg (of drop, of stop): status 2 als er ooit iets veranderde (met
        notmodified als alles al was opgeslagen), anders status 4. Daarna is de key dicht: DS
        kent hem nog (self.done), tot de opslag de app niet bereikt (forgotten file)."""
        async with doc.lock:
            if doc.finished:
                return
            doc.finished = True
            if self.docs.get(doc.key) is doc:
                del self.docs[doc.key]
            self.remember(doc)
            if not doc.ever:
                await self.post_callback(doc, protocol.closed(doc.key, action))
                return
            url, changesurl, changes = self.store(doc)
            fields = protocol.saved(
                doc.key,
                protocol.SAVE,
                url=url,
                changesurl=changesurl,
                changes=changes,
                users=[doc.last_editor] if doc.last_editor else [],
                lastsave_at=doc.last_change,
                filetype=doc.filetype,
                action=action,
                notmodified=not doc.dirty,
            )
            if await self.post_callback(doc, fields):
                self.saved[doc.key] = time.time() * 1000
                return
            if self.done.get(doc.key) is doc:
                del self.done[doc.key]  # info en forcesave geven dan error 1 [gemeten]
            for hook in self.on_lost:
                await hook(doc, fields)

    async def _close_later(self, doc: Doc, userid: str) -> None:
        await asyncio.sleep(self.config.save_delay)
        if not doc.conns and not doc.finished:
            await self.finish(doc, (protocol.DISCONNECT, userid))

    # --- de socket ---

    async def broadcast(self, doc: Doc, data: dict, skip: Conn | None = None) -> None:
        for conn in list(doc.conns):
            if conn is not skip:
                with contextlib.suppress(Exception):
                    await conn.ws.send_text(protocol.message(data))

    def _base(self, ws: WebSocket) -> str:
        """De host en het prefix zoals de browser ze ziet (X-Forwarded-*), voor de links."""
        h = ws.headers
        proto = h.get("x-forwarded-proto", "")
        if proto not in ("http", "https"):
            proto = "https" if ws.url.scheme == "wss" else "http"
        host = h.get("x-forwarded-host") or h.get("host") or "localhost"
        if not HOST_RE.match(host):
            host = "localhost"
        prefix = h.get("x-forwarded-prefix", "")
        if not PREFIX_RE.match(prefix):
            prefix = ""
        return f"{proto}://{host}{prefix}"

    async def _receive(self, ws: WebSocket) -> str | None:
        try:
            return await asyncio.wait_for(ws.receive_text(), RECEIVE_SECONDS)
        except (TimeoutError, WebSocketDisconnect, RuntimeError):
            return None

    async def socket(self, ws: WebSocket, key: str) -> None:
        q = ws.query_params
        if not self.up or q.get("EIO") != "4" or q.get("transport") != "websocket":
            self.record("socket", key=key, refused=True)
            await ws.close(code=1008)
            return
        await ws.accept()
        sid = secrets.token_urlsafe(15)
        await ws.send_text(protocol.eio_open(sid))
        frame = await self._receive(ws)
        kind, packet = protocol.parse(frame or "")
        if kind != "connect" or not isinstance(packet, dict):
            await ws.close(code=1002)
            return
        data = packet.get("data") if isinstance(packet.get("data"), dict) else {}
        reconnect = "session" in packet
        claims, error = self._open_claims(key, packet.get("token"), packet.get("session"))
        self.record("socket", key=key, connect=sorted(packet), data=sorted(data), error=error)
        if claims is None:
            await ws.send_text(protocol.sio_error("Not authorized", error))
            await ws.close(code=1008)
            return
        conn_sid = secrets.token_urlsafe(15)
        await ws.send_text(protocol.sio_connected(conn_sid))
        await ws.send_text(protocol.message(protocol.license_message()))
        frame = await self._receive(ws)
        kind, auth = protocol.parse(frame or "")
        if kind != "message" or not isinstance(auth, dict) or auth.get("type") != "auth":
            await ws.close(code=1002)
            return
        # Het auth-bericht draagt het token nog eens: jwtOpen of jwtSession
        again, error = self._open_claims(key, auth.get("jwtOpen"), auth.get("jwtSession"))
        if again is None:
            await ws.send_text(protocol.message({"type": "error", "code": error}))
            await ws.close(code=1008)
            return
        if reconnect:
            # Bij herverbinden zit de config niet in het sessietoken (geen url, geen
            # callbackUrl); jwtOpen staat nog in de data van CONNECT [gemeten]
            config, _ = self._open_claims(key, data.get("jwtOpen"), None)
            config = config or claims
        else:
            config = claims
        await self._session(ws, key, conn_sid, config, auth, reconnect)

    async def _session(
        self, ws: WebSocket, key: str, sid: str, config: dict, auth: dict, reconnect: bool
    ) -> None:
        document = config.get("document") or {}
        editor = config.get("editorConfig") or {}
        editor_user = editor.get("user") if isinstance(editor.get("user"), dict) else {}
        auth_user = auth.get("user") if isinstance(auth.get("user"), dict) else {}
        user = {
            "id": str(editor_user.get("id") or auth_user.get("id") or "anoniem"),
            "name": str(editor_user.get("name") or auth_user.get("username") or ""),
        }
        known = auth.get("lastOtherSaveTime")
        known = known if isinstance(known, int | float) else -1
        if reconnect and key not in self.docs:
            # Herverbinden op een key die DS dichtdeed (4008) of niet meer kent (4009, na een
            # herstart): eerst een nieuw sessietoken, dan de fout, dan dicht [gemeten]
            code, text = UPDATE_VERSION if key in self.done else OTHER_ERROR
            self.record("socket", key=key, error=code)
            permissions = document.get("permissions") if isinstance(document, dict) else None
            claims = protocol.session_claims(
                key, dict(permissions or {}), user, -1, str(editor.get("mode") or "edit")
            )
            token = protocol.sign(
                protocol.session_secret(self.secret), claims, protocol.SESSION_SECONDS
            )
            await ws.send_text(protocol.message({"type": "refreshToken", "messages": token}))
            await ws.send_text(
                protocol.message({"type": "error", "description": text, "code": code})
            )
            await ws.close(code=1000)
            return
        if (
            key in self.saved
            and key not in self.docs
            or (reconnect and key in self.docs and self.docs[key].last_save > known)
        ):
            # M17: met deze key is intussen opgeslagen
            self.record("socket", key=key, refresh=True)
            await ws.send_text(protocol.message({"type": "refreshFile"}))
            await ws.close(code=1000)
            return
        doc = self.docs.get(key)
        first = doc is None
        if doc is None:
            url = document.get("url") or ((auth.get("openCmd") or {}).get("url"))
            callback = editor.get("callbackUrl") or auth.get("documentCallbackUrl")
            if not isinstance(url, str) or not isinstance(callback, str):
                await ws.send_text(protocol.message({"type": "error", "code": -1}))
                await ws.close(code=1008)
                return
            doc = Doc(
                key=key,
                url=url,
                callback=callback,
                filetype=str(document.get("fileType") or "docx"),
                title=str(document.get("title") or ""),
                permissions=dict(document.get("permissions") or {}),
                base=self._base(ws),
            )
            self.docs[key] = doc
        if doc.closing is not None:
            doc.closing.cancel()
            doc.closing = None
        conn = Conn(
            ws=ws,
            key=key,
            sid=sid,
            user=user,
            index=doc.next_index,
            mode=str(editor.get("mode") or auth.get("mode") or "edit"),
            base=self._base(ws),
            known_save=known,
        )
        doc.next_index += 1
        doc.base = conn.base
        doc.names[user["id"]] = user["name"]
        doc.conns.append(conn)
        try:
            await self._opened(doc, conn, first, reconnect)
            await self._loop(doc, conn)
        finally:
            await self._gone(doc, conn)

    async def _opened(self, doc: Doc, conn: Conn, first: bool, reconnect: bool) -> None:
        fields = protocol.editing(doc.key, doc.users(), (protocol.CONNECT, conn.user["id"]))
        if first:
            await self.send(doc, fields)  # eerst status 1, dan ophalen [gemeten]
            doc.open_error = await self.download(doc)
            doc.opened.set()
        else:
            self.spawn(self.send(doc, fields))
            await doc.opened.wait()
        reply = {
            "type": "auth",
            "result": 1,
            "sessionId": conn.sid,
            "sessionTimeConnect": int(time.time() * 1000),
            "participants": [
                {
                    "id": f"{c.user['id']}{c.index}",
                    "idOriginal": c.user["id"],
                    "username": c.user["name"],
                    "indexUser": c.index,
                    "view": c.mode != "edit",
                    "connectionId": c.sid,
                    "isCloseCoAuthoring": False,
                    "isLiveViewer": False,
                    "encrypted": False,
                }
                for c in doc.conns
            ],
            "locks": {},
            "indexUser": conn.index,
        }
        if not reconnect:
            reply["hasForgotten"] = False
            claims = protocol.session_claims(
                doc.key, doc.permissions, conn.user, conn.index, conn.mode
            )
            reply["jwt"] = protocol.sign(
                protocol.session_secret(self.secret), claims, protocol.SESSION_SECONDS
            )
        reply.update(
            {
                "buildVersion": protocol.VERSION,
                "buildNumber": protocol.BUILD_NUMBER,
                "licenseType": 3,
                "settings": protocol.SOCKET_SETTINGS,
            }
        )
        if not reconnect:
            reply["openedAt"] = int(time.time() * 1000)
        await conn.ws.send_text(protocol.message(reply))
        if doc.open_error is not None:
            await conn.ws.send_text(
                protocol.message(
                    {
                        "type": "documentOpen",
                        "data": {"type": "open", "status": "err", "data": doc.open_error},
                    }
                )
            )
            return
        if not reconnect:
            path = f"/cache/files/data/{doc.key}/Editor.bin/Editor.bin"
            link = protocol.secure_link(conn.base, self.link_secret, path, doc.key, "Editor.bin")
            await conn.ws.send_text(
                protocol.message(
                    {
                        "type": "documentOpen",
                        "data": {"type": "open", "status": "ok", "data": {"Editor.bin": link}},
                    }
                )
            )
        else:  # de teksten van de anderen opnieuw
            for userid, text in doc.texts.items():
                if userid != conn.user["id"]:
                    await conn.ws.send_text(protocol.message(self._change(doc, userid, text)))

    def _change(self, doc: Doc, userid: str, text: str) -> dict:
        return {
            "type": "saveChanges",
            "userid": userid,
            "name": doc.names.get(userid, ""),
            "text": text,
        }

    async def _loop(self, doc: Doc, conn: Conn) -> None:
        while True:
            try:
                frame = await conn.ws.receive_text()
            except (WebSocketDisconnect, RuntimeError):
                return
            kind, data = protocol.parse(frame)
            if kind == "ping":
                await conn.ws.send_text("3" + (data or ""))
                continue
            if kind in ("disconnect", "close"):
                return
            if kind != "message" or not isinstance(data, dict):
                continue
            what = data.get("type")
            if what == "saveChanges" and isinstance(data.get("text"), str):
                if conn.mode != "edit" or doc.open_error is not None:
                    continue
                userid = conn.user["id"]
                now = time.time()
                doc.texts[userid] = data["text"]
                doc.changed_at[userid] = now
                doc.dirty = doc.ever = True
                doc.last_change = now
                doc.last_editor = userid
                self.record("change", key=doc.key, user=userid, length=len(data["text"]))
                await self.broadcast(doc, self._change(doc, userid, data["text"]), skip=conn)
                await conn.ws.send_text(
                    protocol.message({"type": "unSaveLock", "time": int(now * 1000)})
                )
            elif what == "forceSaveStart":
                ok = await self.forcesave(doc, protocol.BY_BUTTON, user=conn.user["id"])
                await conn.ws.send_text(
                    protocol.message(
                        {"type": "forceSave", "messages": {"ended": True, "success": ok}}
                    )
                )

    async def _gone(self, doc: Doc, conn: Conn) -> None:
        if conn in doc.conns:
            doc.conns.remove(conn)
        with contextlib.suppress(Exception):
            await conn.ws.close()
        if doc.dropping or doc.finished or not self.up:
            return
        userid = conn.user["id"]
        if doc.conns:
            fields = protocol.editing(doc.key, doc.users(), (protocol.DISCONNECT, userid))
            self.spawn(self.send(doc, fields))
        elif doc.closing is None:
            doc.closing = self.spawn(self._close_later(doc, userid))

    async def close_conn(self, conn: Conn, reason: str) -> None:
        with contextlib.suppress(Exception):
            await conn.ws.send_text(protocol.message({"type": "drop", "description": reason}))
        with contextlib.suppress(Exception):
            await conn.ws.close(code=1000)

    # --- commando's ---

    async def _info(self, params: dict) -> dict:
        key = params.get("key")
        doc = self.docs.get(key) if isinstance(key, str) else None
        if doc is None:
            gone = self.done.get(key) if isinstance(key, str) else None
            if gone is None:
                return {"key": key, "error": protocol.CMD_KEY}
            # Een dichte key: geen gebruikers, en elke info stuurt een status 4 zonder actions
            # en zonder users, ook na een status 2 [gemeten]
            self.spawn(self.send(gone, protocol.closed(gone.key)))
            return {"key": key, "error": protocol.CMD_OK, "users": []}
        users = doc.users()
        self.spawn(self.send(doc, protocol.editing(doc.key, users)))
        return {"key": key, "error": protocol.CMD_OK, "users": users}

    async def _forcesave(self, params: dict) -> dict:
        key = params.get("key")
        doc = self.docs.get(key) if isinstance(key, str) else None
        if doc is None or doc.finished:
            # Op een dichte key error 4 (niets te bewaren), op een onbekende error 1 [gemeten]
            done = isinstance(key, str) and key in self.done
            return {"key": key, "error": protocol.CMD_NO_CHANGES if done else protocol.CMD_KEY}
        if not doc.dirty:
            return {"key": key, "error": protocol.CMD_NO_CHANGES}
        userdata = params.get("userdata")
        self.spawn(
            self.forcesave(
                doc, protocol.BY_COMMAND, userdata=userdata if isinstance(userdata, str) else None
            )
        )
        return {"key": key, "error": protocol.CMD_OK}

    async def _drop(self, params: dict) -> dict:
        """Met users: die eruit; zonder: iedereen eruit en de key dicht (status 2 of 4). Een
        onbekende of dichte key geeft error 0, zoals de echte [gemeten]. drop onthoudt niemand
        (M15).

        De callbacks zijn die van gewoon weggaan [gemeten]: met users en de laatste eruit:
        status 2 of 4 met actions type 0 van hem (na save_delay); zonder users gaan ze eruit
        van de laatste verbinding naar de eerste, met status 1 (actions type 0) voor elk
        behalve de laatste, en dan meteen status 2 of 4 met actions type 0 van de laatste."""
        key = params.get("key")
        doc = self.docs.get(key) if isinstance(key, str) else None
        if doc is None or doc.finished:
            return {"key": key, "error": protocol.CMD_OK}
        users = params.get("users")
        if isinstance(users, list):
            for conn in [c for c in doc.conns if c.user["id"] in users]:
                await self.close_conn(conn, "drop")
            return {"key": key, "error": protocol.CMD_OK}
        doc.dropping = True
        conns = list(doc.conns)
        for conn in conns:
            await self.close_conn(conn, "drop")
        doc.conns.clear()
        leaving = list(reversed(conns))
        staying = list(conns)
        for conn in leaving[:-1]:
            staying.remove(conn)
            users = list(dict.fromkeys(c.user["id"] for c in staying))
            action = (protocol.DISCONNECT, conn.user["id"])
            self.spawn(self.send(doc, protocol.editing(doc.key, users, action)))
        last = (protocol.DISCONNECT, leaving[-1].user["id"]) if leaving else None
        self.spawn(self.finish(doc, last))
        return {"key": key, "error": protocol.CMD_OK}

    async def _version(self, params: dict) -> dict:
        return protocol.version_answer()

    async def _license(self, params: dict) -> dict:
        return protocol.license_answer(self.connections)

    # --- sturen (/__fake) ---

    async def kill(self) -> None:
        """Alles weg zonder callbacks, zoals docker kill; daarna "down" tot restart."""
        self.up = False
        docs = list(self.docs.values())
        self.docs.clear()
        self.done.clear()
        for doc in docs:
            doc.finished = True
            if doc.closing is not None:
                doc.closing.cancel()
            for conn in list(doc.conns):
                with contextlib.suppress(Exception):
                    await conn.ws.close(code=1011)
        for task in list(self.tasks):
            task.cancel()
        for hook in self.on_reset:
            hook("kill")

    async def stop(self) -> None:
        """Zoals SIGTERM: status 2 (of 4) voor alles meteen, dan "down"."""
        docs = list(self.docs.values())
        for doc in docs:
            doc.dropping = True
            if doc.closing is not None:
                doc.closing.cancel()
            for conn in list(doc.conns):
                with contextlib.suppress(Exception):
                    await conn.ws.close(code=1001)
            doc.conns.clear()
        await asyncio.gather(*(self.finish(doc, None) for doc in docs), return_exceptions=True)
        self.up = False
        for hook in self.on_reset:
            hook("stop")

    async def restart(self) -> None:
        """Weer op, met een leeg geheugen en (tenzij vast) een nieuw linkgeheim: oude links
        geven dan 403."""
        if self.up:
            await self.kill()
        self.docs.clear()
        self.done.clear()
        if not self.config.link_secret:
            self.link_secret = secrets.token_urlsafe(16)
        self.up = True
        self.starting = False
        self._flush()  # het startscript schrijft api.js opnieuw; hier zonder fontbouw
        for hook in self.on_reset:
            hook("restart")

    def _flush(self) -> None:
        """api.js opnieuw geschreven: later dan de fontbouw (de tijden in hele seconden)."""
        self.flushed = max(time.time(), self.fonts_built + 1)

    async def fonts(self, pending: bool) -> None:
        """pending: een DS vlak na de start (api.js en fonts_thumbnail.png in dezelfde seconde
        [gemeten]); anders de font-herstart: docservice en de converter herstarten,
        alle sessies weg zonder callback en zonder forgotten file [gemeten], en dan api.js."""
        if pending:
            self.flushed = self.fonts_built = time.time()
            return
        await self.kill()  # met de haken van kill
        self.up = True
        self._flush()


def _down() -> Response:
    return Response(status_code=503)


def create_app(config: FakeConfig, parts: tuple[str, ...] = ("forgotten", "convert")) -> FastAPI:
    fake = FakeDocServer(config)

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        await fake.aclose()

    app = FastAPI(
        title="Nep-DS", openapi_url=None, docs_url=None, redoc_url=None, lifespan=lifespan
    )
    app.state.fake = fake

    @app.middleware("http")
    async def record_requests(request: Request, call_next):
        response = await call_next(request)
        if not request.url.path.startswith("/__fake/"):
            fake.record(
                "http",
                method=request.method,
                path=_query_safe(request.url.path),
                query=_query_safe(request.url.query),
                http=response.status_code,
            )
        return response

    @app.get("/healthcheck")
    async def healthcheck() -> Response:
        if not fake.up:
            return _down()
        body = "false" if fake.starting else "true"
        return Response(body, media_type="text/plain; charset=utf-8")

    def api_js(request: Request) -> Response:
        if not fake.up:
            return _down()
        prefix = request.headers.get("x-forwarded-prefix", "")
        if not PREFIX_RE.match(prefix):
            prefix = ""
        source = (STATIC / "api.js").read_text()
        source = (
            source.replace("__PREFIX__", json.dumps(prefix))
            .replace("__V__", json.dumps(protocol.V))
            .replace("__JWT__", "true" if fake.jwt else "false")
        )
        return Response(
            source,
            media_type="application/javascript; charset=utf-8",
            headers={"Last-Modified": formatdate(fake.flushed, usegmt=True)},
        )

    @app.api_route("/web-apps/apps/api/documents/api.js", methods=["GET", "HEAD"])
    async def api_root(request: Request) -> Response:
        return api_js(request)

    @app.api_route("/{v}/web-apps/apps/api/documents/api.js", methods=["GET", "HEAD"])
    async def api_versioned(v: str, request: Request) -> Response:
        if v != protocol.V:
            return Response(status_code=404)
        return api_js(request)

    @app.api_route("/sdkjs/common/Images/fonts_thumbnail.png", methods=["GET", "HEAD"])
    async def fonts_thumbnail() -> Response:
        if not fake.up:
            return _down()
        return Response(
            b"\x89PNG\r\n\x1a\n",
            media_type="image/png",
            headers={"Last-Modified": formatdate(fake.fonts_built, usegmt=True)},
        )

    @app.get("/{v}/web-apps/apps/{editor}/main/{name}")
    async def editor_page(v: str, editor: str, name: str) -> Response:
        if not fake.up:
            return _down()
        if v != protocol.V or editor not in EDITORS:
            return Response(status_code=404)
        if name == "index.html":
            return Response(
                (STATIC / "editor.html").read_text(), media_type="text/html; charset=utf-8"
            )
        if name == "editor.js":
            return Response(
                (STATIC / "editor.js").read_text(),
                media_type="application/javascript; charset=utf-8",
            )
        return Response(status_code=404)

    @app.get("/cache/files/data/{part}/{name}/{filename}")
    async def cache_file(part: str, name: str, filename: str, request: Request) -> Response:
        if not fake.up:
            return _down()
        q = request.query_params
        path = request.url.path
        if not protocol.link_ok(fake.link_secret, path, q.get("md5", ""), q.get("expires", "")):
            return Response(status_code=403)
        if name == "Editor.bin" and filename == "Editor.bin":
            doc = fake.docs.get(part)
            if doc is None:
                return Response(status_code=404)
            texts = {u: {"name": doc.names.get(u, ""), "text": t} for u, t in doc.texts.items()}
            return JSONResponse({"paragraphs": doc.paragraphs, "texts": texts})
        content = fake.files.get(part, {}).get(name)
        if content is None or filename != name:
            return Response(status_code=404)
        return Response(content, media_type="application/octet-stream")

    async def json_body(request: Request) -> dict:
        try:
            body = json.loads(await request.body() or b"{}")
        except ValueError:
            return {}
        return body if isinstance(body, dict) else {}

    async def command(request: Request) -> Response:
        if not fake.up:
            return _down()
        body = await json_body(request)
        params = fake._params(body, request.headers.get("authorization"), command=True)
        fake.record("command", c=body.get("c"), signed=params is not None)
        if params is None:
            # Zonder (geldig) token: kale tekst, geen JSON-type [gemeten]
            return Response('{"error":6}', media_type="text/plain")
        handler = fake.commands.get(params.get("c"))
        if handler is None:
            return JSONResponse({"error": protocol.CMD_UNKNOWN})
        return JSONResponse(await handler(params))

    app.add_api_route("/command", command, methods=["POST"])
    app.add_api_route("/coauthoring/CommandService.ashx", command, methods=["POST"])

    async def converter(request: Request) -> Response:
        if not fake.up:
            return _down()
        body = await json_body(request)
        params = fake._params(body, request.headers.get("authorization"), command=False)
        fake.record("convert", key=body.get("key"), signed=params is not None)
        if params is None:
            return convert_error(request, -8)
        if fake.convert is None:  # het deel conversie (O3) ontbreekt
            return convert_error(request, -1)
        return await fake.convert(request, params)

    app.add_api_route("/converter", converter, methods=["POST"])
    app.add_api_route("/ConvertService.ashx", converter, methods=["POST"])

    @app.websocket("/{v}/doc/{key}/c/")
    async def doc_socket(ws: WebSocket, v: str, key: str) -> None:
        if v != protocol.V:
            await ws.close(code=1008)
            return
        await fake.socket(ws, key)

    # --- sturen ---

    @app.get("/__fake/log")
    async def fake_log() -> dict:
        return {"entries": fake.entries}

    @app.get("/__fake/info/{key}")
    async def fake_info(key: str) -> dict:
        doc = fake.docs.get(key)
        if doc is None:
            return {
                "key": key,
                "open": False,
                "users": [],
                "saved": key in fake.saved,
                "closed": key in fake.done,
            }
        return {
            "key": key,
            "open": True,
            "users": doc.users(),
            "names": [doc.names.get(u, "") for u in doc.users()],
            "dirty": doc.dirty,
            "saved": key in fake.saved,
        }

    @app.post("/__fake/kill")
    async def fake_kill() -> dict:
        await fake.kill()
        return {"ok": True}

    @app.post("/__fake/stop")
    async def fake_stop() -> dict:
        await fake.stop()
        return {"ok": True}

    @app.post("/__fake/restart")
    async def fake_restart() -> dict:
        await fake.restart()
        return {"ok": True}

    @app.post("/__fake/health")
    async def fake_health(request: Request) -> dict:
        """{"ok": false} of {"starting": true}: healthcheck geeft false; {"ok": true}: true."""
        body = await json_body(request)
        fake.starting = bool(body.get("starting")) or body.get("ok") is False
        return {"starting": fake.starting}

    @app.post("/__fake/fonts")
    async def fake_fonts(request: Request) -> dict:
        """{"pending": true}: een nieuwe container die zijn font-herstart nog moet doen;
        {"pending": false}: die herstart (sessies weg zonder callback, daarna een nieuwe api.js)."""
        body = await json_body(request)
        await fake.fonts(body.get("pending") is True)
        return {"flushed": fake.flushed, "fonts_built": fake.fonts_built}

    @app.post("/__fake/license")
    async def fake_license(request: Request) -> dict:
        body = await json_body(request)
        value = body.get("connections")
        fake.connections = value if isinstance(value, int) else protocol.UNLIMITED
        return {"connections": fake.connections}

    @app.post("/__fake/jwt-off")
    async def fake_jwt_off(request: Request) -> dict:
        """{"off": true} (standaard): alles zonder token aannemen; {"off": false}: weer eisen."""
        body = await json_body(request)
        fake.jwt = body.get("off") is False
        return {"jwt": fake.jwt}

    for name in parts:
        module_name = f"{__package__}.{name}"
        try:
            module = importlib.import_module(module_name)
        except ModuleNotFoundError as exc:
            if exc.name != module_name:
                raise
            continue
        module.install(fake, app)
    return app


def convert_error(request: Request, code: int) -> Response:
    """Een fout van /converter: JSON met Accept: application/json, anders XML."""
    if "application/json" in request.headers.get("accept", ""):
        return JSONResponse({"error": code})
    xml = f'<?xml version="1.0" encoding="utf-8"?><FileResult><Error>{code}</Error></FileResult>'
    return Response(xml, media_type="application/xml")
