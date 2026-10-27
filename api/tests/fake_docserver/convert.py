"""Het deel conversie van de nep-DS (O3): POST /converter zoals DS 10.0.0.1 het deed
(recorded/convert.json, spike O0).

- zonder geldig token geeft de kern al -8 (app.py); hier alleen getekende verzoeken;
- async false: het antwoord als de conversie klaar is. async true: eerst
  {"percent": 0, "endConvert": false}, en pas na `polls` keer opnieuw vragen het resultaat;
- de bron haalt hij op bij url, met het download-JWT en Want-Repr-Digest, zoals een document.
  Geen 200, of een Repr-Digest die niet klopt: -4. Zonder Repr-Digest gaat het gewoon door
  (digest_none in de opname);
- alleen naar PDF: een andere outputtype geeft -3 (docx naar doc), of -7 voor pptx en ppt;
- de key is een cache: dezelfde key geeft het antwoord van de eerste keer, ook voor een andere
  bron, en ook als dat een fout was (zoals de echte [gemeten]). Een kill of restart maakt het
  geheugen leeg;
- de uitvoer: een PDF (pdf.py) met N pagina's. N volgt de bron: de dia's van een pptx, de
  bladen van een xlsx, de pagina-einden plus 1 van een docx; anders 1;
- fileUrl is een secure link op de host van het verzoek:
  /cache/files/data/conv_<key>_<n>/output.pdf/<stam>.pdf?md5=…&expires=…&shardkey=<key>&
  filename=<stam>.pdf. De stam komt uit title.

Sturen (tests in het proces via fake.conversions, de e2e via POST /__fake/convert):
- fail: de volgende conversies krijgen deze foutcodes, één per conversie;
- hold: conversies wachten na het ophalen van de bron tot hold weer uit gaat (om een conversie
  "lopend" te houden);
- polls: zo vaak "nog bezig" bij async, vóór het resultaat (standaard 1);
- host: een andere host in fileUrl (de app volgt die nooit);
- wrong_key: fileUrl wijst naar de map van een andere key;
- not_pdf: de uitvoer is geen PDF;
- pages: dit aantal pagina's, wat de bron ook is;
- reset: alles terug, en de cache leeg.
De log krijgt "convert_source" (het ophalen van de bron) en "convert_file" (het ophalen van de
uitvoer).
"""

import asyncio
import io
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from . import pdf, protocol

MAX_PAGES = 50
STEM = re.compile(r"[^A-Za-z0-9_-]+")


@dataclass(eq=False)
class Conversion:
    key: str
    polls: int
    done: asyncio.Event = field(default_factory=asyncio.Event)
    result: dict | None = None
    task: asyncio.Task | None = None


