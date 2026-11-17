"""De Nextcloud-endpoints die de officiële clients nodig hebben, naast WebDAV (dav.py).

- GET /status.php: "dit is een Nextcloud-server", zonder inloggen.
- De inlogflow v2: de client vraagt een flow aan, opent de inlogpagina in de browser en peilt
  tot er een app-wachtwoord klaarstaat. De pagina is gewone HTML met een formulier (e-mail,
  wachtwoord, 2FA-code): zo hoeft de web-app er niets voor te kennen.
- OCS: capabilities, de gebruiker (met quotum) en het app-wachtwoord intrekken bij uitloggen.
- /api/v1/account/app-passwords: zelf app-wachtwoorden maken (rclone), bekijken en intrekken.

Eigen code naar de publieke beschrijving van die API's; niets uit Nextcloud gekopieerd.
"""

import html
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select

from app import errors
from app.db import Session
from app.models import AppPassword, NcLoginFlow, User
from app.nc.auth import DavUser, new_app_password
from app.routers.auth import locked_until, second_factor, used_bytes
from app.security import CurrentUser, check_password, client_ip, hash_token, login_limiter
from app.useragent import device_label

router = APIRouter(include_in_schema=False)
# Niet in de OpenAPI: die blijft gelijk aan 0.8.0 (tests/uit080); de web-app kent deze routes zelf
api = APIRouter(prefix="/api/v1", tags=["account"], include_in_schema=False)

NC_VERSION = (30, 0, 0)
FLOW_TTL = timedelta(minutes=20)
PRODUCT = "Platform"


def base_url(request: Request) -> str:
    return str(request.base_url).rstrip("/")


@router.get("/status.php")
async def status() -> dict[str, Any]:
    major, minor, micro = NC_VERSION
    return {
        "installed": True,
        "maintenance": False,
        "needsDbUpgrade": False,
        "version": f"{major}.{minor}.{micro}.0",
        "versionstring": f"{major}.{minor}.{micro}",
        "edition": "",
        "productname": PRODUCT,
        "extendedSupport": False,
    }


# --- Inlogflow v2 -----------------------------------------------------------------------------


def client_name(user_agent: str | None) -> str:
    agent = user_agent or ""
    if "mirall" in agent.lower():
        return "Nextcloud desktop"
    if "nextcloud-android" in agent.lower():
        return "Nextcloud Android"
    if "nextcloud-ios" in agent.lower() or "nextcloud ios" in agent.lower():
        return "Nextcloud iOS"
    return device_label(agent) or "Onbekend apparaat"


async def drop_old_flows(session) -> None:
    await session.execute(
        delete(NcLoginFlow).where(NcLoginFlow.created_at < datetime.now(UTC) - FLOW_TTL)
    )


@router.post("/index.php/login/v2")
@router.post("/login/v2")
async def flow_start(request: Request, session: Session) -> dict[str, Any]:
    login_limiter.check("flow:" + client_ip(request))
    await drop_old_flows(session)
    poll, login = secrets.token_urlsafe(48), secrets.token_urlsafe(48)
    session.add(
        NcLoginFlow(
            poll_hash=hash_token(poll),
            login_hash=hash_token(login),
            client=client_name(request.headers.get("user-agent"))[:200],
        )
    )
    await session.commit()
    base = base_url(request)
    return {
        "poll": {"token": poll, "endpoint": f"{base}/index.php/login/v2/poll"},
        "login": f"{base}/index.php/login/v2/flow/{login}",
    }


@router.post("/index.php/login/v2/poll")
@router.post("/login/v2/poll")
async def flow_poll(request: Request, session: Session, token: Annotated[str, Form()]) -> Response:
    flow = await session.get(NcLoginFlow, hash_token(token))
    if flow is None or flow.created_at.replace(tzinfo=UTC) < datetime.now(UTC) - FLOW_TTL:
        return JSONResponse([], status_code=404)
    if flow.secret is None:
        return JSONResponse([], status_code=404)
    body = {"server": base_url(request), "loginName": flow.login_name, "appPassword": flow.secret}
    await session.delete(flow)
    await session.commit()
    return JSONResponse(body)


