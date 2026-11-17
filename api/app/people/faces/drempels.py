"""De drempels van gezichten (fase 6, X2 "De drempels"; X2b). Voorlopig: laag 4
(app.cli analyse-kwaliteit op de LXC) meet welke waarden de poorten halen. Verandert er een, dan
gaat FACES_VERSION omhoog (version.py).
"""

from __future__ import annotations

T_LINK = 0.50  # een rand tussen twee open gezichten (groepen)
T_SUGGEST = 0.45  # een voorstel: strenger dan 0,363 van OpenCV voor SFace
MARGIN = 0.05  # de beste persoon ligt minstens zoveel boven de tweede
K = 10  # buren per gezicht
MIN_GROUP = 3  # een groep telt vanaf zoveel gezichten
SURE = T_SUGGEST + 0.10  # sure in FaceSuggest: de score is ≥ dit
IOU = 0.5  # een gezicht en een kader zijn hetzelfde vanaf deze overlap
SCORE = 0.9  # YuNet: de standaard van OpenCV
