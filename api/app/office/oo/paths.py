"""Het padbeleid van /office/ voor OnlyOffice (§3.10). O0-stub; O1 maakt het af.

classify(method, path, upgrade) -> PathClass | None: de klasse van een pad (met het prefix
/office), of None (geweigerd). key_of(path): de key in een pad van een sessie, documentdata
of editoractie. De stub kent nog niets: alles is geweigerd. De kern van proxy en tunnel
(O0) gebruikt dit niet; die werkt alleen met office_dev_ungated.
"""

from app.office.engine import PathClass


def classify(method: str, path: str, upgrade: bool) -> PathClass | None:
    return None


def key_of(path: str) -> str | None:
    return None
