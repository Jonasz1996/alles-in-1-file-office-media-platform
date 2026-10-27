"""De ronde (Keuze 20) en het tussentijds opslaan (Keuze 18), met de nep-DS: pending verloopt,
info sluit of opent, saving te oud, wie zijn recht kwijt is, nog verbonden bij de volgende
ronde, bestand weg, forcesave van de klok (error 4 is goed), meldingen alleen voor wie mag,
oude rijen weg, en checkpoint_all."""

import asyncio
import time
from datetime import timedelta

import pytest
from sqlalchemy import update

from app.main import app
from app.models import User
from app.office import runtime, sessions, sweep
from app.office.state import OfficeConfig, office_policy
from tests.factories import maker
from tests.office_contract import RECOVERED
from tests.office_world import office_world, wait_until

pytestmark = pytest.mark.anyio
DAY = 24 * 60


@pytest.fixture
async def world(client, settings):
    async with office_world(client, settings) as w:
        yield w


async def shared(world):
    anna = await world.user("anna@example.nl")
    bram = await world.user("bram@example.nl")
    folder = await world.folder(anna, "Samen")
    share = await world.share(anna, folder, bram, write=True)
    node = await world.docx(anna, folder, "verslag.docx", ["Begin"])
    return anna, bram, share, node


def sent(spy, c):
    return [params for name, params in spy if name == c]


# --- pending ---


async def test_pending_verloopt_na_5_minuten_zonder_verbinding(world):
    anna = await world.user("anna@example.nl")
    node = await world.docx(anna, None, "verslag.docx", ["Begin"])
    r = await world.open_config(anna, node)
    key = r.json()["key"]
    await world.advance(4.9)
    await world.sweep()
    assert (await world.row(key)).state == "pending"
    await world.advance(0.2)
    await world.sweep()
    row = await world.row(key)
    assert row.state == "closed" and row.closed_reason == "lost"
    again = await world.open_config(anna, node)
    assert again.status_code == 200 and again.json()["key"] != key


async def test_pending_met_iemand_in_info_wordt_open(world):
    """De status 1 kwam niet aan, maar DS ziet er wel iemand in: open."""
    anna = await world.user("anna@example.nl")
    node = await world.docx(anna, None, "verslag.docx", ["Begin"])
    h = await world.open(anna, node)
    await world.set_session(h.key, state="pending", connected=[], updated_at=world.now())
    spy = world.spy()
    await world.advance(0.5)
    await world.sweep()
    assert sent(spy, "info") == []  # jonger dan 1 minuut: nog geen info
    await world.advance(0.6)
    await world.sweep()
    assert sent(spy, "info") == [{"key": h.key}]
    row = await world.row(h.key)
    assert row.state == "open" and row.connected == [world.uid(anna)]


async def test_pending_die_ds_niet_kent_blijft_pending_tot_5_minuten(world):
    """DS kent een key pas na de eerste verbinding: error 1 op een pending key sluit niet (de
    veilige lezing; alleen de regel van 5 minuten)."""
    anna = await world.user("anna@example.nl")
    node = await world.docx(anna, None, "verslag.docx", ["Begin"])
    key = (await world.open_config(anna, node)).json()["key"]
    spy = world.spy()
    await world.advance(2)
    await world.sweep()
    assert sent(spy, "info") == [{"key": key}]
    assert (await world.row(key)).state == "pending"
    assert (await world.open_config(anna, node)).json()["key"] == key


# --- info op een open key ---


async def test_info_error_1_sluit_een_open_sessie(world):
    """DS kent de key niet meer (een herstart van DS): dicht, de volgende opener een nieuwe key."""
    anna = await world.user("anna@example.nl")
    node = await world.docx(anna, None, "verslag.docx", ["Begin"])
    h = await world.open(anna, node)
    await world.crash()
    await world.sweep()
    row = await world.row(h.key)
    assert row.state == "closed" and row.closed_reason == "lost"
    again = await world.open(anna, node)
    assert again.key != h.key


