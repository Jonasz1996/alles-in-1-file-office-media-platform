"""Het protocol van de container analyse (fase 6, A0; §3.11, Keuze 56 en 59): de enum, de
tabellen per opdracht, de handdruk met het eigen label, de vormen van de berichten, busy per kind
en cancel. Tegen de nep-worker; de scenario's die ook tegen de echte worker draaien, staan in
analyse_contract.py."""

from __future__ import annotations

import errno
import fcntl
import hashlib
import hmac
import os
import secrets

import pytest
from pydantic import ValidationError

from app.analyse import client, wire
from app.analyse.wire import CHILD, CHILDREN, DEADLINE_MS, FDS, Child, Code, Op
from app.media import client as media_client
from app.media import wire as media_wire
from tests.analyse import analyse_contract as C
from tests.analyse.fake_worker import FakeAnalyseWorker
from tests.media.fake_worker import FakeWorker

pytestmark = pytest.mark.anyio

NONCE = "0123456789abcdef" * 2


# --- de enum en de tabellen ---


def test_op_is_vanaf_a0_compleet():
    assert [op.value for op in Op] == [
        "caps",
        "cancel",
        "faces",
        "group",
        "embed_image",
        "embed_text",
        "rank",
        "forget",
    ]


def test_de_codes_van_done():
    assert [c.value for c in Code] == [
        "ok",
        "bad_request",
        "not_built",
        "busy",
        "timeout",
        "cancelled",
        "unreadable",
        "too_big",
        "image_bad",
        "model_missing",
        "sandbox",
        "internal",
    ]


def test_elke_opdracht_heeft_een_kind_of_pid1():
    assert CHILD == {
        Op.CAPS: None,
        Op.CANCEL: None,
        Op.FACES: Child.WERK,
        Op.GROUP: Child.WERK,
        Op.EMBED_IMAGE: Child.WERK,
        Op.EMBED_TEXT: Child.ZOEKEN,
        Op.RANK: Child.ZOEKEN,
        Op.FORGET: Child.ZOEKEN,
    }


def test_elke_tabel_kent_elke_opdracht():
    for table in (CHILD, DEADLINE_MS, FDS, wire.ARGS, wire.DATA):
        assert set(table) == set(Op)


def test_de_deadlines():
    assert DEADLINE_MS[Op.CAPS] == DEADLINE_MS[Op.CANCEL] == int(wire.ANSWER_S * 1000)
    assert DEADLINE_MS[Op.FACES] == wire.PHOTOS_PER_OP * wire.FACES_MS_PER_PHOTO == 160_000
    assert all(type(ms) is int and ms >= 1 for ms in DEADLINE_MS.values())
    assert wire.ANSWER_S == 5.0 and wire.KILL_GRACE_S == 5.0


def test_hoe_de_kinderen_leven():
    werk, zoeken = CHILDREN[Child.WERK], CHILDREN[Child.ZOEKEN]
    assert (werk.nice, werk.max_ops, werk.max_age_s, werk.decodes_images) == (19, 8, 600.0, True)
    assert werk.rlimit_data == 1024**3
    assert (zoeken.nice, zoeken.max_ops, zoeken.max_age_s, zoeken.decodes_images) == (
        0,
        None,
        None,
        False,
    )
    assert zoeken.rlimit_data == 1536 * 1024**2
    assert wire.ANALYSE_CHILD_BATCHES == 8 and wire.ANALYSE_BEELD_CACHE_MB == 400


def test_zoeken_krijgt_nooit_een_beeld():
    """Alleen werk opent beelden (Keuze 56): een opdracht van zoeken heeft geen previews."""
    for op, child in CHILD.items():
        if child is Child.ZOEKEN or child is None:
            assert FDS[op].previews == (0, 0), op
    assert FDS[Op.FACES].previews == FDS[Op.EMBED_IMAGE].previews == (1, wire.PHOTOS_PER_OP)
    assert FDS[Op.GROUP].extra == ("in", "uit")
    assert FDS[Op.RANK].extra == ("vectoren",)


