"""files.notify_many: één event per (ontvanger, ouder, oude ouder) met hooguit 500 ids, aan
dezelfde mensen en met dezelfde zichtbaarheid van parent_id als notify()."""

import uuid

import pytest
from sqlalchemy import update

from app import events
from app.events import Hub
from app.models import Node, User
from app.routers.files import notify_many
from tests.factories import create_file, maker
from tests.test_files import mkdir, put, root
from tests.test_files_events import drain
from tests.test_shares import person, share_with


@pytest.fixture
def hub(monkeypatch):
    fresh = Hub()
    monkeypatch.setattr(events, "hub", fresh)
    return fresh


async def nodes_of(session, *ids) -> list[Node]:
    return [await session.get(Node, uuid.UUID(str(i))) for i in ids]


@pytest.fixture
async def tree(client, admin):
    """Jonas met Werk/Gedeeld (gedeeld met Anna) en daarin twee bestanden; los.txt in Werk is
    alleen zelf met Bert gedeeld."""
    jonas, tokens = admin
    root_id = (await root(client, jonas))["folder"]["id"]
    werk = await mkdir(client, jonas, root_id, "Werk")
    gedeeld = await mkdir(client, jonas, werk, "Gedeeld")
    a = (await put(client, jonas, gedeeld, "a.txt", b"a")).json()["id"]
    b = (await put(client, jonas, gedeeld, "b.txt", b"b")).json()["id"]
    los = (await put(client, jonas, werk, "los.txt", b"l")).json()["id"]
    _, anna = await person(client, jonas, "anna@example.nl")
    _, bert = await person(client, jonas, "bert@example.nl")
    await share_with(client, jonas, gedeeld, user_id=anna, permission="read")
    await share_with(client, jonas, los, user_id=bert, permission="read")
    return {
        "jonas": tokens["user"]["id"],
        "anna": anna,
        "bert": bert,
        "root": root_id,
        "werk": werk,
        "gedeeld": gedeeld,
        "a": a,
        "b": b,
        "los": los,
    }


@pytest.mark.anyio
async def test_gegroepeerd_per_ouder_en_zichtbaarheid(settings, tree, hub):
    t = tree
    with (
        hub.subscribe(uuid.UUID(t["jonas"])) as j,
        hub.subscribe(uuid.UUID(t["anna"])) as a,
        hub.subscribe(uuid.UUID(t["bert"])) as b,
    ):
        async with maker(settings)() as session:
            nodes = await nodes_of(session, t["a"], t["b"], t["los"])
            await notify_many(session, nodes, "node.changed")
        assert drain(j) == [
            ("node.changed", {"ids": [t["a"], t["b"]], "parent_id": t["gedeeld"]}),
            ("node.changed", {"id": t["los"], "parent_id": t["werk"]}),
        ]
        # Anna ziet Gedeeld (de deling staat erboven)
        assert drain(a) == [("node.changed", {"ids": [t["a"], t["b"]], "parent_id": t["gedeeld"]})]
        # Bert heeft alleen los.txt zelf: geen map erboven
        assert drain(b) == [("node.changed", {"id": t["los"], "parent_id": None})]


@pytest.mark.anyio
async def test_verplaatst_met_oude_ouder(settings, tree, hub):
    t = tree
    async with maker(settings)() as session:
        await session.execute(
            update(Node).where(Node.id == uuid.UUID(t["a"])).values(parent_id=uuid.UUID(t["werk"]))
        )
        await session.commit()
    with hub.subscribe(uuid.UUID(t["jonas"])) as j, hub.subscribe(uuid.UUID(t["anna"])) as a:
        async with maker(settings)() as session:
            nodes = await nodes_of(session, t["a"])
            old = {nodes[0].id: uuid.UUID(t["gedeeld"])}
            await notify_many(session, nodes, "node.changed", old_parents=old)
        assert drain(j) == [
            ("node.changed", {"id": t["a"], "parent_id": t["werk"], "old_parent_id": t["gedeeld"]})
        ]
        # Anna zag alleen de oude plek: waar het wegging, niet waarheen
        assert drain(a) == [
            ("node.changed", {"id": t["a"], "parent_id": None, "old_parent_id": t["gedeeld"]})
        ]


@pytest.mark.anyio
async def test_hooguit_500_per_event(settings, tree, hub):
    t = tree
    async with maker(settings)() as session:
        owner = await session.get(User, uuid.UUID(t["jonas"]))
        made = [
            await create_file(session, owner, uuid.UUID(t["gedeeld"]), f"f{i}.txt", data=b"x")
            for i in range(501)
        ]
        await session.commit()
    with hub.subscribe(uuid.UUID(t["jonas"])) as j, hub.subscribe(uuid.UUID(t["anna"])) as a:
        async with maker(settings)() as session:
            nodes = await nodes_of(session, *(n.id for n in made))
            await notify_many(session, nodes, "node.created")
        for sub in (j, a):
            seen = drain(sub)
            assert [len(data.get("ids", [])) for _, data in seen] == [500, 0]
            assert seen[1] == ("node.created", {"id": str(made[-1].id), "parent_id": t["gedeeld"]})
            ids = [*seen[0][1]["ids"], seen[1][1]["id"]]
            assert ids == [str(n.id) for n in made]


@pytest.mark.anyio
async def test_below_en_vooraf_uitgerekend_seen(settings, tree, hub):
    t = tree
    with (
        hub.subscribe(uuid.UUID(t["jonas"])) as j,
        hub.subscribe(uuid.UUID(t["anna"])) as a,
        hub.subscribe(uuid.UUID(t["bert"])) as b,
    ):
        async with maker(settings)() as session:
            nodes = await nodes_of(session, t["werk"])
            below = {
                nodes[0].id: {
                    uuid.UUID(t["anna"]): {uuid.UUID(t["gedeeld"])},
                    uuid.UUID(t["bert"]): {uuid.UUID(t["los"])},
                }
            }
            await notify_many(session, nodes, "node.deleted", seen={}, below=below)
            await notify_many(session, [], "node.deleted")  # niets: geen event
        assert drain(j) == [("node.deleted", {"id": t["werk"], "parent_id": t["root"]})]
        # Wie alleen iets daaronder gedeeld heeft, hoort over die gedeelde map zelf
        assert drain(a) == [("node.deleted", {"id": t["gedeeld"], "parent_id": None})]
        assert drain(b) == [("node.deleted", {"id": t["los"], "parent_id": None})]