def pages_of(filetype: str, content: bytes) -> int:
    """Hoeveel pagina's de nep-PDF krijgt, uit de bron."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(content))
    except (zipfile.BadZipFile, ValueError):
        return 1
    with archive:
        names = archive.namelist()
        if filetype in ("pptx", "pptm"):
            found = [n for n in names if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)]
        elif filetype in ("xlsx", "xlsm"):
            found = [n for n in names if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", n)]
        elif filetype in ("docx", "docm") and "word/document.xml" in names:
            body = archive.read("word/document.xml")
            return min(MAX_PAGES, 1 + body.count(b'w:type="page"'))
        else:
            return 1
    return min(MAX_PAGES, max(1, len(found)))


class Conversions:
    def __init__(self, fake: Any) -> None:
        self.fake = fake
        self.cache: dict[str, Conversion] = {}
        self.reset()

    def reset(self) -> None:
        self.fail: list[int] = []
        self.polls = 1
        self.host: str | None = None
        self.wrong_key = False
        self.not_pdf = False
        self.pages: int | None = None
        self.hold: asyncio.Event | None = None
        self.started = 0  # zoveel conversies begonnen (voor tests)

    def forget(self, _reason: str = "") -> None:
        for job in self.cache.values():
            if job.task is not None:
                job.task.cancel()
        self.cache.clear()

    def config(self) -> dict:
        return {
            "fail": list(self.fail),
            "hold": self.hold is not None and not self.hold.is_set(),
            "polls": self.polls,
            "host": self.host,
            "wrong_key": self.wrong_key,
            "not_pdf": self.not_pdf,
            "pages": self.pages,
            "started": self.started,
            "keys": list(self.cache),
        }

    def configure(self, body: dict) -> dict:
        if body.get("reset"):
            self.reset()
            self.forget("reset")
        if isinstance(body.get("fail"), list):
            self.fail = [int(c) for c in body["fail"]]
        if "hold" in body:
            if body["hold"]:
                self.hold = asyncio.Event()
            elif self.hold is not None:
                self.hold.set()
                self.hold = None
        if isinstance(body.get("polls"), int):
            self.polls = max(0, body["polls"])
        if "host" in body:
            self.host = body["host"] or None
        for name in ("wrong_key", "not_pdf"):
            if name in body:
                setattr(self, name, bool(body[name]))
        if "pages" in body:
            self.pages = int(body["pages"]) if body["pages"] else None
        return self.config()

    def _base(self, request: Request) -> str:
        host = self.host or request.headers.get("host") or "localhost"
        return f"{request.url.scheme}://{host}"

    async def handle(self, request: Request, params: dict) -> Response:
        key = params.get("key")
        if not isinstance(key, str) or not key or not isinstance(params.get("url"), str):
            return _error(request, -1)
        job = self.cache.get(key)
        if job is None:
            job = Conversion(key=key, polls=self.polls)
            self.cache[key] = job
            self.started += 1
            job.task = self.fake.spawn(self._run(job, params, self._base(request)))
        if not params.get("async"):
            await job.done.wait()
        elif not job.done.is_set() or job.polls > 0:
            job.polls -= 1
            return JSONResponse({"percent": 0, "endConvert": False})
        assert job.result is not None
        if "error" in job.result:
            return _error(request, job.result["error"])
        return JSONResponse(job.result)

    async def _run(self, job: Conversion, params: dict, base: str) -> None:
        try:
            job.result = await self._convert(job, params, base)
        finally:
            if job.result is None:
                job.result = {"error": -1}
            job.done.set()

    async def _convert(self, job: Conversion, params: dict, base: str) -> dict:
        if self.fail:
            return {"error": self.fail.pop(0)}
        filetype = str(params.get("filetype") or "").lower()
        output = str(params.get("outputtype") or "").lower()
        if output != "pdf":  # alleen PDF; docx naar doc -3, pptx naar ppt -7 (§3.6)
            return {"error": -7 if filetype in ("pptx", "ppt") else -3}
        url = params["url"]
        token = protocol.download_token(self.fake.secret, url)
        headers = {**protocol.DOWNLOAD_HEADERS, "Authorization": f"Bearer {token}"}
        try:
            r = await self.fake.http.get(url, headers=headers)
        except (httpx.HTTPError, httpx.InvalidURL, ValueError) as exc:  # ValueError: ASGI
            self.fake.record("convert_source", key=job.key, http=None, error=type(exc).__name__)
            return {"error": -4}
        digest = r.headers.get("repr-digest")
        self.fake.record(
            "convert_source", key=job.key, http=r.status_code, bytes=len(r.content), digest=digest
        )
        if r.status_code != 200 or not protocol.repr_digest_ok(digest, r.content):
            return {"error": -4}
        if self.hold is not None:  # de bron is binnen; DS is nu "bezig"
            await self.hold.wait()
        pages = self.pages or pages_of(filetype, r.content)
        title = str(params.get("title") or "output")
        output = b"geen pdf" if self.not_pdf else pdf.make_pdf(pages, title)
        stem = STEM.sub("_", PurePosixPath(title).stem)[:60] or "output"
        folder = self.fake.next_part(f"conv_{'ander' if self.wrong_key else job.key}")
        self.fake.files[folder] = {"output.pdf": output}
        path = f"/cache/files/data/{folder}/output.pdf/{stem}.pdf"
        link = protocol.secure_link(base, self.fake.link_secret, path, job.key, f"{stem}.pdf")
        return {"fileUrl": link, "fileType": "pdf", "percent": 100, "endConvert": True}

    async def serve(self, part: str, filename: str, request: Request) -> Response:
        """GET /cache/files/data/conv_<key>_<n>/output.pdf/<stam>.pdf (een secure link)."""
        if not self.fake.up:
            return Response(status_code=503)
        q = request.query_params
        path = request.url.path
        if not protocol.link_ok(
            self.fake.link_secret, path, q.get("md5", ""), q.get("expires", "")
        ):
            return Response(status_code=403)
        content = self.fake.files.get(part, {}).get("output.pdf")
        self.fake.record("convert_file", part=part, http=200 if content else 404)
        if content is None or not filename.endswith(".pdf"):
            return Response(status_code=404)
        return Response(content, media_type="application/pdf")


def _error(request: Request, code: int) -> Response:
    from .app import convert_error  # app.py importeert dit deel pas bij create_app

    return convert_error(request, code)


def install(fake: Any, app: FastAPI) -> None:
    conversions = Conversions(fake)
    fake.conversions = conversions
    fake.convert = conversions.handle
    fake.on_reset.append(conversions.forget)

    async def conv_file(part: str, filename: str, request: Request) -> Response:
        return await conversions.serve(part, filename, request)

    # Vóór de algemene route /cache/files/data/{part}/{name}/{filename} van de kern: die eist
    # dat de laatste twee delen gelijk zijn, en bij een conversie zijn ze dat niet
    app.add_api_route("/cache/files/data/{part}/output.pdf/{filename}", conv_file, methods=["GET"])
    app.router.routes.insert(0, app.router.routes.pop())

    async def fake_convert(request: Request) -> dict:
        try:
            body = await request.json()
        except ValueError:
            body = {}
        return conversions.configure(body if isinstance(body, dict) else {})

    app.add_api_route("/__fake/convert", fake_convert, methods=["POST"])
