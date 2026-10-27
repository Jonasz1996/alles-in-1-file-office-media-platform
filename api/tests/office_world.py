"""Een wereld voor de API-tests van O2: de app met office aan, de nep-DS als echte server (voor
de socket) én via ASGITransport (commando's en downloads), en de kant van het platform
(gebruikers, bestanden, delingen, de klok, de ronde en het herstel).

Niet zelf verzameld (geen test_*.py). Gebruik in een testbestand:

    @pytest.fixture
    async def world(client, settings):
        async with office_world(client, settings) as w:
            yield w

De callbacks en downloads van de nep gaan via ASGITransport naar de interne app, door
Failing heen: fail_callbacks(n) laat de volgende n callbacks 503 geven, zonder dat de app ze
ziet (de testhaak van de contracttests; een herstart van de app vergeet hem). De klok van de app
(sessions.clock) loopt hier mee met advance(minutes).
"""

import asyncio
import contextlib
import secrets
import socket
import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import uvicorn
from sqlalchemy import func, select

from app import access, events, security
from app.config import Settings
from app.main import app, internal_app
from app.models import Node, OfficeSession, User, Version
from app.office import engine, inspect, monitor, recovery, runtime, saving, sessions, sweep
from app.office.oo import client as ds_client
from app.office.state import OfficeConfig, OfficeState, office_policy
from app.routers import office as office_router
from app.routers.auth import used_bytes
from app.routers.files import is_trashed
from app.storage import BlobStore
from tests.factories import code_of, create_user, maker
from tests.fake_docserver import FakeConfig, create_app, docx, protocol
from tests.fake_docserver import forgotten as fake_forgotten
from tests.fake_docserver.client import EditorClient
from tests.fake_docserver.client import Refused as SocketRefused
from tests.office_contract import Handle, NodeView, Refused, SessionView

CALLBACK = "/internal/office/callback"


class Failing(httpx.AsyncBaseTransport):
    """Het transport van de nep naar de interne app: telt wat onderweg is, en geeft de volgende
    `left` callbacks 503 zonder dat de app ze ziet."""

    def __init__(self, inner: httpx.AsyncBaseTransport) -> None:
        self.inner = inner
        self.left = 0
        self.inflight = 0
        self.refused = 0

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == CALLBACK and self.left > 0:
            self.left -= 1
            self.refused += 1
            return httpx.Response(503, json={})
        self.inflight += 1
        try:
            return await _buffered(self.inner, request)
        finally:
            self.inflight -= 1


async def _buffered(inner: httpx.AsyncBaseTransport, request: httpx.Request) -> httpx.Response:
    """Het hele antwoord lezen (zo weet de teller wanneer het klaar is), en het als een nieuw
    antwoord teruggeven."""
    response = await inner.handle_async_request(request)
    body = await response.aread()
    await response.aclose()
    return httpx.Response(response.status_code, headers=response.headers, stream=_Body(body))


class _Body(httpx.AsyncByteStream):
    """Een gelezen antwoord, opnieuw als stroom in stukken (zoals van het netwerk)."""

    def __init__(self, data: bytes) -> None:
        self.data = data

    async def __aiter__(self):
        for start in range(0, len(self.data), 64 * 1024):
            yield self.data[start : start + 64 * 1024]


class Counting(httpx.AsyncBaseTransport):
    """Het transport van de app naar de nep (commando's, downloads): telt wat onderweg is."""

    def __init__(self, inner: httpx.AsyncBaseTransport) -> None:
        self.inner = inner
        self.inflight = 0

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.inflight += 1
        try:
            return await _buffered(self.inner, request)
        finally:
            self.inflight -= 1


@dataclass
class U:
    id: uuid.UUID
    email: str
    headers: dict[str, str]
    name: str = ""


@dataclass
class Published:
    user_id: uuid.UUID
    type: str
    data: dict = field(default_factory=dict)


@contextlib.asynccontextmanager
async def served(asgi):
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    config = uvicorn.Config(asgi, ws="websockets-sansio", log_level="warning", lifespan="on")
    server = uvicorn.Server(config)
    task = asyncio.ensure_future(server.serve(sockets=[sock]))
    for _ in range(500):
        if server.started or task.done():
            break
        await asyncio.sleep(0.01)
    assert server.started and not task.done()
    try:
        yield sock.getsockname()[1]
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, 10)
        sock.close()


