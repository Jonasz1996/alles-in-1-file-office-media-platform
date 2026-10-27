"""Het padbeleid van /office/ voor OnlyOffice (§3.10, vast na de spike).

classify(method, path, upgrade) -> PathClass | None: de klasse van een pad zoals de browser het
stuurt (rauw, met procenttekens, met het prefix /office, zonder of met query), of None
(geweigerd: 404). key_of(path): de key in een pad van een sessie, documentdata of editoractie.
Beide zijn puur: geen instellingen, geen database.

Eerst normaliseren: het prefix /office eraf, één versiesegment (<V>, ^\\d+.\\d+.\\d+-<hex>$)
vooraan eraf, en hoofdletters van de mappen maken niet uit. Geweigerd, wat er verder ook staat:
'.', '..', een leeg segment (alleen een '/' op het eind mag), '\\', NUL en andere
controletekens, een '%' zonder twee hex-tekens, en procentcodes die een '/', '\\' of '.' worden.

De klassen (§3.10; A3, A4, A5, A14, A20 van de spike):
- STATIC (GET, HEAD): web-apps/, sdkjs/, sdkjs-plugins/, fonts/, dictionaries/ (alles behalve
  HTML en XML), document_editor_service_worker.js, web-apps/apps/api/documents/api.js;
- PAGE (GET, HEAD): precies web-apps/apps/{documenteditor,spreadsheeteditor,presentationeditor}/
  {main,mobile}/index.html; elk ander HTML-pad is dicht (ook /<V>/index.html, preload.html);
- DATA (GET): cache/files/data/<key>_<n>/… en cache/files/data/<key>/…, nooit conv_… of
  forgotten;
- SESSION (alleen de upgrade): precies doc/<key>/c/. Polling (GET of POST zonder upgrade) is
  dicht: de pakketten van polling zitten in bodies die de StreamGuard niet leest, en zonder die
  controle opent wie lid is van A met een oud token sessie B (M15). De editor verbindt altijd
  meteen met transport=websocket [gemeten];
- SETTINGS (GET): themes.json, plugins.json;
- ACTION (POST): downloadas/<key> en upload/<key>.

action_authorization(settings, path, query, authorization) hoort bij ACTION (A2, A6): de query
mag alleen over de key van het pad gaan (shardkey, en cmd.id en cmd.tokenSession bij
downloadas), en op upload/<key> gaat Authorization alleen mee als het een geldig sessietoken
van die key is.
"""

import json
import re
from urllib.parse import parse_qs, unquote_to_bytes

from app.config import Settings
from app.office import keys
from app.office.engine import PathClass, TokenError
from app.office.oo import stream

PREFIX = "/office"
VERSION = re.compile(r"\d+\.\d+\.\d+-[0-9a-f]{8,64}")
EDITORS = frozenset({"documenteditor", "spreadsheeteditor", "presentationeditor"})
STATIC_ROOTS = frozenset({"web-apps", "sdkjs", "sdkjs-plugins", "fonts", "dictionaries"})
SERVICE_WORKER = "document_editor_service_worker.js"
SETTINGS = frozenset({"themes.json", "plugins.json"})
# Wat een browser als pagina met scripts kan tonen: nooit statisch
MARKUP = (".html", ".htm", ".xhtml", ".xht", ".shtml", ".mht", ".mhtml", ".xml", ".xsl", ".xslt")
DATA_PART = re.compile(r"(.+)_(\d{1,9})")
BAD_PERCENT = re.compile(r"%(?:2e|2f|5c)|%(?![0-9a-f]{2})", re.IGNORECASE)
# Velden van cmd (downloadas) met een token; elk moet over de key van het pad gaan
CMD_TOKENS = ("tokenSession", "jwtSession", "jwtOpen", "jwt", "token", "session")


def _segments(path: str) -> tuple[list[str], list[str]] | None:
    """De segmenten na /office (zonder versiesegment), gedecodeerd en zoals ze binnenkwamen,
    of None als het pad niet mag. Een '/' op het eind geeft een leeg laatste segment."""
    path = path.split("?", 1)[0]
    if not path.startswith(PREFIX + "/"):
        return None
    raw = path[len(PREFIX) + 1 :].split("/")
    out: list[str] = []
    for i, segment in enumerate(raw):
        if segment == "":
            if i == len(raw) - 1 and i > 0:
                out.append("")
                continue
            return None
        if "\\" in segment or BAD_PERCENT.search(segment):
            return None
        decoded = unquote_to_bytes(segment)
        if decoded in (b".", b"..") or any(b < 0x20 or b == 0x7F for b in decoded):
            return None
        if b"/" in decoded or b"\\" in decoded:
            return None
        try:
            out.append(decoded.decode("utf-8"))
        except UnicodeDecodeError:
            return None
    if out and VERSION.fullmatch(raw[0]):
        out, raw = out[1:], raw[1:]
    return (out, raw) if out else None