def _number_arrays(schema: dict) -> list[str]:
    """Paden in een JSON-schema naar een lijst van getallen (een vector)."""
    found: list[str] = []
    defs = schema.get("$defs", {})

    def walk(node, path: str) -> None:
        if isinstance(node, dict):
            if "$ref" in node:
                walk(defs[node["$ref"].rsplit("/", 1)[-1]], path)
            if node.get("type") == "array":
                items = node.get("items", {})
                if isinstance(items, dict) and items.get("type") == "number":
                    found.append(path)
            for key, value in node.items():
                if key != "$defs":
                    walk(value, f"{path}.{key}")
        elif isinstance(node, list):
            for i, value in enumerate(node):
                walk(value, f"{path}[{i}]")

    walk(schema, "")
    return found


def test_een_vector_staat_nooit_in_een_bericht():
    """K1-08, Keuze 64: vectoren gaan in een memfd. Geen vorm heeft een lijst van getallen."""
    for op in Op:
        for model in (wire.ARGS[op], wire.DATA[op]):
            assert _number_arrays(model.model_json_schema()) == [], (op, model.__name__)


def test_args_verbieden_extra_velden_en_data_laat_ze_liggen():
    with pytest.raises(ValidationError):
        wire.FacesArgs.model_validate({"min_px": 40, "nieuw": 1})
    caps = wire.CapsData.model_validate({"models": [], "built": ["caps"], "nieuw": [1.0, 2.0]})
    assert caps.built == ["caps"] and not hasattr(caps, "nieuw")


# --- de handdruk ---


def test_het_label_en_het_bewijs():
    assert wire.LABEL == "platform-analyse-worker" and wire.PROTOCOL == 1
    key = secrets.token_hex(32)
    expect = hmac.new(
        key.encode(), f"platform-analyse-worker|1|{NONCE}|1000".encode(), hashlib.sha256
    ).hexdigest()
    assert wire.proof(key, NONCE, 1000) == expect
    # Het label van media is niet veranderd, en de twee bewijzen verschillen altijd
    media = hmac.new(
        key.encode(), f"platform-media-worker|1|{NONCE}|1000".encode(), hashlib.sha256
    ).hexdigest()
    assert media_wire.proof(key, NONCE, 1000) == media != expect
    assert wire.hello_reply(key, NONCE, 7) == {
        "ev": "hello",
        "v": 1,
        "uid": 7,
        "proof": wire.proof(key, NONCE, 7),
    }


def test_het_framing_komt_van_media():
    for name in ("send", "receive", "close_all", "peer_uid", "socket_address", "encode"):
        assert getattr(wire, name) is getattr(media_wire, name)
    assert wire.MAX_MESSAGE == media_wire.MAX_MESSAGE == 64 * 1024
    assert wire.KEY_MIN == 32 and wire.Closed is media_wire.Closed


@pytest.mark.parametrize(
    "message",
    [
        {"v": 2, "hello": NONCE},
        {"hello": NONCE},
        {"v": 1, "hello": NONCE.upper()},
        {"v": 1, "hello": NONCE[:-1]},
        {"v": 1, "hello": 5},
        {"v": 1, "hello": NONCE, "op": "caps"},
        {"v": 1},
    ],
)
def test_een_hello_die_niet_klopt(message):
    assert wire.hello_problem(message) is not None


def test_een_goede_hello():
    assert wire.hello_problem(wire.hello(NONCE)) is None
    assert wire.hello(NONCE) == {"v": 1, "hello": NONCE}


async def test_de_handdruk_met_de_nep(fake, settings_fake):
    sock = await client.connect(settings_fake)
    sock.close()
    assert fake.hellos == 1
    assert (await client.caps(settings_fake)).built == ["cancel", "caps"]


@pytest.mark.parametrize("fault", ["close", "wrong_uid", "wrong_proof", "media_proof"])
async def test_een_worker_die_zich_niet_bewijst(fault, fake, settings_fake):
    fake.hello_fault = fault
    with pytest.raises(client.AnalyseDown) as exc:
        await client.caps(settings_fake)
    assert exc.value.reason == "handshake"
    assert fake.requests == []  # er ging geen vraag (en geen fd) naar de worker


async def test_geen_antwoord_op_hello(fake, settings_fake, monkeypatch):
    monkeypatch.setattr(client, "HELLO_TIMEOUT", 0.2)
    fake.hello_fault = "silent"
    with pytest.raises(client.AnalyseDown) as exc:
        await client.caps(settings_fake)
    assert exc.value.reason == "no_answer"


