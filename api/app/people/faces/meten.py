"""app.cli analyse-bench en analyse-kwaliteit (fase 6, X2 "Wat de backfill kost" en Vraag 8; X2b).

**analyse-bench**: hoe snel gezichten vinden is op déze CPU. De app maakt FOTOS gemaakte foto's
van 1024 × 768 (ruis, een verloop, een getekend landschap; WebP met de kwaliteit van de
previews), en stuurt ze in batches van PLATFORM_FACES_BATCH door de echte worker (de opdracht
faces, zoals faces.scan). De eerste batch warmt op en telt niet mee. Uitkomst: ms per foto,
foto's per minuut, en de tijd voor wat er nu wacht. Op gemaakte foto's vindt YuNet geen gezicht:
de tijd per extra gezicht meet deze bench niet (de opdrachten van A0 hebben geen SFace los).

**analyse-kwaliteit**: de precisie van de voorstellen tegen de eigen "ja" en "nee" (Vraag 8,
standaard nee: dan meten we op de LXC; niets verlaat de LXC, alleen getallen). Per eigenaar met
gezichten aan, met wat faces.group ook leest (group._read):

- de gezichten met een ja in VOUWEN delen; elk deel om de beurt als open in group (suggest), met
  de rest als bevestigd. Een voorstel voor de persoon van de ja is goed, voor een ander fout;
  geen voorstel telt bij de dekking;
- de open gezichten met een nee, zonder hun afwijzingen: een voorstel voor de persoon die werd
  afgewezen, is fout (in productie houdt de afwijzing het tegen; hier meten we het model);
- de groepen (full) over alle gezichten met een ja, zonder hun personen: de zuiverheid (het
  grootste deel van één persoon per groep) en de recall (het grootste deel van een persoon in
  één groep), voor personen met minstens MIN_GROUP gezichten.

De poorten van X2 ("Harde poorten"): precisie ≥ 0,95, zuiverheid ≥ 0,9, recall ≥ 0,8.

De uitkomst (alleen getallen en een tijd) komt in de tabel setting (faces.bench,
faces.kwaliteit); /beheer toont hem (register.admin_block, FacesBench en AnalyseQuality). Draait
gezichten niet (state.running), dan doen beide niets: geen socket, geen rij, geen bestand.
Nooit een id, naam, kader of vector in de uitvoer.
"""

from __future__ import annotations

import asyncio
import io
import random
import sys
import time
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any

from PIL import Image, ImageDraw
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import face_rows, render
from app.analyse import client, wire
from app.config import Settings
from app.models import PhotoPref, Setting
from app.people.faces import drempels, group, state
from app.people.faces.version import FACES_VERSION

BENCH = "faces.bench"
QUALITY = "faces.kwaliteit"
FOTOS = 200
VOUWEN = 5
MIN_JA = 20  # minder gezichten met een ja: niets te meten
BUSY_WAIT_S = 120.0  # zo lang wacht de bench op een worker die bezig is (faces.scan)
POORT_PRECISIE = 0.95
POORT_ZUIVER = 0.9
POORT_RECALL = 0.8
W, H = 1024, 768


class NotMeasured(Exception):
    """Niets gemeten; de tekst zegt waarom (zonder ids of namen)."""


# --- de uitkomst in de tabel setting -------------------------------------------------------------


async def stored(db: AsyncSession, key: str) -> dict[str, Any] | None:
    row = await db.get(Setting, key, populate_existing=True)
    return dict(row.value) if row is not None and isinstance(row.value, dict) else None


async def _store(settings: Settings, key: str, value: dict[str, Any]) -> None:
    async with group._maker(settings)() as db:
        row = await db.get(Setting, key)
        if row is None:
            db.add(Setting(key=key, value=value))
        else:
            row.value = value
        await db.commit()


# --- analyse-bench -------------------------------------------------------------------------------


