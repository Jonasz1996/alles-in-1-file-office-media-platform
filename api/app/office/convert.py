"""PDF via de conversie van de document server (Keuze 25 en 26): de taak office.pdf, de
inhaalronde office.pdf.backfill, de haken van O0 en de conversie op verzoek.

De staat staat in blob.pdf_state (NULL | pending | ready | failed | none | unsafe | ask) en
blob.pdf_version (bij ready de PDF_VERSION van de PDF, bij failed en none die van de laatste
poging); de PDF zelf in data_dir/office/pdf/<sha[:2]>/<sha>.pdf, los van
previews/<sha>/ (M10): een nieuwe preview gooit hem dus niet weg.

Haken van O0:
- on_preview_done(session, blob): in previews._store, onder blobs.lock en vóór de commit.
  Alleen met office aan en voor een soort die DS omzet. Een blob met ask, unsafe of none blijft
  zo, en failed ook (behalve na een hogere PDF_VERSION). Is de PDF er al (ready), dan een taak
  die alleen de thumbnails opnieuw maakt (_store gooide ze net weg). Anders: groter dan
  office_auto_convert_bytes wordt ask; een eerdere versie (inhoud zonder bestand) wacht op
  een verzoek; de rest krijgt een taak office.pdf (key office.pdf:<sha>) en pending.
- on_discard(blobs, sha): in previews.discard, onder blobs.lock; haalt de PDF weg.
- on_healthy(): in health.py bij "weer gezond" (ook de eerste keer na de start): de
  inhaalronde.

De taak office.pdf (payload {sha, n, ask?, r?, b?}, max_attempts 2, on_dead -> failed), in
deze volgorde:
1. de blob is weg: klaar;
2. de PDF er al, met de juiste pdf_version: alleen de thumbnails opnieuw (zonder DS);
3. office uit (niet ingesteld, of uitgezet op /beheer): pdf_state terug (leeg, of ask);
4. DS niet gezond: pdf_state terug en klaar, zonder poging; de inhaalronde neemt hem terug.
   Mislukte er al een poging (DS ging tijdens een conversie onderuit en is nog niet terug),
   dan wacht de tweede poging zolang DS start of korter dan 10 minuten weg is (een herstart
   of update van DS), elke 30 s zonder poging; daarna telt dit wel. Zo krijgt een bestand dat
   DS laat vastlopen hooguit twee kansen, en kost een herstart van DS geen document;
5. een sessie die opent of opslaat (sessions.busy): de taak over 5 s opnieuw in de rij. Loopt
   er al een conversie (één slot in dit proces): over 60 s, maar wie het slot vrijgeeft, zet
   de eerste die wacht meteen klaar (_next). Beide zonder poging; een worker wacht nooit;
6. het slot: de invoercontrole (Keuze 23) weigert: unsafe, klaar. Zo kost wachten op het slot
   geen controle, alleen het omzetten. Kon de controle het bestand nu niet bekijken (time of
   io: de machine, niet de inhoud), dan een gewone poging (JobError), niet unsafe: na twee
   failed, en de knop Mislukte opnieuw zet hem terug;
7. omzetten: een grant, engine.convert, de PDF naar data_dir/office/.tmp, controle op %PDF-
   (de grootte begrenst het ophalen), dan onder blobs.lock op zijn plek als de blob er nog is,
   previews/<sha>/pages/ leeg, ready met de nieuwe pdf_version. Dan het slot vrij;
8. de thumbnails 256 en 1024 uit pagina 1 in het renderproces; ze vervangen de webp's,
   preview_meta.sizes erbij; previews.announce. Een doc, xls, ppt of rtf (zonder preview
   van B21) krijgt zo zijn eerste preview.

Wachten zonder poging gaat niet via jobs.chain maar door de eigen rij terug in de rij te
zetten (status queued, attempts - 1): een nieuwe taak zou weer bij 0 pogingen beginnen, en dan
kreeg een bestand meer dan twee kansen. jobs.execute zet daarna niets meer op done, want het
zet alleen een rij die nog running is.

Wie op het slot wacht (_waiting, alleen in dit proces), is aan de beurt in deze volgorde: een
conversie op verzoek (request, het kwartier daarna), dan een upload, dan de inhaalronde
(payload b); elk op volgorde van aankomst. Na een herstart weet het proces dat niet meer; dan
komt elke taak na hooguit 60 s gewoon terug.

De fouten van DS (oo/convert.py): new_key (-2, -4) -> een nieuwe taak met n + 1 over 30 s
(jobs.chain, met dezelfde ask en r), hooguit drie nieuwe keys; failed -> failed; none -> none;
retry -> een gewone poging (JobError), en antwoordde DS niet (code 2), dan eerst meteen de
controle van health.py: zo verliest wat op het slot wacht niet elk een poging tegen een DS die
weg is; misconfigured (-8) -> pdf_state terug, en meteen de grote controle van health.py (die
zet misconfigured; de melding staat dan op /beheer). Blijvend mislukt (failed) staat ook als
mislukte taak op /beheer, met de code in de melding: "(code -3)".

De inhaalronde (payload {after}): in stukken van 200 blobs op volgorde van sha, alleen inhoud
van een bestand (geen eerdere versie) waarvan de preview klaar is. Hij neemt NULL (groter dan
office_auto_convert_bytes: ask), en bij een hogere PDF_VERSION ook ready en failed; nooit
ask, unsafe of none. Elk stuk krijgt run_after +60 s, en het volgende stuk ook. Staan er nog
200 of meer conversies klaar, dan komt er geen nieuw stuk bij: dezelfde plek over 60 s opnieuw.
Zo staat nooit de hele bibliotheek in de rij, en gaan nieuwe uploads voor (ook bij het slot).
Met DS niet gezond stopt de ronde; on_healthy start hem opnieuw.

Mislukte opnieuw (retry_failed, de knop op /beheer): elke failed meteen een eigen taak met r (8
hex-tekens), en pending. r komt in de key (keys.convert_key): of DS een fout als -3 of -1
onder de key bewaart, is niet gemeten (-4 en -10 niet, gemeten), en met een nieuwe key maakt
dat niet uit. Kan zo'n taak niet omzetten zonder poging
(office uit, DS niet gezond, -8), dan gaat de inhoud terug naar failed, niet naar leeg: de
inhaalronde zou dan de oude key nemen. Niet via de inhaalronde: loopt er al een, dan komt die
niet meer langs een sha vóór zijn plek.

Op verzoek (request): POST /api/v1/office/files/{id}/convert en /pdf, alleen ingelogd met
leesrecht. Per gebruiker hooguit 10 per 10 minuten (429 rate_limited); hooguit 100 MB voor
zip-formaten en office_ole_max_bytes voor doc, xls, ppt en rtf (413 office_too_large). De
taak krijgt een run_after in het verleden, en staat dus vooraan; moet hij toch op het slot
wachten, dan is hij de eerste die het daarna krijgt. Ook voor een eerdere versie.
"""

