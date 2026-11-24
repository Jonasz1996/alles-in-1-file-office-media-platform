"""De codes van "Wachtwoord vergeten of kan niet inloggen?" (zie ResetRequest).

Een code is willekeurig (80 bits), uitgegeven door de server en gekoppeld aan één openstaand
verzoek met een vervaltijd: er zit geen naam in die je kunt namaken. We bewaren alleen de
sha256. Het alfabet is dat van Crockford (geen I, L, O, U): goed voor te lezen en over te
typen, hoofdletters of niet, met of zonder streepjes.
"""

import hashlib
import re
import secrets

ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
PREFIX = "RESET"
GROUPS = 4
GROUP = 4
# Zo lang kan de beheerder een code gebruiken (uren)
VALID_HOURS = 24

_LOOKS_LIKE = str.maketrans({"O": "0", "I": "1", "L": "1"})
_CODE = re.compile(rf"^(?:{PREFIX})?([{ALPHABET}]{{{GROUPS * GROUP}}})$")


def new_code() -> str:
    """Een nieuwe code, bijvoorbeeld RESET-7KQ2-M9XD-4TNB-H3VA."""
    chars = "".join(secrets.choice(ALPHABET) for _ in range(GROUPS * GROUP))
    return "-".join([PREFIX, *(chars[i : i + GROUP] for i in range(0, len(chars), GROUP))])


def normalize(text: str) -> str | None:
    """De code zoals we hem hashen (RESET-XXXX-XXXX-XXXX-XXXX), of None als de tekst geen code
    is. Spaties, streepjes, kleine letters en O/I/L in plaats van 0/1 mogen."""
    plain = re.sub(r"[\s\-_.]", "", text).upper()
    if plain.startswith(PREFIX):
        plain = plain[len(PREFIX) :]
    plain = plain.translate(_LOOKS_LIKE)
    match = _CODE.match(plain)
    if match is None:
        return None
    chars = match.group(1)
    return "-".join([PREFIX, *(chars[i : i + GROUP] for i in range(0, len(chars), GROUP))])


def code_hash(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()
