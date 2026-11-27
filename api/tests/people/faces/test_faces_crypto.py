"""De versleuteling van de vectoren (fase 6, Keuze 68; X2b): AES-256-GCM met een sleutel per
eigenaar in PLATFORM_FACES_KEYS, buiten de datamap; vec is 540 B met de id van het gezicht als AAD.

- seal/open_vec: 540 B, de vector terug; een andere id, een andere sleutel, een kapotte of te
  korte vec, of geen sleutel: None (telt als gewist);
- ensure_key: 32 B, 0600, één bestand per eigenaar, de tweede keer dezelfde; geen map: NoKeys;
- delete_key: weg (en fsync van de map), de tweede keer False;
- na "uit" (sleutel weg) gaat geen vec van die eigenaar meer open en krijgt group niets van hem.
"""

from __future__ import annotations

import os
import stat
import uuid

import pytest
from sqlalchemy import select

from app.analyse import wire
from app.models import Face
from app.people.faces import crypto, group
from tests.analyse import faces_nep
from tests.factories import create_user, maker
from tests.people.faces.conftest import aan, draai, foto

pytestmark = pytest.mark.anyio

BOX = (0.1, 0.1, 0.2, 0.25)


def test_seal_en_open_met_de_id_als_aad():
    key = os.urandom(crypto.KEY_BYTES)
    face = uuid.uuid4()
    vec = faces_nep.vector(1, 1)
    sealed = crypto.seal(key, face, vec)
    assert len(sealed) == crypto.VEC_BYTES == 540
    assert vec not in sealed
    assert crypto.open_vec(key, face, sealed) == vec
    # Twee keer verzegelen: een andere nonce, andere bytes
    assert crypto.seal(key, face, vec) != sealed
    # Een andere rij, een andere sleutel, kapot, te kort of niets: None
    assert crypto.open_vec(key, uuid.uuid4(), sealed) is None
    assert crypto.open_vec(os.urandom(32), face, sealed) is None
    broken = bytearray(sealed)
    broken[20] ^= 1
    assert crypto.open_vec(key, face, bytes(broken)) is None
    assert crypto.open_vec(key, face, sealed[:-1]) is None
    assert crypto.open_vec(None, face, sealed) is None
    assert crypto.open_vec(key, face, None) is None
    with pytest.raises(ValueError):
        crypto.seal(key, face, vec[:-4])


async def test_een_sleutel_per_eigenaar_0600_buiten_de_datamap(settings):
    owner = uuid.uuid4()
    keys = crypto.keys_dir(settings)
    assert not keys.is_relative_to(settings.data_dir)
    assert crypto.load_key(settings, owner) is None
    async with maker(settings)() as db:
        key = await crypto.ensure_key(settings, db, owner)
        path = crypto.key_path(settings, owner)
        assert path.parent == keys and path.name == f"{owner.hex}.key"
        assert len(key) == crypto.KEY_BYTES == 32
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert await crypto.ensure_key(settings, db, owner) == key
        assert key == crypto.load_key(settings, owner)
        other = uuid.uuid4()
        assert await crypto.ensure_key(settings, db, other) != key
    assert crypto.delete_key(settings, owner) is True
    assert not path.exists()
    assert crypto.delete_key(settings, owner) is False
    assert crypto.load_key(settings, owner) is None
    assert crypto.load_key(settings, other) is not None


async def test_zonder_map_geen_sleutel(settings, tmp_path):
    zonder = settings.model_copy(update={"faces_keys": ""})
    async with maker(settings)() as db:
        with pytest.raises(crypto.NoKeys):
            await crypto.ensure_key(zonder, db, uuid.uuid4())
        assert crypto.delete_key(zonder, uuid.uuid4()) is False
        weg = settings.model_copy(update={"faces_keys": str(tmp_path / "bestaat-niet")})
        with pytest.raises(crypto.NoKeys):
            await crypto.ensure_key(weg, db, uuid.uuid4())
    assert not (tmp_path / "bestaat-niet").exists()


async def test_een_kapotte_sleutel_telt_als_geen(settings):
    owner = uuid.uuid4()
    crypto.key_path(settings, owner).write_bytes(b"te kort")
    assert crypto.load_key(settings, owner) is None


async def test_zonder_sleutel_gaat_niets_meer_open(settings, nep):
    """Twee eigenaren met gezichten; de sleutel van Eva weg: haar rijen gaan niet meer open en
    group krijgt niets van haar; die van Bram wel."""
    async with maker(settings)() as session:
        eva = (await create_user(session, "eva@example.nl")).id
        bram = (await create_user(session, "bram@example.nl")).id
        await session.commit()
    await foto(settings, nep, eva, "eva.jpg", [(BOX, 1)])
    await foto(settings, nep, bram, "bram.jpg", [(BOX, 2)])
    await aan(settings, eva, bram)
    await draai(settings)
    async with maker(settings)() as session:
        rows = {f.owner_id: f for f in await session.scalars(select(Face))}
    assert set(rows) == {eva, bram}
    # Een vec van Eva in de rij van Bram (zelfde sleutel niet, andere id): gaat niet open
    bram_key = crypto.load_key(settings, bram)
    assert crypto.open_vec(bram_key, rows[bram].id, rows[eva].vec) is None
    eva_key = crypto.load_key(settings, eva)
    assert crypto.open_vec(eva_key, rows[bram].id, rows[eva].vec) is None
    assert crypto.open_vec(eva_key, rows[eva].id, rows[eva].vec) is not None
    assert crypto.delete_key(settings, eva)
    assert crypto.open_vec(crypto.load_key(settings, eva), rows[eva].id, rows[eva].vec) is None
    known, opened, persons, rejects = await group._read(settings, eva, None)
    assert (known, opened, persons, rejects) == ([], [], [], [])
    _k, opened, _p, _r = await group._read(settings, bram, None)
    assert [f for f, _v in opened] == [rows[bram].id]
    before = nep.groups
    assert await group.run_group(settings, eva, "full") == 0
    assert nep.groups == before
    assert len(opened[0][1]) == wire.FACE_VEC_BYTES