import asyncio
import itertools
import logging
import os
import re
import secrets
import shutil
import tempfile
import time
from datetime import timedelta
from pathlib import Path
from typing import Any, Literal

import anyio
import anyio.to_thread
from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import errors, jobs, previews
from app import mime as mimes
from app.config import Settings
from app.db import get_engine
from app.models import Blob, Job, Node, User
from app.office import engine, grants, inspect, keys, runtime, sessions, urls
from app.office.state import office_policy, office_state
from app.security import RateLimiter
from app.storage import BlobStore

log = logging.getLogger("platform.office")

TYPE = "office.pdf"
BACKFILL = "office.pdf.backfill"
BACKFILL_KEY = "office.pdf.backfill"
MAX_ATTEMPTS = 2
BATCH = 200
BACKFILL_DELAY = 60.0  # zoals previews.RERENDER_DELAY
WAIT_SECONDS = 5.0  # een sessie die opent of opslaat
# Het slot is bezet: wie het vrijgeeft, zet de volgende meteen klaar (_next); dit is de terugval
SLOT_SECONDS = 60.0
# Zo lang (s) gaat een conversie op verzoek voor bij het slot (langer dan de viewer kijkt)
ASKED_SECONDS = 900.0
NEW_KEY_SECONDS = 30.0  # na -2 of -4
# Na een mislukte poging is DS weg: zo lang (s) wacht de tweede poging tot hij terug is (een
# herstart of update duurt 2 tot 5 minuten), en zo vaak kijkt hij
OUTAGE_SECONDS = 600.0
OUTAGE_WAIT = 30.0
NO_ANSWER = 2  # de code van oo/convert.py als DS niet (bruikbaar) antwoordde
MAX_KEY = 4  # de eerste key en hooguit drie nieuwe (-a2 tot en met -a4)
# Zo groot mag een zip-formaat op verzoek zijn: wat DS zelf nog ophaalt (maxDownloadBytes)
ZIP_MAX_BYTES = 104_857_600
OLE = frozenset({"doc", "xls", "ppt", "rtf"})
# POST /pdf wacht zo lang (s) op een PDF die nog gemaakt wordt; daarna 409 pdf_pending
PDF_WAIT = 60.0
PDF_POLL = 0.5
TITLE = "document"  # DS krijgt nooit de naam van het bestand
SHA = re.compile(r"[0-9a-f]{64}")
RETRY = re.compile(r"[0-9a-f]{8}")  # de r van Mislukte opnieuw
CODE = re.compile(r"\(code (-?\d+)\)")
# Per gebruiker, conversies op verzoek (Keuze 25)
ASKS = RateLimiter(
    10,
    600,
    {
        "message": "Je vroeg al veel voorbeelden met opmaak; probeer het over een paar minuten "
        "opnieuw",
        "code": "rate_limited",
    },
)

EXT_BY_MIME = {
    mimes.DOCX: "docx",
    mimes.XLSX: "xlsx",
    mimes.PPTX: "pptx",
    mimes.ODT: "odt",
    mimes.ODS: "ods",
    mimes.ODP: "odp",
    mimes.ZIP_BY_EXT["docm"]: "docm",
    mimes.ZIP_BY_EXT["xlsm"]: "xlsm",
    mimes.ZIP_BY_EXT["pptm"]: "pptm",
    mimes.OLE_BY_EXT["doc"]: "doc",
    mimes.OLE_BY_EXT["xls"]: "xls",
    mimes.OLE_BY_EXT["ppt"]: "ppt",
    "application/rtf": "rtf",
    "text/rtf": "rtf",
}

