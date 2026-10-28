"""Veel echte editors tegelijk, voor de metingen van office deel 2 (fase 4, §4.7).

Gedeeld door office_editors.py (20 bewerkers) en office_outage.py (stoppen en een kill van DS).

- Crowd: één Chromium (async Playwright, zodat elke editor echt tegelijk typt), per bewerker
  een eigen context (een eigen gebruiker, ingelogd met het refresh-cookie, een lege cache) en
  een pagina op /bewerken/<id>: de hele weg via de app (de schil, POST …/session, de proxy en de
  tunnel op /office/), zoals een browser. Klaar is: de statusregel zegt "Bewerken"
  (PROBE_JS van office_open.py).
- typen: elke bewerker typt zijn eigen woorden (<tag><document><bewerker>w<nnn>) in zijn
  eigen alinea aan het eind van het document, in een menselijk tempo: een woord van 9 tekens
  met KEY_DELAY_MS per toets, dan een pauze van PAUSE_S. Elk woord krijgt de tijd waarop het
  getypt was.
- Sampler: elke seconde de cpu en het geheugen van de app (het serverproces), van Chromium
  (alle processen van de browser, PSS) en van de hele machine (cores in gebruik en
  MemAvailable). Zakt MemAvailable onder MIN_FREE (1,5 GiB), dan zet hij low: de meting stopt
  netjes en zegt hoe ver ze kwam.
- check_words(): welke woorden in het opgeslagen bestand staan (docx_text), en van de rest of
  er een bijna gelijke staat (één teken weg: de eerste toets valt in de echte editor soms weg,
  o2.md afwijking 32).
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import random
import re
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

import office_bench as ob  # noqa: E402 - pas na het pad naar e2e/perf
from office_open import PROBE_JS  # noqa: E402

from conftest import Account, chromium_path  # noqa: E402
from office_harness import docx_text  # noqa: E402

MIN_FREE = int(1.5 * 1024**3)
KEY_DELAY_MS = 110
PAUSE_S = (0.8, 1.6)
START_PAUSE_S = 2.0
READY_TIMEOUT_MS = 180_000
CLOSE_S = 15.0
WORD = re.compile(r"[a-z0-9]+")
# De tekst van het hele document zoals deze editor hem heeft (sdkjs: alles selecteren en de
# selectie als tekst, dezelfde aanroep als een plug-in). Alleen de selectie van deze editor
# verandert; er wordt daarna niet meer getypt.
EDITOR_TEXT_JS = """() => {
  const ed = window.editor || (window.Asc && window.Asc.editor);
  if (!ed || !ed.asc_EditSelectAll || !ed.pluginMethod_GetSelectedText) return null;
  ed.asc_EditSelectAll();
  return ed.pluginMethod_GetSelectedText(
    {Numbering: false, Math: false, NewLineSeparator: String.fromCharCode(10)});
}"""


# --- de sampler ---


def _pss(pid: int) -> int:
    for line in ob._read(f"/proc/{pid}/smaps_rollup").splitlines():
        if line.startswith("Pss:"):
            return int(line.split()[1]) * 1024
    return 0


def browser_pids() -> list[int]:
    """De processen van Chromium onder dit proces (via de driver van Playwright)."""
    out = []
    for pid in ob.descendants(os.getpid()):
        comm = ob._read(f"/proc/{pid}/comm").strip()
        if comm.startswith(("chrom", "headless_shell")):
            out.append(pid)
    return out


class Sampler:
    """Elke interval s: app (cpu, VmRSS), Chromium (cpu, PSS), machine (cores, MemAvailable)."""

    def __init__(self, app_pid: int, interval: float = 1.0, min_free: int = MIN_FREE) -> None:
        self.app_pid, self.interval, self.min_free = app_pid, interval, min_free
        self.rows: list[dict[str, Any]] = []
        self.low = threading.Event()
        self.low_at: float | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _browser(self) -> tuple[float, int, int]:
        cpu, pss, n = 0.0, 0, 0
        for pid in browser_pids():
            with contextlib.suppress(ProcessLookupError, ValueError, IndexError):
                cpu += ob.cpu_seconds(pid)
                pss += _pss(pid)
                n += 1
        return cpu, pss, n

    def _loop(self) -> None:
        while not self._stop.is_set():
            t = time.monotonic()
            idle, total = ob._idle_total()
            bcpu, bpss, bn = self._browser()
            try:
                acpu, arss = ob.tree_cpu(self.app_pid), ob.rss_bytes(self.app_pid)
            except ProcessLookupError:
                acpu, arss = 0.0, 0
            free = ob.mem_available()
            self.rows.append(
                {
                    "t": t,
                    "idle": idle,
                    "total": total,
                    "app_cpu": acpu,
                    "app_rss": arss,
                    "browser_cpu": bcpu,
                    "browser_pss": bpss,
                    "browser_procs": bn,
                    "mem_available": free,
                }
            )
            if free < self.min_free and not self.low.is_set():
                self.low_at = t
                self.low.set()
                print(
                    f"WAARSCHUWING: nog {free / 1024**3:.2f} GiB vrij; de meting stopt",
                    file=sys.stderr,
                )
            self._stop.wait(max(0.0, self.interval - (time.monotonic() - t)))

    def start(self) -> Sampler:
        self._thread = threading.Thread(target=self._loop, name="sampler", daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=10)

    def between(self, t0: float, t1: float) -> dict[str, Any]:
        rows = [r for r in self.rows if t0 <= r["t"] <= t1]
        if len(rows) < 2:
            return {"samples": len(rows)}
        mb, cpus = 1024**2, os.cpu_count() or 1
        span = rows[-1]["t"] - rows[0]["t"]

        def rate(key: str) -> float:
            return (rows[-1][key] - rows[0][key]) / span if span else 0.0

        def peak_rate(key: str) -> float:
            return max(
                (b[key] - a[key]) / (b["t"] - a["t"])
                for a, b in zip(rows, rows[1:], strict=False)
                if b["t"] > a["t"]
            )

        busy = []
        for a, b in zip(rows, rows[1:], strict=False):
            dt = (b["total"] - a["total"]) / cpus
            if dt > 0:
                busy.append(((b["total"] - a["total"]) - (b["idle"] - a["idle"])) / dt)
        return {
            "samples": len(rows),
            "seconds": round(span, 1),
            "app_cpu_s": round(rows[-1]["app_cpu"] - rows[0]["app_cpu"], 2),
            "app_cores_avg": round(rate("app_cpu"), 2),
            "app_cores_max_1s": round(peak_rate("app_cpu"), 2),
            "app_rss_mb_start": round(rows[0]["app_rss"] / mb, 1),
            "app_rss_mb_max": round(max(r["app_rss"] for r in rows) / mb, 1),
            "app_rss_mb_end": round(rows[-1]["app_rss"] / mb, 1),
            "browser_cores_avg": round(rate("browser_cpu"), 2),
            "browser_pss_mb_max": round(max(r["browser_pss"] for r in rows) / mb),
            "browser_procs_max": max(r["browser_procs"] for r in rows),
            "machine_cores_avg": round(sum(busy) / len(busy), 2) if busy else None,
            "machine_cores_max_1s": round(max(busy), 2) if busy else None,
            "mem_available_mb_min": round(min(r["mem_available"] for r in rows) / mb),
        }


# --- de editors ---


@dataclass
class Editor:
    """Eén bewerker: een gebruiker in een eigen context, met een pagina op /bewerken/<node>."""

    account: Account
    node: str
    doc: int
    user: int
    tag: str
    context: Any = None
    page: Any = None
    opened_s: float | None = None
    error: str | None = None
    statuses: list[str] = field(default_factory=list)
    words: list[tuple[str, float]] = field(default_factory=list)  # (woord, time.monotonic)
    page_errors: int = 0
    typing_errors: list[str] = field(default_factory=list)
    shown: str | None = None  # de tekst die de editor zelf had (editor_text), kleine letters

    @property
    def prefix(self) -> str:
        return f"{self.tag}d{self.doc}u{self.user}w"


async def _within(aw: Any, seconds: float) -> tuple[bool, Any]:
    """aw als eigen taak, hooguit seconds: (klaar zonder fout, uitslag). Niet asyncio.wait_for:
    die wacht na de tijd tot de geannuleerde aanroep terugkomt, en een aanroep van Playwright bij
    een renderer die vastzit, komt pas terug als die renderer weer antwoordt."""
    task = asyncio.ensure_future(aw)
    done, _ = await asyncio.wait({task}, timeout=seconds)
    if not done:
        task.cancel()
        task.add_done_callback(lambda t: t.cancelled() or t.exception())
        return False, None
    if task.cancelled() or task.exception() is not None:
        return False, None
    return True, task.result()


async def _bounded(aw: Any, seconds: float) -> bool:
    """aw afwachten, hooguit seconds; False bij een fout of als het te lang duurt."""
    return (await _within(aw, seconds))[0]


def _chrome_main() -> int | None:
    """Het hoofdproces van de browser die deze Python net startte (via de driver van Playwright)."""
    pids = ob.office_watchdog.descendants(os.getpid())
    comm = {p: ob._read(f"/proc/{p}/comm").strip() for p in pids}
    for p in pids:
        if comm[p] != "chrome":
            continue
        stat = ob._read(f"/proc/{p}/stat")
        ppid = int(stat.rsplit(")", 1)[1].split()[1]) if stat else 0
        if comm.get(ppid) != "chrome":
            return p
    return None


class Crowd:
    """Eén Chromium met veel editors (async Playwright)."""

    def __init__(self, server: Any, sampler: Sampler | None = None) -> None:
        self.server = server
        self.sampler = sampler
        self.editors: list[Editor] = []
        self._pw: Any = None
        self.browser: Any = None
        self.version: str | None = None
        self._chrome: int | None = None
        self.killed: list[int] = []
        self.teardown: dict[str, Any] = {}
        self._pending: list[asyncio.Task] = []
        self.ready_s = READY_TIMEOUT_MS / 1000

    async def __aenter__(self) -> Crowd:
        from playwright.async_api import async_playwright

        self._pw = await async_playwright().start()
        self.browser = await self._pw.chromium.launch(
            executable_path=chromium_path(),
            args=["--no-proxy-server"],
            headless=os.environ.get("E2E_HEADED") != "1",
        )
        self.version = self.browser.version
        self._chrome = _chrome_main()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        # Een renderer die vastzit (een enorm document dat de editor niet kan opbouwen) laat
        # close() minutenlang wachten. Dus: elke stap een eigen tijd, en lukt het niet, dan de
        # browser (zijn hoofdproces, op PID) zelf stoppen.
        t0 = time.monotonic()
        # Een editor die niet open kwam, heeft vaak een renderer die vastzit: dan elke close
        # overslaan en meteen de browser stoppen (voor DS is dat een tabblad dat dichtgaat).
        stuck = any(e.opened_s is None for e in self.editors)
        ok = not (stuck and self._chrome)
        for e in self.editors if ok else ():
            if e.context is not None:
                ok = await _bounded(e.context.close(), CLOSE_S) and ok
        t1 = time.monotonic()
        if ok:
            ok = await _bounded(self.browser.close(), 2 * CLOSE_S)
        t2 = time.monotonic()
        if not ok and self._chrome:
            self.killed = ob.office_watchdog.kill_tree(self._chrome)
        for task in self._pending:
            if not task.done():
                task.cancel()
        await _bounded(self._pw.stop(), 2 * CLOSE_S)
        self.teardown = {
            "contexts_s": round(t1 - t0, 1),
            "browser_s": round(t2 - t1, 1),
            "total_s": round(time.monotonic() - t0, 1),
            "killed": bool(self.killed),
            "stuck": stuck,
        }

    @property
    def low(self) -> bool:
        return self.sampler is not None and self.sampler.low.is_set()

    async def open(self, editor: Editor) -> Editor:
        """Een context als editor.account en /bewerken/<node> tot de statusregel Bewerken zegt."""
        ctx = await self.browser.new_context(
            base_url=self.server.url,
            locale="nl-NL",
            timezone_id="Europe/Amsterdam",
            viewport={"width": 1280, "height": 800},
        )
        ctx.set_default_timeout(30_000)
        await ctx.add_init_script(PROBE_JS)
        editor.context = ctx
        r = await ctx.request.post(
            f"{self.server.url}/api/v1/auth/login",
            data={"email": editor.account.email, "password": editor.account.password},
        )
        if not r.ok:
            editor.error = f"inloggen gaf {r.status}"
            return editor
        page = await ctx.new_page()
        editor.page = page

        def on_error(_exc: Any) -> None:
            editor.page_errors += 1

        page.on("pageerror", on_error)
        t0 = time.monotonic()

        async def ready() -> Any:
            await page.goto(f"/bewerken/{editor.node}")
            await page.wait_for_function(
                "() => window.__officeOpen && (window.__officeOpen.ready !== null"
                " || window.__officeOpen.failed)",
                timeout=self.ready_s * 1000,
            )
            return await page.evaluate("() => window.__officeOpen")

        try:
            # De tijd van Playwright alleen is niet genoeg: een renderer die vastzit, laat ook
            # goto en evaluate wachten, en een geannuleerde aanroep van Playwright komt pas terug
            # als de renderer weer antwoordt (asyncio.wait_for wacht daarop). Daarom: de hele weg
            # als eigen taak, hooguit ready_s, en daarna niet meer op die taak wachten; __aexit__
            # stopt dan de browser, en daarmee eindigt ook de taak.
            task = asyncio.create_task(ready())
            self._pending.append(task)
            done, _ = await asyncio.wait({task}, timeout=self.ready_s)
            if not done:
                raise TimeoutError
            probe = task.result()  # een fout van Playwright komt hier naar boven
            if probe.get("failed"):
                editor.error = f"de editorpagina meldt: {str(probe['failed'])[:200]}"
            else:
                editor.opened_s = round(time.monotonic() - t0, 2)
        except Exception as exc:  # noqa: BLE001 - de uitslag zegt wat er misging
            editor.error = f"niet klaar binnen {self.ready_s:.0f} s: {type(exc).__name__}"
        return editor

    async def open_all(self, editors: list[Editor], stagger: float = 0.25) -> None:
        """Alle editors openen, elk stagger s na de vorige (niet allemaal in dezelfde ms)."""
        tasks = []
        for e in editors:
            if self.low:
                e.error = "niet geopend: te weinig geheugen vrij"
                continue
            self.editors.append(e)
            tasks.append(asyncio.create_task(self.open(e)))
            await asyncio.sleep(stagger)
        await asyncio.gather(*tasks)

    @staticmethod
    async def _frame(page: Any, seconds: float = 30) -> Any:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            frame = page.frame(name="frameEditor")
            if frame is not None:
                return frame
            await asyncio.sleep(0.1)
        raise AssertionError("geen iframe frameEditor")

    async def focus_end(self, editor: Editor) -> None:
        """In het documentvlak klikken, naar het eind, en een nieuwe alinea (type_in_editor)."""
        frame = await self._frame(editor.page)
        for selector in ("#id_main_view", "#ws-canvas-outer", "#editor_sdk"):
            area = frame.locator(selector)
            if await area.count() and await area.first.is_visible():
                await area.first.click(position={"x": 420, "y": 300}, timeout=30_000)
                break
        else:
            raise AssertionError("geen documentvlak in de editor")
        await editor.page.keyboard.press("Control+End")
        await editor.page.keyboard.press("Enter")
        # Een woord dat niet meetelt: de eerste toets valt soms weg (o2.md, afwijking 32). Ook
        # vlak na de eerste wijziging liet de editor soms een toets vallen (gemeten: de spatie
        # na "start", of de eerste letter van het eerste woord), dus daarna even wachten.
        await editor.page.keyboard.type("start ", delay=KEY_DELAY_MS)
        await asyncio.sleep(START_PAUSE_S)

    async def type_for(
        self, editor: Editor, seconds: float, stop: asyncio.Event, seed: int = 0
    ) -> None:
        """Woorden typen tot seconds voorbij zijn of stop gezet is (of het geheugen op is)."""
        rnd = random.Random(f"{editor.prefix}-{seed}")
        try:
            await self.focus_end(editor)
        except Exception as exc:  # noqa: BLE001
            editor.typing_errors.append(f"focus: {type(exc).__name__}: {str(exc)[:120]}")
            return
        end = time.monotonic() + seconds
        while time.monotonic() < end and not stop.is_set() and not self.low:
            word = f"{editor.prefix}{len(editor.words) + 1:03d}"
            try:
                await editor.page.keyboard.type(word + " ", delay=KEY_DELAY_MS)
            except Exception as exc:  # noqa: BLE001
                editor.typing_errors.append(f"typen: {type(exc).__name__}: {str(exc)[:120]}")
                return
            editor.words.append((word, time.monotonic()))
            await asyncio.sleep(rnd.uniform(*PAUSE_S))

    async def type_all(self, seconds: float, stop: asyncio.Event | None = None) -> float:
        """Alle open editors typen tegelijk; geeft de echte duur."""
        stop = stop or asyncio.Event()
        ready = [e for e in self.editors if e.opened_s is not None]
        t0 = time.monotonic()
        await asyncio.gather(*(self.type_for(e, seconds, stop) for e in ready))
        return time.monotonic() - t0

    async def editor_text(self, editor: Editor) -> str | None:
        """De tekst die de editor zelf heeft (EDITOR_TEXT_JS), hooguit 10 s; ook in editor.shown."""
        frame = editor.page.frame(name="frameEditor") if editor.page else None
        if frame is None:
            return None
        ok, text = await _within(frame.evaluate(EDITOR_TEXT_JS), 10)
        editor.shown = text.lower() if ok and isinstance(text, str) else None
        return editor.shown

    async def status(self, editor: Editor) -> str:
        """De laatste statusregel; een renderer die vastzit antwoordt niet, dus hooguit 5 s."""
        with contextlib.suppress(Exception):
            ok, probe = await _within(editor.page.evaluate("() => window.__officeOpen"), 5)
            if not ok:
                return ""
            statuses = probe.get("statuses") or []
            editor.statuses = statuses[-5:]
            return statuses[-1] if statuses else ""
        return ""

    async def close(self, editor: Editor) -> bool:
        """Op Sluiten klikken in de kopbalk van de editorpagina."""
        try:
            await editor.page.get_by_role("button", name="Sluiten", exact=True).click(
                timeout=20_000
            )
            return True
        except Exception as exc:  # noqa: BLE001
            editor.typing_errors.append(f"sluiten: {type(exc).__name__}")
            return False


def near_token(word: str, tokens: set[str]) -> str | None:
    """Een woord in tokens dat één teken verschilt (weg, erbij of anders), of None."""
    for n in range(len(word)):
        if word[:n] + word[n + 1 :] in tokens:
            return word[:n] + word[n + 1 :]
    for t in tokens:
        if len(t) == len(word) + 1 and any(t[:n] + t[n + 1 :] == word for n in range(len(t))):
            return t
        if len(t) == len(word) and sum(a != b for a, b in zip(t, word, strict=True)) == 1:
            return t
    return None


def near(word: str, tokens: set[str]) -> bool:
    return near_token(word, tokens) is not None


def saved_text(data: bytes) -> tuple[str, set[str]]:
    """De tekst van een docx (kleine letters) en zijn woorden."""
    text = docx_text(data).lower()
    return text, set(WORD.findall(text))


def is_saved(word: str, text: str) -> bool:
    """Staat het woord in de tekst? Als deel van een langer woord telt ook: de editor laat soms
    een spatie vallen ("startpd1u1w001": de spatie na "start" weg), en dan is het woord er wel.
    De woorden zijn uniek (voorvoegsel plus 3 cijfers), dus een deel is nooit een ander woord."""
    return word in text


def check_words(data: bytes, editors: list[Editor], *, before: float | None = None) -> dict:
    """Welke getypte woorden in de docx staan. before (time.monotonic): alleen de woorden die
    vóór dat moment getypt waren (de rest apart als after). Een woord dat er niet staat, maar
    wel met één teken verschil (near), is een toets die de editor anders kreeg (o2.md, afwijking
    32), geen verlies bij het opslaan; near_found zegt wat er dan wel staat."""
    text, tokens = saved_text(data)
    typed = [(w, t) for e in editors for w, t in e.words]
    if before is not None:
        early = [w for w, t in typed if t <= before]
        late = [w for w, t in typed if t > before]
    else:
        early, late = [w for w, _ in typed], []
    missing = [w for w in early if not is_saved(w, text)]
    late_saved = [w for w in late if is_saved(w, text)]
    # Een ander getypt woord telt niet als "bijna": w003 weg en w002 er, is gewoon weg
    others = tokens - {w for w, _ in typed}
    found = {w: near_token(w, others) for w in missing}
    # Met de tekst van de editors zelf: wat de editor had maar niet in het bestand staat, is weg
    # bij het opslaan; wat de editor zelf al niet had, kwam nooit binnen (een toets die viel)
    pairs = [(w, t, e) for e in editors for w, t in e.words if before is None or t <= before]
    seen = [(w, e) for w, _t, e in pairs if e.shown is not None]
    shown = [w for w, e in seen if is_saved(w, e.shown or "")]
    shown_set = set(shown)
    return {
        "shown_known": len(seen),
        "shown": len(shown),
        "not_shown": [w for w, _e in seen if w not in shown_set][:20],
        "not_shown_n": len(seen) - len(shown),
        "lost_saving": [w for w in shown if not is_saved(w, text)][:20],
        "lost_saving_n": sum(1 for w in shown if not is_saved(w, text)),
        "typed": len(early),
        "saved": len(early) - len(missing),
        "glued": sum(1 for w in early if w not in tokens and is_saved(w, text)),
        "missing": missing[:20],
        "missing_n": len(missing),
        "missing_near": sum(1 for f in found.values() if f),
        "near_found": {w: f for w, f in list(found.items())[:20] if f},
        "after_n": len(late),
        "after_saved": len(late_saved),
    }
