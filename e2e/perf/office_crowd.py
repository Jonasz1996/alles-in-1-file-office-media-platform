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
READY_TIMEOUT_MS = 180_000
WORD = re.compile(r"[a-z0-9]+")


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

    @property
    def prefix(self) -> str:
        return f"{self.tag}d{self.doc}u{self.user}w"


class Crowd:
    """Eén Chromium met veel editors (async Playwright)."""

    def __init__(self, server: Any, sampler: Sampler | None = None) -> None:
        self.server = server
        self.sampler = sampler
        self.editors: list[Editor] = []
        self._pw: Any = None
        self.browser: Any = None
        self.version: str | None = None

    async def __aenter__(self) -> Crowd:
        from playwright.async_api import async_playwright

        self._pw = await async_playwright().start()
        self.browser = await self._pw.chromium.launch(
            executable_path=chromium_path(),
            args=["--no-proxy-server"],
            headless=os.environ.get("E2E_HEADED") != "1",
        )
        self.version = self.browser.version
        return self

    async def __aexit__(self, *exc: Any) -> None:
        for e in self.editors:
            with contextlib.suppress(Exception):
                await e.context.close()
        with contextlib.suppress(Exception):
            await self.browser.close()
        with contextlib.suppress(Exception):
            await self._pw.stop()

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
        try:
            await page.goto(f"/bewerken/{editor.node}")
            await page.wait_for_function(
                "() => window.__officeOpen && (window.__officeOpen.ready !== null"
                " || window.__officeOpen.failed)",
                timeout=READY_TIMEOUT_MS,
            )
            probe = await page.evaluate("() => window.__officeOpen")
            if probe.get("failed"):
                editor.error = f"de editorpagina meldt: {str(probe['failed'])[:200]}"
            else:
                editor.opened_s = round(time.monotonic() - t0, 2)
        except Exception as exc:  # noqa: BLE001 - de uitslag zegt wat er misging
            editor.error = (
                f"niet klaar binnen {READY_TIMEOUT_MS / 1000:.0f} s: {type(exc).__name__}"
            )
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
        # Een woord dat niet meetelt: de eerste toets valt soms weg (o2.md, afwijking 32)
        await editor.page.keyboard.type("start ", delay=KEY_DELAY_MS)

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

    async def status(self, editor: Editor) -> str:
        with contextlib.suppress(Exception):
            probe = await editor.page.evaluate("() => window.__officeOpen")
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


def near(word: str, tokens: set[str]) -> bool:
    """Staat er een woord dat één teken verschilt (weg, erbij of anders)?"""
    for n in range(len(word)):
        if word[:n] + word[n + 1 :] in tokens:
            return True
    for t in tokens:
        if len(t) == len(word) + 1 and any(t[:n] + t[n + 1 :] == word for n in range(len(t))):
            return True
        if len(t) == len(word) and sum(a != b for a, b in zip(t, word, strict=True)) == 1:
            return True
    return False


def check_words(data: bytes, editors: list[Editor], *, before: float | None = None) -> dict:
    """Welke getypte woorden in de docx staan. before (time.monotonic): alleen de woorden die
    vóór dat moment getypt waren (de rest apart als after)."""
    text = docx_text(data)
    tokens = set(WORD.findall(text.lower()))
    typed = [(w, t) for e in editors for w, t in e.words]
    if before is not None:
        early = [w for w, t in typed if t <= before]
        late = [w for w, t in typed if t > before]
    else:
        early, late = [w for w, _ in typed], []
    missing = [w for w in early if w not in tokens]
    late_saved = [w for w in late if w in tokens]
    return {
        "typed": len(early),
        "saved": len(early) - len(missing),
        "missing": missing[:20],
        "missing_n": len(missing),
        "missing_near": sum(1 for w in missing if near(w, tokens)),
        "after_n": len(late),
        "after_saved": len(late_saved),
    }