UNAVAILABLE = "De editor is nu niet bereikbaar; probeer het zo opnieuw"
OFF = "Bewerken van documenten staat op deze server niet aan"

# De conversie die nu loopt (hooguit één per proces; DS heeft er één plek voor, M14)
_converting: set[str] = set()
# Wie op het slot wacht: sha -> (volgnummer van aankomst, uit de inhaalronde)
_waiting: dict[str, tuple[int, bool]] = {}
_arrivals = itertools.count()
# Conversies op verzoek: sha -> tot wanneer (time.monotonic) hij voorgaat
_asked: dict[str, float] = {}
# Taken van on_healthy, zodat ze niet halverwege worden opgeruimd
_tasks: set[asyncio.Task] = set()


def _maker(settings: Settings) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(get_engine(settings), expire_on_commit=False)


def job_key(sha: str) -> str:
    return f"{TYPE}:{sha}"


def ext_of(mime: str | None) -> str | None:
    """De soort voor DS volgens het mime-type van de inhoud (mime.py keek naar de bytes)."""
    return EXT_BY_MIME.get((mime or "").lower())


def office_dir(blobs: BlobStore) -> Path:
    """<data_dir>/office, naast blobs/ en previews/."""
    return blobs.root.parent / "office"


def pdf_path(blobs: BlobStore, sha: str) -> Path:
    return office_dir(blobs) / "pdf" / sha[:2] / f"{sha}.pdf"


async def clean_tmp(settings: Settings) -> None:
    """Bij het starten, vóór de workers: de tussenbestanden van een vorige keer weg. Een kill
    (OOM, de stroom weg) midden in een conversie liet de PDF in office/.tmp staan, en midden in
    het ophalen van een opgeslagen document (saving, recovery) of een upload in blobs/.tmp. Zo
    vroeg gebruikt niets ze nog."""
    blobs = BlobStore(settings.blob_dir)
    for tmp in (office_dir(blobs) / ".tmp", blobs.tmp):
        await anyio.to_thread.run_sync(shutil.rmtree, tmp, True)


async def office_pdf(settings: Settings, blob: Blob | None) -> tuple[Path, int] | None:
    """(pad, pdf_version) van de omgezette PDF van deze inhoud, of None als die er niet is.
    Hangt niet af van DS: een PDF die er is, blijft te zien."""
    if blob is None or blob.pdf_state != "ready" or not blob.pdf_version:
        return None
    path = pdf_path(BlobStore(settings.blob_dir), blob.sha256)
    if not await anyio.Path(path).is_file():
        return None
    return path, blob.pdf_version


def shown_state(blob: Blob | None, ready: bool, version: bool) -> str | None:
    """pdf_state zoals de viewer het ziet. ready: de PDF is er (office_pdf). version: dit is
    een eerdere versie van het bestand; die wordt nooit vanzelf omgezet, dus zonder staat is
    het ask. Zonder office (of voor een soort die DS niet omzet) None, precies fase 3."""
    if ready:
        return "ready"
    office = engine.current()
    if office is None or blob is None or ext_of(blob.mime) not in office.convert_formats:
        return None
    state = blob.pdf_state
    if state == "ready":
        return None  # de PDF is weg (opgeruimd of nooit neergezet)
    if state is None and version:
        return "ask"
    return state


def _healthy() -> bool:
    app = runtime.app()
    return app is not None and office_state(app).healthy


def _restarting() -> bool:
    """Is DS even weg: aan het starten, of korter dan OUTAGE_SECONDS onbereikbaar?"""
    app = runtime.app()
    if app is None:
        return False
    state = office_state(app)
    if state.health == "starting":
        return True
    return (
        state.health == "down"
        and state.down_since is not None
        and jobs.now() - state.down_since < timedelta(seconds=OUTAGE_SECONDS)
    )


async def _enabled(session: AsyncSession) -> bool:
    """Staat office aan op /beheer (OfficeConfig.enabled)? Zonder gebonden app: de standaard."""
    app = runtime.app()
    if app is None:
        return True
    return (await office_policy(app).get(session)).enabled


async def _is_file_content(session: AsyncSession, sha: str) -> bool:
    """Is dit de inhoud van een bestand (en niet alleen van een eerdere versie)?"""
    found = await session.scalar(select(Node.id).where(Node.blob_id == sha).limit(1))
    return found is not None


# --- de haken van O0 -----------------------------------------------------------------------


async def on_preview_done(session: AsyncSession, blob: Blob) -> None:
    """Na een nieuwe preview (onder blobs.lock, vóór de commit): ask, of een taak."""
    office = engine.current()
    if office is None or ext_of(blob.mime) not in office.convert_formats:
        return
    state = blob.pdf_state
    if state in ("ask", "unsafe", "none"):
        return
    # failed: alleen opnieuw na een hogere PDF_VERSION dan die van de mislukte poging
    if state == "failed" and (blob.pdf_version or keys.PDF_VERSION) >= keys.PDF_VERSION:
        return
    if state in (None, "failed"):
        if not await _is_file_content(session, blob.sha256):
            return  # een eerdere versie: alleen op verzoek
        if blob.size > runtime.settings().office_auto_convert_bytes:
            blob.pdf_state = "ask"
            return
        if not await _enabled(session):
            return
        blob.pdf_state = "pending"
    # ready: _store gooide de thumbnails van de PDF net weg; de taak maakt ze opnieuw (en bij
    # een hogere PDF_VERSION ook de PDF). pending: de taak loopt al (dan geen tweede)
    await jobs.enqueue(session, TYPE, {"sha": blob.sha256, "n": 1}, key=job_key(blob.sha256))


