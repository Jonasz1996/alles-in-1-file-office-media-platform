"""De versie van gezichten (fase 6, X2 "Een nieuwe versie"; X2b).

FACES_VERSION gaat omhoog als de modellen of de drempels veranderen; dan zoekt faces.plan met
scope version alles opnieuw (de open gezichten weg, de bevestigde komen terug via de kaders).
LOCK_SHA is lock.digest() over de eigen regels van models.lock (YuNet en SFace), uitgerekend uit
het bestand: tests/people/faces/test_faces_version.py eist dat hij gelijk is, zodat een nieuw model
zonder een hogere FACES_VERSION niet door de tests komt. Een regel van X3 telt niet mee.
"""

from __future__ import annotations

from pathlib import Path

from app.analyse import lock

FACES_VERSION = 1
# De twee modellen van gezichten (de namen in models.lock; app/analyse/worker/models.py)
FACES_MODELS = ("face_detection_yunet_2026may.onnx", "face_recognition_sface_2021dec.onnx")
# lock.digest() over die twee regels van app/analyse/models.lock, bij FACES_VERSION 1
LOCK_SHA = "c34829c2524b2d619166d71855cfcb18d74cda83e0fa0441983f4b7a61a73d60"


def lock_sha(path: Path = lock.LOCK_PATH) -> str:
    """De sha256 over de regels van FACES_MODELS in models.lock, zoals hij nu is."""
    return lock.digest([f for f in lock.load(path) if f.name in FACES_MODELS])