class World:
    """De App én de Driver van tests/office_contract.py, voor de nep-DS."""

    def __init__(self, client: httpx.AsyncClient, settings: Settings, office: Settings) -> None:
        self.client = client
        self.base_settings = settings
        self.settings = office
        self.offset = timedelta()
        self.published: list[Published] = []
        self.editors: list[EditorClient] = []
        self.users: dict[str, U] = {}
        self.port = 0
        self.fake_app: Any = None
        self.failing: Failing | None = None
        self.counting: Counting | None = None
        self.http: httpx.AsyncClient | None = None
        self._parts = 1000

    # --- klok en geheugen ---

    def now(self) -> datetime:
        return datetime.now(UTC) + self.offset

    @property
    def fake(self):
        return self.fake_app.state.fake

    @property
    def state(self) -> OfficeState:
        return sessions.office()

    def fresh_state(self) -> None:
        """Het geheugen van office leeg, zoals na een start van de app."""
        app.state.office = OfficeState(health="ok", ever_ok=True)
        office_policy(app).config = OfficeConfig()
        sweep.forget()
        recovery._warned.clear()
        monitor.forget_cache()
        office_router.session_limiter.reset()
        inspect.forget()

    # --- de App ---

    async def user(self, email: str, **kw: Any) -> U:
        if email in self.users:
            return self.users[email]
        async with maker(self.base_settings)() as session:
            user = await create_user(session, email, **kw)
            await session.commit()
            token = security.access_token(self.base_settings, user, uuid.uuid4())
            found = U(user.id, user.email, {"Authorization": f"Bearer {token}"}, user.display_name)
        self.users[email] = found
        return found

    async def root(self, owner: U) -> uuid.UUID:
        async with maker(self.base_settings)() as session:
            user = await session.get(User, owner.id)
            assert user is not None and user.root_id is not None
            return user.root_id

    async def folder(self, owner: U, name: str, parent: uuid.UUID | None = None) -> uuid.UUID:
        parent = parent or await self.root(owner)
        r = await self.client.post(
            f"/api/v1/files/{parent}/folders", json={"name": name}, headers=owner.headers
        )
        assert r.status_code == 201, r.text
        return uuid.UUID(r.json()["id"])

    async def put_file(
        self, owner: U, folder: uuid.UUID | None, name: str, data: bytes
    ) -> uuid.UUID:
        folder = folder or await self.root(owner)
        r = await self.client.put(
            f"/api/v1/files/{folder}/upload",
            params={"name": name},
            content=data,
            headers=owner.headers,
        )
        assert r.status_code == 201, r.text
        return uuid.UUID(r.json()["id"])

    async def docx(
        self, owner: U, folder: uuid.UUID | None, name: str, paragraphs: Sequence[str]
    ) -> uuid.UUID:
        return await self.put_file(owner, folder, name, docx.make_docx(list(paragraphs)))

    async def share(self, owner: U, folder: uuid.UUID, other: U, write: bool = True) -> str:
        body = {"user_id": str(other.id), "permission": "write" if write else "read"}
        r = await self.client.post(
            f"/api/v1/files/{folder}/shares", json=body, headers=owner.headers
        )
        assert r.status_code == 201, r.text
        return r.json()["id"]

    async def revoke(self, owner: U, share: str) -> None:
        r = await self.client.delete(f"/api/v1/shares/{share}", headers=owner.headers)
        assert r.status_code == 204, r.text

    async def rename(self, user: U, node: uuid.UUID, name: str) -> None:
        r = await self.client.patch(
            f"/api/v1/files/{node}", json={"name": name}, headers=user.headers
        )
        assert r.status_code == 200, r.text

    async def move(self, user: U, node: uuid.UUID, folder: uuid.UUID) -> None:
        r = await self.client.patch(
            f"/api/v1/files/{node}", json={"parent_id": str(folder)}, headers=user.headers
        )
        assert r.status_code == 200, r.text

    async def trash(self, user: U, node: uuid.UUID) -> None:
        r = await self.client.delete(f"/api/v1/files/{node}", headers=user.headers)
        assert r.status_code == 204, r.text

    async def purge(self, user: U, node: uuid.UUID) -> None:
        view = await self.node(node)
        if view is not None and not view.trashed:
            await self.trash(user, node)
        r = await self.client.delete(f"/api/v1/trash/{node}", headers=user.headers)
        assert r.status_code == 204, r.text

    async def upload(self, user: U, node: uuid.UUID, paragraphs: Sequence[str]) -> None:
        r = await self.client.put(
            f"/api/v1/files/{node}/content",
            content=docx.make_docx(list(paragraphs)),
            headers=user.headers,
        )
        assert r.status_code == 200, r.text

    async def room(self, user: U, free_bytes: int | None) -> None:
        async with maker(self.base_settings)() as session:
            row = await session.get(User, user.id)
            assert row is not None
            if free_bytes is None:
                row.quota_bytes = 0
            else:
                row.quota_bytes = max(1, await used_bytes(session, user.id) + free_bytes)
            await session.commit()

    async def content(self, node: uuid.UUID) -> bytes:
        async with maker(self.base_settings)() as session:
            row = await session.get(Node, node)
            assert row is not None and row.blob_id
            return BlobStore(self.settings.blob_dir).path(row.blob_id).read_bytes()

    async def paragraphs(self, node: uuid.UUID) -> list[str]:
        return docx.paragraphs(await self.content(node))

    async def version_count(self, node: uuid.UUID) -> int:
        async with maker(self.base_settings)() as session:
            count = await session.scalar(
                select(func.count()).select_from(Version).where(Version.node_id == node)
            )
            return int(count or 0)

    async def versions(self, node: uuid.UUID) -> list[Version]:
        async with maker(self.base_settings)() as session:
            rows = await session.scalars(
                select(Version).where(Version.node_id == node).order_by(Version.replaced_at)
            )
            return list(rows)

    async def version_paragraphs(self, node: uuid.UUID) -> list[list[str]]:
        store = BlobStore(self.settings.blob_dir)
        return [
            docx.paragraphs(store.path(v.blob_id).read_bytes()) for v in await self.versions(node)
        ]

    async def node(self, node: uuid.UUID) -> NodeView | None:
        async with maker(self.base_settings)() as session:
            row = await session.get(Node, node)
            if row is None:
                return None
            return NodeView(row.name, row.parent_id, await is_trashed(session, row))

    async def node_row(self, node: uuid.UUID) -> Node | None:
        async with maker(self.base_settings)() as session:
            return await session.get(Node, node)

    async def find(self, owner: U, prefix: str) -> list[uuid.UUID]:
        async with maker(self.base_settings)() as session:
            rows = await session.scalars(
                select(Node).where(Node.owner_id == owner.id, Node.kind == "file")
            )
            return [n.id for n in rows if n.name.startswith(prefix)]

    async def session(self, key: str) -> SessionView | None:
        row = await self.row(key)
        if row is None:
            return None
        return SessionView(row.state, row.closed_reason, row.versions_made)

    async def row_count(self) -> int:
        async with maker(self.base_settings)() as session:
            count = await session.scalar(select(func.count()).select_from(OfficeSession))
            return int(count or 0)

    async def row(self, key: str) -> OfficeSession | None:
        async with maker(self.base_settings)() as session:
            return await session.get(OfficeSession, key)

    async def set_session(self, key: str, **fields: Any) -> None:
        async with maker(self.base_settings)() as session:
            row = await session.get(OfficeSession, key)
            assert row is not None
            for name, value in fields.items():
                setattr(row, name, value)
            await session.commit()

    async def events(self, user: U) -> list[str]:
        return [p.type for p in self.published if p.user_id == user.id]

    def event_data(self, user: U, kind: str) -> list[dict]:
        return [p.data for p in self.published if p.user_id == user.id and p.type == kind]

    async def advance(self, minutes: float) -> None:
        self.offset += timedelta(minutes=minutes)

    async def sweep(self) -> None:
        await sweep.run_once()
        await self.settle()

    async def recover(self) -> None:
        await self.settle()
        await recovery.run_once()
        await self.settle()

    def idle(self) -> bool:
        assert self.failing is not None and self.counting is not None
        return (
            not self.fake.tasks
            and self.failing.inflight == 0
            and self.counting.inflight == 0
            and not saving._background
        )

    async def settle(self, seconds: float = 10.0) -> None:
        """Wachten tot de nep en de app klaar zijn (drie keer na elkaar niets onderweg)."""
        quiet = 0
        for _ in range(int(seconds / 0.02)):
            quiet = quiet + 1 if self.idle() else 0
            if quiet >= 3:
                return
            await asyncio.sleep(0.02)
        raise AssertionError("de nep-DS of de app werd niet stil")

    # --- de Driver ---

    async def open_config(self, user: U, node: uuid.UUID, **body: Any) -> httpx.Response:
        return await self.client.post(
            f"/api/v1/office/files/{node}/session", json=body, headers=user.headers
        )

    def editor(self, config: dict) -> EditorClient:
        assert self.http is not None
        editor = EditorClient(f"ws://127.0.0.1:{self.port}", config, http=self.http)
        self.editors.append(editor)
        return editor

    async def open(self, user: U, node: uuid.UUID) -> Handle:
        r = await self.open_config(user, node)
        if r.status_code != 200:
            raise Refused(r.status_code, code_of(r))
        config = r.json()["config"]
        editor = self.editor(config)
        await editor.open()
        return Handle(user, node, config["document"]["key"], state=editor)

    async def type(self, h: Handle, text: str) -> None:
        await h.state.type(text)

    async def save(self, h: Handle, explicit: bool) -> None:
        if explicit:
            await h.state.save()
        else:
            eng = engine.current()
            assert eng is not None
            await eng.checkpoint(h.key, "timer")

    async def close(self, h: Handle) -> None:
        await h.state.close()

    async def reconnect(self, h: Handle) -> bool:
        """Opnieuw verbinden zoals een browser: eerst langs de poort (in productie controleert
        O1 elk pad naar DS met gate.check; hier doet passes() dezelfde controle op de sessie),
        dan de socket naar de nep."""
        if not await self.passes(h.user, h.key):
            return False
        return await self.sneak_in(h)

    async def sneak_in(self, h: Handle) -> bool:
        """Opnieuw verbinden zonder de poort, zoals een socket die toch nog bij DS komt: de
        terugval van Keuze 20 (de volgende ronde stopt dan de sessie)."""
        try:
            await h.state.reconnect()
        except (SocketRefused, ConnectionError, OSError):
            return False
        return True

    async def passes(self, user: U, key: str) -> bool:
        """Wat gate.check(headers, key) van O1 met een key vraagt: lid van die sessie, niet
        banned, sessie niet dicht en nu schrijfrecht."""
        async with maker(self.base_settings)() as session:
            row = await sessions.find_by_key(session, key)
            if row is None or row.state == "closed" or row.node_id is None:
                return False
            uid = self.uid(user)
            if uid in (row.banned or []) or uid not in (row.members or {}):
                return False
            node = await session.get(Node, row.node_id)
            return node is not None and await sessions.can_write(session, user.id, node)

    async def fail_callbacks(self, n: int) -> None:
        assert self.failing is not None
        self.failing.left = n

    async def crash(self) -> None:
        assert self.http is not None
        await self.http.post("/__fake/kill")
        await self.http.post("/__fake/restart")

    async def restart_app(self) -> None:
        """De app stopt en start: dezelfde database en blobs, het geheugen leeg (ook de
        testhaak), en een nieuwe client naar DS."""
        assert self.failing is not None
        self.failing.left = 0
        for task in list(saving._background):
            task.cancel()
        self.fresh_state()
        engine.reset()

    async def use_transport(self, transport: httpx.AsyncBaseTransport) -> None:
        """De app praat voortaan via transport met DS (een nieuwe client)."""
        await ds_client.close()
        ds_client.transport = transport
        engine.reset()

    def forgotten(self) -> dict:
        return fake_forgotten.store(self.fake)

    # --- DS spelen: callbacks met het office-geheim ---

    def uid(self, user: U) -> str:
        return sessions.ds_uid(self.settings, user.id)

    def stage(self, key: str, content: bytes, name: str = "output.docx") -> str:
        """Zet content klaar zoals DS een opslag klaarzet; het adres (op de host van de
        browser, met /office ervoor, zoals DS het in een callback geeft)."""
        self._parts += 1
        part = f"{key}_{self._parts}"
        self.fake.files[part] = {name: content}
        path = f"/cache/files/data/{part}/{name}/{name}"
        return protocol.secure_link(
            "https://cloud.example/office", self.fake.link_secret, path, key, name
        )

    def saved_fields(
        self,
        key: str,
        status: int,
        url: str | None,
        editors: Sequence[U] = (),
        *,
        at: float | None = None,
        forcesavetype: int | None = None,
        users: Sequence[U] | None = None,
    ) -> dict:
        changes = [
            {"created": protocol.created(time.time()), "user": {"id": self.uid(u), "name": u.name}}
            for u in editors
        ]
        fields = protocol.saved(
            key,
            status,
            url=url or "",
            changesurl="",
            changes=changes,
            users=[self.uid(u) for u in (users if users is not None else editors[-1:])],
            lastsave_at=time.time() if at is None else at,
            filetype="docx",
            forcesavetype=forcesavetype,
        )
        if url is None:
            del fields["url"]
        return fields

    async def callback(
        self,
        fields: dict,
        *,
        body_token: bool = True,
        header: bool = True,
        secret: str | None = None,
        extra: dict | None = None,
    ) -> httpx.Response:
        """Een callback zoals DS hem stuurt (token in de body en in de header)."""
        body, authorization = protocol.callback_request(
            secret or self.settings.office_secret, fields
        )
        if not body_token:
            body = {k: v for k, v in body.items() if k != "token"}
        body.update(extra or {})
        headers = {"Authorization": authorization} if header else {}
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=internal_app), base_url="http://platform:8081"
        ) as internal:
            return await internal.post(CALLBACK, json=body, headers=headers)

    async def presence(self, key: str, users: Sequence[U]) -> httpx.Response:
        return await self.callback(protocol.editing(key, [self.uid(u) for u in users]))

    async def begin(self, user: U, node: uuid.UUID) -> str:
        """Een sessie zoals na het openen en de eerste status 1: open, met user erin."""
        r = await self.open_config(user, node)
        assert r.status_code == 200, r.text
        key = r.json()["key"]
        r = await self.presence(key, [user])
        assert r.status_code == 200 and r.json() == {"error": 0}
        return key

    def commands(self, c: str | None = None) -> list[dict]:
        return [e for e in self.fake.entries if e["kind"] == "command" and c in (None, e["c"])]

    def spy(self) -> list[tuple[str, dict]]:
        """Vanaf nu: elk commando dat bij de nep binnenkomt, met zijn parameters (c, params)."""
        sent: list[tuple[str, dict]] = []
        for c, handler in list(self.fake.commands.items()):

            async def wrapped(params: dict, c: str = c, handler: Any = handler) -> dict:
                sent.append((c, {k: v for k, v in params.items() if k not in ("c", "token")}))
                return await handler(params)

            self.fake.commands[c] = wrapped
        return sent

    def answer(self, c: str, reply: Any) -> None:
        """Het commando c geeft voortaan reply(params) (een coroutine-functie) als antwoord."""
        self.fake.commands[c] = reply