async def on_discard(blobs: BlobStore, sha: str) -> None:
    """De inhoud is opgeruimd (onder blobs.lock): de PDF ook weg."""
    await anyio.Path(pdf_path(blobs, sha)).unlink(missing_ok=True)


def on_healthy() -> None:
    """DS is (weer) gezond: de inhaalronde starten, als taak naast de gezondheidslus."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    task = loop.create_task(_start_backfill_logged(), name="office inhaalronde")
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def _start_backfill_logged() -> None:
    try:
        await start_backfill(runtime.settings())
    except Exception:  # noqa: BLE001 - een haak mag de gezondheidslus nooit raken
        log.exception("office: de inhaalronde starten mislukte")


async def start_backfill(settings: Settings) -> bool:
    """Zet de inhaalronde klaar (als die niet al loopt). False zonder office."""
    if engine.current() is None:
        return False
    async with _maker(settings)() as session:
        added = await jobs.enqueue(session, BACKFILL, {"after": ""}, key=BACKFILL_KEY)
        await session.commit()
    return added is not None


# --- de rij bijwerken ----------------------------------------------------------------------


async def _set_state(
    maker: async_sessionmaker[AsyncSession],
    sha: str,
    state: str | None,
    only: tuple[str, ...] | None = None,
    **values: Any,
) -> None:
    async with maker() as session:
        query = update(Blob).where(Blob.sha256 == sha)
        if only is not None:
            query = query.where(Blob.pdf_state.in_(only))
        await session.execute(
            query.values(pdf_state=state, **values).execution_options(synchronize_session=False)
        )
        await session.commit()


async def _release(
    maker: async_sessionmaker[AsyncSession], sha: str, asked: bool, r: str = ""
) -> None:
    """Niet omgezet en geen poging: pending terug naar leeg (of ask, als erom gevraagd was). Een
    taak van Mislukte opnieuw (r) terug naar failed: leeg nam de inhaalronde hem met de oude key,
    en die geeft bij DS de oude fout."""
    state = "failed" if r else "ask" if asked else None
    await _set_state(maker, sha, state, only=("pending",))


async def _own_job(maker: async_sessionmaker[AsyncSession], sha: str, **values: Any) -> None:
    """De lopende taak van deze inhoud bijwerken (wachten of blijvend mislukt)."""
    async with maker() as session:
        await session.execute(
            update(Job)
            .where(Job.type == TYPE, Job.key == job_key(sha), Job.status == "running")
            .values(**values)
            .execution_options(synchronize_session=False)
        )
        await session.commit()


async def _postpone(maker: async_sessionmaker[AsyncSession], sha: str, seconds: float) -> None:
    """Over seconds opnieuw, zonder dat deze poging telt."""
    await _own_job(
        maker,
        sha,
        status="queued",
        started_at=None,
        attempts=Job.attempts - 1,
        run_after=jobs.now() + timedelta(seconds=seconds),
    )


def _rank(sha: str) -> tuple[int, int]:
    """De beurt bij het slot: op verzoek, dan een upload, dan de inhaalronde; dan aankomst."""
    arrival, backfill = _waiting[sha]
    asked = _asked.get(sha, 0.0) > time.monotonic()
    return (0 if asked else 2 if backfill else 1), arrival


async def _next(maker: async_sessionmaker[AsyncSession]) -> None:
    """Het slot is vrij: de eerste die erop wacht, is meteen aan de beurt (run_after in het
    verleden, de workers gewekt), niet pas na SLOT_SECONDS. Lukt dat niet, dan blijft het bij
    die SLOT_SECONDS."""
    now = time.monotonic()
    for sha in [sha for sha, until in _asked.items() if until <= now]:
        del _asked[sha]
    try:
        while _waiting:
            sha = min(_waiting, key=_rank)
            async with maker() as session:
                result = await session.execute(
                    update(Job)
                    .where(Job.type == TYPE, Job.key == job_key(sha), Job.status == "queued")
                    .values(run_after=jobs.now() - timedelta(hours=1))
                    .execution_options(synchronize_session=False)
                )
                await session.commit()
            if result.rowcount:
                jobs.kick()
                return
            _waiting.pop(sha, None)  # wacht niet meer (klaar, of loopt al)
    except Exception:  # noqa: BLE001 - de terugval van SLOT_SECONDS blijft
        log.warning("office: de volgende conversie klaarzetten mislukte", exc_info=True)


async def _attempts(maker: async_sessionmaker[AsyncSession], sha: str) -> int:
    async with maker() as session:
        found = await session.scalar(
            select(Job.attempts).where(
                Job.type == TYPE, Job.key == job_key(sha), Job.status == "running"
            )
        )
    return int(found or 0)


def _failed_message(code: int) -> str:
    return f"Omzetten naar PDF mislukt (code {code})"


async def _give_up(
    maker: async_sessionmaker[AsyncSession], sha: str, state: str, code: int
) -> None:
    """Blijvend: pdf_state failed of none (met de PDF_VERSION van deze poging), en de taak op
    failed met de code (voor /beheer)."""
    await _set_state(
        maker,
        sha,
        state,
        only=("pending", "ready", "failed", "ask"),
        pdf_version=keys.PDF_VERSION,
    )
    await _own_job(maker, sha, status="failed", finished_at=jobs.now(), error=_failed_message(code))
    log.warning("office: %s voor %s", _failed_message(code), sha[:12])


async def last_code(session: AsyncSession, sha: str) -> int:
    """De code van de laatste mislukte conversie van deze inhoud (0 als die onbekend is)."""
    error = await session.scalar(
        select(Job.error)
        .where(Job.type == TYPE, Job.key == job_key(sha), Job.status == "failed")
        .order_by(Job.finished_at.desc())
        .limit(1)
    )
    found = CODE.search(error or "")
    return int(found.group(1)) if found else 0


# --- de taak --------------------------------------------------------------------------------


async def run(settings: Settings, payload: dict[str, Any]) -> None:
    """De taak office.pdf: zie boven."""
    sha = str(payload.get("sha", ""))
    if not SHA.fullmatch(sha):
        raise jobs.JobError("Ongeldige taak: geen inhoud opgegeven")
    try:
        n = int(payload.get("n") or 1)
    except (TypeError, ValueError) as exc:
        raise jobs.JobError("Ongeldige taak: n is geen getal") from exc
    n = min(max(n, 1), MAX_KEY)
    asked = bool(payload.get("ask"))
    r = str(payload.get("r") or "")
    r = r if RETRY.fullmatch(r) else ""
    blobs = BlobStore(settings.blob_dir)
    maker = _maker(settings)
    office = engine.current()
    place = _waiting.pop(sha, None)  # wacht hij straks weer op het slot, dan op dezelfde plek
    async with maker() as session:
        blob = await session.get(Blob, sha)
        if blob is None:
            return  # intussen opgeruimd
        ext = ext_of(blob.mime)
        state, version = blob.pdf_state, blob.pdf_version or 0
        enabled = office is not None and await _enabled(session)
    if (
        state == "ready"
        and version >= keys.PDF_VERSION
        and await anyio.Path(pdf_path(blobs, sha)).is_file()
    ):
        await thumbnails(settings, sha)
        return
    if office is None or not enabled or ext is None or ext not in office.convert_formats:
        await _release(maker, sha, asked, r)
        return
    if not _healthy():
        if await _attempts(maker, sha) > 1:
            if _restarting():  # de tweede kans pas als DS terug is
                await _postpone(maker, sha, OUTAGE_WAIT)
                return
            raise jobs.JobError(
                f"{_failed_message(NO_ANSWER)}; de document server is niet gezond na een "
                "mislukte poging"
            )
        await _release(maker, sha, asked, r)
        return
    async with maker() as session:
        busy = await sessions.busy(session)
    if busy or _converting:
        _waiting[sha] = place or (next(_arrivals), bool(payload.get("b")))
        await _postpone(maker, sha, WAIT_SECONDS if busy else SLOT_SECONDS)
        return
    _converting.add(sha)  # geen await sinds de controle hierboven: dit is het slot
    try:
        verdict = await inspect.check(settings, blobs.path(sha), ext, sha)
        if verdict.reason in inspect.TRANSIENT:  # de machine, niet het bestand: een poging
            raise jobs.JobError(
                f"De invoercontrole kon het bestand nu niet bekijken ({verdict.reason})"
            )
        if not verdict.ok:
            await _set_state(maker, sha, "unsafe")
            log.info("office: %s gaat niet naar DS (%s)", sha[:12], verdict.reason)
            return
        converted = await _convert(settings, office, sha, ext, n, asked, r)
    finally:
        _converting.discard(sha)
        with anyio.CancelScope(shield=True), anyio.move_on_after(10):
            await _next(maker)
    if converted:
        await thumbnails(settings, sha)


async def _convert(
    settings: Settings, office: engine.Engine, sha: str, ext: str, n: int, asked: bool, r: str
) -> bool:
    """Omzetten en de PDF neerzetten; True als hij er nu staat."""
    blobs = BlobStore(settings.blob_dir)
    maker = _maker(settings)
    key = keys.convert_key(sha, n, r)
    grant_id = grants.grant(sha)
    src = urls.internal_url(settings, f"/internal/office/src/{grant_id}")
    tmp = office_dir(blobs) / ".tmp"
    await anyio.Path(tmp).mkdir(parents=True, exist_ok=True)
    work = Path(await anyio.to_thread.run_sync(lambda: tempfile.mkdtemp(dir=tmp)))
    out = work / "out.pdf"
    try:
        try:
            await office.convert(
                src, ext, key, f"{TITLE}.{ext}", out, settings.office_max_pdf_bytes
            )
        except engine.ConvertError as e:
            await _convert_error(settings, maker, sha, n, asked, r, e)
            return False

        def head() -> bytes:
            with open(out, "rb") as f:
                return f.read(5)

        if await anyio.to_thread.run_sync(head) != b"%PDF-":
            await _give_up(maker, sha, "failed", 4)
            return False
        return await _store(maker, blobs, sha, out)
    finally:
        with anyio.CancelScope(shield=True):
            await anyio.to_thread.run_sync(shutil.rmtree, work, True)


async def _convert_error(
    settings: Settings,
    maker: async_sessionmaker[AsyncSession],
    sha: str,
    n: int,
    asked: bool,
    r: str,
    error: engine.ConvertError,
) -> None:
    if error.kind == "retry":
        if error.code == NO_ANSWER:
            await _recheck()  # is DS weg, dan weet de rij dat nu, niet pas bij de volgende ronde
        raise jobs.JobError(f"{_failed_message(error.code)}; later opnieuw") from error
    if error.kind == "none":
        await _give_up(maker, sha, "none", error.code)
    elif error.kind == "failed" or (error.kind == "new_key" and n >= MAX_KEY):
        await _give_up(maker, sha, "failed", error.code)
    elif error.kind == "new_key":
        async with maker() as session:
            await jobs.chain(
                session,
                TYPE,
                {
                    "sha": sha,
                    "n": n + 1,
                    **({"ask": True} if asked else {}),
                    **({"r": r} if r else {}),
                },
                job_key(sha),
                run_after=jobs.now() + timedelta(seconds=NEW_KEY_SECONDS),
            )
            await session.commit()
    else:  # misconfigured: geen poging; health.py kijkt meteen opnieuw (en zet de melding)
        await _release(maker, sha, asked, r)
        await _recheck()


async def _recheck() -> None:
    """Meteen de controle van health.py (na -8 of als DS niet antwoordde)."""
    app = runtime.app()
    if app is None:
        return
    from app.office import health  # hier: health.py importeert deze module

    office_state(app).deep_checked_at = None
    try:
        await health.check_once(app)
    except Exception:  # noqa: BLE001 - de lus van health.py kijkt straks gewoon weer
        log.warning("office: de gezondheid opnieuw controleren mislukte", exc_info=True)


async def _store(
    maker: async_sessionmaker[AsyncSession], blobs: BlobStore, sha: str, tmp: Path
) -> bool:
    """De PDF op zijn plek onder blobs.lock, alleen als de blob er nog is; de gerenderde
    pagina's van een vorige PDF weg."""
    target = pdf_path(blobs, sha)
    pages = previews.directory(blobs, sha) / "pages"
    async with blobs.lock:
        async with maker() as session:
            blob = await session.get(Blob, sha)
            if blob is None:
                return False

            def move() -> None:
                target.parent.mkdir(parents=True, exist_ok=True)
                os.replace(tmp, target)
                shutil.rmtree(pages, ignore_errors=True)

            await anyio.to_thread.run_sync(move)
            blob.pdf_state = "ready"
            blob.pdf_version = keys.PDF_VERSION
            await session.commit()
    return True


