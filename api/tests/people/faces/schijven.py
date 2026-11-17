"""Foto's met gekleurde schijven als gezichten, en een nep-worker die ze vindt (fase 6, X2b; voor
e2e/test_zoek_gezichten.py en e2e/test_zoek_gezichten_echt.py). Zonder numpy en zonder modellen.

- **foto(schijven)** maakt een JPEG van 1600 × 1200: grijze ruis (geen verzadigde kleur) met
  ronde schijven. De kleur van een schijf is de "persoon" (KLEUREN). Zonder schijven is het een
  foto zonder gezicht, ook voor de echte modellen (de e2e met de echte container).
- **vind(beeld)** zoekt in de 1024 van de app per kleur de pixels van die kleur en geeft het kader
  eromheen (0–1). Eén schijf per kleur per foto.
- **Schijven** is de handler van Op.FACES voor FakeAnalyseWorker: hij leest de 1024 uit de fd
  (alleen lezen, zoals de echte worker), vindt de schijven en schrijft per schijf de vector van
  die persoon in de memfd uit (faces_nep.vector: dezelfde persoon lijkt, een ander niet).
- **NepInDraad** draait FakeAnalyseWorker met Schijven en de groep van faces_nep (groep_ref) in
  een eigen draad met een eigen event loop. De app (een eigen proces) praat ermee over de echte
  socket met de echte handdruk; de e2e (Playwright, synchroon) hoeft niets te doen.

Nooit een vector in een log of assert-tekst: de test kijkt alleen naar aantallen en kaders.
"""

from __future__ import annotations

import asyncio
import io
import os
import random
import threading
from collections.abc import Iterable
from pathlib import Path

from PIL import Image, ImageDraw

from app.analyse import wire
from tests.analyse import faces_nep
from tests.analyse.fake_worker import FakeAnalyseWorker, FakeRequest

Box = tuple[float, float, float, float]

# De persoon van een kleur (rood = 1, blauw = 2); de drempels van vind() passen bij deze kleuren
KLEUREN: dict[int, tuple[int, int, int]] = {1: (225, 35, 35), 2: (35, 70, 225)}
BREED, HOOG = 1600, 1200


