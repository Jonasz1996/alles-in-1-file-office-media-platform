"""Het padbeleid van /office/ (§3.10, oo/paths.py): elke klasse, het versieprefix, hoofdletters,
procentcodes en dubbele slashes, de beheerpaden van DS (ook achter een versieprefix), /office/
zelf, elk HTML-pad, en alle paden uit de proxylogs van de spike (tests/fixtures/ds_paths.txt).
Plus de query en Authorization van een editoractie (A2, A6)."""

import json
import secrets
import time
from pathlib import Path
from urllib.parse import quote

import jwt
import pytest

from app.config import Settings
from app.office import proxy
from app.office.engine import PathClass, TokenError
from app.office.oo import paths, tokens

HERE = Path(__file__).parent
RECORDED = HERE / "fake_docserver" / "recorded" / "paths.json"
FIXTURE = HERE / "fixtures" / "ds_paths.txt"
V = "10.0.0-15700fb6e42e543223e10696b3066db9"
KEY = "4f3c2a1b0e9d8c7b6a5f4e3d2c1b0a99-0123456789abcdef"
OTHER = "aaaabbbbccccddddeeeeffff00001111-fedcba9876543210"


def fixture_lines() -> list[tuple[str, str, str, str]]:
    out = []
    for line in FIXTURE.read_text().splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        kind, method, pattern, path = line.split()
        out.append((kind, method, pattern, path))
    return out


def test_de_vaste_lijst_dekt_de_hele_opname():
    """Elk patroon uit de proxylogs van de spike staat in ds_paths.txt, met dezelfde klasse."""
    recorded = json.loads(RECORDED.read_text())["paden"]
    listed = {(method, pattern): kind for kind, method, pattern, _ in fixture_lines()}
    for entry in recorded:
        method = "UPGRADE" if entry["methode"] == "GET upgrade" else entry["methode"]
        wanted = entry["klasse"][0].lower()
        assert listed.get((method, entry["pad"])) == wanted, entry["pad"]
    assert len(recorded) >= 27


@pytest.mark.parametrize(
    ("kind", "method", "pattern", "path"), fixture_lines(), ids=lambda v: str(v)[:60]
)
def test_elk_pad_uit_de_spike_gaat_door(kind, method, pattern, path):
    upgrade = method == "UPGRADE"
    got = paths.classify("GET" if upgrade else method, path, upgrade)
    assert got == PathClass(kind), (path, got)
    if kind in ("data", "action", "session"):
        assert paths.key_of(path) == KEY
    else:
        assert paths.key_of(path) is None


DENIED_GET = [
    "/office",
    "/office/",
    "/office/index.html",
    f"/office/{V}/index.html",
    f"/office/{V}/",
    f"/office/{V}",
    "/office/welcome/",
    "/office/welcome/index.html",
    f"/office/{V}/web-apps/apps/api/documents/preload.html",
    f"/office/{V}/web-apps/apps/api/documents/cache-scripts.html",
    "/office/web-apps/apps/api/documents/preload.html",
    f"/office/{V}/web-apps/apps/documenteditor/main/resources/help/nl/Contents.htm",
    f"/office/{V}/web-apps/apps/documenteditor/main/index_loader.html",
    f"/office/{V}/web-apps/apps/documenteditor/embed/index.html",
    f"/office/{V}/web-apps/apps/documenteditor/main/x.xhtml",
    f"/office/{V}/web-apps/apps/documenteditor/main/x.xml",
    f"/office/{V}/web-apps/apps/documenteditor/main/x.svg.html",
    f"/office/{V}/printfile/{KEY}/output.pdf",
    f"/office/{V}/savefile/{KEY}",
    f"/office/{V}/downloadfile/{KEY}",
    f"/office/{V}/info/info.json",
    "/office/info/info.json",
    f"/office/{V}/internal/cluster/inactive",
    f"/office/{V}/hosting/discovery",
    "/office/hosting/wopi/files/x",
    "/office/example/",
    f"/office/{V}/example/",
    "/office/meta/formats",
    "/office/healthcheck",
    f"/office/{V}/healthcheck",
    "/office/version",
    f"/office/{V}/baseurl",
    "/office/robots.txt",
    "/office/favicon.ico",
    f"/office/{V}/plugins",
    f"/office/cache/files/forgotten/{KEY}/output.docx",
    f"/office/cache/files/data/conv_{KEY}/output.pdf",
    f"/office/cache/files/data/conv_{KEY}_7/output.pdf/x.pdf",
    f"/office/cache/files/data/{KEY}",
    f"/office/cache/files/data/{KEY}/",
    f"/office/cache/files/data/{KEY}/Editor.bin/",
    f"/office/cache/files/{KEY}/Editor.bin/Editor.bin",
    f"/office/{V}/web-apps",
    f"/office/{V}/web-apps/",
    f"/office/{V}/sdkjs/word/",
    f"/office/{V}/{V}/web-apps/apps/api/documents/api.js",  # één versiesegment, niet twee
    "/office/10.0.0-0123ABCD/web-apps/apps/api/documents/api.js",  # versie: kleine hex
]
DENIED_POST = [
    f"/office/{V}/command",
    "/office/command",
    "/office/coauthoring/CommandService.ashx",
    f"/office/{V}/coauthoring/CommandService.ashx",
    f"/office/{V}/converter",
    "/office/converter",
    f"/office/{V}/converter/from-file",
    f"/office/{V}/ConvertService.ashx",
    f"/office/{V}/docbuilder",
    f"/office/{V}/dummyCallback",
    f"/office/{V}/ai-proxy",
    f"/office/{V}/downloadas/",
    f"/office/{V}/downloadas/{KEY}/x",
    f"/office/{V}/upload/{KEY}/u1/1",
    f"/office/{V}/doc/{KEY}/c/?EIO=4&transport=polling",  # polling: dicht
    f"/office/{V}/sdkjs/word/sdk-all-min.js",  # POST op statisch
    f"/office/{V}/themes.json",
]


