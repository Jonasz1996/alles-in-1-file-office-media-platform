"""De vaste vormen van zoeken op beeld (bouwontwerp fase 6, X3; Keuze 75, 76, 78; X3b).

Alleen de standaardbibliotheek: de worker (app/analyse/worker/ops/beeld.py) leest het bestand per
eigenaar met dezelfde kop, en de app laadt numpy nooit (test_no_numpy_in_app.py).

**Het bestand per eigenaar** (Keuze 76): `<PLATFORM_BEELD_CACHE>/<user_id als 32 hex>.vec`.

- een kop van HEAD.size (64) bytes, little endian: MAGIC, het model (32 bytes ASCII, rechts
  aangevuld met NUL), de versie van de stap embed (VERSIONS[Step.EMBED]), dim, het aantal n en
  gen (beeld_owner.gen toen het bestand gemaakt werd);
- n × int64: de num's, strikt oplopend (ORDER BY n.num);
- n × dim × float16: de vectoren als één matrix; rij i hoort bij num i;
- n × SHA_BYTES: de eerste 8 bytes van de sha256 van de inhoud waar rij i van is. De worker leest
  ze niet; de app houdt er een node mee buiten die sinds het bestand andere inhoud kreeg (§3.9:
  "de oude inhoud nooit meer bij deze node", ook voordat het bestand opnieuw is gemaakt).

De worker krijgt het bestand als fd (alleen lezen), nooit het pad (P8), en houdt het in zijn
cache onder (eigenaar, gen, sha256 van het bestand) (K2-23). Een vector gaat nooit in een bericht
(K1-08): "Meer zoals deze" stuurt de num.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

MODEL = "siglip2-b32-256"  # de map onder MODELLEN_ROOT (omzetten.MAP); een test houdt ze gelijk
DIM = 768  # wire.BEELD_DIM; de echte dim staat in caps.beeld
MAGIC = b"PBV1"
HEAD = struct.Struct("<4s32sIIQQ4x")
NUM = struct.Struct("<q")
SHA_BYTES = 8  # het begin van de sha256 van de inhoud, per rij (16 hex-tekens van blob_id)
MODEL_BYTES = 32
MAX_COUNT = 10_000_000  # meer foto's per eigenaar is geen bestand van ons

# Wat rank zegt als de num van "Meer zoals deze" niet in het bestand staat (het bestand is ouder
# dan de vector): de app geeft dan 409 beeld_no_vector. Een vaste zin, zonder waarde.
NO_NUM = "de num staat niet in het bestand"
BAD_FILE = "het vectorbestand klopt niet"


def sha_prefix(sha: str) -> bytes:
    """De SHA_BYTES van een blob_id (64 hex-tekens) voor het bestand."""
    return bytes.fromhex(sha[: SHA_BYTES * 2])


class VecFileError(ValueError):
    """Een kop die niet klopt; de tekst noemt nooit een waarde uit het bestand."""


@dataclass(frozen=True)
class Head:
    model: str
    version: int
    dim: int
    count: int
    gen: int

    def size(self) -> int:
        """Zoveel bytes hoort het hele bestand te hebben."""
        return self.shas_at() + self.count * SHA_BYTES

    def nums_at(self) -> int:
        return HEAD.size

    def vectors_at(self) -> int:
        return HEAD.size + self.count * NUM.size

    def shas_at(self) -> int:
        return self.vectors_at() + self.count * self.dim * 2


def pack_head(head: Head) -> bytes:
    name = head.model.encode("ascii")
    if not name or len(name) > MODEL_BYTES or b"\0" in name:
        raise VecFileError("de naam van het model past niet in de kop")
    if not 1 <= head.dim <= 4096 or not 0 <= head.count <= MAX_COUNT or head.gen < 0:
        raise VecFileError("dim, aantal of gen past niet in de kop")
    return HEAD.pack(MAGIC, name, head.version, head.dim, head.count, head.gen)


def read_head(data: bytes) -> Head:
    """De kop uit de eerste HEAD.size bytes. VecFileError als hij niet klopt."""
    if len(data) < HEAD.size:
        raise VecFileError(BAD_FILE)
    magic, name, version, dim, count, gen = HEAD.unpack_from(data)
    if magic != MAGIC:
        raise VecFileError(BAD_FILE)
    raw = name.rstrip(b"\0")
    if not raw or b"\0" in raw:
        raise VecFileError(BAD_FILE)
    try:
        model = raw.decode("ascii")
    except UnicodeDecodeError:
        raise VecFileError(BAD_FILE) from None
    if not 1 <= dim <= 4096 or count > MAX_COUNT or gen > (1 << 62):
        raise VecFileError(BAD_FILE)
    return Head(model, version, dim, count, gen)
