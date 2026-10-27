"""De callbacks van de editor (Keuze 15, 16 en 17): de test speelt DS met het office-geheim
(tests/office_world.py), de inhoud staat klaar op een echte secure link van de nep-DS."""

import logging
import time
import uuid

import httpx
import jwt
import pytest
from sqlalchemy import update
from sqlalchemy.exc import OperationalError

from app.main import internal_app
from app.models import Node, User
from app.office import saving, sessions
from app.office.oo import client as ds_client
from app.storage import BlobStore
from tests.factories import code_of, maker
from tests.fake_docserver import docx, protocol
from tests.office_contract import RECOVERED
from tests.office_world import office_world

pytestmark = pytest.mark.anyio
OK = {"error": 0}
REFUSED = {"error": 1}


@pytest.fixture
async def world(client, settings):
    async with office_world(client, settings) as w:
        yield w


async def anna_file(world, name="verslag.docx", text=("Begin",)):
    anna = await world.user("anna@example.nl")
    node = await world.docx(anna, None, name, list(text))
    return anna, node


async def save(world, key, status, content, editors, **kw):
    url = world.stage(key, content)
    return await world.callback(world.saved_fields(key, status, url, editors, **kw))


def body(paragraphs) -> bytes:
    return docx.make_docx(list(paragraphs))


# --- status 1 ---


async def test_status_1_werkt_connected_bij_en_meldt_presence(world, caplog):
    caplog.set_level(logging.INFO, logger="platform.office")
    anna, node = await anna_file(world)
    key = (await world.open_config(anna, node)).json()["key"]
    assert (await world.row(key)).state == "pending"
    r = await world.presence(key, [anna])
    assert r.status_code == 200 and r.json() == OK
    row = await world.row(key)
    assert row.state == "open" and row.connected == [world.uid(anna)]
    assert world.event_data(anna, "office.presence")[-1] == {"node_id": str(node), "count": 1}
    # Een onbekende ds_uid: alleen een logregel
    caplog.clear()
    r = await world.callback(protocol.editing(key, [world.uid(anna), "vreemde0000000000000000"]))
    assert r.json() == OK
    assert any("onbekende gebruiker" in rec.getMessage() for rec in caplog.records)


async def test_onbekende_key(world):
    key = f"{uuid.uuid4().hex}-0123456789abcdef"
    for fields, want in (
        (protocol.editing(key, ["u1"]), OK),
        (protocol.closed(key), OK),
        (world.saved_fields(key, 2, "https://x/cache/files/data/k/a/a"), REFUSED),
        (world.saved_fields(key, 6, "https://x/cache/files/data/k/a/a"), REFUSED),
        ({"key": key, "status": 3}, REFUSED),
    ):
        r = await world.callback(fields)
        assert r.status_code == 200 and r.json() == want, fields["status"]


async def test_status_7_en_een_vreemde_status(world, caplog):
    anna, node = await anna_file(world)
    key = await world.begin(anna, node)
    assert (await world.callback({"key": key, "status": 7})).json() == OK
    assert (await world.callback({"key": key, "status": 99})).json() == OK
    assert (await world.row(key)).state == "open"


# --- status 2, 4 en 6 ---


async def test_status_2_een_versie_door_de_laatste_bewerker(world):
    anna, node = await anna_file(world)
    bram = await world.user("bram@example.nl")
    folder = await world.folder(anna, "Samen")
    await world.share(anna, folder, bram, write=True)
    await world.move(anna, node, folder)
    key = await world.begin(anna, node)
    await world.open_config(bram, node)
    v0 = await world.version_count(node)
    content = body(["Begin", "Anna", "Bram"])
    r = await save(world, key, 2, content, [anna, bram])
    assert r.status_code == 200 and r.json() == OK
    assert await world.content(node) == content
    versions = await world.versions(node)
    assert len(versions) == v0 + 1 and versions[-1].replaced_by == bram.id
    row = await world.row(key)
    assert row.state == "closed" and row.closed_reason == "saved"
    assert row.versions_made == 1 and row.checkpoint == row.blob_id
    # Een herhaling van dezelfde callback: geen tweede versie
    r = await save(world, key, 2, content, [anna, bram])
    assert r.json() == OK and await world.version_count(node) == v0 + 1


