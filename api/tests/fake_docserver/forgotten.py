"""De forgotten files van de nep-DS (O2, Keuze 21), zoals gemeten met DS 10.0 (spike stap 5,
recorded/commands.json).

Een opslag (status 2) die de app niet bereikt ({"error": 1}, of 429 en 5xx na alle pogingen)
komt hier terecht (fake.on_lost). Daarna:
- getForgottenList: {"error": 0, "keys": [...]};
- getForgotten {key}: {"key", "error": 0, "url"} met een secure link onder
  /cache/files/forgotten/<key>/<naam>/<naam>, op de host van DS zelf (zonder prefix); een
  onbekende key: error 1;
- deleteForgotten {key}: error 0, en daarna kent getForgotten hem niet meer (error 1); een
  onbekende key: error 1 [niet gemeten; de veilige lezing].
Ze staan in het volume van DS, niet in het geheugen: kill, stop en restart laten ze staan. De
link hangt wel aan het linkgeheim: na een restart geeft een oude link 403, een nieuwe
getForgotten werkt.

Voor tests: add(fake, key, content) zet er zelf een neer; GET /__fake/forgotten toont de keys.
"""

import re

from fastapi import FastAPI, Request
from fastapi.responses import Response

from . import protocol

# Het pad van een opslag in fields["url"]: /cache/files/data/<deel>/<naam>/<naam>
DATA_PATH = re.compile(r"/cache/files/data/([^/?]+)/([^/?]+)/")
# De host van DS zelf, zoals getForgotten hem geeft (de app neemt alleen pad en query)
OWN_BASE = "http://docserver"


def add(fake, key: str, content: bytes, name: str | None = None) -> None:
    """Een forgotten file voor key, alsof een opslag de app niet bereikte."""
    store(fake)[key] = (name or "output.docx", content)


def store(fake) -> dict[str, tuple[str, bytes]]:
    found = getattr(fake, "forgotten", None)
    if found is None:
        found = fake.forgotten = {}
    return found


def install(fake, app: FastAPI) -> None:
    files = store(fake)

    async def lost(doc, fields: dict) -> None:
        """Wat DS bewaart als de laatste opslag de app niet bereikt: dezelfde inhoud als de
        link in de callback."""
        name = f"output.{doc.filetype}"
        content = None
        match = DATA_PATH.search(str(fields.get("url") or ""))
        if match is not None:
            part, name = match.group(1), match.group(2)
            content = fake.files.get(part, {}).get(name)
        if content is None:
            content = fake.output(doc)
        files[doc.key] = (name, content)
        fake.record("forgotten", key=doc.key, bytes=len(content))

    fake.on_lost.append(lost)

    async def forgotten_list(params: dict) -> dict:
        return {"error": protocol.CMD_OK, "keys": list(files)}

    async def get_forgotten(params: dict) -> dict:
        key = params.get("key")
        if not isinstance(key, str) or key not in files:
            return {"key": key, "error": protocol.CMD_KEY}
        name = files[key][0]
        path = f"/cache/files/forgotten/{key}/{name}/{name}"
        url = protocol.secure_link(OWN_BASE, fake.link_secret, path, key, name)
        return {"key": key, "error": protocol.CMD_OK, "url": url}

    async def delete_forgotten(params: dict) -> dict:
        key = params.get("key")
        if not isinstance(key, str) or files.pop(key, None) is None:
            return {"key": key, "error": protocol.CMD_KEY}
        return {"key": key, "error": protocol.CMD_OK}

    fake.commands["getForgottenList"] = forgotten_list
    fake.commands["getForgotten"] = get_forgotten
    fake.commands["deleteForgotten"] = delete_forgotten

    @app.get("/cache/files/forgotten/{key}/{name}/{filename}")
    async def forgotten_file(key: str, name: str, filename: str, request: Request) -> Response:
        if not fake.up:
            return Response(status_code=503)
        q = request.query_params
        if not protocol.link_ok(
            fake.link_secret, request.url.path, q.get("md5", ""), q.get("expires", "")
        ):
            return Response(status_code=403)
        entry = files.get(key)
        if entry is None or entry[0] != name or filename != name:
            return Response(status_code=404)
        return Response(entry[1], media_type="application/octet-stream")

    @app.get("/__fake/forgotten")
    async def fake_forgotten() -> dict:
        return {"keys": {key: len(content) for key, (_, content) in files.items()}}
