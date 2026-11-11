"""models.lock lezen (fase 6, Keuze 58; het formaat is van A0).

Eén regel per bestand: `sha256 bytes naam url`, gescheiden door spaties.

- sha256: 64 kleine hex; bytes: een geheel getal groter dan 0, zonder voorloopnul;
- naam: een pad onder MODELLEN_ROOT, met delen van [A-Za-z0-9._-] gescheiden door "/", zonder
  "." of ".." als deel, nooit absoluut (X3 zet zijn bestanden in een map, siglip2-b32-256/…);
- url: https://…, of "-" voor een bestand dat de host zelf maakt (de omzetting van X3, Keuze 73);
- een veld dat met "#" begint, begint een opmerking tot het eind van de regel; een lege regel
  telt niet. Zo leest bash hem ook: `read -r sha bytes naam url rest`, met rest leeg of "#…".

Alleen de standaardbibliotheek: de worker (zonder numpy in deze module) en install.sh lezen
hetzelfde bestand. Een naam staat er hooguit één keer in.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

LOCK_PATH = Path(__file__).with_name("models.lock")
LICENSES = Path(__file__).with_name("licenties")

_SHA = re.compile(r"[0-9a-f]{64}")
_BYTES = re.compile(r"[1-9][0-9]{0,11}")
_PART = re.compile(r"[A-Za-z0-9._-]+")
_URL = re.compile(r"https://[!-~]+")


class LockError(ValueError):
    """Een regel van models.lock die niet klopt; line is het regelnummer (vanaf 1)."""

    def __init__(self, line: int, why: str) -> None:
        super().__init__(f"models.lock regel {line}: {why}")
        self.line = line


@dataclass(frozen=True)
class ModelFile:
    sha256: str
    size: int
    name: str
    url: str | None  # None: de host maakt hem zelf (url "-")

    def line(self) -> str:
        return f"{self.sha256} {self.size} {self.name} {self.url or '-'}"


def _name_ok(name: str) -> bool:
    parts = name.split("/")
    return all(_PART.fullmatch(p) and p not in (".", "..") for p in parts)


def parse(text: str) -> list[ModelFile]:
    """Alle regels, in de volgorde van het bestand. LockError bij de eerste fout."""
    out: list[ModelFile] = []
    seen: set[str] = set()
    for number, raw in enumerate(text.splitlines(), start=1):
        fields = raw.split()
        for i, field in enumerate(fields):
            if field.startswith("#"):
                fields = fields[:i]
                break
        if not fields:
            continue
        if len(fields) != 4:
            raise LockError(number, f"{len(fields)} velden, verwacht sha256 bytes naam url")
        sha, size, name, url = fields
        if not _SHA.fullmatch(sha):
            raise LockError(number, "sha256 hoort 64 kleine hex")
        if not _BYTES.fullmatch(size):
            raise LockError(number, "bytes hoort een geheel getal groter dan 0")
        if not _name_ok(name):
            raise LockError(number, "naam hoort een pad van [A-Za-z0-9._-] onder MODELLEN_ROOT")
        if url != "-" and not _URL.fullmatch(url):
            raise LockError(number, "url hoort https:// of -")
        if name in seen:
            raise LockError(number, f"{name} staat er twee keer in")
        seen.add(name)
        out.append(ModelFile(sha, int(size), name, None if url == "-" else url))
    return out


def load(path: Path = LOCK_PATH) -> list[ModelFile]:
    return parse(path.read_text(encoding="utf-8"))


def digest(files: list[ModelFile]) -> str:
    """De sha256 over de genormaliseerde regels van deze bestanden (zonder opmerkingen). Zo
    hangt een versie (X2: FACES_VERSION en LOCK_SHA) alleen af van zijn eigen modellen, en
    niet van een regel die X3 erbij zet."""
    text = "".join(f.line() + "\n" for f in files)
    return hashlib.sha256(text.encode()).hexdigest()
