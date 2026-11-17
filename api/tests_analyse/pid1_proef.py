"""pid 1 van de worker zoals __main__ hem start, met een eigen serve (fase 6, X2a; laag 2).

    python -I pid1_proef.py <api> <socket> <modellen> <models.lock>

met PLATFORM_ANALYSE_WORKER_KEY in de omgeving. __main__.main draait echt (prctl, de sleutel uit
de omgeving, argparse, Config); alleen serve is vervangen: die start de worker, doet caps en één
faces via de client, en zet op stdout als JSON wat test_worker_sandbox.py nakijkt:

- key_in_environ: staat de sleutel na de start nog in os.environ (moet False);
- key_sha: de sha256 van config.key (de test kent de sleutel; zo staat hij niet op stdout);
- dumpable: PR_GET_DUMPABLE van pid 1 (moet 0, vóór de import van de app gezet);
- python_ok: geen numpy en geen onnxruntime in pid 1, ook niet na een opdracht;
- kind: wat het kind werk bij klaar zei (pid, dumpable).

Niet test_*.py: pytest verzamelt dit bestand niet.
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import sys

sys.path.insert(0, sys.argv[1])

from app.analyse.worker import __main__ as m  # noqa: E402  (zet dumpable op 0)


async def proef(worker) -> int:
    from app.analyse import client, runtime, wire
    from app.analyse.wire import Child, Op
    from app.analyse.worker import server
    from app.config import Settings

    out: dict = {"key_in_environ": runtime.KEY_ENV in os.environ}
    await worker.start()
    try:
        cfg = worker.config
        settings = Settings(
            data_dir=cfg.socket.parent,
            web_dist=cfg.socket.parent / "web",
            jwt_secret="test" * 8,
            analyse_enabled=True,
            analyse_worker=cfg.socket,
            analyse_worker_key=cfg.key,
        )
        caps = await client.caps(settings)
        out["caps"] = sorted(caps.built)
        webp = bytes.fromhex(sys.stdin.readline().strip())
        fd = wire.sealed_memfd("preview", webp)
        uit = wire.out_memfd("uit")
        try:
            data = await client.call(settings, Op.FACES, wire.FacesArgs(), fds=[fd, uit])
        finally:
            wire.close_all([fd, uit])
        out["faces"] = [p["code"] for p in data["photos"]]
        out["key_sha"] = hashlib.sha256(cfg.key.encode()).hexdigest()
        out["sandbox"] = cfg.sandbox
        out["dumpable"] = ctypes.CDLL(None).prctl(3, 0, 0, 0, 0)
        out["python_ok"] = server.python_ok()
        out["kind"] = worker.kids[Child.WERK].ready
    finally:
        await worker.stop()
    print(json.dumps(out), flush=True)
    return 0


m.serve = proef
sys.exit(m.main(["--socket", sys.argv[2], "--models", sys.argv[3], "--lock", sys.argv[4],
                 "--local"]))  # fmt: skip
