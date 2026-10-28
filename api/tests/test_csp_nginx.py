"""F4 (fase 5, §3.5): media-src 'self' blob: in de CSP, op alle drie de plekken gelijk.

Zonder blob: speelt geen enkele MSE-video (video.src is een object-URL van MediaSource). De CSP
van de web-app staat in main.WEB_CSP, 5 keer in web/nginx.conf (het web-image van de opzet met
twee containers) en in WEB_CSP van de CI-job images, die beide images op dezelfde CSP
controleert. Deze test leest de 5 koppen uit nginx.conf en de waarde uit ci.yml, en eist dat ze
gelijk zijn aan main.WEB_CSP.
"""

import re
from pathlib import Path

from app.main import WEB_CSP

ROOT = Path(__file__).resolve().parents[2]
NGINX = ROOT / "web" / "nginx.conf"
CI = ROOT / ".github" / "workflows" / "ci.yml"

HEADER = re.compile(r'add_header\s+Content-Security-Policy\s+"([^"]*)"\s+always;')


def directives(csp: str) -> dict[str, list[str]]:
    out = {}
    for part in csp.split(";"):
        words = part.split()
        if words:
            out[words[0]] = words[1:]
    return out


def ci_web_csp() -> str:
    """WEB_CSP uit de job images: een gevouwen blok (>-), de regels met een spatie aan elkaar."""
    lines = CI.read_text().splitlines()
    for i, line in enumerate(lines):
        m = re.match(r"^(\s*)WEB_CSP:\s*>-\s*$", line)
        if m:
            indent = len(m.group(1))
            parts = []
            for follow in lines[i + 1 :]:
                if not follow.strip() or len(follow) - len(follow.lstrip()) <= indent:
                    break
                parts.append(follow.strip())
            return " ".join(parts)
    raise AssertionError("WEB_CSP staat niet in ci.yml")


def test_media_src_staat_in_web_csp():
    found = directives(WEB_CSP)
    assert found["media-src"] == ["'self'", "blob:"]
    # De rest bleef zoals in fase 3
    assert found["default-src"] == ["'self'"]
    assert found["img-src"] == ["'self'", "data:", "blob:"]
    assert found["style-src"] == ["'self'"]
    assert "unsafe-inline" not in WEB_CSP


def test_de_vijf_koppen_in_nginx_zijn_web_csp():
    headers = HEADER.findall(NGINX.read_text())
    assert len(headers) == 5, headers
    for value in headers:
        assert value == WEB_CSP


def test_de_ci_job_images_controleert_dezelfde_csp():
    assert ci_web_csp() == WEB_CSP
