"""Het protocol tussen de app en de container analyse (bouwontwerp fase 6, §3.11, Keuze 56, 59
en 75). Van A0; X2a en X3a bouwen erop zonder dit bestand te wijzigen.

Eén verbinding per opdracht, over de Unix-socket in analyse-run. Het framing, MAX_MESSAGE
(64 KB), KEY_MIN (32), peer_uid en het doorgeven van fd's (SCM_RIGHTS, ontvangen met
MSG_CMSG_CLOEXEC) komen uit app.media.wire. De handdruk is eigen: een eigen PROTOCOL en een
eigen LABEL, zodat een bewijs voor de mediaworker nooit werkt bij de analyse, en omgekeerd.

    app:     {"v": 1, "hello": "<nonce: 32 hex>"}                       (zonder fd's)
    worker:  {"ev": "hello", "v": 1, "uid": <uid>, "proof": "<64 hex>"}

proof = HMAC-SHA256(sleutel, "platform-analyse-worker|<PROTOCOL>|<nonce>|<uid>"), met uid de
uid van de app zoals de worker die ziet (SO_PEERCRED). De app eist zijn eigen euid en een
kloppend bewijs; anders is de stand down (handshake) en gaat er niets naar de worker. Een worker
waarvan de zelftest faalt (RefusingWorker), antwoordt op hello met done/sandbox en sluit.

Daarna één vraag, met de fd's als SCM_RIGHTS in de volgorde van FDS[op]:

    vraag:   {"v": 1, "op": "<Op>", "deadline_ms": <int>, "args": {…}}
    worker:  {"ev": "started", "n": <nummer>, "child": "werk" | "zoeken"}   (alleen bij een kind)
             {"ev": "done", "code": "<Code>", "data": {…}}       (Code.OK)
             {"ev": "done", "code": "<Code>", "detail": "…"}     (elke andere Code)

- Elke vraag eindigt met precies één done. caps en cancel doet pid 1 zelf: meteen done, zonder
  started en zonder nummer. Een weigering (bad_request, not_built, busy, sandbox) is ook meteen
  done, zonder started: dan heeft de opdracht geen plaats gekregen.
- **Het werkmodel** (Keuze 56, K2-02). pid 1 heeft per kind een rij van één: een tweede
  opdracht voor hetzelfde kind krijgt busy, een opdracht voor het andere kind niet. CHILD zegt
  welk kind een opdracht doet, CHILDREN hoe elk kind leeft.
- **De deadline.** Elke vraag heeft deadline_ms (1 tot en met DEADLINE_MS[op]), geteld vanaf
  started. Is hij op, dan zet pid 1 RunOptions.terminate, en na KILL_GRACE_S stopt hij het kind
  (SIGKILL bij zijn PID) en start hij een nieuw. De opdracht krijgt timeout.
- **Afbreken.** Op.CANCEL met het nummer uit started breekt een opdracht af (die krijgt
  cancelled); de app kan ook gewoon de verbinding van de opdracht sluiten.
- **Een vector staat nooit in een bericht** (K1-08, Keuze 64). Vectoren gaan in een memfd: de
  invoer van group verzegeld (sealed_memfd), de uitvoer in een memfd van de app (out_memfd).
  rank krijgt voor "Meer zoals deze" een num, geen vector.
- **Wat nooit in een log komt.** Een vraag van embed_text en rank bevat zoektekst. Log dus nooit
  een bericht, alleen de op, het nummer, de code en tijden. detail noemt nooit de waarde van een
  argument (problem()).
"""

from __future__ import annotations

import fcntl
import hashlib
import hmac
import os
import re
import struct
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.media.wire import (  # noqa: F401 - het gedeelde framing, hier opnieuw beschikbaar
    KEY_MIN,
    MAX_FDS,
    MAX_MESSAGE,
    Closed,
    WireError,
    close_all,
    encode,
    peer_uid,
    receive,
    send,
    socket_address,
)

