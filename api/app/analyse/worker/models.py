"""De modellen in het kind (fase 6, Keuze 58, K1-12; X2a). Alleen in een kind van de worker.

Per model één keer: de bytes lezen (O_NOFOLLOW: een symlink is geen model), de sha256 over
precies die bytes vergelijken met models.lock, en dan InferenceSession(bytes) met
CPUExecutionProvider alleen, één draad. Zo laadt onnxruntime nooit iets anders dan wat de sha256
kreeg (geen tweede keer lezen van het pad). get_providers() moet precies CPUExecutionProvider
zijn: het wiel heeft ook AzureExecutionProvider (K1-12).

Klopt een model niet (weg, een andere grootte of sha256, een symlink), dan is de opdracht
model_missing met de naam. caps (pid 1, worker/caps.py) zegt hetzelfde zonder het te laden.
"""

from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path
from typing import Any

from app.analyse import lock
from app.analyse.wire import Code
from app.analyse.worker.child import OpFailed

PROVIDERS = ["CPUExecutionProvider"]
YUNET = "face_detection_yunet_2026may.onnx"
SFACE = "face_recognition_sface_2021dec.onnx"


def read_model(path: Path, size: int) -> bytes:
    """De bytes van een model, hooguit size + 1 (een groter bestand is "anders")."""
    fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError("geen gewoon bestand")
        out = bytearray()
        while len(out) <= size:
            chunk = os.read(fd, min(size + 1 - len(out), 1 << 20))
            if not chunk:
                break
            out += chunk
        return bytes(out)
    finally:
        os.close(fd)


def options() -> Any:
    import onnxruntime

    o = onnxruntime.SessionOptions()
    o.intra_op_num_threads = 1
    o.inter_op_num_threads = 1
    o.execution_mode = onnxruntime.ExecutionMode.ORT_SEQUENTIAL
    o.log_severity_level = 3  # geen waarschuwingen per initializer op stderr
    return o


class Models:
    def __init__(self, root: Path, lock_path: Path = lock.LOCK_PATH) -> None:
        self.root = root
        self.lock_path = lock_path
        self._sessions: dict[str, Any] = {}

    def entry(self, name: str) -> lock.ModelFile:
        for item in lock.load(self.lock_path):
            if item.name == name:
                return item
        raise OpFailed(Code.MODEL_MISSING, f"{name} staat niet in models.lock")

    def session(self, name: str) -> Any:
        found = self._sessions.get(name)
        if found is not None:
            return found
        import onnxruntime

        item = self.entry(name)
        try:
            data = read_model(self.root / name, item.size)
        except OSError:
            raise OpFailed(Code.MODEL_MISSING, f"{name} ontbreekt") from None
        if len(data) != item.size or hashlib.sha256(data).hexdigest() != item.sha256:
            raise OpFailed(Code.MODEL_MISSING, f"{name} is anders dan models.lock")
        sess = onnxruntime.InferenceSession(data, options(), providers=PROVIDERS)
        del data
        if sess.get_providers() != PROVIDERS:
            raise OpFailed(Code.INTERNAL, "onnxruntime gebruikt een andere provider dan de CPU")
        self._sessions[name] = sess
        return sess
