"""Gezichten met de echte container analyse (fase 6, X2b; marker analyse_echt, job analyse-image).

De echte compose, zoals install.sh: compose.yaml + compose.analyse.yaml, en
compose.analyse-limits.yaml als docker grenzen kan geven (test_analyse_container.sh doet het zo
ook). Het image komt uit E2E_ANALYSE_IMAGE (in CI platform:ci), de echte modellen van
models.lock uit E2E_ANALYSE_MODELLEN (in CI haalt test_analyse_container.sh ze in de stap
ervoor). Geen browser: de API van de app via zijn poort op 127.0.0.1. analyse heeft geen
netwerk (network_mode none).

1. De beheerder ziet "Analyse: ok", met beide modellen "sha256 klopt".
2. Eva zet gezichten aan en uploadt drie gemaakte foto's zonder gezicht (ruis, een verloop, een
   vlak met een rand). Eén ervan heeft een merkteken in een JPEG-commentaar.
3. De scan loopt van begin tot eind: drie van de drie klaar, niets mislukt, 0 gezichten; per
   foto [].
4. Eva zoekt op het merkteken (de vraag gaat niet in een log, K1-16).
5. Uitzetten: het wissen loopt af.
6. docker logs van alle containers: geen merkteken en geen sleutel.

Zonder E2E_ANALYSE_IMAGE wordt hij overgeslagen. E2E_ANALYSE_COMPOSE: nog een compose-bestand,
als laatste (lokaal: een lagere nofile voor platform, zoals ANALYSE_TEST_COMPOSE). De map met
sleutels is van uid 10001 (sudo -n chown als de test geen root is) en gaat aan het eind weg, net
als het project en zijn volumes.

    cd api && E2E_ANALYSE_IMAGE=platform:ci E2E_ANALYSE_MODELLEN=/pad/naar/modellen \\
      uv run --with playwright python -m pytest ../e2e/test_zoek_gezichten_echt.py -m analyse_echt
"""

from __future__ import annotations

import io
import os
import secrets
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from PIL import Image, ImageDraw
from tests.people.faces import schijven

from conftest import REPO, Account, Server, create_user, free_port, make_admin

pytestmark = pytest.mark.analyse_echt

API = "/api/v1"
SINGLE = REPO / "infra" / "single"
PLATFORM_UID = 10001
START_S = 120.0  # de app, en analyse healthy (start_period 30 s, interval 30 s)
SCAN_S = 300.0  # de plan-job wacht hooguit 60 s op de index, dan drie foto's met de echte modellen
WIS_S = 120.0


def als_root(*args: str) -> None:
    cmd = list(args) if os.geteuid() == 0 else ["sudo", "-n", *args]
    subprocess.run(cmd, check=True, stdin=subprocess.DEVNULL, timeout=60)