PROTOCOL = 1
LABEL = "platform-analyse-worker"
NONCE = re.compile(r"[0-9a-f]{32}")
DETAIL_CHARS = 500
# Hooguit zoveel tijd voor een antwoord van pid 1: op hello, op caps en cancel, en voor started
# of een weigering na de vraag (Keuze 59: "geen antwoord binnen 5 s" is down)
ANSWER_S = 5.0
# Na de deadline: zo lang krijgt een kind om te stoppen, daarna SIGKILL (§3.11)
KILL_GRACE_S = 5.0


class Op(StrEnum):
    """Alle opdrachten, vanaf A0 compleet (§3.11, Keuze 75). Een worker zonder de opdracht
    antwoordt not_built."""

    CAPS = "caps"
    CANCEL = "cancel"
    FACES = "faces"  # X2
    GROUP = "group"  # X2
    EMBED_IMAGE = "embed_image"  # X3
    EMBED_TEXT = "embed_text"  # X3
    RANK = "rank"  # X3
    FORGET = "forget"  # X3


class Code(StrEnum):
    """De code in done (§3.11)."""

    OK = "ok"
    BAD_REQUEST = "bad_request"  # een vraag of hello die niet klopt (ook framing en fd's)
    NOT_BUILT = "not_built"  # de opdracht hoort bij een pakket dat er nog niet is
    BUSY = "busy"  # het kind van deze opdracht heeft al een opdracht
    TIMEOUT = "timeout"  # de deadline was op
    CANCELLED = "cancelled"  # afgebroken met Op.CANCEL
    UNREADABLE = "unreadable"  # geen beeld (Pillow kan het niet lezen)
    TOO_BIG = "too_big"  # boven de grens: pixels, het geheugen van een kind, de cache
    IMAGE_BAD = "image_bad"  # een beeld dat het model niet aankan (beeld, X3)
    MODEL_MISSING = "model_missing"  # het model ontbreekt of de sha256 klopt niet
    SANDBOX = "sandbox"  # de zelftest faalde: de worker weigert alles
    INTERNAL = "internal"


class Child(StrEnum):
    """De twee kinderen van pid 1 (Keuze 56)."""

    WERK = "werk"
    ZOEKEN = "zoeken"


# Welk kind een opdracht doet; None is pid 1 zelf (zonder plaats en zonder nummer)
CHILD: dict[Op, Child | None] = {
    Op.CAPS: None,
    Op.CANCEL: None,
    Op.FACES: Child.WERK,
    Op.GROUP: Child.WERK,
    Op.EMBED_IMAGE: Child.WERK,
    Op.EMBED_TEXT: Child.ZOEKEN,
    Op.RANK: Child.ZOEKEN,
    Op.FORGET: Child.ZOEKEN,
}

# De hoogste deadline per opdracht, in ms. De app kiest er een tot en met deze waarde.
FACES_MS_PER_PHOTO = 10_000  # Keuze 60: hooguit 10 s per foto
DEADLINE_MS: dict[Op, int] = {
    Op.CAPS: 5_000,
    Op.CANCEL: 5_000,
    Op.FACES: 16 * FACES_MS_PER_PHOTO,
    Op.GROUP: 120_000,  # 60.000 gezichten: ±38–40 s op één core (Keuze 62)
    Op.EMBED_IMAGE: 120_000,
    Op.EMBED_TEXT: 30_000,  # de eerste keer laadt zoeken het tekstmodel
    Op.RANK: 30_000,  # herladen van een bestand van 100.000 foto's
    Op.FORGET: 5_000,
}


@dataclass(frozen=True)
class ChildSpec:
    """Hoe een kind leeft (Keuze 56, §3.11). max_ops en max_age_s: daarna start pid 1 een nieuw
    kind (None: lang, voor de cache). decodes_images: alleen werk opent ooit een beeld."""

    child: Child
    nice: int
    rlimit_data: int
    max_ops: int | None
    max_age_s: float | None
    decodes_images: bool


ANALYSE_CHILD_BATCHES = 8
CHILD_MAX_AGE_S = 600.0
ANALYSE_BEELD_CACHE_MB = 400
CHILDREN: dict[Child, ChildSpec] = {
    Child.WERK: ChildSpec(
        Child.WERK,
        nice=19,
        rlimit_data=1024**3,
        max_ops=ANALYSE_CHILD_BATCHES,
        max_age_s=CHILD_MAX_AGE_S,
        decodes_images=True,
    ),
    Child.ZOEKEN: ChildSpec(
        Child.ZOEKEN,
        nice=0,
        rlimit_data=3 * 1024**3 // 2,
        max_ops=None,
        max_age_s=None,
        decodes_images=False,
    ),
}


