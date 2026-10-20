import base64

from app import totp

# RFC 6238, bijlage B (SHA-1): het geheim is "12345678901234567890"
RFC_SECRET = base64.b32encode(b"12345678901234567890").decode()
RFC_VECTORS = {
    59: "287082",
    1111111109: "081804",
    1111111111: "050471",
    1234567890: "005924",
    2000000000: "279037",
    20000000000: "353130",
}


def test_rfc6238_vectoren():
    for t, code in RFC_VECTORS.items():
        assert totp.code_at(RFC_SECRET, t // 30) == code


def test_verify_venster_en_geen_hergebruik():
    secret = totp.new_secret()
    now = 1_700_000_000
    step = totp.current_step(now)
    code = totp.code_at(secret, step)
    assert totp.verify(secret, code, 0, now) == step
    assert totp.verify(secret, f" {code[:3]} {code[3:]} ", 0, now) == step  # spaties mogen
    assert totp.verify(secret, code, step, now) is None  # al gebruikt
    vorige = totp.code_at(secret, step - 1)
    assert totp.verify(secret, vorige, 0, now) == step - 1  # klok iets achter
    oud = totp.code_at(secret, step - 2)
    assert totp.verify(secret, oud, 0, now) is None
    assert totp.verify(secret, "12345", 0, now) is None
    assert totp.verify(secret, "abcdef", 0, now) is None


def test_geheim_en_uri():
    secret = totp.new_secret()
    assert len(secret) == 32 and "=" not in secret
    uri = totp.provisioning_uri(secret, "jonas@example.nl", "Platform")
    assert uri.startswith("otpauth://totp/Platform%3Ajonas%40example.nl?")
    assert f"secret={secret}" in uri and "issuer=Platform" in uri
    qr = totp.qr_data_uri(uri)
    assert qr.startswith("data:image/svg+xml") and "xmlns" in qr


def test_herstelcodes():
    codes = totp.new_recovery_codes()
    assert len(codes) == len(set(codes)) == 10
    assert all(len(c) == 11 and c[5] == "-" for c in codes)
    assert totp.hash_recovery_code(codes[0]) == totp.hash_recovery_code(codes[0].upper())
    assert totp.hash_recovery_code(codes[0]) == totp.hash_recovery_code(codes[0].replace("-", ""))