async def test_een_verkeerde_sleutel(fake, tmp_path):
    other = secrets.token_hex(32)
    with pytest.raises(client.AnalyseDown) as exc:
        await client.caps(fake.settings(tmp_path, analyse_worker_key=other))
    assert exc.value.reason == "handshake"
    for text in (str(exc.value), repr(exc.value), repr(fake.settings(tmp_path))):
        assert fake.key not in text and other not in text


@pytest.mark.parametrize("key", ["", "k" * 31])
async def test_een_korte_sleutel_raakt_de_socket_niet(key, fake, tmp_path):
    with pytest.raises(client.AnalyseDown) as exc:
        await client.caps(fake.settings(tmp_path, analyse_worker_key=key))
    assert exc.value.reason == "key_missing"
    assert fake.hellos == 0


async def test_een_hergebruikte_sleutel(fake, tmp_path):
    """Gelijk aan het geheim van de app of aan de sleutel van de mediaworker: key_reused, en de
    socket blijft onaangeroerd."""
    secret = "s" * 40
    media = "m" * 40
    cases = [
        {"jwt_secret": secret, "analyse_worker_key": secret},
        {"media_worker_key": media, "analyse_worker_key": media},
    ]
    local = fake.settings(tmp_path, jwt_secret=secret)
    cases.append({"jwt_secret": secret, "analyse_worker_key": local.media_key()})
    for extra in cases:
        settings = fake.settings(tmp_path, **extra)
        assert settings.analyse_key_problem() == "key_reused"
        with pytest.raises(client.AnalyseDown) as exc:
            await client.caps(settings)
        assert exc.value.reason == "key_reused"
    assert fake.hellos == 0
    assert fake.settings(tmp_path).analyse_key_problem() is None


async def test_een_bewijs_van_media_telt_niet_bij_analyse(tmp_path):
    """Een mediaworker met dezelfde sleutel op de socket: de handdruk faalt."""
    key = secrets.token_hex(32)
    async with FakeWorker(tmp_path / "media.sock", key=key) as media:
        analyse = FakeAnalyseWorker(media.socket, key=key)
        with pytest.raises(client.AnalyseDown) as exc:
            await client.caps(analyse.settings(tmp_path))
        assert exc.value.reason == "handshake"
        assert media.requests == []


async def test_een_bewijs_van_analyse_telt_niet_bij_media(fake):
    with pytest.raises(media_client.WorkerDown):
        await media_client.connect(path=fake.socket, key=fake.key)
    assert fake.requests == []


# --- de vraag ---


def test_request_en_done():
    assert wire.request(Op.CAPS, wire.CapsArgs()) == {
        "v": 1,
        "op": "caps",
        "deadline_ms": 5000,
        "args": {},
    }
    assert wire.request(Op.CANCEL, {"n": 3}, 100)["deadline_ms"] == 100
    assert wire.done(Code.OK) == {"ev": "done", "code": "ok", "data": {}}
    assert wire.done(Code.OK, data=wire.CancelData(found=True))["data"] == {"found": True}
    bad = wire.done(Code.INTERNAL, "x" * 2000, data={"geheim": 1})
    assert bad == {"ev": "done", "code": "internal", "detail": "x" * wire.DETAIL_CHARS}
    assert wire.started(4, Child.ZOEKEN) == {"ev": "started", "n": 4, "child": "zoeken"}


def _refusal(message: dict, nfds: int = 0, built=None) -> wire.Refusal:
    with pytest.raises(wire.Refusal) as exc:
        wire.parse(message, nfds, built)
    return exc.value


def test_parse_in_volgorde():
    good = wire.request(Op.FACES, wire.FacesArgs())
    assert wire.parse(good, 3).previews == 2
    assert _refusal({**good, "v": 2}).code is Code.BAD_REQUEST
    assert _refusal({**good, "extra": 1}).code is Code.BAD_REQUEST
    assert _refusal({**good, "op": "verwijder"}).code is Code.BAD_REQUEST
    # Niet gebouwd gaat vóór de deadline, de args en de fd's
    assert _refusal({**good, "deadline_ms": 0}, 0, {Op.CAPS}).code is Code.NOT_BUILT
    assert _refusal({**good, "deadline_ms": 0}, 3).code is Code.BAD_REQUEST
    assert _refusal({**good, "args": {"max": 41}}, 3).code is Code.BAD_REQUEST
    assert _refusal(good, 1).code is Code.BAD_REQUEST


