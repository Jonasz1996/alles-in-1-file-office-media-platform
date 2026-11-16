"""Versleuteling van de vectoren, met een sleutel per eigenaar (fase 6, Keuze 68; X2b).

face.vec is 540 B: een nonce van 12 B, dan AES-256-GCM over de 512 B van de vector (128 ×
float32) met een tag van 16 B. De extra gegevens (AAD) zijn de id van het gezicht: een vec die
naar een andere rij gekopieerd wordt, gaat niet open.

De sleutels staan in PLATFORM_FACES_KEYS (/keys in de container, SLEUTELS_ROOT op de host), buiten
$DATA_ROOT: één bestand per eigenaar (<uuid hex>.key, 32 willekeurige bytes, 0600), gemaakt bij
de eerste vector. "Uit" haalt eerst de sleutel weg (unlink, dan fsync van de map): vanaf dan is
elke kopie van zijn vectoren onleesbaar, ook een snapshot of een kopie van de database van vóór
"uit". Een rij die niet open gaat (geen sleutel, een andere sleutel, kapot), telt als gewist.

Nooit een sleutel, vector of byte daarvan in een log, fout of antwoord: alleen aantallen.
"""

from __future__ import annotations

import os
import secrets
import uuid
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.analyse import wire
from app.config import Settings

NONCE = 12
TAG = 16
VEC_BYTES = NONCE + wire.FACE_VEC_BYTES + TAG  # 540
KEY_BYTES = 32


class NoKeys(Exception):
    """PLATFORM_FACES_KEYS ontbreekt of is geen map."""


def keys_dir(settings: Settings) -> Path:
    raw = settings.faces_keys.strip()
    if not raw:
        raise NoKeys
    return Path(raw)


def key_path(settings: Settings, owner: uuid.UUID) -> Path:
    return keys_dir(settings) / f"{owner.hex}.key"


def _fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def load_key(settings: Settings, owner: uuid.UUID) -> bytes | None:
    """De sleutel van owner, of None als hij er niet (meer) is."""
    try:
        data = key_path(settings, owner).read_bytes()
    except FileNotFoundError:
        return None
    return data if len(data) == KEY_BYTES else None


def ensure_key(settings: Settings, owner: uuid.UUID) -> bytes:
    """De sleutel van owner; de eerste keer een nieuwe (O_EXCL, 0600, met fsync)."""
    found = load_key(settings, owner)
    if found is not None:
        return found
    folder = keys_dir(settings)
    if not folder.is_dir():
        raise NoKeys
    path = key_path(settings, owner)
    key = secrets.token_bytes(KEY_BYTES)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    except FileExistsError:
        again = load_key(settings, owner)
        if again is None:
            raise NoKeys from None
        return again
    try:
        os.write(fd, key)
        os.fsync(fd)
    finally:
        os.close(fd)
    _fsync_dir(folder)
    return key


def delete_key(settings: Settings, owner: uuid.UUID) -> bool:
    """De sleutel van owner weg (unlink, dan fsync van de map). True als hij er was."""
    try:
        path = key_path(settings, owner)
    except NoKeys:
        return False
    try:
        path.unlink()
    except FileNotFoundError:
        return False
    _fsync_dir(path.parent)
    return True


def seal(key: bytes, face_id: uuid.UUID, vector: bytes) -> bytes:
    """540 B: nonce, dan AES-GCM over de vector met de id als AAD."""
    if len(vector) != wire.FACE_VEC_BYTES:
        raise ValueError("een vector is 512 B")
    nonce = secrets.token_bytes(NONCE)
    return nonce + AESGCM(key).encrypt(nonce, vector, face_id.bytes)


def open_vec(key: bytes | None, face_id: uuid.UUID, sealed: bytes | None) -> bytes | None:
    """De vector, of None: geen sleutel, geen vec, of hij gaat niet open (telt als gewist)."""
    if key is None or sealed is None or len(sealed) != VEC_BYTES:
        return None
    try:
        return AESGCM(key).decrypt(sealed[:NONCE], sealed[NONCE:], face_id.bytes)
    except InvalidTag:
        return None