async def thumbnails(settings: Settings, sha: str) -> bool:
    """De thumbnails 256 en 1024 uit pagina 1 van de PDF (het renderproces, de pdf-tak). Een
    fout hier laat de PDF staan: dan blijven de thumbnails van B21."""
    blobs = BlobStore(settings.blob_dir)
    maker = _maker(settings)
    source = pdf_path(blobs, sha)
    tmp = previews.root(blobs) / ".tmp"
    await anyio.Path(tmp).mkdir(parents=True, exist_ok=True)
    work = Path(await anyio.to_thread.run_sync(lambda: tempfile.mkdtemp(dir=tmp)))
    try:
        out, scratch = work / "out", work / "work"
        await anyio.Path(out).mkdir()
        await anyio.Path(scratch).mkdir()
        request = {
            "source": str(source),
            "pdf": str(source),
            "out": str(out),
            "work": str(scratch),
            "mime": "application/pdf",
            "name": "",
            "max_pixels": settings.preview_max_pixels,
            "memory_mb": settings.preview_memory_mb,
        }
        try:
            result = await previews.render(settings, request, scratch)
        except Exception:  # noqa: BLE001 - de PDF zelf is goed
            log.warning("office: thumbnails van de PDF van %s mislukt", sha[:12], exc_info=True)
            return False
        sizes = sorted(
            s
            for s in result.get("sizes") or []
            if s in (previews.THUMB, previews.LARGE) and (out / f"{s}.webp").is_file()
        )
        if result.get("state") != "ready" or not sizes:
            return False
        pages = (result.get("meta") or {}).get("pages")
        stored = await _place_thumbnails(maker, blobs, sha, out, sizes, pages)
    finally:
        with anyio.CancelScope(shield=True):
            await anyio.to_thread.run_sync(shutil.rmtree, work, True)
    if stored:
        await previews.announce(maker, sha)
    return stored