async def test_open_twee_rondes_niemand_dan_lost(world):
    anna = await world.user("anna@example.nl")
    node = await world.docx(anna, None, "verslag.docx", ["Begin"])
    h = await world.open(anna, node)
    users: list = []

    async def info(params):
        return {"key": params.get("key"), "error": 0, "users": list(users)}

    world.answer("info", info)
    await world.sweep()
    assert (await world.row(h.key)).state == "open"  # één keer niemand
    await world.advance(1)
    await world.sweep()
    assert (await world.row(h.key)).state == "open"  # nog geen 2 minuten: geen info
    # Er is weer iemand: de teller begint opnieuw
    users.append(world.uid(anna))
    await world.advance(1.1)
    await world.sweep()
    row = await world.row(h.key)
    assert row.state == "open" and row.connected == [world.uid(anna)]
    users.clear()
    await world.advance(2.1)
    await world.sweep()
    assert (await world.row(h.key)).state == "open"
    await world.advance(2.1)
    await world.sweep()
    row = await world.row(h.key)
    assert row.state == "closed" and row.closed_reason == "lost"
    # Zonder die regel gaf de lege sessie voor altijd 409 office_busy; nu een nieuwe key
    again = await world.open_config(anna, node)
    assert again.status_code == 200 and again.json()["key"] != h.key


async def test_info_als_ds_weg_is_concludeert_niets(world):
    anna = await world.user("anna@example.nl")
    node = await world.docx(anna, None, "verslag.docx", ["Begin"])
    h = await world.open(anna, node)

    async def broken(params):
        raise RuntimeError("weg")

    world.answer("info", broken)
    for _ in range(3):
        await world.advance(2.1)
        await world.sweep()
    assert (await world.row(h.key)).state == "open"


# --- saving te oud ---


@pytest.mark.parametrize(
    ("answer", "state", "reason"),
    [
        ("users", "open", None),
        ("empty", "closed", "lost"),
        ("unknown", "closed", "lost"),
    ],
)
async def test_saving_ouder_dan_6_minuten_vraagt_info(world, answer, state, reason):
    anna = await world.user("anna@example.nl")
    node = await world.docx(anna, None, "verslag.docx", ["Begin"])
    h = await world.open(anna, node)
    await world.set_session(h.key, state="saving", updated_at=world.now())

    async def info(params):
        key = params.get("key")
        if answer == "unknown":
            return {"key": key, "error": 1}
        users = [world.uid(anna)] if answer == "users" else []
        return {"key": key, "error": 0, "users": users}

    world.answer("info", info)
    spy = world.spy()
    await world.advance(5.9)
    await world.sweep()
    assert (await world.row(h.key)).state == "saving"
    assert sent(spy, "info") == []
    await world.advance(0.2)
    await world.sweep()
    assert sent(spy, "info") == [{"key": h.key}]
    row = await world.row(h.key)
    assert row.state == state and row.closed_reason == reason


# --- rechten ---


async def test_ingetrokken_krijgt_drop_en_gaat_naar_banned(world):
    anna, bram, share, node = await shared(world)
    a = await world.open(anna, node)
    b = await world.open(bram, node)
    spy = world.spy()
    await world.revoke(anna, share)
    await world.sweep()
    assert {"key": a.key, "users": [world.uid(bram)]} in sent(spy, "drop")
    row = await world.row(a.key)
    assert world.uid(bram) not in row.members and world.uid(bram) in row.banned
    assert world.uid(anna) in row.members and world.uid(anna) not in row.banned
    assert world.event_data(bram, "office.dropped") == [
        {"node_id": str(node), "key": a.key, "reason": "access"}
    ]
    assert world.event_data(anna, "office.dropped") == []
    await wait_until(lambda: not b.state.reading, 5)
    # De volgende ronde: hij is weg, de sessie loopt door
    await world.advance(2.1)
    await world.sweep()
    assert (await world.row(a.key)).state == "open"
    assert len(world.event_data(bram, "office.dropped")) == 1


async def test_geblokkeerd_krijgt_drop(world):
    anna, bram, share, node = await shared(world)
    a = await world.open(anna, node)
    await world.open(bram, node)
    spy = world.spy()
    async with maker(world.base_settings)() as session:
        await session.execute(
            update(User).where(User.id == bram.id).values(disabled_at=world.now())
        )
        await session.commit()
    await world.sweep()
    assert {"key": a.key, "users": [world.uid(bram)]} in sent(spy, "drop")
    row = await world.row(a.key)
    assert world.uid(bram) in row.banned and world.uid(bram) not in row.members


async def test_een_gast_verliest_schrijfrecht_niet_door_de_ronde(world):
    """Een gast met een deling met write mag bewerken; de ronde laat hem staan."""
    anna = await world.user("anna@example.nl")
    gast = await world.user("gast@example.nl", role="guest")
    folder = await world.folder(anna, "Samen")
    await world.share(anna, folder, gast, write=True)
    node = await world.docx(anna, folder, "verslag.docx", ["Begin"])
    h = await world.open(gast, node)
    await world.sweep()
    row = await world.row(h.key)
    assert world.uid(gast) in row.members and row.banned == []


