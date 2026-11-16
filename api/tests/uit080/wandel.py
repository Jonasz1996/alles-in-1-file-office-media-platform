"""Een wandeling langs elke route met de analyse uit (fase 6, golf 6; de FEITEN van golf 6).

Met ANALYSE uit (PLATFORM_ANALYSE_ENABLED niet aan, zoals 0.8.0 en de .env van Jonas) moet
elke route die 0.8.0 kende hetzelfde antwoorden als in 0.8.0, en mag er geen job in de rij
analyse, geen Setting, geen rij en geen bestand onder DATA_ROOT bij komen die 0.8.0 niet had.

Deze module bouwt een vaste wereld (Jonas de beheerder, Eva met een foto, een pdf, een map, een
album en een persoon op de foto, en de gast Gijs) alleen via de API, en vraagt dan elke route van
de app: als niemand, als Eva, als Gijs en als Jonas. Wat terugkomt, wordt een vorm:

- de status en het type van het antwoord;
- bij een fout (4xx, 5xx) het hele `detail` (de melding, de code en de params), met elke id als
  `<id>` en elke tijd als `<tijd>`;
- bij een goed antwoord de vorm van de JSON: de sleutels, per waarde het type, bij een lijst de
  lengte en de vormen van de elementen (de waarden zelf hangen van tijd en ids af).

Daarna de stand: de jobs (soort en stand), de sleutels van Setting, het aantal rijen per tabel
en de bestanden onder de datamap.

Alleen de API en `app.config`, `app.migrate`, `app.db` en `app.main` van de app zelf: zo draait
dezelfde wandeling op de code van 0.8.0 (`3fca440`), en dat gaf `antwoorden_080.json`:

    git archive 3fca440 api/app api/migrations | tar -x -C /tmp/v080
    cd api && .venv/bin/python -I -c "import sys; sys.path[:0] = ['/tmp/v080/api', '.']; \\
        from tests.uit080 import wandel; wandel.opnemen('tests/uit080/antwoorden_080.json')"

GET /api/v1/events (de stroom van gebeurtenissen) blijft open en hoort er niet bij; zijn
inlog-poort staat in tests/test_events.py.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import os
import re
import sqlite3
import tempfile
import uuid
from pathlib import Path
from typing import Any

from httpx import ASGITransport, AsyncClient

WIE = ("niemand", "eva", "gijs", "jonas")
WACHTWOORD = "lang-genoeg-123"
GEEN = "00000000-0000-4000-8000-00000000abcd"  # een id die nergens bestaat
NIET = {("GET", "/api/v1/events")}  # blijft open (zie boven)
VOLGORDE = ("GET", "HEAD", "OPTIONS", "POST", "PUT", "PATCH", "DELETE")
GROOT = 4096  # een grotere vorm (de openapi.json) bewaren we als zijn sha256

_ID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|[0-9a-f]{32}")
_TIJD = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:?\d{2})?")


def _schoon(v: Any) -> Any:
    """Een foutmelding zonder ids en tijden."""
    if isinstance(v, dict):
        return {k: _schoon(x) for k, x in sorted(v.items())}
    if isinstance(v, list):
        return [_schoon(x) for x in v]
    if isinstance(v, str):
        return _TIJD.sub("<tijd>", _ID.sub("<id>", v))
    return v


def vorm(v: Any) -> Any:
    """De vorm van een goed antwoord: sleutels, typen, lengtes."""
    if isinstance(v, dict):
        return {k: vorm(x) for k, x in sorted(v.items())}
    if isinstance(v, list):
        return [len(v), *sorted({json.dumps(vorm(x), sort_keys=True) for x in v})]
    if isinstance(v, bool) or v is None:
        return v
    return type(v).__name__


def _jpeg() -> bytes:
    """Een kleine, vaste JPEG (altijd dezelfde bytes)."""
    from PIL import Image

    img = Image.new("RGB", (64, 48))
    img.putdata([((x * 4) % 256, (y * 5) % 256, 128) for y in range(48) for x in range(64)])
    out = io.BytesIO()
    img.save(out, "JPEG", quality=80)
    return out.getvalue()


PDF = (
    b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
    b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]>>endobj\n"
    b"trailer<</Root 1 0 R>>\n%%EOF\n"
)


async def _wereld(c: AsyncClient) -> tuple[dict[str, dict], dict[str, str]]:
    """Jonas, Eva en Gijs, en Eva's spullen; geeft (headers per wie, waarden per padparameter)."""

    async def ok(r, *codes: int) -> dict:
        assert r.status_code in codes, (r.request.method, r.request.url.path, r.text)
        return r.json() if r.content else {}

    jonas = await ok(
        await c.post(
            "/api/v1/auth/setup",
            json={"email": "jonas@example.nl", "display_name": "Jonas", "password": WACHTWOORD},
        ),
        201,
    )
    h = {"niemand": {}, "jonas": {"Authorization": f"Bearer {jonas['access_token']}"}}
    for naam, rol in (("eva", "user"), ("gijs", "guest")):
        await ok(
            await c.post(
                "/api/v1/users",
                json={
                    "email": f"{naam}@example.nl",
                    "display_name": naam.title(),
                    "password": WACHTWOORD,
                    "role": rol,
                },
                headers=h["jonas"],
            ),
            201,
        )
        login = await ok(
            await c.post(
                "/api/v1/auth/login",
                json={"email": f"{naam}@example.nl", "password": WACHTWOORD},
            ),
            200,
        )
        h[naam] = {"Authorization": f"Bearer {login['access_token']}"}
    eva = h["eva"]
    me = await ok(await c.get("/api/v1/auth/me", headers=eva), 200)
    root = (await ok(await c.get("/api/v1/files/root", headers=eva), 200))["folder"]["id"]
    foto = await ok(
        await c.put(
            f"/api/v1/files/{root}/upload",
            params={"name": "strand.jpg"},
            content=_jpeg(),
            headers=eva,
        ),
        200,
        201,
    )
    await ok(
        await c.put(
            f"/api/v1/files/{root}/upload", params={"name": "verslag.pdf"}, content=PDF, headers=eva
        ),
        200,
        201,
    )
    map_ = {"name": "Vakantie"}
    await ok(await c.post(f"/api/v1/files/{root}/folders", json=map_, headers=eva), 201)
    album = await ok(await c.post("/api/v1/albums", json={"name": "Zomer"}, headers=eva), 201)
    await ok(
        await c.post(
            f"/api/v1/albums/{album['id']}/items", json={"node_ids": [foto["id"]]}, headers=eva
        ),
        200,
    )
    await ok(
        await c.post(f"/api/v1/files/{foto['id']}/people", json={"name": "Anna"}, headers=eva),
        201,
    )
    mensen = await ok(await c.get("/api/v1/people", headers=eva), 200)
    waarden = {
        "node_id": foto["id"],
        "album_id": album["id"],
        "person_id": mensen["people"][0]["id"],
        "user_id": me["id"],
        "kind": "day",
        "ref": "2024-07-01",
        "n": "1",
        "i": "0",
        "track": "0",
        "size": "96",
        "sha": "a" * 64,
        "key": "1:2",
        "token": "x",
        "path": "x",
    }
    return h, waarden