@pytest.mark.parametrize("path", DENIED_GET)
def test_dicht_met_get(path):
    for method in ("GET", "HEAD", "POST"):
        assert paths.classify(method, path, False) is None, (method, path)
    assert paths.classify("GET", path, True) is None


@pytest.mark.parametrize("path", DENIED_POST)
def test_dicht_met_post(path):
    for method in ("POST", "PUT", "DELETE", "PATCH"):
        assert paths.classify(method, path, False) is None, (method, path)


@pytest.mark.parametrize(
    "path",
    [
        f"/office/{V}/web-apps/../command",
        f"/office/{V}/web-apps/./apps/api/documents/api.js",
        f"/office/{V}/web-apps//apps/api/documents/api.js",
        f"//office/{V}/sdkjs/word/sdk-all-min.js",
        f"/office//{V}/sdkjs/word/sdk-all-min.js",
        f"/office/{V}/sdkjs/%2e%2e/%2e%2e/command",
        f"/office/{V}/sdkjs/%2E%2E/command",
        f"/office/{V}/sdkjs/word%2fsdk-all-min.js",
        f"/office/{V}/sdkjs/word%2Fsdk-all-min.js",
        f"/office/{V}/sdkjs/word%5csdk-all-min.js",
        f"/office/{V}/sdkjs/word\\sdk-all-min.js",
        f"/office/{V}/sdkjs/sdk%00.js",
        f"/office/{V}/sdkjs/sdk%0a.js",
        f"/office/{V}/sdkjs/sdk%7f.js",
        f"/office/{V}/sdkjs/sdk%zz.js",
        f"/office/{V}/sdkjs/sdk%2.js",
        f"/office/{V}/sdkjs/sdk-all-min%2ejs",
        f"/office/{V}/sdkjs/%ff.js",  # geen utf-8
        "/office%2fweb-apps/apps/api/documents/api.js",
        "/officeweb-apps/apps/api/documents/api.js",
        "/web-apps/apps/api/documents/api.js",
        f"/office/{V}/sdkjs/../web-apps/apps/documenteditor/main/index.html",
    ],
)
def test_normaliseren_weigert(path):
    for method in ("GET", "HEAD", "POST"):
        assert paths.classify(method, path, False) is None, (method, path)
    assert paths.key_of(path) is None


