"""Achter een reverse proxy (PLATFORM_TRUSTED_PROXIES, 0.8.2): alleen dan gelooft uvicorn
X-Forwarded-For en -Proto, en alleen van die adressen. Zonder: nooit (er zit geen proxy voor)."""

from __future__ import annotations

import pytest
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from app import serve
from app.config import Settings


def _cfg(tmp_path, proxies: str):
    settings = Settings(data_dir=tmp_path, trusted_proxies=proxies)
    config = serve._config(settings, 8080)
    config.load()
    return config


def test_zonder_proxy_geen_forwarded_koppen(tmp_path):
    config = _cfg(tmp_path, "")
    assert config.proxy_headers is False
    assert not isinstance(config.loaded_app, ProxyHeadersMiddleware)


def test_met_proxy_alleen_van_die_adressen(tmp_path):
    config = _cfg(tmp_path, " 192.168.102.244, 192.168.102.0/24 ,")
    assert config.proxy_headers is True
    assert config.forwarded_allow_ips == ["192.168.102.244", "192.168.102.0/24"]
    assert isinstance(config.loaded_app, ProxyHeadersMiddleware)


@pytest.mark.anyio
async def test_het_ip_en_https_van_de_client_via_de_proxy(tmp_path):
    seen: dict = {}

    async def app(scope, receive, send):
        seen.update(client=scope["client"][0], scheme=scope["scheme"])

    mw = ProxyHeadersMiddleware(app, trusted_hosts=["192.168.102.244"])
    headers = [(b"x-forwarded-for", b"203.0.113.9"), (b"x-forwarded-proto", b"https")]
    scope = {"type": "http", "scheme": "http", "client": ("192.168.102.244", 5000),
             "headers": headers}  # fmt: skip
    await mw(scope, None, None)
    assert seen == {"client": "203.0.113.9", "scheme": "https"}
    # Van een ander adres: de koppen tellen niet
    scope = {**scope, "scheme": "http", "client": ("192.168.90.10", 5000)}
    await mw(scope, None, None)
    assert seen == {"client": "192.168.90.10", "scheme": "http"}