STYLE = """
body{font-family:system-ui,sans-serif;background:#f4f5f7;color:#1d2330;margin:0}
main{max-width:380px;margin:10vh auto;background:#fff;padding:28px;border-radius:12px;
box-shadow:0 2px 12px rgba(0,0,0,.08)}h1{font-size:1.3rem;margin:0 0 8px}
label{display:block;margin:14px 0 4px;font-size:.9rem}
input{width:100%;box-sizing:border-box;padding:9px;border:1px solid #c5cad3;border-radius:8px;
font-size:1rem}button{margin-top:20px;width:100%;padding:10px;border:0;border-radius:8px;
background:#2f5bd3;color:#fff;font-size:1rem;cursor:pointer}.fout{color:#b3261e}
.klein{font-size:.85rem;color:#5b6475}
@media (prefers-color-scheme:dark){body{background:#15181e;color:#e6e8ec}
main{background:#1f232b}input{background:#15181e;color:#e6e8ec;border-color:#3a404c}}
"""


@router.get("/index.php/login/v2/style.css")
async def flow_style() -> Response:
    return Response(STYLE, media_type="text/css", headers={"Cache-Control": "max-age=3600"})


def page(title: str, body: str, status: int = 200) -> HTMLResponse:
    return HTMLResponse(
        f"<!doctype html><html lang=nl><head><meta charset=utf-8>"
        f'<meta name=viewport content="width=device-width,initial-scale=1">'
        f"<title>{html.escape(title)}</title>"
        f'<link rel=stylesheet href="/index.php/login/v2/style.css"></head>'
        f"<body><main>{body}</main></body></html>",
        status_code=status,
        headers={
            "Content-Security-Policy": "default-src 'none'; style-src 'self'; form-action 'self'",
            "Cache-Control": "no-store",
        },
    )


def form(flow: NcLoginFlow, error: str = "", email: str = "") -> HTMLResponse:
    fout = f"<p class=fout role=alert>{html.escape(error)}</p>" if error else ""
    return page(
        "Apparaat koppelen",
        f"<h1>Apparaat koppelen</h1>"
        f"<p><b>{html.escape(flow.client)}</b> wil toegang tot je bestanden. Log in om dat "
        f"toe te staan.</p>{fout}"
        f"<form method=post><label for=e>E-mailadres</label>"
        f"<input id=e name=email type=email autocomplete=username required "
        f'value="{html.escape(email)}">'
        f"<label for=p>Wachtwoord</label>"
        f"<input id=p name=password type=password autocomplete=current-password required>"
        f"<label for=c>Code uit je authenticator-app (als 2FA aan staat)</label>"
        f"<input id=c name=code inputmode=numeric autocomplete=one-time-code>"
        f"<button type=submit>Toegang geven</button></form>"
        f"<p class=klein>Je krijgt een eigen app-wachtwoord voor dit apparaat. Intrekken kan "
        f"later via je account.</p>",
    )


async def live_flow(session, login_token: str) -> NcLoginFlow | None:
    flow = await session.scalar(
        select(NcLoginFlow).where(NcLoginFlow.login_hash == hash_token(login_token))
    )
    if flow is None or flow.created_at.replace(tzinfo=UTC) < datetime.now(UTC) - FLOW_TTL:
        return None
    return flow


GONE = "<h1>Verlopen</h1><p>Deze koppeling is verlopen of al gebruikt. Begin opnieuw in de app.</p>"


@router.get("/index.php/login/v2/flow/{login_token}")
@router.get("/login/v2/flow/{login_token}")
async def flow_page(login_token: str, session: Session) -> HTMLResponse:
    flow = await live_flow(session, login_token)
    if flow is None or flow.secret is not None:
        return page("Verlopen", GONE, 404)
    return form(flow)


