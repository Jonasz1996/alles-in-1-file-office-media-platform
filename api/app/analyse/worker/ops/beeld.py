"""Zoeken op beeld in de container (fase 6, X3b; Keuze 75, 77, 78; K1-08, K2-13, K2-23).

Vier opdrachten. embed_image draait in het kind werk (decodeert previews); embed_text, rank en
forget in het kind zoeken, dat nooit een beeld opent (Keuze 75): Pillow en ops/faces.py laadt
deze module pas in embed_image.

- **embed_image** (werk): ≤ 16 previews als fd (de 256.webp, Keuze 77), en uit. Per preview:
  pread vanaf 0, alleen WebP, hooguit 4 MP (zoals faces: too_big, unreadable, image_bad); de
  voorbewerking van meta.json (RGB, resize naar de grootte met de resample van Pillow, × schaal,
  (x − gemiddelde) / std, NCHW: zoals beeld_echt.voorbewerk van X3a); het beeldmodel in int8 voor
  alle goede previews in één run; L2 en float16. uit krijgt k vakken van dim × 2 bytes in de
  volgorde van de previews; een vak van een foto die niet ok is, blijft nul.
- **embed_text** (zoeken): tokenizer.bin (afkappen op 63, <eos>, opvullen tot 64), het tekstmodel
  in int8, L2, float16 in uit. Voor de kwaliteitsmeting en de tests; rank doet hetzelfde zelf.
- **rank** (zoeken): de fd van het bestand per eigenaar (app/beeld/types.py), de eigenaar, zijn
  gen, en een tekst óf een num ("Meer zoals deze": de vector zoekt deze module zelf op in het
  bestand, K1-08), k ≤ 1.000. De cosinus van elke vector met de vraag, de beste k, [[num, s]]
  met s = cos × 10.000. Bij like valt de foto zelf weg. Met k = 0 alleen het bestand in de cache
  zetten (opwarmen, K2-13).
- **forget** (zoeken): de vectoren van die eigenaar uit het geheugen (bij "uit", K1-08).

**De cache** (Keuze 75, K2-23). Per eigenaar één bestand in het geheugen, als float32 (de matrix
× vraag kost dan ±20–50 ms over 55.000–100.000 foto's op één core). De sleutel is (eigenaar, gen,
sha256 van het bestand): twee eigenaren met dezelfde gen hebben elk hun eigen vectoren, en een
bestand met dezelfde gen maar een andere inhoud (na wissen begint gen opnieuw) laadt opnieuw. De
sha256 over 154 MB kost ±0,1–0,2 s; daarom onthoudt de cache hem per (apparaat, inode, grootte,
mtime, ctime) van de fd, zoals caps dat voor de modellen doet. De app schrijft een nieuw bestand
altijd naar een tijdelijk bestand en dan os.replace, dus een nieuwe inhoud is een nieuwe inode.
Samen hooguit ANALYSE_BEELD_CACHE_MB (400 MB, ±136.000 foto's); daarboven gaat de eigenaar eruit
die het langst niet zocht. Heeft één eigenaar meer foto's dan de grens, dan too_big (de app valt
terug op gewoon zoeken, R43).

**Wat nooit naar buiten gaat** (K1-16): geen vector in een bericht (alleen in de memfd uit), geen
tekst van een vraag in een fout, op stderr of in een detail. Een fout noemt nooit een waarde: een
kop die niet klopt is bad_request met een vaste zin.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import stat
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from app.analyse import wire
from app.analyse.tokenizer import Tokenizer, TokenizerFout
from app.analyse.wire import Code
from app.analyse.worker import omzetten
from app.analyse.worker.child import Job, OpFailed, Stopped
from app.analyse.worker.models import read_model
from app.beeld import types as vt

MAP = omzetten.MAP
BEELD = f"{MAP}/{omzetten.BEELD}"
TEKST = f"{MAP}/{omzetten.TEKST}"
TOKENIZER = f"{MAP}/{omzetten.TOKENIZER_BIN}"
META = f"{MAP}/{omzetten.META}"
MAX_META = 1_000_000  # meta.json is ±2 kB
MIB = 1024 * 1024
CACHE_BYTES = wire.ANALYSE_BEELD_CACHE_MB * MIB
CHUNK_ROWS = 4096  # rijen per keer lezen, hashen en omzetten (4096 × 768 × 2 = 6 MB)
SHA_MEMO = 64  # zoveel (inode …) → sha256 onthoudt de cache
RESAMPLE = {"nearest": 0, "lanczos": 1, "bilinear": 2, "bicubic": 3, "box": 4, "hamming": 5}


# --- het model: meta.json, tokenizer.bin -----------------------------------------------------


@dataclass(frozen=True)
class Meta:
    """Wat de worker uit meta.json gebruikt (de omzetter van X3a schrijft het)."""

    dim: int
    logit_scale: float
    logit_bias: float
    beeld_in: str
    beeld_uit: str
    size: tuple[int, int]  # (hoogte, breedte)
    resample: int  # de resample van Pillow
    schaal: float
    gemiddelde: tuple[float, float, float]
    std: tuple[float, float, float]
    tekst_in: str
    tekst_uit: str
    lengte: int


def _checked_bytes(models: Any, name: str, limit: int) -> bytes:
    """De bytes van een bestand uit models.lock, met de sha256 nagekeken (zoals Models.session).
    Ontbreekt het of klopt het niet: model_missing met de naam (nooit de inhoud)."""
    item = models.entry(name)
    if item.size > limit:
        raise OpFailed(Code.MODEL_MISSING, f"{name} is groter dan verwacht")
    try:
        data = read_model(models.root / name, item.size)
    except OSError:
        raise OpFailed(Code.MODEL_MISSING, f"{name} ontbreekt") from None
    if len(data) != item.size or hashlib.sha256(data).hexdigest() != item.sha256:
        raise OpFailed(Code.MODEL_MISSING, f"{name} is anders dan models.lock")
    return data


def _number(value: object) -> float:
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError("geen getal")
    return float(value)


def parse_meta(raw: bytes) -> Meta:
    """meta.json streng gelezen; ValueError als iets niet klopt."""
    doc = json.loads(raw.decode("utf-8"))
    beeld, tekst = doc["beeld"], doc["tekst"]
    vb = beeld["voorbewerking"]
    dim = doc["dim"]
    if type(dim) is not int or not 1 <= dim <= 4096 or doc.get("model") != MAP:
        raise ValueError("dim of model")
    h, w = vb["grootte"]
    if type(h) is not int or type(w) is not int or not (1 <= h <= 4096 and 1 <= w <= 4096):
        raise ValueError("grootte")
    resample = vb["resample_pil"]
    if type(resample) is not int or resample not in RESAMPLE.values():
        raise ValueError("resample")
    gem = tuple(_number(x) for x in vb["gemiddelde"])
    std = tuple(_number(x) for x in vb["std"])
    if len(gem) != 3 or len(std) != 3 or min(std) <= 0:
        raise ValueError("gemiddelde of std")
    if vb.get("rgb") is not True or vb.get("volgorde") != "NCHW":
        raise ValueError("volgorde")
    lengte = tekst["lengte"]
    if type(lengte) is not int or not 1 <= lengte <= 4096:
        raise ValueError("lengte")
    names = (beeld["invoer"], beeld["uitvoer"], tekst["invoer"], tekst["uitvoer"])
    if not all(isinstance(n, str) and n for n in names):
        raise ValueError("namen")
    schaal = _number(vb["schaal"])
    if schaal <= 0:
        raise ValueError("schaal")
    return Meta(
        dim=dim,
        logit_scale=_number(doc["logit_scale"]),
        logit_bias=_number(doc["logit_bias"]),
        beeld_in=beeld["invoer"],
        beeld_uit=beeld["uitvoer"],
        size=(h, w),
        resample=resample,
        schaal=schaal,
        gemiddelde=gem,  # type: ignore[arg-type]
        std=std,  # type: ignore[arg-type]
        tekst_in=tekst["invoer"],
        tekst_uit=tekst["uitvoer"],
        lengte=lengte,
    )


_loaded: dict[tuple[str, str, str], Any] = {}


def meta(models: Any) -> Meta:
    key = (str(models.root), str(models.lock_path), META)
    found = _loaded.get(key)
    if found is None:
        raw = _checked_bytes(models, META, MAX_META)
        try:
            found = parse_meta(raw)
        except (ValueError, KeyError, TypeError, UnicodeDecodeError):
            raise OpFailed(Code.MODEL_MISSING, f"{META} klopt niet") from None
        _loaded[key] = found
    return found


def tokenizer(models: Any) -> Tokenizer:
    key = (str(models.root), str(models.lock_path), TOKENIZER)
    found = _loaded.get(key)
    if found is None:
        raw = _checked_bytes(models, TOKENIZER, 1 << 30)
        try:
            found = Tokenizer.from_bin(raw)
        except TokenizerFout:
            raise OpFailed(Code.MODEL_MISSING, f"{TOKENIZER} klopt niet") from None
        _loaded[key] = found
    return found


# --- rekenen ------------------------------------------------------------------------------------


def _run(sess: Any, name: str, feeds: dict, job: Job) -> np.ndarray:
    """Eén run van onnxruntime die pid 1 kan stoppen (RunOptions.terminate, zoals faces)."""
    import onnxruntime

    options = onnxruntime.RunOptions()
    options.log_severity_level = 3
    job.stop.run_options = options
    job.stop.check()
    try:
        (out,) = sess.run([name], feeds, options)
    except Exception:
        if job.stop.set:
            raise Stopped() from None
        raise
    finally:
        job.stop.run_options = None
    return out


def unit_rows(out: np.ndarray, dim: int) -> tuple[np.ndarray, np.ndarray]:
    """Elke rij L2-genormaliseerd (float32) en per rij of hij bruikbaar is (eindig, lengte > 0)."""
    a = np.asarray(out, dtype=np.float64).reshape(len(out), -1)
    if a.shape[1] != dim:
        raise OpFailed(Code.INTERNAL, "het model geeft een andere dim dan meta.json")
    norm = np.linalg.norm(a, axis=1)
    good = np.isfinite(norm) & (norm > 0) & np.all(np.isfinite(a), axis=1)
    safe = np.where(good, norm, 1.0)
    return (a / safe[:, None]).astype(np.float32), good


def preprocess(rgb: np.ndarray, m: Meta) -> np.ndarray:
    """De voorbewerking van meta.json: float32 [3, H, W] (Keuze 77; beeld_echt.voorbewerk)."""
    from PIL import Image

    h, w = m.size
    img = Image.fromarray(rgb, mode="RGB").resize((w, h), resample=m.resample)
    a = np.asarray(img, dtype=np.float32) * np.float32(m.schaal)
    a = (a - np.asarray(m.gemiddelde, np.float32)) / np.asarray(m.std, np.float32)
    return np.ascontiguousarray(a.transpose(2, 0, 1), dtype=np.float32)


def text_vector(text: str, job: Job, m: Meta) -> np.ndarray:
    """De vector van een vraag in woorden: float32 [dim], lengte 1."""
    tok = tokenizer(job.models)
    sess = job.models.session(TEKST)
    ids = tok.encode(text)
    if len(ids) != m.lengte:
        raise OpFailed(Code.MODEL_MISSING, f"{TOKENIZER} past niet bij {META}")
    out = _run(sess, m.tekst_uit, {m.tekst_in: np.asarray([ids], dtype=np.int64)}, job)
    rows, good = unit_rows(out, m.dim)
    if not good[0]:
        raise OpFailed(Code.INTERNAL, "het tekstmodel gaf geen bruikbare vector")
    return rows[0]


def _write_all(fd: int, data: bytes, offset: int) -> None:
    view = memoryview(data)
    while view:
        done = os.pwrite(fd, view, offset)
        view, offset = view[done:], offset + done


def _empty_out(fd: int, size: int) -> None:
    try:
        os.ftruncate(fd, 0)
        os.ftruncate(fd, size)
    except OSError:
        raise OpFailed(Code.BAD_REQUEST, "uit is geen memfd om in te schrijven") from None


# --- embed_image (werk) ------------------------------------------------------------------------


def embed_image(job: Job) -> dict:
    try:
        wire.EmbedImageArgs.model_validate(job.args)
    except ValueError:
        raise OpFailed(Code.BAD_REQUEST, "args van embed_image") from None
    if len(job.fds) < 2:
        raise OpFailed(Code.BAD_REQUEST, "embed_image vraagt previews en uit")
    from app.analyse.worker.ops import faces  # Pillow: alleen in werk

    previews, out = job.fds[:-1], job.fds[-1]
    # Het model eerst: ontbreekt het, dan model_missing voor de hele opdracht
    m = meta(job.models)
    sess = job.models.session(BEELD)
    slot = m.dim * 2
    _empty_out(out, len(previews) * slot)
    codes: list[str] = []
    batch: list[np.ndarray] = []
    where: list[int] = []
    for i, fd in enumerate(previews):
        job.stop.check()
        try:
            rgb = faces.decode(faces.read_preview(fd))
            batch.append(preprocess(rgb, m))
        except faces.PhotoError as exc:
            codes.append(exc.code)
            continue
        except (OSError, ValueError):
            codes.append("image_bad")
            continue
        codes.append(Code.OK.value)
        where.append(i)
    if batch:
        pixels = np.stack(batch)
        del batch
        rows, good = unit_rows(_run(sess, m.beeld_uit, {m.beeld_in: pixels}, job), m.dim)
        if len(rows) != len(where):
            raise OpFailed(Code.INTERNAL, "het beeldmodel gaf een ander aantal vectoren")
        for row, ok, i in zip(rows, good, where, strict=True):
            if not ok:
                codes[i] = "image_bad"
                continue
            _write_all(out, row.astype("<f2").tobytes(), i * slot)
    photos = [{"code": c} for c in codes]
    return wire.EmbedImageData.model_validate({"photos": photos}).model_dump(mode="json")


# --- embed_text (zoeken) -----------------------------------------------------------------------


def embed_text(job: Job) -> dict:
    try:
        args = wire.EmbedTextArgs.model_validate(job.args)
    except ValueError:
        raise OpFailed(Code.BAD_REQUEST, "args van embed_text") from None
    if len(job.fds) != 1:
        raise OpFailed(Code.BAD_REQUEST, "embed_text vraagt uit")
    m = meta(job.models)
    vec = text_vector(args.text, job, m)
    _empty_out(job.fds[0], m.dim * 2)
    _write_all(job.fds[0], vec.astype("<f2").tobytes(), 0)
    return wire.EmbedTextData().model_dump(mode="json")


# --- de cache ------------------------------------------------------------------------------------


@dataclass
class Entry:
    """De vectoren van één eigenaar in het geheugen."""

    owner: str
    gen: int
    sha: str
    nums: np.ndarray  # int64, strikt oplopend
    mat: np.ndarray  # float32 [n, dim]
    used: float = field(default_factory=time.monotonic)

    @property
    def nbytes(self) -> int:
        return int(self.nums.nbytes + self.mat.nbytes)


_StatKey = tuple[int, int, int, int, int]


def need_bytes(count: int, dim: int) -> int:
    return count * (dim * 4 + 8)


class Cache:
    """De vectoren per eigenaar, hooguit limit bytes samen; de eigenaar die het langst niet zocht
    gaat er als eerste uit."""

    def __init__(self, limit: int = CACHE_BYTES, clock=time.monotonic) -> None:
        self.limit = limit
        self.clock = clock
        self.entries: dict[str, Entry] = {}
        self.shas: dict[_StatKey, str] = {}
        self.loads = 0  # hoe vaak een bestand gelezen werd (voor de tests)

    def total(self) -> int:
        return sum(e.nbytes for e in self.entries.values())

    def drop(self, owner: str) -> bool:
        return self.entries.pop(owner, None) is not None

    def clear(self) -> None:
        self.entries.clear()
        self.shas.clear()

    def _room(self, need: int) -> None:
        while self.entries and self.total() + need > self.limit:
            oldest = min(self.entries.values(), key=lambda e: e.used)
            del self.entries[oldest.owner]

    def _remember(self, key: _StatKey, sha: str) -> None:
        self.shas[key] = sha
        while len(self.shas) > SHA_MEMO:
            self.shas.pop(next(iter(self.shas)))

    def get(self, owner: str, gen: int, fd: int, head: vt.Head, job: Job) -> Entry:
        """De vectoren van owner uit het bestand van fd (in de cache, of nu gelezen)."""
        info = os.fstat(fd)
        key = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
        found = self.entries.get(owner)
        sha = self.shas.get(key)
        if found is not None and sha is not None and (found.gen, found.sha) == (gen, sha):
            found.used = self.clock()
            return found
        need = need_bytes(head.count, head.dim)
        if need > self.limit:
            raise OpFailed(Code.TOO_BIG, "meer foto's dan de cache van de container kan houden")
        # Het oude bestand van deze eigenaar eerst weg (minder geheugen tegelijk): uit de cache én
        # uit found, anders blijft zijn matrix bestaan tot na het laden (BZ5: +307 MB bij 100.000)
        found = None
        self.drop(owner)
        self._room(need)
        nums, mat, sha = load(fd, head, job)
        self.loads += 1
        self._remember(key, sha)
        entry = Entry(owner, gen, sha, nums, mat, self.clock())
        self.entries[owner] = entry
        return entry


CACHE = Cache()


def _pread_into(fd: int, buf: memoryview, offset: int) -> None:
    while buf:
        n = os.preadv(fd, [buf], offset)
        if n <= 0:
            raise OpFailed(Code.BAD_REQUEST, vt.BAD_FILE)
        buf, offset = buf[n:], offset + n


def load(fd: int, head: vt.Head, job: Job) -> tuple[np.ndarray, np.ndarray, str]:
    """Het hele bestand lezen, in stukken: de sha256 onderweg, float16 → float32 per stuk (zo
    staat er naast de matrix hooguit één stuk extra in het geheugen), en tussen de stukken kijken
    of pid 1 wil stoppen."""
    digest = hashlib.sha256()
    first = bytearray(vt.HEAD.size)
    _pread_into(fd, memoryview(first), 0)
    digest.update(first)
    n, dim = head.count, head.dim
    nums = np.empty(n, dtype="<i8")
    _pread_into(fd, memoryview(nums.view(np.uint8)), head.nums_at())
    digest.update(nums.view(np.uint8))
    if n and not bool(np.all(np.diff(nums) > 0)) or (n and nums[0] < 0):
        raise OpFailed(Code.BAD_REQUEST, vt.BAD_FILE)
    mat = np.empty((n, dim), dtype=np.float32)
    part = np.empty((CHUNK_ROWS, dim), dtype="<f2")
    offset = head.vectors_at()
    for start in range(0, n, CHUNK_ROWS):
        job.stop.check()
        rows = min(CHUNK_ROWS, n - start)
        view = part[:rows]
        raw = memoryview(view.reshape(-1).view(np.uint8))
        _pread_into(fd, raw, offset)
        digest.update(raw)
        offset += rows * dim * 2
        block = view.astype(np.float32)
        if not bool(np.all(np.isfinite(block))):
            raise OpFailed(Code.BAD_REQUEST, vt.BAD_FILE)
        mat[start : start + rows] = block
    return nums.astype(np.int64, copy=False), mat, digest.hexdigest()


def read_head(fd: int, m: Meta) -> vt.Head:
    """De kop van het bestand per eigenaar, nagekeken tegen meta.json en de grootte van de fd."""
    try:
        info = os.fstat(fd)
    except OSError:
        raise OpFailed(Code.BAD_REQUEST, vt.BAD_FILE) from None
    if not stat.S_ISREG(info.st_mode):
        raise OpFailed(Code.BAD_REQUEST, vt.BAD_FILE)
    try:
        raw = os.pread(fd, vt.HEAD.size, 0)
        head = vt.read_head(raw)
    except (OSError, vt.VecFileError):
        raise OpFailed(Code.BAD_REQUEST, vt.BAD_FILE) from None
    if head.model != MAP or head.dim != m.dim or head.size() != info.st_size:
        raise OpFailed(Code.BAD_REQUEST, vt.BAD_FILE)
    return head


def top(scores: np.ndarray, nums: np.ndarray, k: int, skip: int | None = None) -> list[list[int]]:
    """De beste k als [[num, s]], s = round(cos × 10.000); bij gelijke score de kleinste num
    eerst. skip: een rij die niet meedoet (de foto zelf bij "Meer zoals deze")."""
    n = int(scores.shape[0])
    if skip is not None:
        scores[skip] = -np.inf
        n -= 1
    k = min(k, n)
    if k <= 0:
        return []
    if k < scores.shape[0]:
        idx = np.argpartition(-scores, k - 1)[:k]
    else:
        idx = np.arange(scores.shape[0])
    idx = idx[np.isfinite(scores[idx])]
    idx = idx[np.lexsort((nums[idx], -scores[idx]))][:k]
    s = np.rint(np.clip(scores[idx], -1.0, 1.0) * 10_000).astype(np.int64)
    return [[int(a), int(b)] for a, b in zip(nums[idx], s, strict=True)]


# --- rank en forget (zoeken) -------------------------------------------------------------------


def rank(job: Job, cache: Cache | None = None) -> dict:
    cache = CACHE if cache is None else cache
    try:
        args = wire.RankArgs.model_validate(job.args)
    except ValueError:
        raise OpFailed(Code.BAD_REQUEST, "args van rank") from None
    if len(job.fds) != 1:
        raise OpFailed(Code.BAD_REQUEST, "rank vraagt het vectorbestand")
    m = meta(job.models)
    if args.text is not None:  # het model eerst: ontbreekt het, dan geen bestand laden
        job.models.session(TEKST)
        tokenizer(job.models)
    fd = job.fds[0]
    head = read_head(fd, m)
    if head.gen != args.gen:
        raise OpFailed(Code.BAD_REQUEST, vt.BAD_FILE)
    entry = cache.get(args.owner, args.gen, fd, head, job)
    if args.k == 0:
        return wire.RankData().model_dump(mode="json")
    skip: int | None = None
    if args.num is not None:
        i = int(np.searchsorted(entry.nums, args.num))
        if i >= len(entry.nums) or int(entry.nums[i]) != args.num:
            raise OpFailed(Code.BAD_REQUEST, vt.NO_NUM)
        query = entry.mat[i].copy()
        skip = i
    else:
        assert args.text is not None
        query = text_vector(args.text, job, m)
    job.stop.check()
    scores = entry.mat @ query
    hits = top(scores, entry.nums, args.k, skip)
    return wire.RankData.model_validate({"hits": hits}).model_dump(mode="json")


def forget(job: Job, cache: Cache | None = None) -> dict:
    cache = CACHE if cache is None else cache
    try:
        args = wire.ForgetArgs.model_validate(job.args)
    except ValueError:
        raise OpFailed(Code.BAD_REQUEST, "args van forget") from None
    if job.fds:
        raise OpFailed(Code.BAD_REQUEST, "forget vraagt geen fd")
    found = cache.drop(args.owner)
    return wire.ForgetData(found=found).model_dump(mode="json")


def models_dir(models: Any) -> Path:
    return Path(models.root) / MAP