def test_een_dubbele_slash_komt_alleen_bij_statisch_door():
    """De presentatie-editor van DS 10.0.0.1 vraagt sdkjs/slide/themes//themes.js: web-apps zet
    SetThemesPath("../../../../sdkjs/slide/themes/") en sdkjs plakt er "/themes.js" achter. DS
    zelf geeft 200 (nginx voegt de slashes samen); via de app gaf het 404, en dan had een
    presentatie geen ingebouwde ontwerpen (metingen deel 1, bevinding 1).

    Het padbeleid blijft elk leeg segment weigeren. De proxy voegt de slashes samen
    (proxy.merged), vraagt het padbeleid opnieuw en laat alleen STATIC door, met het
    samengevoegde pad naar DS (o1.md, afwijking 19). Hier het deel van het padbeleid: voor elk
    pad uit de spike geeft een extra '/' op elke plek na het prefix None, en het samengevoegde
    pad weer precies de eigen klasse. Via '//' komt dus niets door wat zonder '//' dicht was,
    en alleen statisch telt."""
    themes = f"/office/{V}/sdkjs/slide/themes//themes.js"
    assert paths.classify("GET", themes, False) is None
    assert paths.key_of(themes) is None
    assert proxy.merged(themes) == f"/office/{V}/sdkjs/slide/themes/themes.js"
    assert paths.classify("GET", proxy.merged(themes), False) == PathClass.STATIC
    assert proxy.merged(f"/office//{V}///fonts//000") == f"/office/{V}/fonts/000"
    assert proxy.merged("/officex//a") == "/officex//a"  # niet van ons: blijft zoals het is
    checked = 0
    for kind, method, _, path in fixture_lines():
        upgrade = method == "UPGRADE"
        verb = "GET" if upgrade else method
        raw, sep, query = path.partition("?")
        for i, ch in enumerate(raw):
            if ch != "/" or i < len("/office"):
                continue
            doubled = raw[:i] + "/" + raw[i:]
            assert paths.classify(verb, doubled + sep + query, upgrade) is None, doubled
            again = proxy.merged(doubled)
            assert again == raw, doubled
            assert paths.classify(verb, again + sep + query, upgrade) == PathClass(kind)
            checked += 1
    assert checked > 100
    # Wat na samenvoegen nog steeds dicht of geen STATIC is
    for path, wanted in (
        (f"/office/{V}/sdkjs//../command", None),
        (f"/office/{V}/sdkjs//%2e%2e/command", None),
        (f"/office/{V}/sdkjs//word/a.xml", None),
        (f"/office/{V}/sdkjs/word//", None),
        ("/office//healthcheck", None),
        (f"/office/{V}//index.html", None),
        (f"/office/{V}/web-apps/apps/presentationeditor/main//index.html", PathClass.PAGE),
        (f"/office/{V}//themes.json", PathClass.SETTINGS),
        (f"/office/cache/files/data//{KEY}/Editor.bin/Editor.bin", PathClass.DATA),
    ):
        assert paths.classify("GET", path, False) is None, path
        assert paths.classify("GET", proxy.merged(path), False) == wanted, path
    sw = f"/office/{V}//document_editor_service_worker.js"
    assert paths.own_file(sw) is None
    assert paths.own_file(proxy.merged(sw)) == (
        "text/javascript; charset=utf-8",
        paths.OWN_SERVICE_WORKER,
    )


def test_versieprefix_en_hoofdletters():
    js = "/sdkjs/word/sdk-all-min.js"
    for prefix in ("", f"/{V}", "/1.2.3-0123abcd", "/10.0.0-" + "a" * 64):
        assert paths.classify("GET", "/office" + prefix + js, False) == PathClass.STATIC
    assert paths.classify("GET", "/office/10.0.0-" + "a" * 65 + js, False) is None
    assert paths.classify("GET", "/office/10.0-0123abcd" + js, False) is None
    # De mappen zonder hoofdletters: hetzelfde pad, dezelfde klasse
    page = f"/office/{V}/Web-Apps/Apps/DocumentEditor/Main/Index.HTML"
    assert paths.classify("GET", page, False) == PathClass.PAGE
    assert paths.classify("GET", f"/office/{V}/SDKJS/word/sdk.JS", False) == PathClass.STATIC
    assert paths.classify("GET", f"/office/{V}/Themes.JSON", False) == PathClass.SETTINGS
    assert paths.classify("POST", f"/office/{V}/DownloadAs/{KEY}", False) == PathClass.ACTION
    assert paths.classify("GET", f"/OFFICE/{V}/sdkjs/a.js", False) is None  # het prefix wel
    data = f"/office/CACHE/Files/DATA/{KEY}/Editor.bin/Editor.bin"
    assert paths.classify("GET", data, False) == PathClass.DATA
    assert paths.key_of(data) == KEY
    # De key zelf houdt zijn hoofdletters
    assert paths.key_of(f"/office/{V}/upload/AbC-1") == "AbC-1"
    # Wat voor een beheerpad van DS staat, ook in hoofdletters: dicht
    for name in ("COMMAND", "Healthcheck", "CoAuthoring/CommandService.ashx", "ConVerter"):
        assert paths.classify("POST", f"/office/{V}/{name}", False) is None, name
        assert paths.classify("GET", f"/office/{V}/{name}", False) is None, name


