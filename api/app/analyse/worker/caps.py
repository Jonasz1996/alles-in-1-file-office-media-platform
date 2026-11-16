"""caps van pid 1 (fase 6, §3.11, Keuze 58 en 59; X2a). Zonder numpy en zonder een model te laden.

- models: per regel van models.lock ok, ontbreekt of anders. "anders" is een andere grootte, een
  andere sha256, of geen gewoon bestand (een symlink telt niet: O_NOFOLLOW). De sha256 gaat over
  de bytes die de worker leest; pid 1 onthoudt hem per (apparaat, inode, grootte, mtime, ctime),
  zodat caps elke 30 s niet 38 MB leest. Het kind rekent hem bij het laden opnieuw (K1-12).
- built: de opdrachten die deze worker doet (caps en cancel altijd).
- limits: de grenzen van de cgroup (cpu.max en memory.max, of v1), None als er geen zijn.
- beeld (X3b, Keuze 75): model, dim, scale (exp van logit_scale) en bias uit meta.json, als die
  ok is; anders None (dan zoekt de app niet op beeld).

**Welke regels van models.lock meetellen** (X3b; x3a.md, Open 1). De bronnen van een omzetting
(`<map>/bron/…`) niet: install.sh haalt ze alleen om om te zetten en wist ze daarna (Keuze 84).
Een model in een map (`siglip2-b32-256/…`) pas als die map er is: install.sh zet de map pas neer
als de omzetting klaar en nagerekend is, en met BEELD=uit is er geen map. Zo blijft de stand ok
voor gezichten zonder beeld, en is beeld pas klaar als de map er is en elk bestand klopt.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import stat
from pathlib import Path

from app.analyse import lock, wire
from app.analyse.wire import Op
from app.analyse.worker import omzetten

CGROUP = Path("/sys/fs/cgroup")
META = f"{omzetten.MAP}/{omzetten.META}"
MAX_META = 1_000_000
_Key = tuple[int, int, int, int, int]


class ModelStates:
    def __init__(self, root: Path, lock_path: Path = lock.LOCK_PATH) -> None:
        self.root = root
        self.lock_path = lock_path
        self._sha: dict[str, tuple[_Key, str]] = {}

    def _sha256(self, name: str, fd: int, key: _Key) -> str:
        cached = self._sha.get(name)
        if cached is not None and cached[0] == key:
            return cached[1]
        digest = hashlib.sha256()
        offset = 0
        while True:
            chunk = os.pread(fd, 1 << 20, offset)
            if not chunk:
                break
            digest.update(chunk)
            offset += len(chunk)
        value = digest.hexdigest()
        self._sha[name] = (key, value)
        return value

    def state(self, item: lock.ModelFile) -> wire.ModelState:
        try:
            fd = os.open(self.root / item.name, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
        except FileNotFoundError:
            return "ontbreekt"
        except OSError:
            return "anders"  # een symlink (ELOOP), geen rechten, een map
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_size != item.size:
                return "anders"
            key = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
            return "ok" if self._sha256(item.name, fd, key) == item.sha256 else "anders"
        except OSError:
            return "anders"
        finally:
            os.close(fd)

    def counted(self, item: lock.ModelFile) -> bool:
        """Telt deze regel mee? Niet de bron van een omzetting; een model in een map pas als die
        map er is (zie boven)."""
        parts = item.name.split("/")
        if "bron" in parts[:-1]:
            return False
        if len(parts) == 1:
            return True
        try:
            info = os.lstat(self.root / parts[0])
        except OSError:
            return False
        return stat.S_ISDIR(info.st_mode)

    def gather(self) -> list[wire.ModelCaps]:
        return [
            wire.ModelCaps(name=m.name, state=self.state(m))
            for m in lock.load(self.lock_path)
            if self.counted(m)
        ]

    def beeld(self, models: list[wire.ModelCaps]) -> wire.BeeldCaps | None:
        """Het beeldmodel uit meta.json, als die er ok is (de bytes opnieuw nagerekend)."""
        if not any(m.name == META and m.state == "ok" for m in models):
            return None
        item = next((m for m in lock.load(self.lock_path) if m.name == META), None)
        if item is None or item.size > MAX_META:
            return None
        try:
            fd = os.open(self.root / META, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
        except OSError:
            return None
        try:
            raw = os.pread(fd, item.size + 1, 0)
        except OSError:
            return None
        finally:
            os.close(fd)
        if len(raw) != item.size or hashlib.sha256(raw).hexdigest() != item.sha256:
            return None
        try:
            doc = json.loads(raw.decode("utf-8"))
            scale = math.exp(float(doc["logit_scale"]))
            bias = float(doc["logit_bias"])
            if not (math.isfinite(scale) and math.isfinite(bias)):
                return None
            return wire.BeeldCaps(model=doc["model"], dim=doc["dim"], scale=scale, bias=bias)
        except (ValueError, KeyError, TypeError, OverflowError, UnicodeDecodeError):
            return None


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="ascii").strip()
    except (OSError, UnicodeDecodeError):
        return None


def limits(root: Path = CGROUP) -> wire.LimitsCaps | None:
    """cpus en mem_mb uit de cgroup (v2, anders v1). Allebei begrensd, anders None."""
    cpus: float | None = None
    mem: int | None = None
    cpu_max = _read(root / "cpu.max")
    if cpu_max is not None:
        quota, _, period = cpu_max.partition(" ")
        if quota.isdigit() and period.isdigit() and int(period):
            cpus = int(quota) / int(period)
    else:
        quota_v1 = _read(root / "cpu" / "cpu.cfs_quota_us")
        period_v1 = _read(root / "cpu" / "cpu.cfs_period_us")
        if quota_v1 and period_v1 and quota_v1.isdigit() and period_v1.isdigit() and int(period_v1):
            cpus = int(quota_v1) / int(period_v1)
    mem_max = _read(root / "memory.max")
    if mem_max is None:
        mem_max = _read(root / "memory" / "memory.limit_in_bytes")
        # v1 zonder grens: een heel groot getal (PAGE_COUNTER_MAX)
        if mem_max is not None and mem_max.isdigit() and int(mem_max) >= 1 << 60:
            mem_max = "max"
    if mem_max is not None and mem_max.isdigit():
        mem = int(mem_max) // (1024 * 1024)
    if cpus is None or mem is None:
        return None
    return wire.LimitsCaps(cpus=round(cpus, 3), mem_mb=mem)


def built(ops: frozenset[Op]) -> list[str]:
    return sorted({Op.CAPS.value, Op.CANCEL.value} | {op.value for op in ops})


def gather(states: ModelStates, ops: frozenset[Op], cgroup: Path = CGROUP) -> wire.CapsData:
    models = states.gather()
    return wire.CapsData(
        models=models, built=built(ops), beeld=states.beeld(models), limits=limits(cgroup)
    )