def _routes() -> list[tuple[str, str]]:
    from fastapi.routing import iter_route_contexts

    from app.main import app

    found = set()
    for ctx in iter_route_contexts(app.routes):
        for method in ctx.methods or ():
            if (method, ctx.path) not in NIET:
                found.add((method, ctx.path))
    return sorted(found, key=lambda mp: (VOLGORDE.index(mp[0]), mp[1]))


def _url(method: str, path: str, waarden: dict[str, str]) -> str:
    """GET, HEAD en OPTIONS met de wereld; de rest met een id die nergens bestaat (dan wist
    of verandert de wandeling niets, en zien alle volgende vragen dezelfde wereld)."""
    lees = method in ("GET", "HEAD", "OPTIONS")

    def vul(m: re.Match) -> str:
        name = m.group(1).split(":")[0]
        if name.endswith("_id") or name in ("sid", "upload_id"):
            return waarden.get(name, GEEN) if lees else GEEN
        return waarden.get(name, GEEN)

    return re.sub(r"{([^}]+)}", vul, path)


async def _vraag(c: AsyncClient, method: str, url: str, headers: dict, body: bool) -> Any:
    kw: dict[str, Any] = {"headers": headers}
    if body:
        kw["json"] = {}
    try:
        r = await asyncio.wait_for(c.request(method, url, **kw), 30)
    except TimeoutError:
        return "<te laat>"
    soort = r.headers.get("content-type", "").split(";")[0]
    if soort != "application/json" or not r.content:
        return [r.status_code, soort]
    data = r.json()
    if r.status_code >= 400:
        detail = data.get("detail") if isinstance(data, dict) else data
        return [r.status_code, soort, _schoon(detail)]
    uit = vorm(data)
    tekst = json.dumps(uit, sort_keys=True, separators=(",", ":"))
    if len(tekst) > GROOT:
        return [r.status_code, soort, "sha256:" + hashlib.sha256(tekst.encode()).hexdigest()]
    return [r.status_code, soort, uit]