def foto(schijven: Iterable[tuple[int, float, float, float]] = (), *, seed: int = 0) -> bytes:
    """Een JPEG van 1600 × 1200 met ruis en schijven (persoon, midden x, midden y, straal), alles
    als deel van de breedte (x, straal) of de hoogte (y). seed maakt elke foto anders (de bytes
    en de pHash), zodat de app ze niet als dezelfde inhoud ziet."""
    rnd = random.Random(f"schijven-{seed}")
    klein = Image.new("L", (BREED // 16, HOOG // 16))
    klein.putdata([rnd.randint(95, 165) for _ in range((BREED // 16) * (HOOG // 16))])
    beeld = klein.resize((BREED, HOOG), Image.Resampling.BILINEAR).convert("RGB")
    teken = ImageDraw.Draw(beeld)
    for persoon, x, y, r in schijven:
        cx, cy, straal = x * BREED, y * HOOG, r * BREED
        teken.ellipse((cx - straal, cy - straal, cx + straal, cy + straal), fill=KLEUREN[persoon])
    out = io.BytesIO()
    beeld.save(out, "JPEG", quality=90)
    return out.getvalue()


def kader(x: float, y: float, r: float) -> Box:
    """Het kader van een schijf van foto() (0–1)."""
    return (x - r, y - r * BREED / HOOG, 2 * r, 2 * r * BREED / HOOG)


def _van(rgb: tuple[int, int, int]) -> int | None:
    r, g, b = rgb
    if r > 170 and g < 100 and b < 100:
        return 1
    if b > 170 and r < 100 and g < 130:
        return 2
    return None


def vind(beeld: Image.Image) -> list[tuple[Box, int]]:
    """Per kleur van KLEUREN het kader (x, y, w, h als deel van het beeld) van zijn pixels, op een
    verkleining tot 256 breed (genoeg voor een schijf van ≥ 40 px in de 1024)."""
    w, h = beeld.size
    stap = max(1, w // 256)
    klein = beeld.convert("RGB").resize((w // stap, h // stap), Image.Resampling.NEAREST)
    kw, kh = klein.size
    grenzen: dict[int, list[int]] = {}
    data = klein.tobytes()
    for i in range(kw * kh):
        persoon = _van((data[3 * i], data[3 * i + 1], data[3 * i + 2]))
        if persoon is None:
            continue
        x, y = i % kw, i // kw
        g = grenzen.setdefault(persoon, [x, y, x, y])
        g[0], g[1], g[2], g[3] = min(g[0], x), min(g[1], y), max(g[2], x), max(g[3], y)
    uit = []
    for persoon in sorted(grenzen):
        x0, y0, x1, y1 = grenzen[persoon]
        uit.append(((x0 / kw, y0 / kh, (x1 + 1 - x0) / kw, (y1 + 1 - y0) / kh), persoon))
    return uit


class Schijven:
    """Op.FACES: de schijven in elke preview, met een vector per schijf."""

    def __init__(self) -> None:
        self.teller = 0
        self.fotos = 0  # zoveel previews kreeg de nep
        self.gezichten = 0  # zoveel schijven vond hij

    async def faces(self, req: FakeRequest) -> wire.FacesData:
        args = req.args
        assert isinstance(args, wire.FacesArgs)
        photos: list[wire.FacesPhoto] = []
        vecs: list[bytes] = []
        for fd in req.previews:
            self.fotos += 1
            try:
                with Image.open(io.BytesIO(faces_nep._read(fd))) as beeld:
                    beeld.load()
                    w, h = beeld.size
                    gevonden = vind(beeld)
            except OSError:
                photos.append(wire.FacesPhoto(code="unreadable"))
                continue
            boxes = []
            for (x, y, bw, bh), persoon in gevonden:
                px = int(min(bw * w, bh * h))
                if px < args.min_px or len(boxes) >= args.max:
                    continue
                boxes.append(wire.FaceBox(x=x, y=y, w=bw, h=bh, score=0.99, px=px))
                self.teller += 1
                vecs.append(faces_nep.vector(persoon, self.teller))
            self.gezichten += len(boxes)
            photos.append(wire.FacesPhoto(code="ok", w=w, h=h, faces=boxes, n=len(boxes)))
        view = memoryview(b"".join(vecs))
        while view:
            view = view[os.write(req.extra["uit"], view) :]
        return wire.FacesData(photos=photos)


class NepInDraad:
    """FakeAnalyseWorker met Schijven en de groep van faces_nep, in een eigen draad."""

    def __init__(self, socket_path: Path) -> None:
        self.fake = FakeAnalyseWorker(socket_path)
        self.schijven = Schijven()
        self.fake.on(wire.Op.FACES, self.schijven.faces)
        self.fake.on(wire.Op.GROUP, faces_nep.FacesNep().group)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._klaar = threading.Event()
        self._fout: BaseException | None = None

    @property
    def key(self) -> str:
        return self.fake.key

    @property
    def socket(self) -> Path:
        return self.fake.socket

    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        try:
            loop.run_until_complete(self.fake.start())
        except BaseException as exc:  # de draad zegt het tegen start()
            self._fout = exc
            self._klaar.set()
            loop.close()
            return
        self._klaar.set()
        try:
            loop.run_forever()
        finally:
            loop.run_until_complete(self.fake.stop())
            loop.close()

    def start(self) -> NepInDraad:
        self._thread = threading.Thread(target=self._run, name="nep-analyse", daemon=True)
        self._thread.start()
        if not self._klaar.wait(10):
            raise RuntimeError("de nep-worker startte niet binnen 10 s")
        if self._fout is not None:
            raise RuntimeError("de nep-worker startte niet") from self._fout
        return self

    def stop(self) -> None:
        if self._loop is not None and self._thread is not None and self._thread.is_alive():
            self._loop.call_soon_threadsafe(self._loop.stop)
            self._thread.join(10)

    def __enter__(self) -> NepInDraad:
        return self.start()

    def __exit__(self, *exc: object) -> None:
        self.stop()