async def _place_thumbnails(
    maker: async_sessionmaker[AsyncSession],
    blobs: BlobStore,
    sha: str,
    out: Path,
    sizes: list[int],
    pages: Any,
) -> bool:
    target = previews.directory(blobs, sha)
    async with blobs.lock:
        async with maker() as session:
            blob = await session.get(Blob, sha)
            if blob is None or blob.pdf_state != "ready":
                return False

            def move() -> None:
                target.mkdir(parents=True, exist_ok=True)
                for size in sizes:
                    (out / f"{size}.webp").replace(target / f"{size}.webp")

            await anyio.to_thread.run_sync(move)
            meta = dict(blob.preview_meta or {})
            meta["sizes"] = sorted({*meta.get("sizes", []), *sizes})
            if isinstance(pages, int) and pages > 0:
                meta["pages"] = pages
            blob.preview_meta = meta
            if blob.preview in ("none", "failed"):
                blob.preview = "ready"  # een doc, xls, ppt of rtf: nu wel een preview
            await session.commit()
    return True


async def on_dead(settings: Settings, payload: dict[str, Any], _error: str) -> None:
    """Twee keer mislukt: failed. Komt alleen terug met een hogere PDF_VERSION of de knop op
    /beheer."""
    sha = str(payload.get("sha", ""))
    if not SHA.fullmatch(sha):
        return
    maker = _maker(settings)
    async with maker() as session:
        blob = await session.get(Blob, sha)
        if blob is None:
            return
        if blob.pdf_state == "ready" and (blob.pdf_version or 0) >= keys.PDF_VERSION:
            return  # de PDF is er (de thumbnails mislukten); die blijft
    await _set_state(
        maker, sha, "failed", only=("pending", "ready", "ask"), pdf_version=keys.PDF_VERSION
    )
    log.warning("office: omzetten naar PDF van %s definitief mislukt", sha[:12])


