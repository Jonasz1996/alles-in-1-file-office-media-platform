"""Keys voor de document server (Keuze 14 en 25).

document.key is de naam van een bewerksessie in DS: iedereen die tegelijk bewerkt, krijgt
dezelfde, en na het sluiten komt er een nieuwe (DS onthoudt een key en geeft bij hergebruik
de oude inhoud). De vorm volgt wat DS toestaat: hooguit 128 tekens uit [0-9A-Za-z._=-], en
(strenger dan DS) niet beginnend met een punt: een key staat als padsegment in adressen naar
DS, en "." of ".." mag daar nooit staan.

Een conversie heeft een eigen key per inhoud (sha) en per PDF_VERSION. DS geeft bij dezelfde
key het oude resultaat terug, ook voor een ander bestand: daarom staat de sha erin. Na een
fout waarbij DS een nieuwe key vraagt (-2 of -4) komt er "-a<n>" achter.
"""

import re
import secrets
import uuid

KEY_RE = re.compile(r"^[0-9A-Za-z_=-][0-9A-Za-z._=-]{0,127}$")

# Omhoog als de PDF's anders gemaakt moeten worden; dan krijgt elke blob een nieuwe PDF, een
# nieuwe ETag voor zijn pagina's en een nieuw adres voor zijn thumbnails (Keuze 25)
PDF_VERSION = 1


def new_key(node_id: uuid.UUID) -> str:
    """Een nieuwe document.key voor een bestand: het id plus 64 willekeurige bits."""
    return f"{node_id.hex}-{secrets.token_hex(8)}"


def convert_key(sha: str, n: int = 1) -> str:
    """De key van de conversie van inhoud sha; n is de poging met een nieuwe key (vanaf 2)."""
    key = f"p{PDF_VERSION}-{sha}"
    return key if n < 2 else f"{key}-a{n}"


def valid(key: str) -> bool:
    return bool(KEY_RE.fullmatch(key))