@router.post("/index.php/login/v2/flow/{login_token}")
@router.post("/login/v2/flow/{login_token}")
async def flow_grant(
    login_token: str,
    request: Request,
    session: Session,
    email: Annotated[str, Form(max_length=320)],
    password: Annotated[str, Form(max_length=1024)],
    code: Annotated[str, Form(max_length=32)] = "",
) -> HTMLResponse:
    flow = await live_flow(session, login_token)
    if flow is None or flow.secret is not None:
        return page("Verlopen", GONE, 404)
    try:
        login_limiter.check(client_ip(request))
    except Exception:
        return form(flow, "Te veel pogingen; probeer het over een paar minuten opnieuw.", email)
    user = await session.scalar(select(User).where(func.lower(User.email) == email.lower()))
    ok = await check_password(user.password_hash if user else None, password)
    if user is None or not ok:
        return form(flow, "E-mailadres of wachtwoord klopt niet.", email)
    if user.totp_secret is not None:
        if locked_until(user):
            return form(flow, "Te veel foute codes; probeer het later opnieuw.", email)
        if not code.strip():
            return form(flow, "Vul ook de code uit je authenticator-app in.", email)
        if not await second_factor(session, user, request, code.strip()):
            return form(flow, "De code klopt niet.", email)
    if user.disabled_at is not None:
        return form(flow, "Je account is geblokkeerd; vraag je beheerder.", email)
    if user.role == "guest":
        return form(flow, "Een gastaccount heeft geen eigen bestanden om te synchroniseren.")
    flow = await live_flow(session, login_token)  # vers, na de commit van second_factor
    if flow is None or flow.secret is not None:
        return page("Verlopen", GONE, 404)
    secret = new_app_password()
    session.add(AppPassword(user_id=user.id, name=flow.client, token_hash=hash_token(secret)))
    flow.login_name, flow.secret = user.email, secret
    await session.commit()
    return page(
        "Gekoppeld",
        f"<h1>Gekoppeld</h1><p><b>{html.escape(flow.client)}</b> heeft nu toegang. Je kunt dit "
        f"venster sluiten en teruggaan naar de app.</p>",
    )


# --- OCS --------------------------------------------------------------------------------------


def ocs(request: Request, data: Any, status: int = 200, message: str = "OK") -> Response:
    v2 = "/v2.php/" in request.url.path
    ok = 200 <= status < 300
    meta = {
        "status": "ok" if ok else "failure",
        "statuscode": (200 if v2 else 100) if ok else status,
        "message": message,
    }
    body = {"ocs": {"meta": meta, "data": data}}
    if request.query_params.get("format") == "xml" or (
        "format" not in request.query_params
        and "application/json" not in request.headers.get("accept", "")
    ):
        # Oude clients zonder format=json: dezelfde gegevens als XML
        return Response(
            '<?xml version="1.0"?>\n' + to_xml("ocs", body["ocs"]),
            media_type="application/xml; charset=utf-8",
            status_code=status if v2 or not ok else 200,
        )
    return JSONResponse(body, status_code=status if v2 or not ok else 200)


def to_xml(tag: str, value: Any) -> str:
    if isinstance(value, dict):
        inner = "".join(to_xml(k, v) for k, v in value.items())
    elif isinstance(value, list):
        inner = "".join(to_xml("element", v) for v in value)
    elif isinstance(value, bool):
        inner = "1" if value else ""
    elif value is None:
        inner = ""
    else:
        inner = html.escape(str(value))
    return f"<{tag}>{inner}</{tag}>"


def capabilities() -> dict[str, Any]:
    major, minor, micro = NC_VERSION
    return {
        "version": {
            "major": major,
            "minor": minor,
            "micro": micro,
            "string": f"{major}.{minor}.{micro}",
            "edition": "",
            "extendedSupport": False,
        },
        "capabilities": {
            "core": {"pollinterval": 60, "webdav-root": "remote.php/webdav"},
            "dav": {"chunking": "1.0"},
            "files": {
                "bigfilechunking": True,
                "undelete": False,
                "versioning": False,
                "blacklisted_files": [".htaccess"],
            },
            "theming": {"name": PRODUCT, "url": "", "slogan": "", "color": "#2f5bd3"},
        },
    }