def _image(i: int) -> Image.Image:
    rng = random.Random(i)
    kind = i % 3
    if kind == 0:  # ruis
        return Image.merge("RGB", [Image.effect_noise((W, H), 40 + 10 * c) for c in range(3)])
    if kind == 1:  # een verloop
        ramp = Image.linear_gradient("L").resize((W, H))
        return Image.merge("RGB", (ramp, ramp.transpose(Image.Transpose.FLIP_LEFT_RIGHT), ramp))
    img = Image.new("RGB", (W, H), (120, 170, 230))  # een landschap
    draw = ImageDraw.Draw(img)
    draw.rectangle([0, H * 0.6, W, H], fill=(60, 140, 60))
    for _ in range(12):
        x, top = rng.uniform(0, W), rng.uniform(H * 0.25, H * 0.55)
        draw.polygon([(x - 120, H * 0.6), (x, top), (x + 120, H * 0.6)], fill=(90, 95, 110))
    return img


def _photos(n: int) -> list[bytes]:
    """De gemaakte foto's als WebP (12 verschillende, om de beurt)."""
    made: list[bytes] = []
    for i in range(min(n, 12)):
        buf = io.BytesIO()
        _image(i).save(buf, "WEBP", quality=render.WEBP_QUALITY)
        made.append(buf.getvalue())
    return [made[i % len(made)] for i in range(n)]


async def _faces(settings: Settings, batch: list[bytes]) -> int:
    """Eén batch door faces, zoals faces.scan; geeft het aantal gevonden gezichten."""
    fds = [wire.sealed_memfd("preview", data) for data in batch]
    out = wire.out_memfd("uit")
    args = wire.FacesArgs(
        min_px=settings.faces_min_px,
        max=min(settings.faces_max_per_photo, wire.FACES_MAX),
        score=drempels.SCORE,
    )
    try:
        waited = 0.0
        while True:
            try:
                answer = await client.call(settings, wire.Op.FACES, args, fds=[*fds, out])
                break
            except client.AnalyseBusy:
                if waited >= BUSY_WAIT_S:
                    raise NotMeasured(
                        "de worker is bezig (faces.scan): zet de analyse op pauze in /beheer"
                        " en probeer het opnieuw"
                    ) from None
                await asyncio.sleep(1.0)
                waited += 1.0
    finally:
        wire.close_all([*fds, out])
    data = wire.FacesData.model_validate(answer)
    if len(data.photos) != len(batch):
        raise NotMeasured("de worker gaf een antwoord dat niet klopt (bad_output)")
    return sum(p.n for p in data.photos)


async def bench(settings: Settings, photos: int = FOTOS) -> dict[str, Any]:
    if not state.running(settings):
        raise NotMeasured("gezichten staan uit (ANALYSE=uit of geen PLATFORM_FACES_KEYS)")
    size = max(1, settings.faces_batch)
    made = await asyncio.to_thread(_photos, photos + size)
    await _faces(settings, made[:size])  # opwarmen: het kind laadt de modellen
    found = 0
    start = time.perf_counter()
    for at in range(size, len(made), size):
        found += await _faces(settings, made[at : at + size])
    took = time.perf_counter() - start
    ms = took * 1000 / photos
    result = {
        "at": state.now().isoformat(),
        "version": FACES_VERSION,
        "photos": photos,
        "batch": size,
        "ms_per_photo": round(ms, 1),
        "photos_per_minute": round(60_000 / ms, 1),
        "faces": found,
    }
    await _store(settings, BENCH, result)
    return result


def eta_s(found: dict[str, Any] | None, waiting: int) -> int | None:
    """De geschatte tijd voor wat er wacht, uit de laatste bench."""
    if not found or not waiting or not found.get("photos_per_minute"):
        return None
    return round(waiting * 60 / float(found["photos_per_minute"]))


# --- analyse-kwaliteit ---------------------------------------------------------------------------


