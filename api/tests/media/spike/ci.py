"""De spike in CI (bouwontwerp fase 5, §3.7): de job `media-spike` draait dit op de runner.

1. De stappen in het image (S1, S2, S5, S7, S10): `docker run` van het platform-image met
   `python3 -I /spike/spike.py`, onder `--cpus 3` (regel 14), zonder netwerk en zonder caps.
2. S3: `docker compose` met `s3.compose.yaml` en het afgeleide image (de drie regels van
   Keuze 47): een fd van de app naar de worker over een socket in een benoemd volume.
3. `spike.json`: alles samen, met de omgeving van de runner.

    python3 -I ci.py --uit spike.json [--image platform:ci] [--worker-image platform-spike:ci]
                     [--stappen S1,S2,S3,S5,S7,S10] [--standin]

`--standin`: het worker-image heeft geen echte ffmpeg (lokaal, met python:3.12-slim als basis);
S3 slaat dan alleen de delen met ffmpeg over. Exitcode 1 alleen als een script crasht of docker
het image niet heeft; een andere uitkomst dan het ontwerp staat in spike.json.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
import time
import traceback

sys.dont_write_bytecode = True
HIER = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HIER)

from gedeeld import Stap, log  # noqa: E402

BEELD_STAPPEN = ("S1", "S2", "S5", "S7", "S10")
ALLE = ("S1", "S2", "S3", "S5", "S7", "S10")
PROJECT = "spike-s3"
ELDERS = {
    "S4": "sandbox (Chromium 141)",
    "S6": "sandbox",
    "S8": "sandbox",
    "S9": "sandbox",
    "S11": "sandbox",
    "S12": "sandbox",
    "S13": "CI, job e2e-media-perf (Chrome for Testing); niet in deze job",
}


def sh(argv: list[str], *, timeout: float = 600, env=None, invoer: str | None = None):
    """Een commando op de runner; de uitvoer komt terug, stderr ook in het log."""
    log("$ " + " ".join(argv))
    try:
        r = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
            input=invoer,
            check=False,
        )
    except subprocess.TimeoutExpired as e:
        return 124, e.stdout or "", f"deadline van {timeout} s"
    if r.stderr.strip():
        log(r.stderr.rstrip()[-4000:])
    return r.returncode, r.stdout, r.stderr


def runner() -> dict:
    rc, docker, _ = sh(["docker", "version", "--format", "{{.Server.Version}}"])
    rc2, compose, _ = sh(["docker", "compose", "version", "--short"])
    model = None
    try:
        with open("/proc/cpuinfo") as f:
            model = next(
                (r.split(":", 1)[1].strip() for r in f if r.startswith("model name")), None
            )
    except OSError:
        pass
    return {
        "docker": docker.strip() if rc == 0 else None,
        "compose": compose.strip() if rc2 == 0 else None,
        "python": sys.version.split()[0],
        "kernel": platform.release(),
        "cpu_model": model,
        "cpus": os.cpu_count(),
    }


def image_id(naam: str) -> str | None:
    rc, uit, _ = sh(["docker", "image", "inspect", "--format", "{{.Id}}", naam])
    return uit.strip() if rc == 0 else None


def beeld(image: str, stappen: list[str], commit: str | None) -> dict:
    """S1, S2, S5, S7 en S10 in het image, onder --cpus 3 (regel 14)."""
    argv = [
        "docker", "run", "--rm", "--cpus", "3", "--memory", "3g", "--memory-swap", "3g",
        "--network", "none", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
        "--pids-limit", "256", "-e", f"SPIKE_IMAGE={image}", "-e", f"SPIKE_COMMIT={commit or ''}",
        "-v", f"{HIER}:/spike:ro", image,
        "python3", "-I", "/spike/spike.py", "--stappen", ",".join(stappen),
    ]  # fmt: skip
    log("$ " + " ".join(argv))
    t0 = time.monotonic()
    # stderr gaat direct naar het log van de job (het verloop per stap)
    r = subprocess.run(argv, stdout=subprocess.PIPE, text=True, timeout=1500, check=False)
    try:
        uit = json.loads(r.stdout)
    except ValueError:
        return {
            "crash": True,
            "rc": r.returncode,
            "fout": f"geen JSON van spike.py (rc {r.returncode}): {r.stdout[-2000:]}",
            "duur_s": round(time.monotonic() - t0, 1),
        }
    uit["rc"] = r.returncode
    return uit


def compose(env: dict, *args: str, timeout: float = 300):
    return sh(
        ["docker", "compose", "-p", PROJECT, "-f", os.path.join(HIER, "s3.compose.yaml"), *args],
        env=env,
        timeout=timeout,
    )


def s3(worker_image: str, standin: bool) -> dict:
    stap = Stap(
        "S3",
        "fd's tussen containers: de worker als media (10002), het benoemde volume, de socket,"
        " de afscherming en de zelftest van Keuze 11",
        "compose.media.yaml; de terugval",
    )
    stap.verwacht = (
        "De app (10001) geeft de blob als fd over de socket in het benoemde volume media-run"
        " (media:platform, 2750; socket 0660, groep platform) aan de worker (10002, network none,"
        " read_only, tmpfs op /data, cap_drop ALL, no-new-privileges). De worker leest via de fd,"
        " kan er niet op schrijven en kan hem via /proc niet opnieuw openen; een kind ziet"
        " /proc/<worker>/fd niet; ffmpeg is 0711. ffmpeg leest de fd en schrijft naar de pipe van"
        " de app."
    )
    env = dict(
        os.environ,
        SPIKE_WORKER_IMAGE=worker_image,
        SPIKE_DIR=HIER,
        SPIKE_FFMPEG_STANDIN="1" if standin else "0",
    )
    compose(env, "down", "-v", "--remove-orphans")
    try:
        rc, _, fout = compose(env, "up", "-d", "worker")
        gestart = f"rc {rc}" + (f": {fout[-300:]}" if rc else "")
        if not stap.check("de worker start met de afscherming", rc == 0, "rc 0", gestart):
            stap.gemeten = "de worker-container start niet"
            stap.besluit = "S3 faalt al bij het starten"
            stap.terugval = "S3, terugval 3: de nooduitgang MEDIA_SANDBOX=uit"
            return stap.uit()
        rc, uit, fout = compose(env, "run", "--rm", "--no-deps", "-T", "app", timeout=240)
        _, wid, _ = compose(env, "ps", "-a", "-q", "worker")
        inspectie = {}
        if wid.strip():
            rc_i, tekst, _ = sh(["docker", "inspect", wid.strip().splitlines()[0]])
            if rc_i == 0:
                inspectie = json.loads(tekst)[0]
        rc_l, logs, _ = compose(env, "logs", "--no-color", "worker")
        stap.gegevens["worker_log"] = logs[-3000:]
        rc_v, vol, _ = sh(["docker", "volume", "inspect", f"{PROJECT}_media-run"])
        stap.gegevens["volume"] = json.loads(vol)[0] if rc_v == 0 and vol.strip() else None
    finally:
        compose(env, "down", "-v", "--remove-orphans")
    try:
        app = json.loads(uit.strip().splitlines()[-1]) if uit.strip() else None
    except ValueError:
        app = None
    if app is None:
        stap.check(
            "de app geeft een uitkomst",
            False,
            "JSON van s3_app.py",
            f"rc {rc}: {uit[-500:]} {fout[-500:]}",
        )
        stap.gemeten = "geen uitkomst van de app"
        stap.besluit = "niet te beoordelen; zie worker_log"
        stap.terugval = "S3, terugval 1–3 pas na het lezen van het log"
        return stap.uit()
    beoordeel(stap, app, inspectie, standin)
    return stap.uit()


def beoordeel(stap: Stap, app: dict, inspectie: dict, standin: bool) -> None:
    w = app.get("worker") or {}
    om = w.get("omgeving", {})
    zt = w.get("zelftest", {})
    hc = inspectie.get("HostConfig", {})
    stap.gegevens.update({"app": app, "inspect": _kort_inspect(inspectie)})
    stap.check(
        "de worker draait als media (10002)",
        om.get("uid") == 10002 and om.get("naam") == "media",
        "uid 10002, naam media",
        f"uid {om.get('uid')}, gid {om.get('gid')}, naam {om.get('naam')},"
        f" groepen {om.get('groepen')}",
    )
    afscherming = {
        "NetworkMode": hc.get("NetworkMode"),
        "ReadonlyRootfs": hc.get("ReadonlyRootfs"),
        "CapDrop": hc.get("CapDrop"),
        "SecurityOpt": hc.get("SecurityOpt"),
        "PidsLimit": hc.get("PidsLimit"),
        "Tmpfs": hc.get("Tmpfs"),
        "NanoCpus": hc.get("NanoCpus"),
        "Memory": hc.get("Memory"),
    }
    stap.check(
        "de afscherming volgens docker inspect",
        afscherming["NetworkMode"] == "none"
        and afscherming["ReadonlyRootfs"] is True
        and afscherming["CapDrop"] in (["ALL"], ["all"])
        and any("no-new-privileges" in s for s in afscherming["SecurityOpt"] or [])
        and afscherming["PidsLimit"] == 256
        and set(afscherming["Tmpfs"] or {}) >= {"/data", "/tmp"},
        "network none, read_only, cap_drop ALL, no-new-privileges, pids 256, tmpfs /data en /tmp",
        afscherming,
    )
    status = om.get("status", {})
    net = om.get("netwerk", {})
    koppel = om.get("koppelingen", {})
    stap.check(
        "in de worker: geen netwerk, / alleen lezen, /data leeg (tmpfs), geen caps, no_new_privs",
        net.get("interfaces") == ["lo"]
        and "FOUT" not in str(net)
        and "EROFS" in str(om.get("rootfs_schrijven"))
        and om.get("data") == []
        and koppel.get("/data", {}).get("type") == "tmpfs"
        and om.get("data_map", {}).get("modus") == "0700"
        and om.get("data_map", {}).get("uid") == 10002
        and int(status.get("CapEff", "1"), 16) == 0
        and status.get("NoNewPrivs") == "1",
        "alleen lo, verbinden faalt, EROFS, /data [] op tmpfs (0700, 10002), CapEff 0,"
        " NoNewPrivs 1",
        {
            "netwerk": net,
            "rootfs": om.get("rootfs_schrijven"),
            "data": om.get("data"),
            "data_map": om.get("data_map"),
            "koppelingen": koppel,
            "status": status,
            "pids_max": om.get("pids_max"),
            "rlimit_nofile": om.get("rlimit_nofile"),
            "rlimit_core": om.get("rlimit_core"),
            "cpu_grens": om.get("cpu_grens"),
            "geheugen_grens": om.get("geheugen_grens"),
        },
    )
    rm_w, rm_a = om.get("run_media", {}), app.get("run_media", {})
    stap.check(
        "het benoemde volume /run/media: media:platform, 2750",
        all(
            d.get("modus") == "2750" and d.get("uid") == 10002 and d.get("groep") == "platform"
            for d in (rm_w, rm_a)
        ),
        "modus 2750, uid 10002, groep platform (gezien door worker en app)",
        {"worker": rm_w, "app": rm_a},
    )
    so_w, so_a = om.get("socket", {}), app.get("socket", {})
    stap.check(
        "de socket: 0660, groep platform",
        so_a.get("modus") == "0660"
        and so_a.get("groep") == "platform"
        and so_a.get("uid") == 10002,
        "modus 0660, uid 10002, groep platform",
        {"worker": so_w, "app": so_a},
    )
    stap.check(
        "de app (10001) verbindt, maar maakt niets in /run/media",
        app.get("app", {}).get("uid") == 10001
        and app.get("verbinden") == "ok"
        and "EACCES" in str(app.get("app_maakt_in_run_media")),
        "uid 10001; verbinden ok; een bestand maken: EACCES",
        {
            "app": app.get("app"),
            "verbinden": app.get("verbinden"),
            "maken": app.get("app_maakt_in_run_media"),
        },
    )
    blob = zt.get("blob", {})
    stap.check(
        "fd over SCM_RIGHTS: de worker leest een stuk midden in de blob, de hash klopt",
        app.get("events", [])[:1] == ["started"]
        and zt.get("sha_stuk") == app.get("sha_stuk_verwacht")
        and blob.get("pad_bestaat_hier") is False
        and blob.get("cloexec") is True,
        "hash gelijk; het pad bestaat niet in de worker; de fd heeft CLOEXEC",
        {"events": app.get("events"), "blob": blob, "app_blob": app.get("blob")},
    )
    stap.check(
        "schrijven op de doorgegeven fd faalt",
        "EBADF" in str(zt.get("schrijven_op_fd")),
        "EBADF",
        zt.get("schrijven_op_fd"),
    )
    heropen = zt.get("heropen_via_proc", {})
    stap.check(
        "selftest: /proc/self/fd/N opnieuw openen faalt, en de blob bleef gelijk",
        "EACCES" in str(heropen.get("O_WRONLY"))
        and "EACCES" in str(heropen.get("O_RDWR"))
        and app.get("blob_gelijk") is True,
        "O_WRONLY en O_RDWR: EACCES; blob ongewijzigd",
        {"heropen": heropen, "blob_gelijk": app.get("blob_gelijk"), "blob": app.get("blob")},
    )
    k1, k0 = zt.get("kind_bij_dumpbaar_1", {}), zt.get("kind_bij_dumpbaar_0", {})
    stap.check(
        "selftest: niet dumpbaar, dus een kind kan /proc/<worker>/fd niet openen",
        str(k1.get("fd_lijst")).startswith("ok")
        and k1.get("fd_open_eigen") == "ok"
        and all(
            "PermissionError" in str(k0.get(x)) for x in ("fd_lijst", "fd_open", "fd_open_eigen")
        )
        and zt.get("dumpbaar_nu") == 0,
        "dumpbaar 1 (controle): de lijst en het eigen bestand in /tmp open; dumpbaar 0: alles"
        " PermissionError",
        {"dumpbaar_bij_start": zt.get("dumpbaar_bij_start"), "bij_1": k1, "bij_0": k0},
    )
    stap.check(
        "een kind opent de blob via /proc ook niet als de worker dumpbaar is (0600 van de app)",
        "PermissionError" in str(k1.get("fd_open")),
        "PermissionError",
        k1.get("fd_open"),
    )
    prog = zt.get("programmas", {})
    stap.check(
        "selftest: ffmpeg en ffprobe 0711 van root; de worker kan ze niet lezen",
        all(
            p.get("modus") == "0711"
            and p.get("uid") == 0
            and "PermissionError" in str(p.get("lezen"))
            for p in prog.values()
        )
        and len(prog) == 2,
        "modus 0711, uid 0, lezen: PermissionError",
        prog,
    )
    buren = zt.get("buren", {})
    ff, py = buren.get("ffmpeg_0711", {}), buren.get("controle_python", {})
    stap.check(
        "selftest: een buur ziet /proc/<ffmpeg>/fd niet, wel status (VmRSS)",
        ff.get("start") == "ok"
        and "PermissionError" in str(ff.get("buur", {}).get("fd_lijst"))
        and "kB" in str(ff.get("buur", {}).get("status"))
        and str(py.get("buur", {}).get("fd_lijst", "")).startswith("ok"),
        "ffmpeg (0711): fd PermissionError, status leesbaar; controle (python): fd leesbaar",
        buren,
    )
    pijp = zt.get("pijp", {})
    stap.check(
        "de pipe is van de app (10001)",
        pijp.get("uid") == 10001 and pijp.get("fifo") is True,
        "uid 10001, een fifo",
        pijp,
    )
    if standin:
        stap.gegevens["overgeslagen"] = "ffprobe en remux via de fd: de stand-in heeft geen ffmpeg"
    else:
        ff = zt.get("ffmpeg", {})
        probe = ff.get("probe", {}) if isinstance(ff, dict) else {}
        remux = ff.get("remux", {}) if isinstance(ff, dict) else {}
        stap.check(
            "ffprobe leest de fd in de worker",
            probe.get("rc") == 0 and "matroska" in str(probe.get("format")),
            "rc 0, matroska",
            probe,
        )
        mp4 = app.get("fmp4", {})
        stap.check(
            "remux in de worker: ffmpeg leest de fd en schrijft fMP4 naar de pipe van de app",
            remux.get("rc") == 0
            and mp4.get("volgorde", [])[:2] == ["ftyp", "moov"]
            and mp4.get("moof", 0) >= 3
            and mp4.get("moof") == mp4.get("mdat"),
            "rc 0; de app leest ftyp, moov en dan moof+mdat",
            {"remux": remux, "fmp4": mp4},
        )
        zonder = ff.get("remux_zonder_lseek", {}) if isinstance(ff, dict) else {}
        if ff.get("positie_na_probe") and zonder.get("rc") not in (None, 0):
            stap.gegevens["bevinding_positie"] = (
                "Een fd uit SCM_RIGHTS deelt zijn positie met de app en met elk kind: na ffprobe"
                f" staat hij op {ff.get('positie_na_probe')}, en ffmpeg leest `fd:` vanaf daar"
                f" (rc {zonder.get('rc')} zonder terugzetten). De worker zet de positie vóór elke"
                " exec op 0 (`os.lseek`) en start nooit twee kinderen tegelijk op dezelfde fd;"
                " de app opent de blob per opdracht opnieuw (M0: wire.py en de worker)."
            )
    _besluit_s3(stap)


def _besluit_s3(stap: Stap) -> None:
    anders = stap.anders()
    bevinding = stap.gegevens.get("bevinding_positie")
    if not anders:
        stap.gemeten = f"Alle {len(stap.checks)} controles zoals verwacht."
        stap.besluit = (
            "compose.media.yaml zoals Keuze 11: het benoemde volume media-run op /run/media, de"
            " worker als media met network_mode none, read_only, tmpfs op /tmp en /data, cap_drop"
            " ALL en no-new-privileges; de selftest zoals Keuze 11. Geen terugval nodig."
        )
        if bevinding:
            stap.besluit += " Bevinding: " + bevinding
        return
    stap.gemeten = f"{len(stap.checks) - len(anders)} van {len(stap.checks)} zoals verwacht;"
    stap.gemeten += " anders: " + "; ".join(anders)
    volume = {
        "het benoemde volume /run/media: media:platform, 2750",
        "de socket: 0660, groep platform",
        "de app (10001) verbindt, maar maakt niets in /run/media",
    }
    fd = {
        "fd over SCM_RIGHTS: de worker leest een stuk midden in de blob, de hash klopt",
        "schrijven op de doorgegeven fd faalt",
        "remux in de worker: ffmpeg leest de fd en schrijft fMP4 naar de pipe van de app",
        "ffprobe leest de fd in de worker",
    }
    zelftest = [a for a in anders if a.startswith("selftest")]
    if set(anders) & fd:
        stap.terugval = (
            "S3, terugval 2: de worker krijgt de blobs alleen lezen (/data/blobs:ro) en de app"
            " geeft een pad; dat komt in spike.md en bij de risico's"
        )
    elif set(anders) & volume:
        stap.terugval = (
            "S3, terugval 1: een bindkoppeling ${DATA_ROOT}/media/run:/run/media (install.sh maakt"
            " de map: eigenaar 10002, de groep van platform, modus 2750)"
        )
    besluit = []
    if zelftest:
        besluit.append(
            "de zelftest van Keuze 11 faalt (" + "; ".join(zelftest) + "): de worker zou zo niet"
            " starten (media_worker_down); eerst uitzoeken, de afscherming is dan niet wat het"
            " ontwerp zegt"
        )
    if not besluit:
        besluit.append("compose.media.yaml zoals Keuze 11, met de terugval hierboven")
    stap.besluit = "; ".join(besluit) + "."
    if bevinding:
        stap.besluit += " Bevinding: " + bevinding


def _kort_inspect(i: dict) -> dict:
    hc = i.get("HostConfig", {})
    return {
        "User": i.get("Config", {}).get("User"),
        "Image": i.get("Config", {}).get("Image"),
        "ExitCode": i.get("State", {}).get("ExitCode"),
        "Mounts": [
            (m.get("Type"), m.get("Name"), m.get("Destination"), m.get("RW"))
            for m in i.get("Mounts", [])
        ],
        "Ulimits": hc.get("Ulimits"),
        "MemorySwap": hc.get("MemorySwap"),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--uit", required=True)
    ap.add_argument("--image", default="platform:ci")
    ap.add_argument("--worker-image", default="platform-spike:ci")
    ap.add_argument("--stappen", default=",".join(ALLE))
    ap.add_argument("--standin", action="store_true")
    args = ap.parse_args()
    gekozen = [s.strip().upper() for s in args.stappen.split(",") if s.strip()]
    commit = os.environ.get("GITHUB_SHA")
    uit: dict = {
        "spike": "bouwontwerp fase 5, §3.7: de stappen in het image (job media-spike)",
        "commit": commit,
        "gemaakt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "runner": runner(),
        "image": {"naam": args.image, "id": None, "worker": args.worker_image, "worker_id": None},
        "stappen": [],
        "elders": ELDERS,
    }
    crash = False
    stappen: dict[str, dict] = {}
    beeld_stappen = [s for s in gekozen if s in BEELD_STAPPEN]
    if beeld_stappen:
        uit["image"]["id"] = image_id(args.image)
        if uit["image"]["id"] is None:
            log(f"!! het image {args.image} is er niet")
            crash = True
        else:
            b = beeld(args.image, beeld_stappen, commit)
            crash |= bool(b.get("crash")) or "stappen" not in b
            uit["image"]["omgeving"] = b.get("omgeving")
            uit["image"]["bronnen"] = b.get("bronnen")
            if "fout" in b:
                uit["image"]["fout"] = b["fout"]
            for s in b.get("stappen", []):
                stappen[s["stap"]] = s
    if "S3" in gekozen:
        uit["image"]["worker_id"] = image_id(args.worker_image)
        if uit["image"]["worker_id"] is None:
            log(f"!! het image {args.worker_image} is er niet")
            crash = True
        else:
            try:
                stappen["S3"] = s3(args.worker_image, args.standin)
            except Exception:
                crash = True
                stappen["S3"] = {
                    "stap": "S3",
                    "uitkomst": "fout in het script",
                    "fout": traceback.format_exc()[-4000:],
                }
    uit["stappen"] = [stappen[s] for s in ALLE if s in stappen]
    uit["samenvatting"] = [
        {
            "stap": s.get("stap"),
            "uitkomst": s.get("uitkomst"),
            "anders": s.get("anders"),
            "besluit": s.get("besluit"),
            "terugval": s.get("terugval"),
        }
        for s in uit["stappen"]
    ]
    uit["crash"] = crash
    os.makedirs(os.path.dirname(os.path.abspath(args.uit)), exist_ok=True)
    with open(args.uit, "w") as f:
        json.dump(uit, f, ensure_ascii=False, indent=1, default=str)
        f.write("\n")
    log("== samenvatting")
    for s in uit["samenvatting"]:
        anders = ("anders: " + "; ".join(s["anders"])) if s.get("anders") else ""
        log(f"{s['stap']:>4}  {s['uitkomst']}  {anders}")
    return 1 if crash else 0


if __name__ == "__main__":
    sys.exit(main())