@dataclass(frozen=True)
class FdRule:
    """De fd's van een vraag: eerst `previews` (min, max) previews (alleen lezen), dan precies
    `extra`, in die volgorde. "uit" is een memfd van de app (out_memfd), "in" een verzegelde
    memfd (sealed_memfd), "vectoren" het vectorbestand van een eigenaar (alleen lezen)."""

    previews: tuple[int, int]
    extra: tuple[str, ...]

    def fits(self, n: int) -> bool:
        lo, hi = self.previews
        return lo + len(self.extra) <= n <= hi + len(self.extra)

    def count(self, n: int) -> int:
        """Het aantal previews bij n fd's."""
        return n - len(self.extra)


PHOTOS_PER_OP = 16  # PLATFORM_FACES_BATCH en PLATFORM_BEELD_BATCH, hooguit
FDS: dict[Op, FdRule] = {
    Op.CAPS: FdRule((0, 0), ()),
    Op.CANCEL: FdRule((0, 0), ()),
    Op.FACES: FdRule((1, PHOTOS_PER_OP), ("uit",)),
    Op.GROUP: FdRule((0, 0), ("in", "uit")),
    Op.EMBED_IMAGE: FdRule((1, PHOTOS_PER_OP), ("uit",)),
    Op.EMBED_TEXT: FdRule((0, 0), ("uit",)),
    Op.RANK: FdRule((0, 0), ("vectoren",)),
    Op.FORGET: FdRule((0, 0), ()),
}

# De vormen in de memfd's
FACE_DIM = 128
FACE_VEC_BYTES = FACE_DIM * 4  # float32, L2-genormaliseerd (Keuze 60)
FACES_MAX = 40  # PLATFORM_FACES_MAX_PER_PHOTO, hooguit
GROUP_MAGIC = b"PFG1"
# 'PFG1', dan n, known, persons, rejects (u32, little endian); dan n × 512 B vectoren, known ×
# int32 persoon-index, rejects × (int32 gezicht, int32 persoon) (Keuze 62)
GROUP_HEAD = struct.Struct("<4s4I")
GROUP_OUT = struct.Struct("<ifi")  # per gezicht: voorstel, score, groep (−1: geen)
BEELD_DIM = 768
BEELD_VEC_BYTES = BEELD_DIM * 2  # float16 (Keuze 75); de echte dim staat in caps.beeld
TEXT_MAX = 200
RANK_K_MAX = 1_000
OWNER = r"^[0-9a-f]{32}$"  # een eigenaar: 32 hex die de app kiest (bv. uuid.hex), nooit een naam


# --- de argumenten (de worker en de nep valideren, de app bouwt ze) ---


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CapsArgs(_Args):
    pass


class CancelArgs(_Args):
    n: int = Field(ge=1)


class FacesArgs(_Args):
    """fd's: [preview × k, uit]. De 1024.webp van elke foto; de vectoren van alle foto's komen
    achter elkaar in uit, n × FACE_VEC_BYTES per foto, in de volgorde van de previews."""

    min_px: int = Field(default=40, ge=1, le=1024)
    max: int = Field(default=40, ge=1, le=FACES_MAX)
    score: float = Field(default=0.9, ge=0, le=1)


class GroupArgs(_Args):
    """fd's: [in (verzegeld, GROUP_HEAD …), uit]; uit krijgt n × GROUP_OUT.size bytes."""

    mode: Literal["suggest", "full"]
    k: int = Field(default=10, ge=1, le=100)
    t_link: float = Field(default=0.50, ge=-1, le=1)
    t_suggest: float = Field(default=0.45, ge=-1, le=1)
    margin: float = Field(default=0.05, ge=0, le=2)
    min_group: int = Field(default=3, ge=2, le=1000)