def _data_key(part: str) -> str | None:
    """De key in cache/files/data/<key>_<n> of cache/files/data/<key>. Een key met '_' telt
    hier niet (die geven wij nooit uit): anders was 'a_1' zowel key 'a_1' als key 'a', deel 1."""
    match = DATA_PART.fullmatch(part)
    key = match.group(1) if match else part
    return key if keys.valid(key) and "_" not in key else None


def _parse(path: str) -> tuple[list[str], list[str], list[str]] | None:
    """(gedecodeerd, in kleine letters, zoals binnengekomen), of None."""
    parsed = _segments(path)
    if parsed is None:
        return None
    found, raw = parsed
    return found, [s.lower() for s in found], raw


def _key(found: list[str], low: list[str], raw: list[str]) -> str | None:
    """De key in het pad; een key in procenttekens telt niet (DS en wij zouden hem anders
    kunnen lezen)."""
    if len(found) == 4 and low[0] == "doc" and low[2] == "c" and found[3] == "":
        return found[1] if raw[1] == found[1] and keys.valid(found[1]) else None
    if len(found) >= 5 and low[:3] == ["cache", "files", "data"]:
        if any(s == "" for s in found[4:]) or raw[3] != found[3]:
            return None
        return _data_key(found[3])
    if len(found) == 2 and low[0] in ("downloadas", "upload"):
        return found[1] if raw[1] == found[1] and keys.valid(found[1]) else None
    return None


def classify(method: str, path: str, upgrade: bool) -> PathClass | None:
    parsed = _parse(path)
    if parsed is None:
        return None
    found, low, raw = parsed
    method = method.upper()
    key = _key(found, low, raw)
    if upgrade:
        if method == "GET" and low[0] == "doc" and key is not None:
            return PathClass.SESSION
        return None
    if low[0] == "doc":
        return None  # polling: dicht (zie boven)
    if low[0] == "cache":
        return PathClass.DATA if method == "GET" and key is not None else None
    if low[0] in ("downloadas", "upload"):
        return PathClass.ACTION if method == "POST" and key is not None else None
    if method not in ("GET", "HEAD"):
        return None
    last = low[-1]
    if last.endswith(MARKUP):
        if len(low) == 5 and low[:2] == ["web-apps", "apps"] and low[2] in EDITORS:
            if low[3] in ("main", "mobile") and last == "index.html":
                return PathClass.PAGE
        return None
    if len(low) == 1 and last in SETTINGS:
        return PathClass.SETTINGS if method == "GET" else None
    if low == [SERVICE_WORKER]:
        return PathClass.STATIC
    if len(low) >= 2 and low[0] in STATIC_ROOTS and last != "":
        return PathClass.STATIC
    return None


def key_of(path: str) -> str | None:
    parsed = _parse(path)
    return _key(*parsed) if parsed is not None else None


def _bearer(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, _, value = authorization.strip().partition(" ")
    value = value.strip()
    if scheme.lower() != "bearer" or not value or " " in value:
        return None
    return value


def action_authorization(
    settings: Settings, path: str, query: str, authorization: str | None
) -> str | None:
    """Een editoractie (ACTION): TokenError als de query over een andere key gaat dan het pad;
    anders de Authorization die mee mag naar DS (alleen op upload/<key>, en alleen een geldig
    sessietoken van die key), of None."""
    parsed = _parse(path)
    key = _key(*parsed) if parsed is not None else None
    if parsed is None or key is None or parsed[1][0] not in ("downloadas", "upload"):
        raise TokenError("geen editoractie")
    try:
        params = parse_qs(query, keep_blank_values=True, strict_parsing=False)
    except ValueError as exc:
        raise TokenError("query") from exc
    if params.get("shardkey", [key]) != [key]:
        raise TokenError("andere shardkey")
    if parsed[1][0] == "downloadas":
        commands = params.get("cmd")
        if commands is None:
            return None
        if len(commands) != 1:
            raise TokenError("cmd twee keer")
        try:
            cmd = json.loads(commands[0])
        except ValueError as exc:
            raise TokenError("cmd") from exc
        if not isinstance(cmd, dict) or cmd.get("id") != key:
            raise TokenError("cmd over een andere key")
        for name in CMD_TOKENS:
            if name in cmd and stream.token_key(settings, cmd[name], check_exp=True) != key:
                raise TokenError("token van een andere key")
        return None
    token = _bearer(authorization)
    if token is None:
        return None
    if stream.token_key(settings, token, session_only=True, check_exp=True) != key:
        return None
    return f"Bearer {token}"