@router.get("/ocs/v1.php/cloud/capabilities")
@router.get("/ocs/v2.php/cloud/capabilities")
async def ocs_capabilities(request: Request) -> Response:
    return ocs(request, capabilities())


async def user_data(session, user: User) -> dict[str, Any]:
    used = await used_bytes(session, user.id)
    total = user.quota_bytes or 0
    quota: dict[str, Any] = {"used": used}
    if total:
        quota |= {"free": max(0, total - used), "total": total, "quota": total}
        quota["relative"] = round(used * 100 / total, 2)
    else:
        quota |= {"free": -3, "total": used, "quota": -3, "relative": 0}  # -3: onbeperkt
    return {
        "enabled": True,
        "id": user.email,
        "display-name": user.display_name,
        "displayname": user.display_name,
        "email": user.email,
        "quota": quota,
        "language": user.locale or "nl",
        "locale": user.locale or "nl",
        "groups": [],
    }


@router.get("/ocs/v1.php/cloud/user")
@router.get("/ocs/v2.php/cloud/user")
async def ocs_user(request: Request, user: DavUser, session: Session) -> Response:
    return ocs(request, await user_data(session, user))


@router.get("/ocs/v1.php/cloud/users/{login}")
@router.get("/ocs/v2.php/cloud/users/{login}")
async def ocs_user_by_id(request: Request, login: str, user: DavUser, session: Session) -> Response:
    if login.lower() not in (user.email.lower(), str(user.id)):
        return ocs(request, [], 404, "Niet gevonden")
    return ocs(request, await user_data(session, user))


@router.delete("/ocs/v1.php/core/apppassword")
@router.delete("/ocs/v2.php/core/apppassword")
async def ocs_drop_app_password(request: Request, user: DavUser, session: Session) -> Response:
    """Uitloggen in de client: zijn eigen app-wachtwoord vervalt."""
    row_id = getattr(request.state, "app_password_id", None)
    if row_id is not None:
        await session.execute(
            delete(AppPassword).where(AppPassword.id == row_id, AppPassword.user_id == user.id)
        )
        await session.commit()
    return ocs(request, [])


# --- App-wachtwoorden beheren (voor de web-app en rclone) -------------------------------------


class AppPasswordOut(BaseModel):
    id: uuid.UUID
    name: str
    created_at: datetime
    last_used_at: datetime | None


class NewAppPassword(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class AppPasswordMade(AppPasswordOut):
    # Alleen nu te zien; daarna nooit meer
    password: str
    login: str


@api.get("/account/app-passwords")
async def list_app_passwords(user: CurrentUser, session: Session) -> list[AppPasswordOut]:
    rows = await session.scalars(
        select(AppPassword).where(AppPassword.user_id == user.id).order_by(AppPassword.created_at)
    )
    return [AppPasswordOut.model_validate(r, from_attributes=True) for r in rows]


@api.post("/account/app-passwords", status_code=201)
async def make_app_password(
    body: NewAppPassword, user: CurrentUser, session: Session
) -> AppPasswordMade:
    if user.role == "guest":
        raise errors.fail(403, "guest", "Een gastaccount heeft geen eigen bestanden")
    secret = new_app_password()
    row = AppPassword(user_id=user.id, name=body.name.strip(), token_hash=hash_token(secret))
    session.add(row)
    await session.commit()
    return AppPasswordMade(
        id=row.id,
        name=row.name,
        created_at=row.created_at,
        last_used_at=None,
        password=secret,
        login=user.email,
    )


@api.delete("/account/app-passwords/{password_id}", status_code=204)
async def drop_app_password(password_id: uuid.UUID, user: CurrentUser, session: Session) -> None:
    result = await session.execute(
        delete(AppPassword).where(AppPassword.id == password_id, AppPassword.user_id == user.id)
    )
    await session.commit()
    if not result.rowcount:
        raise errors.fail(404, "not_found", "Niet gevonden")