async def test_status_4_sluit_zonder_wijzigingen(world):
    anna, node = await anna_file(world)
    key = await world.begin(anna, node)
    r = await world.callback(protocol.closed(key, (0, world.uid(anna))))
    assert r.json() == OK
    row = await world.row(key)
    assert row.state == "closed" and row.closed_reason == "no_changes"
    # Status 2 zonder adres (niets te halen) sluit ook zonder wijzigingen
    key2 = await world.begin(anna, node)
    r = await world.callback(world.saved_fields(key2, 2, None, [anna]))
    assert r.json() == OK and (await world.row(key2)).closed_reason == "no_changes"


async def test_6_6_2_binnen_30_minuten_een_versie(world):
    anna, node = await anna_file(world)
    key = await world.begin(anna, node)
    v0 = await world.version_count(node)
    for n, status in enumerate((6, 6, 2)):
        content = body(["Begin", f"stand {n}"])
        r = await save(world, key, status, content, [anna], at=time.time() + n)
        assert r.json() == OK
        if status == 6:
            assert (await world.row(key)).state == "open"  # de key blijft
            assert (await world.open_config(anna, node)).json()["key"] == key
        await world.advance(5)
    assert await world.version_count(node) == v0 + 1
    assert await world.paragraphs(node) == ["Begin", "stand 2"]


async def test_status_6_na_31_minuten_een_tweede_versie(world):
    anna, node = await anna_file(world)
    key = await world.begin(anna, node)
    v0 = await world.version_count(node)
    await save(world, key, 6, body(["een"]), [anna], at=time.time())
    await world.advance(31)
    await save(world, key, 6, body(["twee"]), [anna], at=time.time() + 1)
    assert await world.version_count(node) == v0 + 2
    row = await world.row(key)
    assert row.versions_made == 2


async def test_30_tussenstanden_laten_de_oudste_staan(world):
    """30 keer status 6 binnen 30 minuten: één versie (die van vóór de sessie), en die blijft."""
    anna, node = await anna_file(world)
    before = await world.content(node)
    key = await world.begin(anna, node)
    start = time.time()
    for n in range(30):
        r = await save(world, key, 6, body([f"stand {n}"]), [anna], at=start + n)
        assert r.json() == OK
        await world.advance(0.9)
    versions = await world.versions(node)
    assert len(versions) == 1
    async with maker(world.base_settings)() as session:
        node_row = await session.get(Node, node)
        assert node_row.blob_id != versions[0].blob_id
    assert BlobStore(world.settings.blob_dir).path(versions[0].blob_id).read_bytes() == before


async def test_een_oud_merk_na_een_nieuwer_schrijft_niets(world):
    anna, node = await anna_file(world)
    key = await world.begin(anna, node)
    now = time.time()
    await save(world, key, 6, body(["nieuw"]), [anna], at=now)
    r = await save(world, key, 6, body(["oud"]), [anna], at=now - 10)
    assert r.json() == OK
    assert await world.paragraphs(node) == ["nieuw"]
    # Een gelijk merk mag (A9: hele seconden); de sha beslist
    r = await save(world, key, 6, body(["zelfde seconde"]), [anna], at=now)
    assert r.json() == OK and await world.paragraphs(node) == ["zelfde seconde"]
    assert (await world.row(key)).applied_mark == protocol.lastsave(now)


async def test_status_3_met_url_een_kopie_naast_het_origineel(world):
    anna, node = await anna_file(world)
    folder = await world.folder(anna, "Werk")
    await world.move(anna, node, folder)
    before = await world.content(node)
    key = await world.begin(anna, node)
    url = world.stage(key, body(["misschien kapot"]))
    fields = world.saved_fields(key, 2, url, [anna])
    fields["status"] = 3
    r = await world.callback(fields)
    assert r.json() == OK
    assert await world.content(node) == before
    copies = await world.find(anna, "verslag" + RECOVERED)
    assert len(copies) == 1
    view = await world.node(copies[0])
    assert view.parent_id == folder and not view.trashed
    assert await world.paragraphs(copies[0]) == ["misschien kapot"]
    row = await world.row(key)
    assert row.state == "closed" and row.closed_reason == "copy"
    recovered = world.event_data(anna, "office.recovered")
    assert recovered[-1]["reason"] == "status3" and recovered[-1]["copy_id"] == str(copies[0])


