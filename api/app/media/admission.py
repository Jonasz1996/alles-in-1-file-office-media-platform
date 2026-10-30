"""Toelating en het CPU-budget (Keuze 26). M4.

`view` zegt wat er nu past (voor decide), `reserve` legt een plan vast als Grant, `release`
geeft hem terug, `snapshot` is voor /beheer en de tests.

**Het budget** `B` = `MediaConfig.cpu_budget` (standaard 3,0: de cpu-grens van de worker op de
LXC; /beheer kan lager), min 1,0 zolang de office-monitor zegt dat DS start of zijn fonts maakt
(`office_state(app).health == "starting"`). Lopende streams lopen door; alleen nieuwe plannen
krijgen een lagere trede.

**De regel** (dezelfde als decide.admit, zodat een plan dat decide goedkeurde hier ook past):
`in gebruik + kosten(plan) + reserve ≤ 0,9 × B`, met `reserve = (vrije plaatsen − plaatsen van
het plan) × kosten(480 veryfast) × f`. Een publieke link telt zijn eigen plek mee in de
reserve en neemt nooit de laatste plek. Een plan boven 1,8 × f gebruikt 2 plaatsen (decide zet
`plan.places`).

**Grenzen:** `max_transcodes` plaatsen (het kleinste van /beheer, het plafond
`PLATFORM_MEDIA_MAX_TRANSCODES` en wat de worker zegt in caps), 2 transcodes per gebruiker
(`max_user_transcodes`), 1 per publieke link, en hooguit 6 remuxen (de plaatsen van de worker
voor remux, Keuze 12), waarvan hooguit REMUX_PER_USER per gebruiker (review golf 2, S3: eerst kon
één account alle 6 nemen). Een publieke link heeft voor remux geen eigen grens: hij heeft hooguit
2 sessies, en de derde sluit de oudste (sessions.py, §2 M3). Een grens hier weigerde de tweede
kijker en die derde (503 in plaats van de oudste dicht), en beschermt het totaal niet: één
account kan veel links maken (na-controle golf 2). Minder dan 10 GB vrij op `data_dir`: geen
nieuwe transcode en geen nieuwe remux (`reason: disk`, Keuze 31). Direct play kan altijd.

**Een wissel** (`replace` in play: een ander audiospoor, een lagere trede): de grant van de sessie
die play vervangt, telt niet mee (`replacing`). Die sessie gaat in dezelfde beurt dicht, zoals
`replace` ook de grens van de sessies niet raakt. Zonder dat kreeg wie zijn grens of de laatste
plaats had (een transcode via een link, 3 remuxen, de laatste live plaats), bij een wissel 503,
of een lagere trede omdat zijn eigen oude stroom nog meetelde (na-controle golf 2).

**De worker.** Een Grant reserveert ook de plaats in de worker (Keuze 12): de app laat nooit meer
transcodes toe dan de worker plaatsen heeft, en een producent start pas als de vorige van zijn
sessie weg is (streams.py). Daarbij telt het totaal van de worker (`max_procs`): de plaatsen
achtergrond en kort (1 + 2) kunnen elk moment bezet zijn, dus hooguit `max_procs − 3` live
opdrachten (transcode en remux samen; review golf 2, C5). Zo krijgt een goedgekeurd plan geen
`busy` van de worker. Een worker met `max_procs` ≤ 3 (het harnas en de belastingtest, regel 13)
kan die belofte niet doen: dan geldt deze grens niet, en kan een plan toch `busy` krijgen (de
speler probeert het na Retry-After opnieuw).

**Teruggeven** is idempotent per grant-id: elke grant telt één keer, ook als release twee keer
komt (sessions._release en een fout in play).

De staat staat op `MediaState.admission` van de app (één per proces). Zonder gebonden app (de
tests met ASGITransport draaien de lifespan niet) is dat de app uit app.main.
"""

from __future__ import annotations

import contextlib
import logging
import shutil
import uuid
from collections.abc import Iterator
from pathlib import Path

from app.config import MediaConfig
from app.media import ladder, runtime
from app.media.errors import MediaBusy
from app.media.state import MediaPolicy, MediaState, media_policy, media_state
from app.media.types import AdmissionView, BudgetView, Grant, Plan, Rung
from app.media.wire import WorkClass

log = logging.getLogger("platform.media")

