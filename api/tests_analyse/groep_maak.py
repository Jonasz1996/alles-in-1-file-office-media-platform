"""Synthetische gezichtsvectoren voor group (fase 6, X2b; laag 2).

Dezelfde vorm als de meting van het bouwontwerp (haalbaarheid.md, "Gezichten groeperen",
scripts/groep.py): n // 12 identiteiten als willekeurige eenheidsvectoren; 70% van de gezichten
hoort bij een identiteit (het midden plus ruis met σ 0,05–0,10 per as, cos met het midden
±0,66–0,87), 30% is een losse voorbijganger. Alleen de tijd, het geheugen en de vorm tellen; dit
zegt niets over echte gezichten (laag 4).
"""

from __future__ import annotations

import numpy as np

from app.analyse import wire


def maak(n: int, seed: int = 6) -> tuple[np.ndarray, np.ndarray]:
    """(vectoren n × 128 float32, identiteit per gezicht; een voorbijganger heeft een eigen
    negatieve identiteit)."""
    rng = np.random.default_rng(seed)
    ids = max(1, n // 12)
    mid = rng.standard_normal((ids, wire.FACE_DIM)).astype(np.float32)
    mid /= np.linalg.norm(mid, axis=1, keepdims=True)
    who = rng.integers(0, ids, n)
    alone = rng.random(n) < 0.30
    sigma = rng.uniform(0.05, 0.10, n).astype(np.float32)[:, None]
    x = mid[who] + sigma * rng.standard_normal((n, wire.FACE_DIM)).astype(np.float32)
    x[alone] = rng.standard_normal((int(alone.sum()), wire.FACE_DIM)).astype(np.float32)
    x /= np.linalg.norm(x, axis=1, keepdims=True)
    who = np.where(alone, -1 - np.arange(n), who)
    return np.ascontiguousarray(x, dtype="<f4"), who


def invoer(
    vecs: np.ndarray, person: list[int], persons: int, rejects: list[tuple[int, int]] = ()
) -> bytes:
    """De memfd van de app: PFG1, dan de vectoren (eerst de bevestigde), de persoon-index en de
    afwijzingen."""
    known = len(person)
    head = wire.GROUP_HEAD.pack(wire.GROUP_MAGIC, len(vecs), known, persons, len(rejects))
    rej = np.asarray(list(rejects), dtype="<i4").reshape(-1, 2)
    return (
        head
        + np.ascontiguousarray(vecs, dtype="<f4").tobytes()
        + np.asarray(person, dtype="<i4").tobytes()
        + rej.tobytes()
    )


def uitvoer(data: bytes) -> np.ndarray:
    """De uitvoer als (voorstel, score, groep) per gezicht."""
    dtype = np.dtype([("s", "<i4"), ("f", "<f4"), ("g", "<i4")])
    return np.frombuffer(data, dtype=dtype)


def zuiverheid_en_recall(groep: np.ndarray, wie: np.ndarray) -> tuple[float, float]:
    """Zuiverheid: van de gegroepeerde gezichten het deel dat bij de meerderheid van zijn groep
    hoort. Recall: van de gezichten van identiteiten met ≥ 3 gezichten het deel dat in de grootste
    groep van zijn identiteit zit."""
    wie = wie - wie.min()
    mee = groep >= 0
    goed = sum(np.bincount(wie[groep == g]).max() for g in np.unique(groep[mee]))
    zuiver = goed / max(1, int(mee.sum()))
    tel = np.bincount(wie)
    groot = np.isin(wie, np.flatnonzero(tel >= 3))
    gevonden = 0
    for ident in np.unique(wie[groot]):
        g = groep[wie == ident]
        g = g[g >= 0]
        if len(g):
            gevonden += np.bincount(g).max()
    return float(zuiver), gevonden / max(1, int(groot.sum()))
