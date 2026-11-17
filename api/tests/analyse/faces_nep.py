"""De nep van de opdrachten faces en group, voor laag 1 (fase 6, X2 "Testen zonder echte
gewichten"; X2b). Zonder numpy en zonder modellen.

- **faces.** De nep leest de 1024 van elke foto uit zijn fd (alleen lezen, zoals de echte worker)
  en zoekt de sha256 van die bytes op in wat de test met add() opgaf: kaders, en per gezicht een
  "persoon" (een getal). De vector van een gezicht is een vaste eenheidsvector per persoon plus
  wat ruis per gezicht (cosinus met dezelfde persoon ±0,97, met een ander ±0); die gaan achter
  elkaar in de memfd uit. Een foto die de nep niet kent, heeft geen gezichten. code() geeft een
  foto een andere code (unreadable, too_big); lie() laat de nep liegen (bad_output).
- **group.** De referentie in puur Python (groep_ref.run) over de invoer uit de verzegelde memfd.

FakeAnalyseWorker roept ze aan via fake.on(Op.FACES, nep.faces) en fake.on(Op.GROUP, nep.group);
zie install().
"""

from __future__ import annotations

import hashlib
import math
import os
import random
import struct
from dataclasses import dataclass, field

from app.analyse import wire
from tests.analyse import groep_ref
from tests.analyse.fake_worker import FakeAnalyseWorker, FakeRequest

Box = tuple[float, float, float, float]
_VEC = struct.Struct(f"<{wire.FACE_DIM}f")


def _read(fd: int) -> bytes:
    size = os.fstat(fd).st_size
    out = bytearray()
    while len(out) < size:
        chunk = os.pread(fd, size - len(out), len(out))
        if not chunk:
            break
        out += chunk
    return bytes(out)


def unit(values: list[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in values))
    return [v / norm for v in values]


def base(person: int) -> list[float]:
    """De richting van een persoon: vast per getal."""
    rnd = random.Random(f"persoon-{person}")
    return unit([rnd.gauss(0, 1) for _ in range(wire.FACE_DIM)])


def vector(person: int, variant: int, noise: float = 0.25) -> bytes:
    """Een gezicht van persoon met wat ruis (variant): 512 B, float32, norm 1."""
    rnd = random.Random(f"gezicht-{person}-{variant}")
    b = base(person)
    ruis = unit([rnd.gauss(0, 1) for _ in range(wire.FACE_DIM)])
    return _VEC.pack(*unit([x + noise * r for x, r in zip(b, ruis, strict=True)]))


@dataclass
class FacesNep:
    photos: dict[str, list[tuple[Box, int]]] = field(default_factory=dict)
    codes: dict[str, str] = field(default_factory=dict)
    lies: list[str] = field(default_factory=list)
    counter: int = 0
    seen: int = 0  # zoveel previews kreeg de nep
    groups: int = 0  # zoveel keer group

    def add(self, data: bytes, faces: list[tuple[Box, int]]) -> str:
        """De 1024 met deze bytes heeft deze gezichten (kader, persoon)."""
        sha = hashlib.sha256(data).hexdigest()
        self.photos[sha] = list(faces)
        return sha

    def code(self, data: bytes, code: str) -> None:
        self.codes[hashlib.sha256(data).hexdigest()] = code

    def lie(self, how: str) -> None:
        """Eén keer liegen: "nan", "norm", "box", "short" (te weinig bytes), "count" (n klopt
        niet met de foto's)."""
        self.lies.append(how)

    async def faces(self, req: FakeRequest) -> wire.FacesData:
        photos: list[wire.FacesPhoto] = []
        vecs: list[bytes] = []
        for fd in req.previews:
            self.seen += 1
            key = hashlib.sha256(_read(fd)).hexdigest()
            code = self.codes.get(key, "ok")
            if code != "ok":
                photos.append(wire.FacesPhoto(code=code))
                continue
            boxes = []
            for (x, y, w, h), person in self.photos.get(key, []):
                boxes.append(wire.FaceBox(x=x, y=y, w=w, h=h, score=0.99, px=int(w * 1024)))
                self.counter += 1
                vecs.append(vector(person, self.counter))
            photos.append(wire.FacesPhoto(code="ok", w=1024, h=768, faces=boxes, n=len(boxes)))
        lie = self.lies.pop(0) if self.lies else None
        if lie in ("nan", "norm") and vecs:
            values = list(_VEC.unpack(vecs[0]))
            values[0] = float("nan") if lie == "nan" else values[0] * 3
            vecs[0] = _VEC.pack(*values)
        elif lie == "box":
            for photo in photos:
                if photo.faces:
                    photo.faces[0] = photo.faces[0].model_copy(update={"x": 0.9, "w": 0.5})
                    break
        elif lie == "short" and vecs:
            vecs[-1] = vecs[-1][:-4]
        elif lie == "count":
            photos = photos[:-1]
        data = b"".join(vecs)
        view = memoryview(data)
        while view:
            view = view[os.write(req.extra["uit"], view) :]
        return wire.FacesData(photos=photos)

    async def group(self, req: FakeRequest) -> wire.GroupData:
        self.groups += 1
        data = _read(req.extra["in"])
        args = req.args
        assert isinstance(args, wire.GroupArgs)
        out = groep_ref.run(data, args)
        view = memoryview(out)
        while view:
            view = view[os.write(req.extra["uit"], view) :]
        n = wire.GROUP_HEAD.unpack_from(data)[1]
        return wire.GroupData(n=n)


def install(fake: FakeAnalyseWorker) -> FacesNep:
    nep = FacesNep()
    fake.on(wire.Op.FACES, nep.faces)
    fake.on(wire.Op.GROUP, nep.group)
    return nep