# --- de inhaalronde -------------------------------------------------------------------------


async def backfill(settings: Settings, payload: dict[str, Any]) -> None:
    """Eén stuk van de inhaalronde: zie boven."""
    after = str(payload.get("after") or "")
    office = engine.current()
    if office is None or not _healthy():
        return
    mimes_wanted = [m for m, ext in EXT_BY_MIME.items() if ext in office.convert_formats]
    async with _maker(settings)() as session:
        if not await _enabled(session):
            return
        at = jobs.now() + timedelta(seconds=BACKFILL_DELAY)
        queued = await session.scalar(
            select(func.count()).where(Job.type == TYPE, Job.status == "queued")
        )
        if (queued or 0) >= BATCH:  # de rij is nog lang genoeg: dit stuk straks opnieuw
            await jobs.chain(session, BACKFILL, {"after": after}, BACKFILL_KEY, run_after=at)
            await session.commit()
            return
        is_content = select(Node.id).where(Node.blob_id == Blob.sha256).exists()
        rows = (
            await session.execute(
                select(Blob.sha256, Blob.size, Blob.pdf_state)
                .where(
                    Blob.sha256 > after,
                    Blob.mime.in_(mimes_wanted),
                    Blob.preview.in_(("ready", "none", "failed")),
                    or_(
                        Blob.pdf_state.is_(None),
                        and_(
                            Blob.pdf_state.in_(("ready", "failed")),
                            Blob.pdf_version < keys.PDF_VERSION,
                        ),
                    ),
                    is_content,
                )
                .order_by(Blob.sha256)
                .limit(BATCH)
            )
        ).all()
        limit = settings.office_auto_convert_bytes
        ask = {sha for sha, size, state in rows if state != "ready" and size > limit}
        convert = [(sha, state) for sha, _, state in rows if sha not in ask]
        await jobs.enqueue_many(
            session,
            TYPE,
            [({"sha": sha, "n": 1, "b": True}, job_key(sha)) for sha, _ in convert],
            run_after=at,
        )
        if ask:
            await session.execute(
                update(Blob)
                .where(Blob.sha256.in_(ask))
                .values(pdf_state="ask")
                .execution_options(synchronize_session=False)
            )
        waiting = [sha for sha, state in convert if state != "ready"]
        if waiting:
            await session.execute(
                update(Blob)
                .where(
                    Blob.sha256.in_(waiting),
                    or_(Blob.pdf_state.is_(None), Blob.pdf_state == "failed"),
                )
                .values(pdf_state="pending")
                .execution_options(synchronize_session=False)
            )
        if len(rows) == BATCH:
            await jobs.chain(session, BACKFILL, {"after": rows[-1][0]}, BACKFILL_KEY, run_after=at)
        await session.commit()


async def retry_failed(session: AsyncSession) -> int:
    """Voor de knop Mislukte opnieuw op /beheer (O5): elke failed meteen een taak met een eigen
    r (nieuwe keys bij DS) en pending; zie boven. Geeft het aantal. De aanroeper commit."""
    shas = list(
        (await session.scalars(select(Blob.sha256).where(Blob.pdf_state == "failed"))).all()
    )
    r = secrets.token_hex(4)
    for start in range(0, len(shas), BATCH):
        part = shas[start : start + BATCH]
        await jobs.enqueue_many(
            session, TYPE, [({"sha": sha, "n": 1, "r": r}, job_key(sha)) for sha in part]
        )
        await session.execute(
            update(Blob)
            .where(Blob.sha256.in_(part), Blob.pdf_state == "failed")
            .values(pdf_state="pending")
            .execution_options(synchronize_session=False)
        )
    return len(shas)


# --- op verzoek -----------------------------------------------------------------------------


def _office_on() -> engine.Engine:
    office = engine.current()
    if office is None:
        raise errors.fail(503, "office_off", OFF)
    return office


def limit_for(settings: Settings, ext: str) -> int:
    return settings.office_ole_max_bytes if ext in OLE else ZIP_MAX_BYTES