@dataclass
class Quality:
    owners: int = 0
    tested: int = 0  # gezichten met een ja, om de beurt open
    right: int = 0
    wrong: int = 0
    nee_tested: int = 0  # open gezichten met een nee
    nee_wrong: int = 0  # opnieuw de afgewezen persoon voorgesteld
    in_groups: int = 0  # gezichten met een ja in een groep (full)
    pure: int = 0  # daarvan van de grootste persoon in hun groep
    recall_faces: int = 0  # gezichten van personen met ≥ MIN_GROUP
    recalled: int = 0  # daarvan in de grootste groep van hun persoon

    def precision(self) -> float | None:
        made = self.right + self.wrong + self.nee_wrong
        return self.right / made if made else None

    def summary(self) -> dict[str, Any]:
        def ratio(a: int, b: int) -> float | None:
            return round(a / b, 4) if b else None

        precision = self.precision()
        purity = ratio(self.pure, self.in_groups)
        recall = ratio(self.recalled, self.recall_faces)
        gates = [
            precision is not None and precision >= POORT_PRECISIE,
            purity is not None and purity >= POORT_ZUIVER,
            recall is not None and recall >= POORT_RECALL,
        ]
        return {
            "at": state.now().isoformat(),
            "version": FACES_VERSION,
            "owners": self.owners,
            "tested": self.tested,
            "precision": round(precision, 4) if precision is not None else None,
            "coverage": ratio(self.right + self.wrong, self.tested),
            "nee_tested": self.nee_tested,
            "nee_wrong": self.nee_wrong,
            "purity": purity,
            "recall": recall,
            "ok": all(gates),
        }


async def _owners(settings: Settings) -> list[uuid.UUID]:
    async with group._maker(settings)() as db:
        if not await state.allowed(db):
            return []
        on = await db.scalars(select(PhotoPref.user_id).where(PhotoPref.faces == "aan"))
        return sorted(set(on))


async def _measure_owner(settings: Settings, owner: uuid.UUID, q: Quality) -> None:
    known, opened, persons, rejects = await group._read(settings, owner, None)
    if len(known) < MIN_JA:
        return
    q.owners += 1
    for fold in range(VOUWEN):
        train = [k for i, k in enumerate(known) if i % VOUWEN != fold]
        test = [k for i, k in enumerate(known) if i % VOUWEN == fold]
        index = {p: i for i, p in enumerate(sorted({p for _f, _v, p in train}))}
        back = {i: p for p, i in index.items()}
        result = await group.call(
            settings,
            "suggest",
            [(f, v, index[p]) for f, v, p in train],
            [(f, v) for f, v, _p in test],
            len(index),
            [],
        )
        for (_f, _v, truth), (s, _score, _g) in zip(test, result, strict=True):
            q.tested += 1
            if s >= 0:
                if back[s] == truth:
                    q.right += 1
                else:
                    q.wrong += 1
    # Open gezichten met een nee, zonder hun afwijzingen
    rejected: dict[int, set[int]] = defaultdict(set)
    for at, person in rejects:
        rejected[at - len(known)].add(person)
    nee = sorted(rejected)
    if nee:
        result = await group.call(
            settings, "suggest", known, [opened[i] for i in nee], len(persons), []
        )
        for i, (s, _score, _g) in zip(nee, result, strict=True):
            q.nee_tested += 1
            if s >= 0 and s in rejected[i]:
                q.nee_wrong += 1
    # De groepen over alle gezichten met een ja, zonder hun personen
    result = await group.call(settings, "full", [], [(f, v) for f, v, _p in known], 0, [])
    per_group: dict[int, Counter[int]] = defaultdict(Counter)
    per_person: dict[int, Counter[int]] = defaultdict(Counter)
    for (_f, _v, person), (_s, _score, g) in zip(known, result, strict=True):
        per_person[person][g] += 1
        if g >= 0:
            per_group[g][person] += 1
    for members in per_group.values():
        q.in_groups += sum(members.values())
        q.pure += max(members.values())
    for spread in per_person.values():
        total = sum(spread.values())
        if total < drempels.MIN_GROUP:
            continue
        q.recall_faces += total
        q.recalled += max((n for g, n in spread.items() if g >= 0), default=0)