def test_elk_html_pad_is_page_of_dicht_nooit_statisch():
    roots = ["web-apps", "sdkjs", "sdkjs-plugins", "fonts", "dictionaries"]
    for root in roots:
        for name in ("x.html", "x.HTM", "a/b/index.html", "x.xhtml", "x.shtml", "x.mhtml"):
            got = paths.classify("GET", f"/office/{V}/{root}/{name}", False)
            assert got is None, (root, name)
    for editor in ("documenteditor", "spreadsheeteditor", "presentationeditor"):
        for kind in ("main", "mobile"):
            path = f"/office/{V}/web-apps/apps/{editor}/{kind}/index.html"
            assert paths.classify("GET", path, False) == PathClass.PAGE
            assert paths.classify("HEAD", path, False) == PathClass.PAGE
            assert paths.classify("POST", path, False) is None
            assert paths.classify("GET", path, True) is None
    assert (
        paths.classify("GET", f"/office/{V}/web-apps/apps/pdfeditor/main/index.html", False) is None
    )


def test_de_service_worker_is_van_de_app():
    """Op document_editor_service_worker.js een eigen service worker, nooit die van DS: die gaf
    de editorpagina en documentdata uit Cache Storage terug, buiten de poort (docs/fase-4/o1.md)."""
    for path in (
        f"/office/{V}/document_editor_service_worker.js",
        "/office/document_editor_service_worker.js",
        f"/office/{V}/Document_Editor_Service_Worker.JS?v=1",
    ):
        assert paths.classify("GET", path, False) == PathClass.STATIC, path
        kind, content = paths.own_file(path)
        assert kind == "text/javascript; charset=utf-8"
        assert content == paths.OWN_SERVICE_WORKER
    script = paths.OWN_SERVICE_WORKER.decode()
    assert "fetch" not in script  # geen fetch-handler: alles gaat langs de poort
    assert 'addEventListener("install", () => self.skipWaiting())' in script
    assert 'name.startsWith("document_editor_")' in script and "caches.delete(name)" in script
    for path in (
        f"/office/{V}/sdkjs/word/document_editor_service_worker.js",
        "/office/x/document_editor_service_worker.js",
        f"/office/{V}/web-apps/apps/api/documents/api.js",
        f"/office/{V}/web-apps/apps/documenteditor/main/index.html",
        f"/office/{V}/%2e%2e/document_editor_service_worker.js",
    ):
        assert paths.own_file(path) is None, path


def test_methoden_per_klasse():
    js = f"/office/{V}/sdkjs/word/sdk-all-min.js"
    data = f"/office/cache/files/data/{KEY}/Editor.bin/Editor.bin"
    action = f"/office/{V}/downloadas/{KEY}"
    themes = f"/office/{V}/themes.json"
    assert paths.classify("HEAD", js, False) == PathClass.STATIC
    assert paths.classify("OPTIONS", js, False) is None
    assert paths.classify("PUT", js, False) is None
    assert paths.classify("HEAD", data, False) is None
    assert paths.classify("POST", data, False) is None
    assert paths.classify("GET", action, False) is None
    assert paths.classify("PUT", action, False) is None
    assert paths.classify("HEAD", themes, False) is None
    assert paths.classify("GET", f"/office/{V}/plugins.json", False) == PathClass.SETTINGS
    assert paths.classify("get", js, False) == PathClass.STATIC


def test_de_upgrade():
    socket = f"/office/{V}/doc/{KEY}/c/"
    assert paths.classify("GET", socket + "?EIO=4&transport=websocket", True) == PathClass.SESSION
    assert paths.classify("GET", f"/office/doc/{KEY}/c/", True) == PathClass.SESSION
    assert paths.key_of(socket) == KEY
    for bad in (
        f"/office/{V}/doc/{KEY}/c",
        f"/office/{V}/doc/{KEY}/c/x",
        f"/office/{V}/doc/{KEY}/c//",
        f"/office/{V}/doc/../c/",
        f"/office/{V}/doc/.k/c/",
        f"/office/{V}/doc/{'k' * 129}/c/",
        f"/office/{V}/doc/k%31/c/",
        f"/office/{V}/doc/k%2Fx/c/",
        f"/office/{V}/sdkjs/word/sdk-all-min.js",
        f"/office/{V}/downloadas/{KEY}",
    ):
        assert paths.classify("GET", bad, True) is None, bad
    assert paths.classify("POST", socket, True) is None
    # Zonder upgrade (polling): dicht
    assert paths.classify("GET", socket + "?EIO=4&transport=polling", False) is None
    assert paths.classify("POST", socket + "?EIO=4&transport=polling", False) is None