async def request(
    session: AsyncSession, user: User, node: Node, sha: str | None = None
) -> Literal["pending", "ready"]:
    """Een voorbeeld met opmaak op verzoek. node is al gecontroleerd op leesrecht (de router);
    sha is de inhoud van een eerdere versie, of None voor die van node. Commit zelf.

    Een PDF die er al is, is ready, ook met office uit op /beheer (uit stopt alleen nieuwe).

    Fouten: 503 office_off | office_unavailable; 422 not_a_file | office_format |
    office_unsafe; 413 office_too_large; 502 office_convert_failed {code}; 429 rate_limited."""
    office = _office_on()
    settings = runtime.settings()
    sha = sha or node.blob_id
    if node.kind != "file" or not sha:
        raise errors.fail(422, "not_a_file", "Alleen bestanden kunnen worden omgezet")
    blob = await session.get(Blob, sha, populate_existing=True)
    if blob is None:
        raise errors.fail(404, "not_found", "Niet gevonden")
    ext = ext_of(blob.mime)
    if ext is None or ext not in office.convert_formats:
        raise errors.fail(
            422, "office_format", "Van dit soort bestand kan geen voorbeeld met opmaak komen"
        )
    if await office_pdf(settings, blob) is not None and blob.pdf_version >= keys.PDF_VERSION:
        return "ready"
    if not await _enabled(session):
        raise errors.fail(503, "office_off", OFF)
    if blob.pdf_state == "unsafe":
        raise unsafe()
    if blob.pdf_state in ("failed", "none"):
        code = -5 if blob.pdf_state == "none" else await last_code(session, sha)
        raise convert_failed(code)
    limit = limit_for(settings, ext)
    if blob.size > limit:
        raise errors.fail(
            413,
            "office_too_large",
            f"Dit bestand is te groot voor een voorbeeld met opmaak (hooguit {limit // 1024**2}"
            " MB)",
        )
    path = BlobStore(settings.blob_dir).path(sha)
    await session.commit()  # geen databaseverbinding vasthouden tijdens de invoercontrole
    verdict = await inspect.check(settings, path, ext, sha)
    if verdict.reason in inspect.TRANSIENT:  # de machine, niet het bestand: zo opnieuw
        raise errors.fail(503, "office_unavailable", UNAVAILABLE)
    if not verdict.ok:
        await session.execute(
            update(Blob)
            .where(Blob.sha256 == sha)
            .values(pdf_state="unsafe")
            .execution_options(synchronize_session=False)
        )
        await session.commit()
        raise unsafe()
    if not _healthy():
        raise errors.fail(503, "office_unavailable", UNAVAILABLE)
    ASKS.check(str(user.id))
    blob = await session.get(Blob, sha, populate_existing=True)
    if blob is None:
        raise errors.fail(404, "not_found", "Niet gevonden")
    asked = blob.pdf_state == "ask"
    first = jobs.now() - timedelta(hours=1)  # vooraan in de rij
    _asked[sha] = time.monotonic() + ASKED_SECONDS  # en de eerste bij het slot
    job = await session.scalar(
        select(Job)
        .where(Job.key == job_key(sha), Job.status.in_(jobs.ACTIVE))
        .order_by(Job.created_at)
        .limit(1)
    )
    if job is None:
        payload = {"sha": sha, "n": 1, **({"ask": True} if asked else {})}
        await jobs.enqueue(session, TYPE, payload, key=job_key(sha), run_after=first)
    elif job.status == "queued":
        job.run_after = first
        if asked:
            job.payload = {**(job.payload or {}), "ask": True}
    if blob.pdf_state != "ready":
        blob.pdf_state = "pending"
    await session.commit()
    jobs.kick()
    return "pending"


def unsafe() -> Exception:
    return errors.fail(
        422,
        "office_unsafe",
        "Dit bestand kwam niet door de controle; er komt geen voorbeeld met opmaak van",
    )


def convert_failed(code: int) -> Exception:
    error = errors.fail(
        502, "office_convert_failed", f"Dit document kon niet naar PDF worden omgezet (code {code})"
    )
    # De parameter heet code (de catalogus van de web-app: "(code {code})"); dat kan niet als
    # sleutelwoord van errors.fail, waar code de foutcode is
    error.detail["params"] = {"code": code}
    return error


async def wait_ready(
    session: AsyncSession, sha: str, seconds: float | None = None
) -> Literal["ready", "pending"]:
    """Wacht hooguit seconds (standaard PDF_WAIT) tot de PDF er is. Mislukt hij intussen:
    dezelfde fouten als request. Houdt tussendoor geen databaseverbinding vast."""
    settings = runtime.settings()
    wait = PDF_WAIT if seconds is None else seconds
    with anyio.move_on_after(max(0.0, wait)):
        while True:
            blob = await session.get(Blob, sha, populate_existing=True)
            if blob is None:
                raise errors.fail(404, "not_found", "Niet gevonden")
            if await office_pdf(settings, blob) is not None:
                await session.commit()
                return "ready"
            state = blob.pdf_state
            if state in ("failed", "none"):
                code = -5 if state == "none" else await last_code(session, sha)
                await session.commit()
                raise convert_failed(code)
            if state == "unsafe":
                await session.commit()
                raise unsafe()
            await session.commit()
            await anyio.sleep(PDF_POLL)
    return "pending"


jobs.register(TYPE, run, on_dead=on_dead, max_attempts=MAX_ATTEMPTS)
jobs.register(BACKFILL, backfill)