class EmbedImageArgs(_Args):
    """fd's: [preview × k, uit]; uit krijgt k vakken van caps.beeld.dim × 2 bytes (float16), in
    de volgorde van de previews. Een vak van een foto die niet ok is, blijft nul."""


class EmbedTextArgs(_Args):
    """fd's: [uit]; uit krijgt caps.beeld.dim × 2 bytes. Voor de kwaliteitsmeting en tests."""

    text: str = Field(min_length=1, max_length=TEXT_MAX)


class RankArgs(_Args):
    """fd's: [vectoren]: het bestand van de eigenaar. Met k > 0 precies één van text en num
    (num: "Meer zoals deze"; de worker zoekt de vector zelf op). Met k = 0 geen van beide: dan
    alleen het bestand in de cache zetten (opwarmen)."""

    owner: str = Field(pattern=OWNER)
    gen: int = Field(ge=0)
    text: str | None = Field(default=None, min_length=1, max_length=TEXT_MAX)
    num: int | None = Field(default=None, ge=0)
    k: int = Field(ge=0, le=RANK_K_MAX)

    @model_validator(mode="after")
    def _one(self) -> RankArgs:
        given = (self.text is not None) + (self.num is not None)
        if self.k == 0 and given:
            raise ValueError("met k 0 geen text en geen num")
        if self.k > 0 and given != 1:
            raise ValueError("precies één van text en num")
        return self


class ForgetArgs(_Args):
    owner: str = Field(pattern=OWNER)


ARGS: dict[Op, type[_Args]] = {
    Op.CAPS: CapsArgs,
    Op.CANCEL: CancelArgs,
    Op.FACES: FacesArgs,
    Op.GROUP: GroupArgs,
    Op.EMBED_IMAGE: EmbedImageArgs,
    Op.EMBED_TEXT: EmbedTextArgs,
    Op.RANK: RankArgs,
    Op.FORGET: ForgetArgs,
}


# --- de data in done/ok (de app valideert; een extra veld laat hij liggen) ---


class _Data(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)


ModelState = Literal["ok", "ontbreekt", "anders"]


class ModelCaps(_Data):
    """Een modelbestand uit models.lock (of de lijst van X3): ok, ontbreekt, of anders (een
    andere grootte of sha256)."""

    name: str
    state: ModelState


class BeeldCaps(_Data):
    """Het model van beeld (X3, Keuze 75): naam, dimensie, en logit_scale en logit_bias."""

    model: str
    dim: int = Field(ge=1, le=4096)
    scale: float
    bias: float


class LimitsCaps(_Data):
    """De grenzen van de container zoals de worker ze ziet (cgroup). Kent de worker ze niet, dan
    is CapsData.limits None."""

    cpus: float
    mem_mb: int


class CapsData(_Data):
    """caps: de modellen, de opdrachten die er zijn, en (X3) het beeldmodel."""

    models: list[ModelCaps] = []
    built: list[str] = []
    beeld: BeeldCaps | None = None
    limits: LimitsCaps | None = None

    def missing(self) -> list[str]:
        return [m.name for m in self.models if m.state != "ok"]


class CancelData(_Data):
    found: bool  # False: er liep geen opdracht met dat nummer (meer)


class FaceBox(_Data):
    """Een kader als deel van de preview (0–1, Keuze 37), de score, en de korte kant in px."""

    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    w: float = Field(ge=0, le=1)
    h: float = Field(ge=0, le=1)
    score: float = Field(ge=0, le=1)
    px: int = Field(ge=0)


PhotoCode = Literal["ok", "unreadable", "too_big", "image_bad"]


class FacesPhoto(_Data):
    """Eén foto in faces. Niet ok: w, h en n zijn 0, en er staat niets van in uit."""

    code: PhotoCode
    w: int = Field(default=0, ge=0)
    h: int = Field(default=0, ge=0)
    faces: list[FaceBox] = Field(default=[], max_length=FACES_MAX)
    n: int = Field(default=0, ge=0, le=FACES_MAX)

    @model_validator(mode="after")
    def _n(self) -> FacesPhoto:
        if self.n != len(self.faces) or (self.code != "ok" and self.n):
            raise ValueError("n hoort bij faces")
        return self


