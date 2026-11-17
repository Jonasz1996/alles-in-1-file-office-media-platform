"""Nooit een vector, embedding of score in een antwoord (bouwontwerp fase 6, X3; K1-08, K1-16;
X3b).

Elke route die met beeld te maken heeft, met beeld aan en een bestand: geen sleutel vec, embedding
of score (op welke diepte ook), geen lijst van 768 getallen, en geen base64 van 1.536 bytes (de
vector als bytes).
"""

from __future__ import annotations

import base64
import binascii
import re

import pytest

from tests.beeld import nep
from tests.beeld.nep import foto

pytestmark = pytest.mark.anyio

VERBODEN = {"vec", "vector", "embedding", "embeddings", "score", "scores", "s"}
B64 = re.compile(r"[A-Za-z0-9+/=_-]{2000,}")


def _nergens(value, waar: str) -> None:
    if isinstance(value, dict):
        for key, inner in value.items():
            assert key.lower() not in VERBODEN, f"{waar}: sleutel {key}"
            _nergens(inner, f"{waar}.{key}")
    elif isinstance(value, list):
        getallen = [v for v in value if isinstance(v, int | float) and not isinstance(v, bool)]
        assert len(getallen) < 64, f"{waar}: een lijst van {len(getallen)} getallen"
        for i, inner in enumerate(value):
            _nergens(inner, f"{waar}[{i}]")
    elif isinstance(value, str):
        for stuk in B64.findall(value):
            try:
                raw = base64.b64decode(stuk + "==", validate=False)
            except (binascii.Error, ValueError):
                continue
            assert len(raw) < 1536, f"{waar}: base64 van {len(raw)} bytes"


async def test_geen_vector_en_geen_score_in_een_antwoord(client, admin, mensen, beeld):
    jonas, _ = admin
    (eva, _), _ = mensen
    fake, settings = beeld
    await nep.beeld_aan(client, eva)
    await nep.run_jobs(settings)
    rood = await foto(client, eva, settings, "rood.jpg", "rood")
    await foto(client, eva, settings, "blauw.jpg", "blauw", seed=1)
    await nep.embed_all(settings)
    vragen = [
        ("GET", "/api/v1/search", {"mode": "image", "q": "rood"}, eva),
        ("GET", "/api/v1/search", {"mode": "image", "like": rood["id"]}, eva),
        ("GET", "/api/v1/search", {"like": rood["id"]}, eva),
        ("GET", "/api/v1/search", {"q": "rood"}, eva),
        ("GET", "/api/v1/photos/beeld", None, eva),
        ("GET", "/api/v1/me/photo-settings", None, eva),
        ("GET", "/api/v1/admin/search", None, jonas),
        ("GET", f"/api/v1/files/{rood['id']}", None, eva),
    ]
    for method, path, params, headers in vragen:
        r = await client.request(method, path, params=params, headers=headers)
        assert r.status_code == 200, (path, r.text)
        _nergens(r.json(), path)
        assert "geheimwoord" not in r.text
    # en de fouten ook niet
    for params in ({"mode": "image", "q": "rood", "after": 1}, {"mode": "image"}):
        r = await client.get("/api/v1/search", params=params, headers=eva)
        assert r.status_code == 400
        _nergens(r.json(), "fout")