class Compose(Server):
    """De echte compose in plaats van `python -m app.serve`: de app in de container platform,
    via een poort op 127.0.0.1. request, call en token van Server werken zo gewoon. De sleutel
    staat alleen in .env (0600) en in de omgeving van platform en analyse; nooit in een argument
    of in een melding van de test."""

    def __init__(self, werk: Path, image: str, modellen: Path, extra: str | None) -> None:
        super().__init__(werk / "niet-gebruikt")
        self.werk, self.image, self.modellen = werk, image, modellen
        self.project = f"gezichtenecht-{os.getpid()}"
        self.key = secrets.token_hex(32)
        self.sleutels = werk / "sleutels"
        self.files = [
            "-f",
            str(SINGLE / "compose.yaml"),
            "-f",
            str(SINGLE / "compose.analyse.yaml"),
        ]
        if self._limits():
            self.files += ["-f", str(SINGLE / "compose.analyse-limits.yaml")]
        self.files += ["-f", str(werk / "test.yaml")]
        if extra:
            self.files += ["-f", extra]
        self.up = False

    @staticmethod
    def _limits() -> bool:
        info = subprocess.run(
            ["docker", "info", "--format", "{{.MemoryLimit}} {{.CPUCfsQuota}}"],
            capture_output=True, text=True, check=False, timeout=60,
        )  # fmt: skip
        return info.stdout.strip() == "true true"

    def mask(self, text: str) -> str:
        return text.replace(self.key, "<sleutel>")

    def compose(self, *args: str, timeout: float = 600) -> str:
        cmd = ["docker", "compose", "--project-name", self.project, "--project-directory",
               str(SINGLE), "--env-file", str(self.werk / ".env"), *self.files, *args]  # fmt: skip
        r = subprocess.run(
            cmd, capture_output=True, text=True, check=False, timeout=timeout,
            stdin=subprocess.DEVNULL,
        )  # fmt: skip
        if r.returncode != 0:
            uit = self.mask((r.stdout + r.stderr)[-2000:])
            raise RuntimeError(f"docker compose {args[0]} gaf {r.returncode}: {uit}")
        return r.stdout + r.stderr

    def start(self) -> None:
        self.port, self.https_port = free_port(), free_port()
        self.sleutels.mkdir(mode=0o700)
        als_root("chown", f"{PLATFORM_UID}:{PLATFORM_UID}", str(self.sleutels))
        fd = os.open(self.werk / ".env", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as env:
            env.write(
                "DATA_ROOT=/niet-gebruikt\n"
                f"ANALYSE_WORKER_KEY={self.key}\n"
                f"MODELLEN_ROOT={self.modellen}\n"
                f"SLEUTELS_ROOT={self.sleutels}\n"
                f"PORT=127.0.0.1:{self.port}\n"
                f"HTTPS_PORT=127.0.0.1:{self.https_port}\n"
            )
        # Het image van de test, en een eigen volume voor /data (zoals test_analyse_container.sh)
        (self.werk / "test.yaml").write_text(
            "services:\n"
            "  platform:\n"
            f"    image: {self.image}\n"
            "    pull_policy: never\n"
            "    volumes:\n"
            "      - platform-data:/data\n"
            "  analyse:\n"
            f"    image: {self.image}\n"
            "  analyse-omzetten:\n"
            f"    image: {self.image}\n"
            "volumes:\n"
            "  platform-data:\n"
        )
        self.compose("config", "-q")
        self.up = True
        self.compose("up", "-d", "--no-build", "--pull", "never")
        self._wait_ready(START_S)

    def logs(self) -> str:
        return self.compose("logs", "--no-color", "--timestamps")

    def stop(self) -> None:
        if self._http is not None:
            self._http.close()
            self._http = None
        if self.up:
            self.up = False
            self.compose("down", "-v", "--remove-orphans", "--timeout", "10")

    def log_tail(self, lines: int = 60) -> str:
        try:
            return "\n".join(self.mask(self.logs()).splitlines()[-lines:])
        except RuntimeError as exc:
            return str(exc)


@pytest.fixture(scope="module")
def echt() -> Iterator[Compose]:
    image = os.environ.get("E2E_ANALYSE_IMAGE")
    if not image:
        pytest.skip("geen image (E2E_ANALYSE_IMAGE staat niet; de job analyse-image zet hem)")
    modellen = Path(os.environ.get("E2E_ANALYSE_MODELLEN") or "/nonexistent")
    if not modellen.is_dir() or not any(modellen.glob("*.onnx")):
        pytest.fail(f"E2E_ANALYSE_MODELLEN wijst niet naar de modellen van models.lock: {modellen}")
    werk = Path(tempfile.mkdtemp(prefix="gezichtenecht-"))
    c = Compose(werk, image, modellen.resolve(), os.environ.get("E2E_ANALYSE_COMPOSE"))
    try:
        c.start()
        yield c
    finally:
        try:
            c.stop()
        finally:
            if c.sleutels.exists():
                als_root("rm", "-rf", str(c.sleutels))
            shutil.rmtree(werk, ignore_errors=True)


def modellen_van_gezichten() -> list[str]:
    """De namen van de modellen van gezichten in models.lock (die van beeld, siglip2-…/, niet)."""
    namen = []
    for regel in (REPO / "api" / "app" / "analyse" / "models.lock").read_text().splitlines():
        delen = regel.split()
        if len(delen) >= 3 and not regel.startswith("#") and "/" not in delen[2]:
            namen.append(delen[2])
    return sorted(namen)


# --- de foto's ---------------------------------------------------------------------------------


def met_commentaar(jpeg: bytes, tekst: str) -> bytes:
    """Een JPEG met een COM-segment direct na SOI."""
    assert jpeg[:2] == b"\xff\xd8"
    data = tekst.encode()
    return jpeg[:2] + b"\xff\xfe" + (len(data) + 2).to_bytes(2, "big") + data + jpeg[2:]


def verloop() -> bytes:
    beeld = Image.linear_gradient("L").resize((1600, 1200)).convert("RGB")
    out = io.BytesIO()
    beeld.save(out, "JPEG", quality=90)
    return out.getvalue()


def vlak() -> bytes:
    beeld = Image.new("RGB", (1600, 1200), (90, 140, 110))
    ImageDraw.Draw(beeld).rectangle((100, 100, 1500, 1100), outline=(20, 20, 20), width=24)
    out = io.BytesIO()
    beeld.save(out, "JPEG", quality=90)
    return out.getvalue()


# --- hulp --------------------------------------------------------------------------------------


def wacht(c: Compose, account: Account, pad: str, klaar: Callable[[Any], bool], wat: str,
          seconden: float) -> Any:  # fmt: skip
    eind = time.monotonic() + seconden
    gezien: Any = None
    while time.monotonic() < eind:
        gezien = c.call(account, "GET", f"{API}{pad}", expect=200).json()
        if klaar(gezien):
            return gezien
        time.sleep(0.5)
    raise AssertionError(f"{wat} niet binnen {seconden:.0f} s: {gezien}")


def wacht_op_previews(c: Compose, account: Account, ids: list[str]) -> None:
    eind = time.monotonic() + SCAN_S
    stand: dict[str, Any] = {}
    while time.monotonic() < eind:
        stand = c.call(
            account, "POST", f"{API}/previews", json={"node_ids": ids}, expect=200
        ).json()
        if all(
            stand.get(i, {}).get("state") == "ready" and "/1024.webp" in (stand[i]["large"] or "")
            for i in ids
        ):
            return
        time.sleep(0.5)
    raise AssertionError(f"de previews kwamen niet binnen {SCAN_S:.0f} s: {stand}")


# --- de test -----------------------------------------------------------------------------------


def test_de_echte_scan_van_begin_tot_eind(echt: Compose) -> None:
    merk = f"merkteken-x2b-{secrets.token_hex(8)}"
    admin = make_admin(echt)
    eva = create_user(echt, admin, name="Eva Echt")

    # --- 1. de beheerder: de monitor zegt ok, met de modellen van models.lock ---
    blok = wacht(
        echt, admin, "/admin/search",
        lambda s: s["faces"] is not None and s["faces"]["analyse"] == "ok",
        "Analyse: ok", START_S,
    )["faces"]  # fmt: skip
    assert blok["reason"] is None and blok["allowed"] is True
    assert sorted((m["name"], m["sha_ok"]) for m in blok["models"]) == [
        (naam, True) for naam in modellen_van_gezichten()
    ]

    # --- 2. aanzetten en drie foto's zonder gezicht ---
    gezet = echt.call(eva, "PATCH", f"{API}/me/photo-settings", json={"faces": "aan"}, expect=200)
    assert gezet.json()["faces"] == "aan"
    fotos = {
        "ruis.jpg": met_commentaar(schijven.foto((), seed=7), merk),
        "verloop.jpg": verloop(),
        "vlak.jpg": vlak(),
    }
    ids = {}
    for naam, data in fotos.items():
        r = echt.call(
            eva, "PUT", f"{API}/files/{eva.root_id}/upload",
            params={"name": naam}, content=data, expect=201,
        )  # fmt: skip
        ids[naam] = r.json()["id"]
    wacht_op_previews(echt, eva, list(ids.values()))

    # --- 3. de scan loopt van begin tot eind ---
    stand = wacht(
        echt, eva, "/people/faces/status",
        lambda s: s["photos"]["waiting"] == 0 and s["photos"]["done"] + s["photos"]["failed"] == 3,
        "de scan", SCAN_S,
    )  # fmt: skip
    assert stand["setting"] == "aan" and stand["analyse"] == "ok" and not stand["wiping"]
    assert stand["photos"] == {"total": 3, "done": 3, "waiting": 0, "failed": 0, "skipped": 0}
    assert stand["faces"] == {"found": 0, "named": 0, "open": 0, "groups": 0}
    for naam, node in ids.items():
        gevonden = echt.call(eva, "GET", f"{API}/files/{node}/faces", expect=200).json()
        assert gevonden == [], naam

    # --- 4. zoeken op het merkteken ---
    gezocht = echt.call(eva, "GET", f"{API}/search", params={"q": merk}, expect=200).json()
    assert isinstance(gezocht["results"], list)

    # --- 5. uitzetten: het wissen loopt af ---
    uit = echt.call(eva, "PATCH", f"{API}/me/photo-settings", json={"faces": "uit"}, expect=200)
    assert uit.json()["faces"] == "uit"
    stand = wacht(
        echt, eva, "/people/faces/status", lambda s: not s["wiping"], "het wissen", WIS_S,
    )  # fmt: skip
    assert stand["setting"] == "uit" and stand["faces"]["found"] == 0

    # --- 6. docker logs: geen merkteken, geen sleutel (K1-16) ---
    logs = echt.logs()
    regels = len(logs.splitlines())
    assert regels > 0
    if merk in logs:
        pytest.fail(f"het merkteken staat in docker logs ({logs.count(merk)} keer)")
    if echt.key in logs:
        pytest.fail("de sleutel staat in docker logs")