def _met_body(method: str, path: str) -> bool:
    from fastapi.routing import iter_route_contexts

    from app.main import app

    for ctx in iter_route_contexts(app.routes):
        if ctx.path == path and method in (ctx.methods or ()):
            route = ctx.route
            return getattr(route, "body_field", None) is not None
    return False


def _stand(data_dir: Path) -> dict[str, Any]:
    """Jobs, Setting, rijen per tabel en bestanden onder de datamap, na de wandeling."""
    db = sqlite3.connect(data_dir / "platform.db")
    try:
        jobs = sorted(f"{t} {s}" for t, s in db.execute("select type, status from job"))
        keys = sorted(k for (k,) in db.execute("select key from setting"))
        tabellen = [
            n
            for (n,) in db.execute("select name from sqlite_master where type = 'table'")
            if n not in ("alembic_version", "sqlite_sequence", "sqlite_stat1")
        ]
        rijen = {n: db.execute(f'select count(*) from "{n}"').fetchone()[0] for n in tabellen}
    finally:
        db.close()
    bestanden = set()
    for folder, _dirs, names in os.walk(data_dir):
        for name in names:
            rel = os.path.relpath(os.path.join(folder, name), data_dir)
            if not rel.startswith("platform.db"):
                bestanden.add(_ID.sub("<id>", rel))
    return {"jobs": jobs, "setting": keys, "rijen": dict(sorted(rijen.items())),
            "bestanden": sorted(bestanden)}  # fmt: skip


async def wandel(data_dir: Path) -> dict[str, Any]:
    """De hele wandeling in een lege datamap; geeft de antwoorden en de stand."""
    from app import db
    from app.certs import CertManager
    from app.config import Settings, get_settings
    from app.main import app
    from app.migrate import upgrade
    from app.security import SecurityConfig, SecurityPolicy, login_limiter

    settings = Settings(data_dir=data_dir, web_dist=data_dir / "web", jwt_secret="test" * 8)
    assert not settings.analyse_enabled
    await upgrade(settings)
    app.dependency_overrides[get_settings] = lambda: settings
    app.state.certs = CertManager(settings)
    app.state.security = SecurityPolicy(SecurityConfig(require_2fa=False))
    login_limiter.reset()
    antwoorden: dict[str, Any] = {}
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            h, waarden = await _wereld(c)
            for method, path in _routes():
                url, body = _url(method, path, waarden), _met_body(method, path)
                for wie in WIE:
                    sleutel = f"{method} {path} {wie}"
                    antwoorden[sleutel] = await _vraag(c, method, url, h[wie], body)
    finally:
        app.dependency_overrides.clear()
        del app.state.certs
        del app.state.security
        await db.get_engine(settings).dispose()
        db._engine.cache_clear()
    return {"antwoorden": antwoorden, "stand": _stand(data_dir)}


def opnemen(doel: str) -> None:
    """Schrijf de wandeling van deze code naar doel (zie de docstring): één antwoord per regel."""
    with tempfile.TemporaryDirectory() as tmp:
        uitkomst = asyncio.run(wandel(Path(tmp)))

    def regels(d: dict) -> str:
        return ",\n".join(
            f"  {json.dumps(k, ensure_ascii=False)}: "
            f"{json.dumps(v, sort_keys=True, ensure_ascii=False, separators=(',', ':'))}"
            for k, v in d.items()
        )

    tekst = (
        '{\n "antwoorden": {\n' + regels(uitkomst["antwoorden"]) + "\n },\n"
        ' "stand": {\n' + regels(uitkomst["stand"]) + "\n }\n}\n"
    )
    assert json.loads(tekst) == json.loads(json.dumps(uitkomst))
    Path(doel).write_text(tekst)


def _main() -> None:  # pragma: no cover - alleen met de hand
    import sys

    opnemen(sys.argv[1] if len(sys.argv) > 1 else f"wandeling-{uuid.uuid4().hex[:6]}.json")


if __name__ == "__main__":  # pragma: no cover
    _main()