async def test_status_3_zonder_url(world):
    """A8: geen adres en geen herstelbestand. Dicht als verloren, en een melding."""
    anna, node = await anna_file(world)
    before = await world.content(node)
    key = await world.begin(anna, node)
    r = await world.callback({"key": key, "status": 3, "users": [world.uid(anna)]})
    assert r.json() == OK
    row = await world.row(key)
    assert row.state == "closed" and row.closed_reason == "lost"
    assert await world.content(node) == before
    unsaved = world.event_data(anna, "office.unsaved")[-1]
    assert unsaved == {
        "node_id": str(node),
        "name": "verslag.docx",
        "reason": "error_status",
        "final": True,
    }


async def test_status_3_in_een_map_in_de_prullenbak_naar_de_hoofdmap(world):
    anna, node = await anna_file(world)
    folder = await world.folder(anna, "Weg")
    await world.move(anna, node, folder)
    key = await world.begin(anna, node)
    await world.trash(anna, folder)
    url = world.stage(key, body(["kapot"]))
    fields = world.saved_fields(key, 2, url, [anna])
    fields["status"] = 3
    assert (await world.callback(fields)).json() == OK
    copies = await world.find(anna, "verslag" + RECOVERED)
    assert len(copies) == 1
    assert (await world.node(copies[0])).parent_id == await world.root(anna)


async def test_definitief_weg_schrijft_niets(world):
    anna, node = await anna_file(world)
    key = await world.begin(anna, node)
    await world.purge(anna, node)
    r = await save(world, key, 2, body(["voor niets"]), [anna])
    assert r.json() == OK
    assert await world.find(anna, "verslag") == []
    row = await world.row(key)
    assert row.state == "closed" and row.closed_reason == "gone"


async def test_eigenaar_weg_schrijft_niets(world):
    anna, node = await anna_file(world)
    key = await world.begin(anna, node)
    async with maker(world.base_settings)() as session:
        await session.delete(await session.get(Node, node))
        await session.delete(await session.get(User, anna.id))
        await session.commit()
    r = await save(world, key, 2, body(["voor niets"]), [anna])
    assert r.json() == OK
    assert (await world.row(key)).closed_reason == "gone"


async def test_te_groot_en_quotum_vol(world, monkeypatch):
    anna, node = await anna_file(world)
    before = await world.content(node)
    key = await world.begin(anna, node)
    big = body(["x" * 5000])
    monkeypatch.setattr(world.settings, "office_max_save_bytes", len(big) - 1)
    r = await save(world, key, 6, big, [anna])
    assert r.status_code == 200 and r.json() == REFUSED
    assert await world.content(node) == before
    assert world.event_data(anna, "office.unsaved")[-1]["reason"] == "too_large"
    assert world.event_data(anna, "office.unsaved")[-1]["final"] is False
    assert (await world.row(key)).state == "open"  # een tussenstand: de key blijft
    monkeypatch.setattr(world.settings, "office_max_save_bytes", 512 * 1024**2)
    await world.room(anna, 10)
    r = await save(world, key, 2, big, [anna])
    assert r.json() == REFUSED
    assert await world.content(node) == before
    unsaved = world.event_data(anna, "office.unsaved")[-1]
    assert unsaved["reason"] == "no_room" and unsaved["final"] is True
    row = await world.row(key)
    assert row.state == "closed" and row.closed_reason == "refused"
    assert await world.find(anna, "verslag" + RECOVERED) == []


async def test_quotum_vol_bij_het_neerzetten(world, monkeypatch):
    """Na het downloaden nam iets anders de ruimte in (hier: de grens van de download kijkt
    niet naar het quotum): place_file weigert (no_room), en ook dat is een blijvende fout."""
    anna, node = await anna_file(world)
    before = await world.content(node)
    key = await world.begin(anna, node)
    await world.room(anna, 0)

    async def no_limit(session, settings, owner):
        return settings.office_max_save_bytes, False

    monkeypatch.setattr(saving, "limit_for", no_limit)
    r = await save(world, key, 6, body(["Begin", "iets meer"]), [anna])
    assert r.json() == REFUSED
    assert await world.content(node) == before
    unsaved = world.event_data(anna, "office.unsaved")[-1]
    assert unsaved["reason"] == "no_room" and unsaved["final"] is False
    assert (await world.row(key)).last_error == "no_room"