def test_key_van_documentdata():
    base = "/office/cache/files/data/"
    assert paths.key_of(f"{base}{KEY}_12/output.pdf/x.pdf") == KEY
    assert paths.key_of(f"{base}{KEY}/media/a.png/a.png") == KEY
    assert paths.key_of(f"{base}k1-x_0/output.pdf/x.pdf") == "k1-x"
    # Een key met '_' bestaat bij ons niet; anders was a_1 zowel key a_1 als key a, deel 1
    assert paths.key_of(f"{base}a_b/output.pdf/x.pdf") is None
    assert paths.key_of(f"{base}a_b_1/output.pdf/x.pdf") is None
    assert paths.key_of(f"{base}_1/output.pdf/x.pdf") is None
    assert paths.key_of(f"{base}.a_1/output.pdf/x.pdf") is None
    assert paths.classify("GET", f"{base}a_b/output.pdf/x.pdf", False) is None
    assert paths.key_of(f"/office/{V}/sdkjs/word/sdk-all-min.js") is None
    assert paths.key_of(f"/office/{V}/themes.json") is None
    assert paths.key_of("/office/") is None


# --- de query en Authorization van een editoractie ---


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        data_dir=tmp_path,
        jwt_secret="app-" + secrets.token_hex(16),
        office_url="http://office",
        office_app_url="http://platform:8081",
        office_secret=secrets.token_urlsafe(32),
    )


def session_token(settings: Settings, key: str, secret: str | None = None, **extra) -> str:
    now = int(time.time())
    claims = {"document": {"key": key}, "editorConfig": {"user": {"id": "u1"}}, **extra}
    claims.setdefault("iat", now)
    claims.setdefault("exp", now + 3600)
    return jwt.encode(claims, secret or tokens.session_secret(settings), algorithm="HS256")


def config_token(settings: Settings, key: str) -> str:
    return tokens.browser_token(settings, {"document": {"key": key}})


def query(**params) -> str:
    return "&".join(f"{k}={quote(v, safe='')}" for k, v in params.items())


def test_upload_geeft_alleen_een_geldig_sessietoken_door(settings):
    path = f"/office/{V}/upload/{KEY}"
    good = session_token(settings, KEY)
    q = query(shardkey=KEY)
    assert paths.action_authorization(settings, path, q, f"Bearer {good}") == f"Bearer {good}"
    assert paths.action_authorization(settings, path, q, f"bearer  {good} ") == f"Bearer {good}"
    for wrong in (
        None,
        "",
        "Bearer",
        f"Basic {good}",
        f"Bearer {good} extra",
        f"Bearer {session_token(settings, OTHER)}",  # een andere key
        f"Bearer {session_token(settings, KEY, secret=settings.office_secret + 'x')}",
        f"Bearer {config_token(settings, KEY)}",  # het config-token: geen sessietoken
        f"Bearer {session_token(settings, KEY, exp=int(time.time()) - 3600)}",  # verlopen
        f"Bearer {session_token(settings, KEY, operation='download')}",
        f"Bearer {session_token(settings, KEY, typ='office_gate')}",
        "Bearer kapot",
    ):
        assert paths.action_authorization(settings, path, q, wrong) is None, wrong


def test_query_van_een_editoractie(settings):
    upload = f"/office/{V}/upload/{KEY}"
    download = f"/office/{V}/downloadas/{KEY}"
    mine = session_token(settings, KEY)
    theirs = session_token(settings, OTHER)
    ok_cmd = json.dumps({"c": "save", "id": KEY, "userid": "u1", "tokenSession": mine})
    assert (
        paths.action_authorization(settings, download, query(cmd=ok_cmd, shardkey=KEY), None)
        is None
    )
    assert paths.action_authorization(settings, download, "", None) is None
    assert paths.action_authorization(settings, upload, "", None) is None
    for path, q in (
        (upload, query(shardkey=OTHER)),
        (upload, f"shardkey={KEY}&shardkey={OTHER}"),
        (download, query(shardkey=OTHER)),
        (download, query(cmd=json.dumps({"c": "save", "id": OTHER}))),
        (download, query(cmd=json.dumps({"c": "save"}))),
        (download, query(cmd=json.dumps({"c": "save", "id": KEY, "tokenSession": theirs}))),
        (download, query(cmd=json.dumps({"c": "save", "id": KEY, "tokenSession": "kapot"}))),
        (download, query(cmd=json.dumps({"c": "save", "id": KEY, "jwt": theirs}))),
        (download, query(cmd=json.dumps([KEY]))),
        (download, query(cmd="{kapot")),
        (download, f"cmd={quote(ok_cmd)}&cmd={quote(ok_cmd)}"),
    ):
        with pytest.raises(TokenError):
            paths.action_authorization(settings, path, q, None)
    with pytest.raises(TokenError):
        paths.action_authorization(settings, f"/office/{V}/sdkjs/a.js", "", None)