class FacesData(_Data):
    photos: list[FacesPhoto] = Field(max_length=PHOTOS_PER_OP)

    def vector_bytes(self) -> int:
        """Zoveel bytes hoort uit te hebben."""
        return sum(p.n for p in self.photos) * FACE_VEC_BYTES


class GroupData(_Data):
    n: int = Field(ge=0)  # zoveel gezichten; uit heeft n × GROUP_OUT.size bytes


class EmbedPhoto(_Data):
    code: PhotoCode


class EmbedImageData(_Data):
    photos: list[EmbedPhoto] = Field(max_length=PHOTOS_PER_OP)


class EmbedTextData(_Data):
    pass


class RankData(_Data):
    """[[num, s], …], beste eerst; s is de cosinus × 10.000."""

    hits: list[tuple[int, int]] = Field(default=[], max_length=RANK_K_MAX)


class ForgetData(_Data):
    found: bool


DATA: dict[Op, type[_Data]] = {
    Op.CAPS: CapsData,
    Op.CANCEL: CancelData,
    Op.FACES: FacesData,
    Op.GROUP: GroupData,
    Op.EMBED_IMAGE: EmbedImageData,
    Op.EMBED_TEXT: EmbedTextData,
    Op.RANK: RankData,
    Op.FORGET: ForgetData,
}


# --- de handdruk ---


def hello(nonce: str) -> dict:
    return {"v": PROTOCOL, "hello": nonce}


def proof(key: str, nonce: str, uid: int) -> str:
    """Het bewijs van de worker, met het eigen LABEL: dat van media werkt hier nooit."""
    text = f"{LABEL}|{PROTOCOL}|{nonce}|{uid}".encode()
    return hmac.new(key.encode(), text, hashlib.sha256).hexdigest()


def hello_problem(message: dict) -> str | None:
    """Wat er niet klopt aan een hello van de app (bad_request), of None."""
    if message.get("v") != PROTOCOL:
        return f"de worker spreekt PROTOCOL {PROTOCOL}"
    nonce = message.get("hello")
    if not isinstance(nonce, str) or not NONCE.fullmatch(nonce) or len(message) != 2:
        return "eerst hello met een nonce van 32 hex, zonder fd's"
    return None


def hello_reply(key: str, nonce: str, uid: int) -> dict:
    return {"ev": "hello", "v": PROTOCOL, "uid": uid, "proof": proof(key, nonce, uid)}


# --- de vraag en de antwoorden ---


def request(op: Op, args: BaseModel | dict, deadline_ms: int | None = None) -> dict:
    """De vraag van de app. Zonder deadline_ms de hoogste van de opdracht."""
    data = args.model_dump(mode="json") if isinstance(args, BaseModel) else dict(args)
    ms = DEADLINE_MS[op] if deadline_ms is None else int(deadline_ms)
    return {"v": PROTOCOL, "op": op.value, "deadline_ms": ms, "args": data}


def started(n: int, child: Child) -> dict:
    return {"ev": "started", "n": n, "child": child.value}


def done(code: Code, detail: str = "", data: BaseModel | dict | None = None) -> dict:
    """Het laatste antwoord op een vraag. data alleen bij ok."""
    out: dict = {"ev": "done", "code": code.value}
    if code is Code.OK:
        if isinstance(data, BaseModel):
            data = data.model_dump(mode="json")
        out["data"] = data or {}
    else:
        out["detail"] = detail[:DETAIL_CHARS]
    return out


class Refusal(Exception):
    """Een vraag die de worker niet doet: code en een korte reden zonder waarden."""

    def __init__(self, code: Code, detail: str = "") -> None:
        super().__init__(detail or code.value)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class Request:
    op: Op
    args: _Args
    deadline_ms: int
    previews: int  # het aantal previews onder de fd's


def _problems(exc: ValidationError) -> str:
    """De velden en de soort fout, nooit de waarde (zoektekst, K1-08)."""
    parts = []
    for err in exc.errors(include_url=False, include_input=False, include_context=False):
        where = ".".join(str(p) for p in err.get("loc", ())) or "args"
        parts.append(f"{where}: {err.get('type', 'ongeldig')}")
    return "; ".join(parts)[:DETAIL_CHARS]