# --- tokens ---


async def test_tokens(world):
    anna, node = await anna_file(world)
    key = await world.begin(anna, node)
    fields = protocol.editing(key, [world.uid(anna)])
    # Alleen in de header, alleen in de body: allebei goed
    assert (await world.callback(fields, body_token=False)).json() == OK
    assert (await world.callback(fields, header=False)).json() == OK
    secret = world.settings.office_secret
    bad = [
        await world.callback(fields, secret="een-ander-geheim-van-genoeg-lengte!!"),
        await world.callback(fields, body_token=False, header=False),
    ]

    async def raw(token: str, *, in_body: bool = True):
        payload = {**fields, "token": token} if in_body else dict(fields)
        headers = {} if in_body else {"Authorization": f"Bearer {token}"}
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=internal_app),
            base_url="http://platform:8081",
        ) as internal:
            return await internal.post("/internal/office/callback", json=payload, headers=headers)

    now = int(time.time())
    bad.append(await raw(jwt.encode({**fields, "operation": "callback", "exp": now - 600}, secret)))
    bad.append(await raw(jwt.encode({**fields, "operation": "download", "exp": now + 60}, secret)))
    bad.append(await raw(jwt.encode({**fields, "exp": now + 60}, secret)))
    none = jwt.encode({**fields, "operation": "callback", "exp": now + 60}, None, algorithm="none")
    bad.append(await raw(none))
    bad.append(await raw(none, in_body=False))
    bad.append(await raw("geen-jwt"))
    for r in bad:
        assert r.status_code == 401 and code_of(r) == "office_token", r.text


async def test_losse_bodyvelden_tellen_niet(world):
    anna, node = await anna_file(world)
    before = await world.content(node)
    key = await world.begin(anna, node)
    # Het token zegt status 1; de body zegt status 2 met een adres
    url = world.stage(key, body(["ingesmokkeld"]))
    r = await world.callback(
        protocol.editing(key, [world.uid(anna)]), extra={"status": 2, "url": url}
    )
    assert r.json() == OK
    assert await world.content(node) == before
    assert (await world.row(key)).state == "open"