async def test_nog_in_een_tunnel_bij_de_volgende_ronde_stopt_de_sessie(world):
    """De drop haalde hem uit de editor, maar een tunnel van hem staat nog open (O1 telt ze in
    OfficeState.tunnels): forcesave "stop" en drop voor iedereen."""
    anna, bram, share, node = await shared(world)
    a = await world.open(anna, node)
    b = await world.open(bram, node)
    await world.type(a, "Anna")
    await world.revoke(anna, share)
    await world.sweep()
    await wait_until(lambda: not b.state.reading, 5)
    world.state.tunnels[a.key] = {bram.id: 1}
    spy = world.spy()
    await world.sweep()
    assert sent(spy, "forcesave") == [{"key": a.key, "userdata": "stop"}]
    assert {"key": a.key} in sent(spy, "drop")
    await world.settle()
    row = await world.row(a.key)
    assert row.state == "closed" and row.closed_reason == "saved"
    assert "Anna" in await world.paragraphs(node)
    world.state.tunnels.pop(a.key, None)


async def test_bestand_weg_drop_voor_iedereen(world):
    anna, bram, share, node = await shared(world)
    a = await world.open(anna, node)
    await world.open(bram, node)
    spy = world.spy()
    await world.purge(anna, node)
    await world.sweep()
    # Eerst drop voor iedereen; de status 1 die DS daarna voor wie nog even bleef stuurt, komt
    # op een dichte rij en geeft hooguit nog een drop met die gebruikers (Keuze 15)
    drops = sent(spy, "drop")
    assert drops[0] == {"key": a.key}
    everyone = {world.uid(anna), world.uid(bram)}
    assert all(d["key"] == a.key and set(d["users"]) <= everyone for d in drops[1:])
    row = await world.row(a.key)
    assert row.state == "closed" and row.closed_reason == "gone"


# --- meldingen alleen voor wie mag ---


async def test_recovered_en_unsaved_komen_nooit_bij_wie_zijn_recht_kwijt_is(world):
    anna, bram, share, node = await shared(world)
    b = await world.open(bram, node)
    await world.type(b, "Bram schreef")
    presence_before = len(world.event_data(bram, "office.presence"))
    await world.revoke(anna, share)
    await world.sweep()
    await world.settle()
    copies = await world.find(anna, "verslag" + RECOVERED)
    assert len(copies) == 1 and "Bram schreef" in await world.paragraphs(copies[0])
    assert world.event_data(anna, "office.recovered")[-1]["copy_id"] == str(copies[0])
    assert "office.recovered" not in await world.events(bram)
    assert "office.unsaved" not in await world.events(bram)
    assert len(world.event_data(bram, "office.presence")) == presence_before
    # Ook een melding "niet opgeslagen" (quotum vol) gaat alleen naar wie nog mag
    c = await world.open(anna, node)
    await world.type(c, "Anna")
    await world.room(anna, 0)
    await world.save(c, explicit=False)
    await world.settle()
    assert world.event_data(anna, "office.unsaved")
    assert "office.unsaved" not in await world.events(bram)


# --- tussentijds opslaan ---


async def test_tussentijds_opslaan_na_5_minuten_en_error_4_is_goed(world):
    anna = await world.user("anna@example.nl")
    node = await world.docx(anna, None, "verslag.docx", ["Begin"])
    h = await world.open(anna, node)
    await world.type(h, "na vijf minuten")
    spy = world.spy()
    await world.advance(4.9)
    await world.sweep()
    assert sent(spy, "forcesave") == []
    await world.advance(0.2)
    await world.sweep()
    assert sent(spy, "forcesave") == [{"key": h.key, "userdata": "timer"}]
    await world.settle()
    assert "na vijf minuten" in await world.paragraphs(node)
    row = await world.row(h.key)
    assert row.state == "open"
    versions = await world.version_count(node)
    # Niets veranderd: DS geeft error 4 en dat is goed (geen melding, de sessie blijft open)
    await world.advance(5.1)
    await world.sweep()
    assert len(sent(spy, "forcesave")) == 2
    row = await world.row(h.key)
    assert row.state == "open" and row.last_error is None
    assert await world.version_count(node) == versions
    assert world.event_data(anna, "office.unsaved") == []