def parse(message: dict, nfds: int, built: frozenset[Op] | set[Op] | None = None) -> Request:
    """Een vraag van de app, nagekeken in deze volgorde: PROTOCOL, op, of hij gebouwd is
    (built; None = alles), deadline_ms, args, en het aantal fd's (FDS). Refusal als iets niet
    klopt; detail noemt nooit een waarde."""
    if message.get("v") != PROTOCOL:
        raise Refusal(Code.BAD_REQUEST, f"de worker spreekt PROTOCOL {PROTOCOL}")
    if set(message) - {"v", "op", "deadline_ms", "args"}:
        raise Refusal(Code.BAD_REQUEST, "onbekende velden in de vraag")
    try:
        op = Op(message.get("op"))
    except ValueError:
        raise Refusal(Code.BAD_REQUEST, "onbekende op") from None
    if built is not None and op not in built:
        raise Refusal(Code.NOT_BUILT, f"{op.value} is nog niet gebouwd")
    ms = message.get("deadline_ms")
    if type(ms) is not int or not 1 <= ms <= DEADLINE_MS[op]:
        raise Refusal(Code.BAD_REQUEST, f"deadline_ms hoort 1 tot en met {DEADLINE_MS[op]}")
    raw = message.get("args")
    if not isinstance(raw, dict):
        raise Refusal(Code.BAD_REQUEST, "args hoort een object")
    try:
        args = ARGS[op].model_validate(raw)
    except ValidationError as exc:
        raise Refusal(Code.BAD_REQUEST, _problems(exc)) from None
    rule = FDS[op]
    if not rule.fits(nfds):
        raise Refusal(Code.BAD_REQUEST, f"{nfds} fd's past niet bij {op.value}")
    return Request(op, args, ms, rule.count(nfds))


# --- memfd's ---

# De waarden van Linux (fcntl.h). De Python van uv (python-build-standalone) kent de namen niet
# in fcntl; die van Debian wel, met dezelfde waarden
F_ADD_SEALS = getattr(fcntl, "F_ADD_SEALS", 1033)
F_GET_SEALS = getattr(fcntl, "F_GET_SEALS", 1034)
F_SEAL_SEAL = getattr(fcntl, "F_SEAL_SEAL", 0x0001)
F_SEAL_SHRINK = getattr(fcntl, "F_SEAL_SHRINK", 0x0002)
F_SEAL_GROW = getattr(fcntl, "F_SEAL_GROW", 0x0004)
F_SEAL_WRITE = getattr(fcntl, "F_SEAL_WRITE", 0x0008)
SEALS = F_SEAL_WRITE | F_SEAL_GROW | F_SEAL_SHRINK


def sealed_memfd(name: str, data: bytes) -> int:
    """Een memfd met data, verzegeld tegen schrijven, groeien en krimpen (CLOEXEC). Voor de
    invoer van group; de worker eist de zegels (is_sealed)."""
    fd = os.memfd_create(name, os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING)
    try:
        view = memoryview(data)
        while view:
            view = view[os.write(fd, view) :]
        fcntl.fcntl(fd, F_ADD_SEALS, SEALS | F_SEAL_SEAL)
    except BaseException:
        os.close(fd)
        raise
    return fd


def out_memfd(name: str) -> int:
    """Een lege memfd voor de uitvoer (CLOEXEC). De app leest hem na done met read_all."""
    return os.memfd_create(name, os.MFD_CLOEXEC)


def is_sealed(fd: int) -> bool:
    try:
        seals = fcntl.fcntl(fd, F_GET_SEALS)
    except OSError:
        return False
    return seals & SEALS == SEALS


def read_all(fd: int, limit: int) -> bytes:
    """De hele inhoud van een memfd (vanaf 0), hooguit limit bytes; groter is ValueError."""
    size = os.fstat(fd).st_size
    if size > limit:
        raise ValueError(f"{size} bytes, hooguit {limit}")
    out = bytearray()
    while len(out) < size:
        chunk = os.pread(fd, size - len(out), len(out))
        if not chunk:
            break
        out += chunk
    return bytes(out)