@pytest.mark.parametrize(
    ("op", "ok", "bad"),
    [
        (Op.CAPS, [0], [1]),
        (Op.CANCEL, [0], [1]),
        (Op.FACES, [2, 17], [0, 1, 18]),
        (Op.EMBED_IMAGE, [2, 17], [1, 18]),
        (Op.GROUP, [2], [1, 3]),
        (Op.EMBED_TEXT, [1], [0, 2]),
        (Op.RANK, [1], [0, 2]),
        (Op.FORGET, [0], [1]),
    ],
)
def test_het_aantal_fds(op, ok, bad):
    args = {Op.CAPS: wire.CapsArgs(), Op.CANCEL: wire.CancelArgs(n=1)}.get(op)
    message = wire.request(op, args or C.STANDAARD[op].args)
    for n in ok:
        assert wire.parse(message, n).previews == FDS[op].count(n)
    for n in bad:
        assert _refusal(message, n).code is Code.BAD_REQUEST


def test_de_reden_noemt_nooit_een_waarde():
    for message in (
        wire.request(Op.RANK, {"owner": "0" * 32, "gen": 0, "k": 0, "text": C.GEHEIM}),
        wire.request(Op.EMBED_TEXT, {"text": C.GEHEIM * 30}),
        wire.request(Op.FORGET, {"owner": C.GEHEIM}),
        wire.request(Op.CANCEL, {"n": C.GEHEIM}),
    ):
        rule = wire.FDS[Op(message["op"])]
        refusal = _refusal(message, rule.previews[0] + len(rule.extra))
        assert refusal.code is Code.BAD_REQUEST
        assert C.GEHEIM not in refusal.detail and C.GEHEIM not in str(refusal)


def test_rank_vraagt_precies_een_van_text_en_num():
    base = {"owner": "a" * 32, "gen": 1}
    wire.RankArgs(**base, k=0)
    wire.RankArgs(**base, k=10, text="fiets")
    wire.RankArgs(**base, k=10, num=12)
    for bad in (
        {"k": 0, "text": "fiets"},
        {"k": 0, "num": 1},
        {"k": 10},
        {"k": 10, "text": "fiets", "num": 1},
        {"k": wire.RANK_K_MAX + 1, "num": 1},
        {"k": 10, "text": "x" * (wire.TEXT_MAX + 1)},
        {"k": 10, "text": ""},
    ):
        with pytest.raises(ValidationError):
            wire.RankArgs(**base, **bad)
    with pytest.raises(ValidationError):
        wire.RankArgs(owner="Jonas", gen=1, k=0)


def test_de_vorm_van_faces():
    box = wire.FaceBox(x=0, y=0.5, w=1, h=0.25, score=0.9, px=40)
    wire.FacesPhoto(code="ok", w=10, h=10, faces=[box, box], n=2)
    wire.FacesPhoto(code="unreadable")
    for bad in (
        {"code": "ok", "faces": [box], "n": 2},
        {"code": "too_big", "faces": [box], "n": 1},
        {"code": "kapot"},
        {"code": "ok", "faces": [box] * 41, "n": 41},
    ):
        with pytest.raises(ValidationError):
            wire.FacesPhoto(**bad)
    with pytest.raises(ValidationError):
        wire.FaceBox(x=1.5, y=0, w=0, h=0, score=0.9, px=1)
    with pytest.raises(ValidationError):
        wire.FacesData(photos=[wire.FacesPhoto(code="ok")] * 17)
    data = wire.FacesData(photos=[wire.FacesPhoto(code="ok", faces=[box] * 3, n=3)])
    assert data.vector_bytes() == 3 * 512 == 3 * wire.FACE_VEC_BYTES


def test_de_kop_van_group():
    head = wire.GROUP_HEAD.pack(wire.GROUP_MAGIC, 10, 2, 1, 0)
    assert len(head) == 20 and head[:4] == b"PFG1"
    assert wire.GROUP_HEAD.unpack(head) == (b"PFG1", 10, 2, 1, 0)
    assert wire.GROUP_OUT.size == 12


# --- de memfd's ---