FILL = 0.9  # hooguit 0,9 × B
OFFICE_REST = 1.0  # B − 1,0 zolang DS start
DISK_MIN = 10 * 1024**3  # bytes vrij op data_dir (Keuze 31)
REMUX_PLACES = 6  # de plaatsen van de worker voor remux (Keuze 12)
REMUX_PER_USER = 3  # de helft: één account neemt nooit alle plaatsen (review golf 2, S3)
# De plaatsen van de worker die niet live zijn (Keuze 12), als caps ze niet noemt
OTHER_PLACES = {WorkClass.BACKGROUND: 1, WorkClass.SHORT: 2}
RETRY = 30  # Retry-After bij cpu, user, link en disk: een transcode is niet zo weg
RETRY_WORKER = 2
RESERVE_RUNG = Rung(height=480, preset="veryfast", codec="h264")
LIVE = ("remux", "transcode")


class Admission:
    """Op MediaState.admission (M4): de grants die nu gelden."""

    def __init__(self) -> None:
        self.grants: dict[uuid.UUID, Grant] = {}
        # De grants van sessies die play nu vervangt (replacing): die tellen niet mee
        self.ignored: set[uuid.UUID] = set()

    def counted(self) -> list[Grant]:
        return [g for g in self.grants.values() if g.id not in self.ignored]

    def transcodes(self) -> list[Grant]:
        return [g for g in self.counted() if g.mode == "transcode"]

    def remuxes(self) -> list[Grant]:
        return [g for g in self.counted() if g.mode == "remux"]

    @property
    def in_use(self) -> float:
        return round(sum(g.cost for g in self.counted()), 4)

    @property
    def places_used(self) -> int:
        return sum(g.places for g in self.transcodes())


def _host():
    """De app: de gebonden (runtime.bind), anders die uit app.main (één per proces)."""
    bound = runtime.app()
    if bound is not None:
        return bound
    from app.main import app

    return app


def _state() -> MediaState:
    return media_state(_host())


def admission() -> Admission:
    """De Admission van deze app; maakt er een als de staat er nog geen heeft."""
    state = _state()
    if not isinstance(state.admission, Admission):
        state.admission = Admission()
    return state.admission


def config() -> MediaConfig:
    """De instellingen zoals ze gelden (MediaPolicy.effective); standaard tot ze gelezen zijn."""
    found = media_policy(_host()).config or MediaConfig()
    return MediaPolicy.effective(found, runtime.settings())


def _office_starting() -> bool:
    from app.office.state import office_state

    try:
        return office_state(_host()).health == "starting"
    except Exception:  # office hoort de toelating nooit te breken
        return False


def budget() -> float:
    """B: het budget van /beheer, min 1,0 zolang DS start (Keuze 26)."""
    b = config().cpu_budget
    if _office_starting():
        b -= OFFICE_REST
    return max(0.0, round(b, 4))


def live_max() -> int | None:
    """Hoeveel live opdrachten (transcode en remux) de worker hooguit tegelijk kan starten, ook
    als de achtergrond en de korte plaatsen vol zijn: max_procs − (achtergrond + kort). None als
    er dan niets overblijft (max_procs ≤ 3: het harnas en de belastingtest); dan geldt deze grens
    niet (review golf 2, C5)."""
    caps = _state().caps
    total = caps.max_procs if caps is not None else runtime.settings().media_max_procs
    places = caps.places if caps is not None else {}
    other = sum(places.get(wc.value, n) for wc, n in OTHER_PLACES.items())
    room = total - other
    return room if room > 0 else None


def max_transcodes() -> int:
    """Het kleinste van /beheer, het plafond uit de omgeving en de plaatsen van de worker."""
    limit = config().max_transcodes
    caps = _state().caps
    if caps is not None:
        limit = min(limit, caps.max_transcodes)
    return max(0, limit)


def factors() -> tuple[float, float]:
    """(f, f_dec) van de meting (Keuze 26); 1,0 tot er gemeten is, en altijd 1,0 met
    PLATFORM_MEDIA_BENCH_FIXED (de kostentabel van de proef)."""
    if runtime.settings().media_bench_fixed:
        return 1.0, 1.0
    bench = config().bench
    if bench is not None:
        return bench.f, bench.f_dec
    return _state().bench_factor, 1.0


def _existing(path: Path) -> Path:
    for candidate in (path, *path.parents):
        if candidate.exists():
            return candidate
    return Path.cwd()


def disk_free(path: Path | None = None) -> int:
    """Vrije bytes op data_dir (of het dichtstbijzijnde bestaande pad erboven)."""
    target = _existing(path if path is not None else runtime.settings().data_dir)
    try:
        return shutil.disk_usage(target).free
    except OSError:
        return DISK_MIN  # niet te meten: niet blokkeren


def disk_ok(path: Path | None = None) -> bool:
    return disk_free(path) >= DISK_MIN


