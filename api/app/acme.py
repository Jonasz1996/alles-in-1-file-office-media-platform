"""Eigen ACME-client (RFC 8555) om certificaten bij Let's Encrypt aan te vragen.

Alleen wat het platform nodig heeft: één account-sleutel (EC P-256), één domein per
certificaat en de HTTP-01-challenge. Die challenge beantwoordt de app zelf op
/.well-known/acme-challenge/<token> via de dict `challenges`.
"""

import asyncio
import base64
import hashlib
import json
import logging
from dataclasses import dataclass

import httpx
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.x509.oid import NameOID

LETS_ENCRYPT = "https://acme-v02.api.letsencrypt.org/directory"
LETS_ENCRYPT_STAGING = "https://acme-staging-v02.api.letsencrypt.org/directory"

log = logging.getLogger("platform.acme")


class AcmeError(Exception):
    """Fout van de ACME-server, met de uitleg die die zelf meestuurt."""

    def __init__(self, message: str, problem: dict | None = None):
        super().__init__(message)
        self.problem = problem or {}

    @property
    def type(self) -> str:
        return self.problem.get("type", "")


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def new_key() -> ec.EllipticCurvePrivateKey:
    return ec.generate_private_key(ec.SECP256R1())


def key_to_pem(key: ec.EllipticCurvePrivateKey) -> bytes:
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )


def key_from_pem(pem: bytes) -> ec.EllipticCurvePrivateKey:
    key = serialization.load_pem_private_key(pem, password=None)
    if not isinstance(key, ec.EllipticCurvePrivateKey):
        raise ValueError("Account-sleutel moet een EC-sleutel zijn")
    return key


def jwk(key: ec.EllipticCurvePrivateKey) -> dict[str, str]:
    numbers = key.public_key().public_numbers()
    return {
        "crv": "P-256",
        "kty": "EC",
        "x": b64(numbers.x.to_bytes(32, "big")),
        "y": b64(numbers.y.to_bytes(32, "big")),
    }


def thumbprint(key: ec.EllipticCurvePrivateKey) -> str:
    """RFC 7638: SHA-256 over de JWK met gesorteerde sleutels en zonder spaties."""
    canonical = json.dumps(jwk(key), sort_keys=True, separators=(",", ":"))
    return b64(hashlib.sha256(canonical.encode()).digest())


def sign_es256(key: ec.EllipticCurvePrivateKey, data: bytes) -> bytes:
    """JWS wil r en s los achter elkaar (64 bytes), niet de DER-vorm van cryptography."""
    r, s = decode_dss_signature(key.sign(data, ec.ECDSA(hashes.SHA256())))
    return r.to_bytes(32, "big") + s.to_bytes(32, "big")


def csr_der(domain: str, key: ec.EllipticCurvePrivateKey) -> bytes:
    csr = (
        x509.CertificateSigningRequestBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, domain)]))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(domain)]), critical=False)
        .sign(key, hashes.SHA256())
    )
    return csr.public_bytes(serialization.Encoding.DER)


@dataclass
class Certificate:
    key_pem: bytes
    chain_pem: bytes