def test_een_verzegelde_memfd():
    fd = wire.sealed_memfd("in", b"abc")
    try:
        assert wire.is_sealed(fd)
        assert wire.read_all(fd, 3) == b"abc"
        with pytest.raises(OSError) as exc:
            os.write(fd, b"x")
        assert exc.value.errno == errno.EPERM
        with pytest.raises(OSError):
            os.ftruncate(fd, 0)
        with pytest.raises(ValueError):
            wire.read_all(fd, 2)
        assert fcntl.fcntl(fd, fcntl.F_GETFD) & fcntl.FD_CLOEXEC
    finally:
        os.close(fd)
    out = wire.out_memfd("uit")
    try:
        assert not wire.is_sealed(out)
        os.write(out, b"vrij")
        assert wire.read_all(out, 10) == b"vrij"
    finally:
        os.close(out)
    r, w = os.pipe()
    try:
        assert not wire.is_sealed(r)  # geen memfd
    finally:
        os.close(r)
        os.close(w)


def test_de_waarden_van_de_zegels():
    """De Python van uv kent de namen niet; waar fcntl ze wel kent, zijn ze gelijk."""
    values = {
        "F_ADD_SEALS": 1033,
        "F_GET_SEALS": 1034,
        "F_SEAL_SEAL": 1,
        "F_SEAL_SHRINK": 2,
        "F_SEAL_GROW": 4,
        "F_SEAL_WRITE": 8,
    }
    for name, value in values.items():
        assert getattr(wire, name) == value
        assert getattr(fcntl, name, value) == value


# --- de client: busy per kind, cancel, MAX_MESSAGE, not_built ---


async def test_busy_per_kind(fake, settings_fake):
    werk = fake.hold(Op.GROUP)
    fake.hold(Op.FACES)
    fake.on(Op.FORGET, _leeg)
    group = await _start(settings_fake, Op.GROUP)
    try:
        with pytest.raises(client.AnalyseBusy) as exc:
            await _start(settings_fake, Op.FACES)
        assert exc.value.child == "werk" and "werk" in str(exc.value)
        assert await client.call(settings_fake, Op.FORGET, wire.ForgetArgs(owner="b" * 32)) == {
            "found": False
        }
        werk.release()
        assert await group.result() == {}
    finally:
        group.close()


async def test_cancel_met_het_nummer(fake, settings_fake):
    fake.hold(Op.EMBED_TEXT)
    job = await _start(settings_fake, Op.EMBED_TEXT)
    try:
        assert job.child is Child.ZOEKEN and job.n == 1
        assert await client.cancel(settings_fake, job.n + 1) is False
        assert await client.cancel(settings_fake, job.n) is True
        with pytest.raises(client.AnalyseFailed) as exc:
            await job.result()
        assert exc.value.code == "cancelled"
    finally:
        job.close()


async def test_een_te_grote_vraag_gaat_niet_weg(fake, settings_fake):
    fake.on(Op.FORGET, _leeg)
    big = {"owner": "c" * 32, "opvulling": "x" * wire.MAX_MESSAGE}
    with pytest.raises(client.AnalyseFailed) as exc:
        await client.call(settings_fake, Op.FORGET, big)
    assert exc.value.code == "bad_request"
    with pytest.raises(client.AnalyseFailed):
        await client.call(settings_fake, Op.CAPS, {}, fds=[0] * (wire.MAX_FDS + 1))
    assert fake.hellos == 0


async def test_de_opdrachten_van_x2_en_x3_zijn_not_built(fake, settings_fake):
    for op in (Op.FACES, Op.GROUP, Op.EMBED_IMAGE, Op.EMBED_TEXT, Op.RANK, Op.FORGET):
        opdracht = C.STANDAARD[op]
        fds = opdracht.fds()
        try:
            with pytest.raises(client.AnalyseFailed) as exc:
                await client.call(settings_fake, op, opdracht.args, fds=fds)
        finally:
            wire.close_all(fds)
        assert exc.value.code == "not_built", op


async def test_een_onbekende_opdracht_is_bad_request(fake, settings_fake):
    message = {"v": 1, "op": "x", "deadline_ms": 5, "args": {}}
    reply = await C.ask(_driver(fake, settings_fake), message)
    assert (reply["ev"], reply["code"]) == ("done", "bad_request")


# --- hulpjes ---


async def _leeg(req):
    return {"found": False}


async def _start(settings, op: Op) -> client.Job:
    opdracht = C.STANDAARD[op]
    fds = opdracht.fds()
    try:
        return await client.start(settings, op, opdracht.args, fds=fds)
    finally:
        wire.close_all(fds)


def _driver(fake: FakeAnalyseWorker, settings):
    """Genoeg van een Driver voor C.ask."""

    class D:
        socket = fake.socket
        key = fake.key

        def settings(self):
            return settings

    return D()