async def quality(settings: Settings) -> dict[str, Any]:
    if not state.running(settings):
        raise NotMeasured("gezichten staan uit (ANALYSE=uit of geen PLATFORM_FACES_KEYS)")
    q = Quality()
    try:
        for owner in await _owners(settings):
            await _measure_owner(settings, owner, q)
    except client.AnalyseBusy:
        raise NotMeasured(
            "de worker is bezig: zet de analyse op pauze in /beheer en probeer het opnieuw"
        ) from None
    except group.BadOutput:
        raise NotMeasured("de worker gaf een antwoord dat niet klopt (bad_output)") from None
    if not q.owners:
        raise NotMeasured(
            f"te weinig om te meten: niemand met gezichten aan heeft {MIN_JA} gezichten met een ja"
        )
    result = q.summary()
    await _store(settings, QUALITY, result)
    return result


async def waiting(settings: Settings) -> int:
    async with group._maker(settings)() as db:
        return int((await face_rows.server_counts(db))["waiting"])


# --- de commando's (app/cli.py) ------------------------------------------------------------------


def _pct(value: float | None) -> str:
    return "-" if value is None else f"{value * 100:.1f}%"


def _duration(seconds: int | None) -> str:
    if seconds is None:
        return "-"
    hours, rest = divmod(seconds, 3600)
    return f"{hours} uur {rest // 60} min" if hours else f"{rest // 60} min {rest % 60} s"


async def _run_bench(settings: Settings, photos: int) -> int:
    found = await bench(settings, photos)
    left = await waiting(settings)
    print(
        f"analyse-bench: {found['photos']} gemaakte foto's in batches van {found['batch']}:"
        f" {found['ms_per_photo']} ms per foto, {found['photos_per_minute']} foto's per minuut"
    )
    if found["faces"]:
        print(f"gevonden gezichten: {found['faces']} (gemaakte foto's horen er geen te hebben)")
    else:
        print("per extra gezicht: niet gemeten (gemaakte foto's hebben geen gezicht)")
    print(f"wacht nu: {left} foto's, geschat {_duration(eta_s(found, left))}")
    return 0


async def _run_quality(settings: Settings) -> int:
    found = await quality(settings)
    print(
        f"analyse-kwaliteit: {found['owners']} eigenaar(s), {found['tested']} gezichten met een ja"
        f" om de beurt open ({VOUWEN} delen)"
    )
    print(
        f"precisie {_pct(found['precision'])} (poort {POORT_PRECISIE * 100:.0f}%), dekking"
        f" {_pct(found['coverage'])}; met een nee: {found['nee_wrong']} van"
        f" {found['nee_tested']} opnieuw de afgewezen persoon"
    )
    print(
        f"groepen: zuiverheid {_pct(found['purity'])} (poort {POORT_ZUIVER * 100:.0f}%),"
        f" recall {_pct(found['recall'])} (poort {POORT_RECALL * 100:.0f}%)"
    )
    print("poorten: gehaald" if found["ok"] else "poorten: niet gehaald")
    return 0 if found["ok"] else 3


def run(settings: Settings, command: str, photos: int = FOTOS) -> int:
    """analyse-bench (exitcode 0 met een uitkomst) en analyse-kwaliteit (0: de poorten gehaald,
    3: gemeten maar niet gehaald); 1 als er niets gemeten is (uit, de worker weg of bezig, te
    weinig ja). Alleen getallen op stdout; de reden op stderr, zonder ids of namen."""
    job = _run_bench(settings, photos) if command == "analyse-bench" else _run_quality(settings)
    try:
        return asyncio.run(job)
    except NotMeasured as exc:
        print(f"{command}: niets gemeten: {exc}", file=sys.stderr)
    except client.AnalyseDown:
        print(f"{command}: niets gemeten: de analyse is niet bereikbaar", file=sys.stderr)
    except client.AnalyseFailed:
        print(f"{command}: niets gemeten: de opdracht mislukte in de worker", file=sys.stderr)
    return 1