async def test_tussentijds_opslaan_volgt_checkpoint_minutes(world):
    anna = await world.user("anna@example.nl")
    node = await world.docx(anna, None, "verslag.docx", ["Begin"])
    h = await world.open(anna, node)
    await world.type(h, "x")
    office_policy(app).config = OfficeConfig(checkpoint_minutes=2)  # wat /beheer (O5) zet
    spy = world.spy()
    await world.advance(2.1)
    await world.sweep()
    assert sent(spy, "forcesave") == [{"key": h.key, "userdata": "timer"}]


# --- oude rijen ---


async def test_dichte_rijen_ouder_dan_30_dagen_gaan_weg(world):
    anna = await world.user("anna@example.nl")
    first = await world.docx(anna, None, "een.docx", ["een"])
    second = await world.docx(anna, None, "twee.docx", ["twee"])
    h1 = await world.open(anna, first)
    await world.close(h1)
    await world.settle()
    await world.advance(20 * DAY)
    h2 = await world.open(anna, second)
    await world.close(h2)
    await world.settle()
    assert (await world.row(h1.key)).state == "closed"
    await world.advance(10 * DAY + 1)
    await world.sweep()
    assert await world.row(h1.key) is None
    assert await world.row(h2.key) is not None


# --- de ronde zelf ---


async def test_een_fout_in_een_sessie_stopt_de_rest_niet(world, monkeypatch):
    anna = await world.user("anna@example.nl")
    first = await world.docx(anna, None, "een.docx", ["een"])
    second = await world.docx(anna, None, "twee.docx", ["twee"])
    k1 = (await world.open_config(anna, first)).json()["key"]
    await world.advance(0.1)
    k2 = (await world.open_config(anna, second)).json()["key"]
    real = sweep._alive

    async def alive(row):
        if row.key == k1:
            raise RuntimeError("kapot")
        return await real(row)

    monkeypatch.setattr(sweep, "_alive", alive)
    await world.advance(6)
    assert await sweep.run_once() == 2
    assert (await world.row(k1)).state == "pending"
    assert (await world.row(k2)).state == "closed"


async def test_de_ronde_draait_als_taak(world, monkeypatch):
    rounds: list[int] = []

    async def once():
        rounds.append(1)
        return 0

    fast = world.settings.model_copy(update={"office_sweep_seconds": 0.01})
    runtime.bind(app, fast)
    monkeypatch.setattr(sweep, "run_once", once)
    try:
        await sweep.start(app)
        await wait_until(lambda: len(rounds) >= 2, 5)
    finally:
        for task in world.state.tasks:
            task.cancel()
        await asyncio.gather(*world.state.tasks, return_exceptions=True)
        world.state.tasks.clear()
        runtime.bind(app, world.settings)


# --- checkpoint_all ---


async def test_checkpoint_all_slaat_alles_op_en_wacht(world):
    anna = await world.user("anna@example.nl")
    nodes = [await world.docx(anna, None, f"d{n}.docx", ["Begin"]) for n in range(3)]
    handles = [await world.open(anna, node) for node in nodes]
    await world.type(handles[0], "een")
    await world.type(handles[1], "twee")
    spy = world.spy()
    assert await sweep.checkpoint_counts(5) == (2, 2)  # de derde: error 4, niets te doen
    assert sorted(p["userdata"] for p in sent(spy, "forcesave")) == ["stop"] * 3
    assert "een" in await world.paragraphs(nodes[0])
    assert "twee" in await world.paragraphs(nodes[1])
    for h in handles:
        assert (await world.row(h.key)).state == "open"
    await world.type(handles[2], "drie")
    assert await sweep.checkpoint_all(5) == 1
    assert "drie" in await world.paragraphs(nodes[2])


async def test_checkpoint_all_wacht_hooguit_wait(world):
    anna = await world.user("anna@example.nl")
    node = await world.docx(anna, None, "verslag.docx", ["Begin"])
    h = await world.open(anna, node)
    await world.type(h, "komt niet aan")
    await world.fail_callbacks(1000)
    start = time.monotonic()
    assert await sweep.checkpoint_counts(1) == (1, 0)
    assert time.monotonic() - start < 3
    await world.fail_callbacks(0)


async def test_checkpoint_all_zonder_open_sessies(world):
    assert await sweep.checkpoint_counts(1) == (0, 0)
    assert sessions.office() is world.state
    assert timedelta(minutes=sweep.SAVING_MINUTES) > timedelta(minutes=5)