def reserve_unit(f: float) -> float:
    """De kosten van 480 veryfast × f: de plaats die de reserve per vrije plaats vasthoudt."""
    return ladder.cost(RESERVE_RUNG) * f


def view(user_id: uuid.UUID, link_id: uuid.UUID | None) -> BudgetView:
    """Wat er nu past, voor decide (Keuze 26)."""
    adm = admission()
    cfg = config()
    f, f_dec = factors()
    transcodes = adm.transcodes()
    mine = [g for g in transcodes if g.user_id == user_id and g.link_id is None]
    if link_id is not None:
        mine = [g for g in transcodes if g.link_id == link_id]
    return BudgetView(
        budget=budget(),
        in_use=adm.in_use,
        places_free=max(0, max_transcodes() - adm.places_used),
        user_free=max(0, cfg.max_user_transcodes - len(mine)) if link_id is None else 1,
        link_free=max(0, 1 - len(mine)) if link_id is not None else None,
        factor=f,
        factor_dec=f_dec,
        disk_ok=disk_ok(),
        retry_after=RETRY,
    )


def fits(plan: Plan, budget_view: BudgetView, *, public: bool) -> bool:
    """Past dit plan in dit budget (de regel van Keuze 26, zoals decide.admit)?"""
    free = budget_view.places_free - plan.places
    if free < (1 if public else 0):
        return False
    unit = reserve_unit(budget_view.factor)
    reserve = (budget_view.places_free if public else free) * unit
    return budget_view.in_use + plan.cost + reserve <= FILL * budget_view.budget + 1e-9


def reserve(plan: Plan, user_id: uuid.UUID, link_id: uuid.UUID | None) -> Grant:
    """Het plan vastleggen; MediaBusy(reason, retry_after) als het niet (meer) past. Direct en
    audio kosten niets en tellen niet."""
    grant = Grant(
        user_id=user_id,
        link_id=link_id,
        mode=plan.mode,
        cost=round(plan.cost, 4) if plan.mode in LIVE else 0,
        places=plan.places if plan.mode == "transcode" else 0,
    )
    if plan.mode not in LIVE:
        return grant
    adm = admission()
    if not disk_ok():
        raise MediaBusy("disk", RETRY)
    room = live_max()
    if room is not None and len(adm.transcodes()) + len(adm.remuxes()) >= room:
        raise MediaBusy("worker", RETRY_WORKER)
    if plan.mode == "remux":
        remuxes = adm.remuxes()
        mine = sum(g.user_id == user_id and g.link_id is None for g in remuxes)
        if link_id is None and mine >= REMUX_PER_USER:
            raise MediaBusy("user", RETRY)
        if len(remuxes) >= REMUX_PLACES:
            raise MediaBusy("worker", RETRY_WORKER)
    else:
        current = view(user_id, link_id)
        if link_id is not None and (current.link_free or 0) <= 0:
            raise MediaBusy("link", RETRY)
        if link_id is None and current.user_free <= 0:
            raise MediaBusy("user", RETRY)
        if not fits(plan, current, public=link_id is not None):
            raise MediaBusy("cpu", RETRY)
    adm.grants[grant.id] = grant
    log.info("toelating: %s, %.2f cores, %d plaatsen", plan.mode, grant.cost, grant.places)
    return grant


@contextlib.contextmanager
def replacing(grant: Grant | None) -> Iterator[None]:
    """Play met `replace`: binnen dit blok telt de grant van de oude sessie niet mee voor view en
    reserve. Die sessie gaat in dezelfde beurt dicht (sessions.create) en geeft haar grant dan
    terug. Alleen om synchrone code: tussen view en reserve wacht play nergens op. Release
    binnen het blok werkt gewoon (de grant gaat dan echt weg)."""
    adm = admission()
    if grant is None or grant.id not in adm.grants:
        yield
        return
    adm.ignored.add(grant.id)
    try:
        yield
    finally:
        adm.ignored.discard(grant.id)


def release(grant: Grant) -> None:
    """De grant teruggeven. Idempotent: een tweede keer (of een grant die nooit vastlag, zoals
    direct play) doet niets."""
    found = admission().grants.pop(grant.id, None)
    if found is not None:
        log.info("toelating terug: %s, %.2f cores", found.mode, found.cost)


def snapshot() -> AdmissionView:
    """Voor /beheer en de tests: het budget, wat er in gebruik is, en de grants."""
    adm = admission()
    return AdmissionView(
        budget=budget(),
        in_use=adm.in_use,
        max_transcodes=max_transcodes(),
        places_used=adm.places_used,
        grants=sorted(adm.grants.values(), key=lambda g: (g.mode, str(g.id))),
    )