async def test_vreemde_adressen_worden_nooit_opgehaald(world):
    anna, node = await anna_file(world)
    before = await world.content(node)
    key = await world.begin(anna, node)
    seen: list[httpx.Request] = []
    inner = ds_client.transport

    class Recording(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            seen.append(request)
            if request.url.host != "office":
                raise AssertionError(f"naar een andere host: {request.url}")
            return await inner.handle_async_request(request)

    await world.use_transport(Recording())
    other = f"{uuid.uuid4().hex}-0123456789abcdef"
    good = world.stage(key, body(["via de goede weg"]))
    path = good.split("/office", 1)[1]
    for url in (
        f"https://cloud.example/office/cache/files/data/{other}_1/output.docx/output.docx",
        f"https://cloud.example/office/cache/files/data/{key}_1/../../{other}/x",
        f"https://cloud.example/office/cache/files/data/{key}_1/%2e%2e/x",
        "file:///etc/passwd",
    ):
        r = await world.callback(world.saved_fields(key, 6, url, [anna]))
        assert r.status_code == 200 and r.json() == REFUSED, url
    assert seen == [] and await world.content(node) == before
    # Een andere host met een goed pad: alleen het pad telt, naar PLATFORM_OFFICE_URL
    r = await world.callback(world.saved_fields(key, 6, "https://evil.example" + path, [anna]))
    assert r.json() == OK
    assert [str(s.url).split("?")[0] for s in seen] == ["http://office" + path.split("?")[0]]
    assert await world.paragraphs(node) == ["via de goede weg"]


# --- tijdelijke fouten ---


async def test_database_bezet_geeft_503(world, monkeypatch):
    anna, node = await anna_file(world)
    key = await world.begin(anna, node)

    async def locked(session, key):
        raise OperationalError("SELECT", {}, Exception("database is locked"))

    monkeypatch.setattr(sessions, "find_by_key", locked)
    r = await save(world, key, 6, body(["x"]), [anna])
    assert r.status_code == 503


async def test_download_die_halverwege_stopt(world):
    anna, node = await anna_file(world)
    before = await world.content(node)
    v0 = await world.version_count(node)
    key = await world.begin(anna, node)
    inner = ds_client.transport

    class Broken(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"PK\x03\x04" + b"x" * 100
            raise httpx.ReadError("weg")

    class Halfway(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            if "/cache/files/data/" in request.url.path:
                return httpx.Response(200, stream=Broken())
            return await inner.handle_async_request(request)

    await world.use_transport(Halfway())
    r = await save(world, key, 2, body(["half"]), [anna])
    assert r.status_code == 503
    assert await world.content(node) == before and await world.version_count(node) == v0
    assert (await world.row(key)).state == "saving"  # DS probeert opnieuw
    tmp = world.settings.blob_dir / ".tmp"
    assert not tmp.exists() or list(tmp.iterdir()) == []
    # De volgende poging lukt
    await world.use_transport(inner)
    r = await save(world, key, 2, body(["heel"]), [anna])
    assert r.json() == OK and await world.paragraphs(node) == ["heel"]
    assert (await world.row(key)).closed_reason == "saved"


async def test_ds_link_geeft_5xx(world):
    anna, node = await anna_file(world)
    key = await world.begin(anna, node)
    url = world.stage(key, body(["x"]))
    world.fake.up = False  # de nep geeft 503 op alles
    r = await world.callback(world.saved_fields(key, 6, url, [anna]))
    world.fake.up = True
    assert r.status_code == 503


# --- wie slaat op ---


async def test_status_6_zonder_schrijver_geen_kopie(world):
    anna, node = await anna_file(world)
    bram = await world.user("bram@example.nl")
    folder = await world.folder(anna, "Samen")
    share = await world.share(anna, folder, bram, write=True)
    await world.move(anna, node, folder)
    key = await world.begin(bram, node)
    before = await world.content(node)
    await world.revoke(anna, share)
    r = await save(world, key, 6, body(["Bram"]), [bram])
    assert r.json() == REFUSED
    assert await world.content(node) == before
    assert await world.find(anna, "verslag" + RECOVERED) == []
    # De laatste opslag zonder schrijver: een herstelkopie bij Anna, alleen Anna hoort het
    r = await save(world, key, 2, body(["Bram"]), [bram])
    assert r.json() == OK
    copies = await world.find(anna, "verslag" + RECOVERED)
    assert len(copies) == 1 and await world.content(node) == before
    assert (await world.node(copies[0])).parent_id == await world.root(anna)
    assert "office.recovered" in await world.events(anna)
    assert "office.recovered" not in await world.events(bram)
    row = await world.row(key)
    assert row.closed_reason == "copy" and row.last_error == "no_writer"


async def test_de_eigenaar_slaat_op_als_de_bewerker_geen_recht_meer_heeft(world):
    anna, node = await anna_file(world)
    bram = await world.user("bram@example.nl")
    folder = await world.folder(anna, "Samen")
    share = await world.share(anna, folder, bram, write=True)
    await world.move(anna, node, folder)
    key = await world.begin(anna, node)
    await world.open_config(bram, node)
    await world.revoke(anna, share)
    r = await save(world, key, 2, body(["samen"]), [anna, bram])
    assert r.json() == OK
    assert await world.paragraphs(node) == ["samen"]
    versions = await world.versions(node)
    assert versions[-1].replaced_by == anna.id


async def test_geblokkeerde_bewerker_telt_niet(world):
    anna, node = await anna_file(world)
    bram = await world.user("bram@example.nl")
    folder = await world.folder(anna, "Samen")
    await world.share(anna, folder, bram, write=True)
    await world.move(anna, node, folder)
    key = await world.begin(anna, node)
    await world.open_config(bram, node)
    async with maker(world.base_settings)() as session:
        await session.execute(
            update(User).where(User.id == bram.id).values(disabled_at=world.now())
        )
        await session.commit()
    await save(world, key, 2, body(["x"]), [anna, bram])
    assert (await world.versions(node))[-1].replaced_by == anna.id


# --- de tabel van dichte sessies (Keuze 15) ---

FINAL_REASONS = ["saved", "no_changes", "copy", "refused", "gone"]


@pytest.mark.parametrize("reason", [*FINAL_REASONS, "lost"])
async def test_dicht_status_1_drop(world, reason):
    anna, node = await anna_file(world)
    key = await world.begin(anna, node)
    await world.set_session(key, state="closed", closed_reason=reason)
    r = await world.presence(key, [anna])
    assert r.json() == OK
    await world.settle()
    drops = world.commands("drop")
    assert drops, "geen drop"
    dropped = world.event_data(anna, "office.dropped")
    assert dropped[-1] == {"node_id": str(node), "key": key, "reason": "stale"}
    assert (await world.row(key)).state == "closed"


@pytest.mark.parametrize("reason", FINAL_REASONS)
@pytest.mark.parametrize("status", [2, 3, 6])
async def test_dicht_eigen_eindopslag_schrijft_niets(world, reason, status):
    anna, node = await anna_file(world)
    before = await world.content(node)
    key = await world.begin(anna, node)
    await world.set_session(key, state="closed", closed_reason=reason)
    url = world.stage(key, body(["laat"]))
    fields = world.saved_fields(key, 2 if status == 3 else status, url, [anna])
    fields["status"] = status
    r = await world.callback(fields)
    assert r.json() == OK
    assert await world.content(node) == before
    assert await world.find(anna, "verslag" + RECOVERED) == []


@pytest.mark.parametrize("reason", [*FINAL_REASONS, "lost"])
async def test_dicht_status_4(world, reason):
    anna, node = await anna_file(world)
    key = await world.begin(anna, node)
    await world.set_session(key, state="closed", closed_reason=reason)
    assert (await world.callback(protocol.closed(key))).json() == OK
    assert (await world.row(key)).closed_reason == reason


@pytest.mark.parametrize("status", [2, 6])
async def test_lost_late_opslag_op_het_origineel(world, status):
    anna, node = await anna_file(world)
    key = await world.begin(anna, node)
    v0 = await world.version_count(node)
    await world.set_session(key, state="closed", closed_reason="lost")
    r = await save(world, key, status, body(["laat maar goed"]), [anna])
    assert r.json() == OK
    assert await world.paragraphs(node) == ["laat maar goed"]
    assert await world.version_count(node) == v0 + 1  # wat er stond, blijft als versie
    assert await world.find(anna, "verslag" + RECOVERED) == []
    assert (await world.row(key)).closed_reason == "lost"


@pytest.mark.parametrize("status", [2, 3, 6])
async def test_lost_late_opslag_na_een_wijziging_een_kopie(world, status):
    anna, node = await anna_file(world)
    key = await world.begin(anna, node)
    await world.set_session(key, state="closed", closed_reason="lost")
    await world.upload(anna, node, ["van buiten"])
    url = world.stage(key, body(["laat"]))
    fields = world.saved_fields(key, 2 if status == 3 else status, url, [anna])
    fields["status"] = status
    r = await world.callback(fields)
    assert r.json() == OK
    assert await world.paragraphs(node) == ["van buiten"]
    copies = await world.find(anna, "verslag" + RECOVERED)
    assert len(copies) == 1 and await world.paragraphs(copies[0]) == ["laat"]


async def test_lost_status_3_ongewijzigd_ook_een_kopie(world):
    """Status 3 is altijd een kopie, ook als late opslag (de inhoud kan kapot zijn)."""
    anna, node = await anna_file(world)
    before = await world.content(node)
    key = await world.begin(anna, node)
    await world.set_session(key, state="closed", closed_reason="lost")
    url = world.stage(key, body(["laat"]))
    fields = world.saved_fields(key, 2, url, [anna])
    fields["status"] = 3
    assert (await world.callback(fields)).json() == OK
    assert await world.content(node) == before
    assert len(await world.find(anna, "verslag" + RECOVERED)) == 1