def office_settings(settings: Settings, **extra: Any) -> Settings:
    return Settings(
        **{
            **settings.model_dump(),
            "office_url": "http://office",
            "office_app_url": "http://platform:8081",
            "office_secret": secrets.token_urlsafe(32),
            **extra,
        }
    )


@contextlib.asynccontextmanager
async def office_world(client: httpx.AsyncClient, settings: Settings, **extra: Any):
    office = office_settings(settings, **extra)
    world = World(client, settings, office)
    runtime.bind(app, office)
    engine.reset()
    original = events.hub.publish

    def record(user_ids, type: str, data: dict) -> None:
        for user_id in set(user_ids):
            world.published.append(Published(user_id, type, data))
        original(user_ids, type, data)

    events.hub.publish = record  # type: ignore[method-assign]
    sessions.clock = world.now
    world.failing = Failing(httpx.ASGITransport(app=internal_app))
    config = FakeConfig(
        secret=office.office_secret,
        save_delay=0.2,
        time_scale=0.01,
        transport=world.failing,
    )
    world.fake_app = create_app(config)
    world.counting = Counting(httpx.ASGITransport(app=world.fake_app))
    ds_client.transport = world.counting
    world.fresh_state()
    try:
        async with served(world.fake_app) as port:
            world.port = port
            world.http = httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", trust_env=False)
            try:
                yield world
            finally:
                for editor in world.editors:
                    with contextlib.suppress(Exception):
                        await editor.close()
                with contextlib.suppress(Exception):
                    await world.settle(3)
                await world.http.aclose()
    finally:
        for task in list(saving._background):
            task.cancel()
        sessions.clock = sessions._real_now
        events.hub.publish = original  # type: ignore[method-assign]
        await ds_client.close()
        ds_client.transport = None
        engine.reset()
        runtime.unbind()
        sweep.forget()
        recovery._warned.clear()
        for name in ("office", "office_policy"):
            if hasattr(app.state, name):
                delattr(app.state, name)


async def wait_until(check, seconds: float = 5.0, step: float = 0.02) -> None:
    """Begrensd wachten tot check() waar is (een functie of coroutine-functie)."""
    deadline = time.monotonic() + seconds
    for _ in range(int(seconds / step) + 1):
        result = check()
        if asyncio.iscoroutine(result):
            result = await result
        if result:
            return
        if time.monotonic() > deadline:
            break
        await asyncio.sleep(step)
    raise AssertionError("wachten duurde te lang")


async def watchers_of(session, node_id: uuid.UUID) -> set[uuid.UUID]:
    return set((await access.watchers(session, [node_id])).get(node_id, {}))