class AcmeClient:
    def __init__(
        self,
        directory_url: str,
        account_key: ec.EllipticCurvePrivateKey,
        http: httpx.AsyncClient,
        poll_interval: float = 2.0,
        poll_timeout: float = 120.0,
    ):
        self.directory_url = directory_url
        self.key = account_key
        self.http = http
        self.poll_interval = poll_interval
        self.poll_timeout = poll_timeout
        self.directory: dict = {}
        self.kid: str | None = None
        self._nonce: str | None = None

    async def _directory(self) -> dict:
        if not self.directory:
            r = await self.http.get(self.directory_url)
            r.raise_for_status()
            self.directory = r.json()
        return self.directory

    async def _new_nonce(self) -> str:
        r = await self.http.head((await self._directory())["newNonce"])
        nonce = r.headers.get("Replay-Nonce")
        if not nonce:
            raise AcmeError("De ACME-server gaf geen nonce")
        return nonce

    async def post(self, url: str, payload: dict | None, accept: str | None = None):
        """Ondertekende POST. payload None = POST-as-GET (lege payload)."""
        for attempt in range(3):
            nonce = self._nonce or await self._new_nonce()
            self._nonce = None
            protected: dict = {"alg": "ES256", "nonce": nonce, "url": url}
            if self.kid:
                protected["kid"] = self.kid
            else:
                protected["jwk"] = jwk(self.key)
            header = b64(json.dumps(protected).encode())
            body = "" if payload is None else b64(json.dumps(payload).encode())
            signature = b64(sign_es256(self.key, f"{header}.{body}".encode()))
            headers = {"Content-Type": "application/jose+json"}
            if accept:
                headers["Accept"] = accept
            r = await self.http.post(
                url,
                content=json.dumps({"protected": header, "payload": body, "signature": signature}),
                headers=headers,
            )
            self._nonce = r.headers.get("Replay-Nonce")
            if r.status_code < 400:
                return r
            problem = _problem(r)
            if problem.get("type") == "urn:ietf:params:acme:error:badNonce" and attempt < 2:
                continue
            raise AcmeError(_describe(problem, r.status_code), problem)
        raise AcmeError("De ACME-server bleef de nonce weigeren")

    async def register(self, email: str | None) -> str:
        payload: dict = {"termsOfServiceAgreed": True}
        if email:
            payload["contact"] = [f"mailto:{email}"]
        r = await self.post((await self._directory())["newAccount"], payload)
        self.kid = r.headers["Location"]
        return self.kid

    async def _poll(self, url: str, done: set[str], what: str) -> dict:
        waited = 0.0
        while True:
            body = (await self.post(url, None)).json()
            status = body.get("status")
            if status in done:
                return body
            if status == "invalid":
                raise AcmeError(_invalid_reason(body, what), body.get("error"))
            if waited >= self.poll_timeout:
                raise AcmeError(f"{what}: geen antwoord van Let's Encrypt binnen de tijd")
            await asyncio.sleep(self.poll_interval)
            waited += self.poll_interval

    async def issue(self, domain: str, challenges: dict[str, str]) -> Certificate:
        """Vraagt een certificaat aan voor `domain`. Tijdens de validatie staat het
        antwoord op de challenge in `challenges` (token -> key authorization)."""
        if self.kid is None:
            raise AcmeError("Eerst registreren")
        directory = await self._directory()
        r = await self.post(
            directory["newOrder"], {"identifiers": [{"type": "dns", "value": domain}]}
        )
        order_url = r.headers["Location"]
        order = r.json()
        tokens: list[str] = []
        try:
            for authz_url in order["authorizations"]:
                authz = (await self.post(authz_url, None)).json()
                if authz.get("status") == "valid":
                    continue
                challenge = next(
                    (c for c in authz.get("challenges", []) if c.get("type") == "http-01"), None
                )
                if challenge is None:
                    raise AcmeError("Let's Encrypt biedt geen HTTP-01-challenge aan")
                token = challenge["token"]
                challenges[token] = f"{token}.{thumbprint(self.key)}"
                tokens.append(token)
                await self.post(challenge["url"], {})
                await self._poll(authz_url, {"valid"}, "Controle van het domein")
        finally:
            for token in tokens:
                challenges.pop(token, None)

        order = await self._poll(order_url, {"ready", "valid"}, "Bestelling")
        cert_key = new_key()
        if order["status"] == "ready":
            await self.post(order["finalize"], {"csr": b64(csr_der(domain, cert_key))})
            order = await self._poll(order_url, {"valid"}, "Certificaat maken")
        r = await self.post(order["certificate"], None, accept="application/pem-certificate-chain")
        return Certificate(key_pem=key_to_pem(cert_key), chain_pem=r.content)


def _problem(r: httpx.Response) -> dict:
    try:
        body = r.json()
    except ValueError:
        return {"detail": r.text[:300]}
    return body if isinstance(body, dict) else {}


def _describe(problem: dict, status: int) -> str:
    detail = problem.get("detail") or f"HTTP {status}"
    kind = problem.get("type", "").rsplit(":", 1)[-1]
    return f"{detail} ({kind})" if kind else detail


def _invalid_reason(body: dict, what: str) -> str:
    """Zoekt de echte reden op: die staat bij de mislukte challenge, niet bij de authz."""
    errors = [c["error"] for c in body.get("challenges", []) if c.get("error")]
    if body.get("error"):
        errors.append(body["error"])
    if errors:
        return f"{what} mislukt: {_describe(errors[0], 400)}"
    return f"{what} mislukt"
