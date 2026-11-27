"""Het bestand per eigenaar (bouwontwerp fase 6, X3; Keuzes 69, 76; K2-23; X3b).

- uit de rijen: de kop (model, versie, dim, aantal, gen), de num's op volgorde, de f16-matrix in
  die volgorde en per rij het begin van de sha256 van de inhoud (prefixes);
- met de foto's in de prullenbak; zonder die van een ander; zonder wat Keuze 69 uitsluit (een
  link nooit, een ander met beeld uit niet, zonder rij in node_origin wel); zonder een andere
  versie of een vector van de verkeerde lengte;
- os.replace: een nieuwe inode, een oude fd leest het oude bestand; geen tijdelijk bestand over,
  ook niet na een fout; een symlink als map wordt geweigerd; het opruimwerk haalt een oud
  tijdelijk bestand van een gestopte build weg;
- hooguit eens per PLATFORM_BEELD_REBUILD_S (5 minuten), en al na SOON_S (10 s) als de eigenaar
  net zocht en het bestand ouder is dan FRESH_S (30 s);
- 0.8.2 (P4): een bestand met een andere gen in de kop dan built_gen (een andere node maakte het,
  of de database kwam uit een kopie) is niet bij: een vraag, de job en het opruimwerk maken het
  opnieuw.
"""

from __future__ import annotations

import os
import stat
import time
import uuid
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app import beeld_rows
from app.analysis import VERSIONS, Step
from app.beeld import jobs as bjobs
from app.beeld import types as vt
from app.beeld import vecfile
from app.models import BeeldOwner, BlobEmbed, Job, Node, NodeOrigin
from tests.beeld import nep
from tests.beeld.nep import foto
from tests.test_files import mkdir, root
from tests.test_shares import share_with

pytestmark = pytest.mark.anyio


def lees(path) -> tuple[vt.Head, list[int], list[bytes], list[bytes]]:
    raw = path.read_bytes()
    head = vt.read_head(raw[: vt.HEAD.size])
    assert len(raw) == head.size()
    nums = [vt.NUM.unpack_from(raw, head.nums_at() + i * vt.NUM.size)[0] for i in range(head.count)]
    size = head.dim * 2
    vecs = [
        raw[head.vectors_at() + i * size : head.vectors_at() + (i + 1) * size]
        for i in range(head.count)
    ]
    shas = [
        raw[head.shas_at() + i * vt.SHA_BYTES : head.shas_at() + (i + 1) * vt.SHA_BYTES]
        for i in range(head.count)
    ]
    return head, nums, vecs, shas


async def _rijen(settings, user_id: str) -> list[tuple[int, bytes, str]]:
    """(num, vec, sha) van de beeldbestanden van deze eigenaar met een vector, op num."""
    async with nep.maker(settings)() as db:
        rows = await db.execute(
            sa.select(Node.num, BlobEmbed.vec, Node.blob_id)
            .join(BlobEmbed, BlobEmbed.blob_sha == Node.blob_id)
            .where(Node.owner_id == uuid.UUID(user_id), Node.ftype == "image")
            .order_by(Node.num)
        )
        return [(int(r[0]), bytes(r[1]), str(r[2])) for r in rows]


async def _bouw(settings, user_id: str) -> vecfile.Built:
    return await vecfile.build(nep.maker(settings), settings.beeld_dir, uuid.UUID(user_id))


async def test_het_bestand_uit_de_rijen(client, mensen, beeld):
    (eva, eva_id), _ = mensen
    fake, settings = beeld
    await nep.beeld_aan(client, eva)
    await nep.run_jobs(settings)
    for i, kleur in enumerate(("rood", "groen", "blauw", "paars")):
        await foto(client, eva, settings, f"{kleur}.jpg", kleur, seed=i)
    await nep.embed_all(settings)
    built = await _bouw(settings, eva_id)
    head, nums, vecs, shas = lees(built.path)
    owner = await nep.owner(settings, eva_id)
    assert owner is not None
    assert (head.model, head.version, head.dim, head.count, head.gen) == (
        vt.MODEL,
        VERSIONS[Step.EMBED],
        vt.DIM,
        4,
        owner.gen,
    )
    assert (built.rows, built.gen) == (4, owner.gen)
    assert (owner.built_gen, owner.built_rows) == (owner.gen, 4)
    assert nums == sorted(nums)
    rijen = await _rijen(settings, eva_id)
    assert [(n, v, vt.sha_prefix(s)) for n, v, s in rijen] == list(
        zip(nums, vecs, shas, strict=True)
    )
    # de matrix is float16, L2 = 1
    for vec in vecs:
        assert abs(sum(x * x for x in nep.unpack(vec)) - 1.0) < 1e-2
    # alleen voor de app: 0600 in een map van 0700
    assert stat.S_IMODE(built.path.stat().st_mode) == 0o600
    assert stat.S_IMODE(settings.beeld_dir.stat().st_mode) == 0o700
    # prefixes: voor de num's die erin staan, niet voor een andere
    fd = vecfile.open_for_worker(settings.beeld_dir, uuid.UUID(eva_id))
    assert fd is not None
    try:
        assert vecfile.prefixes(fd, [nums[2], nums[0], 10**9]) == {
            nums[2]: shas[2],
            nums[0]: shas[0],
        }
        assert vecfile.prefixes(fd, []) == {}
    finally:
        os.close(fd)
    # een bestand dat niet klopt (afgekapt): VecFileError, zonder iets uit het bestand
    kort = settings.beeld_dir / "kort.bin"
    kort.write_bytes(built.path.read_bytes()[:-1])
    fd = os.open(kort, os.O_RDONLY)
    try:
        with pytest.raises(vt.VecFileError) as fout:
            vecfile.prefixes(fd, [nums[0]])
        assert str(fout.value) == vt.BAD_FILE
    finally:
        os.close(fd)


async def test_prullenbak_ja_een_ander_nee_en_keuze_69(client, mensen, beeld):
    """In het bestand van Eva: haar foto in de prullenbak wel; die van Bram (zijn eigen map)
    niet; in haar map: van Bram met beeld uit niet, met beeld aan wel; via een link nooit;
    een node zonder rij in node_origin (van vóór 0011) wel."""
    (eva, eva_id), (bram, bram_id) = mensen
    fake, settings = beeld
    await nep.beeld_aan(client, eva)
    await nep.run_jobs(settings)
    rood = await foto(client, eva, settings, "rood.jpg", "rood")
    weg = await foto(client, eva, settings, "weg.jpg", "groen", seed=1)
    oud = await foto(client, eva, settings, "oud.jpg", "oranje", seed=2)
    await foto(client, bram, settings, "eigen.jpg", "paars", seed=3)
    eva_root = (await root(client, eva))["folder"]["id"]
    gedeeld = await mkdir(client, eva, eva_root, "Gedeeld")
    await share_with(client, eva, gedeeld, user_id=bram_id, permission="write")
    data = nep.photo(nep.KLEUREN["blauw"], seed=4)
    van_bram = await nep.upload(client, bram, "blauw.jpg", data, gedeeld)
    van_bram["sha"] = await nep.sha_of(settings, van_bram["id"])
    nep.preview(settings, van_bram["sha"], nep.KLEUREN["blauw"])
    await nep.beeld_aan(client, bram)
    await nep.run_jobs(settings)
    await nep.embed_all(settings)
    # daarna: weg in de prullenbak, rood alsof hij via een link kwam, oud van vóór 0011
    assert (await client.delete(f"/api/v1/files/{weg['id']}", headers=eva)).status_code == 204
    async with nep.maker(settings)() as db:
        # de link: zoals origin.py hem schrijft voor een upload via een link
        await db.execute(
            sa.update(NodeOrigin)
            .where(NodeOrigin.node_id == uuid.UUID(rood["id"]))
            .values(created_by=None, via_link=1)
        )
        await db.execute(sa.delete(NodeOrigin).where(NodeOrigin.node_id == uuid.UUID(oud["id"])))
        await db.commit()
    assert {rood["sha"], weg["sha"], oud["sha"], van_bram["sha"]} <= await nep.vectors(settings)

    async def in_bestand() -> set[str]:
        built = await _bouw(settings, eva_id)
        _, _, _, shas = lees(built.path)
        async with nep.maker(settings)() as db:
            rows = await db.execute(
                sa.select(Node.id, Node.blob_id).where(Node.owner_id == uuid.UUID(eva_id))
            )
            per_sha = {vt.sha_prefix(str(sha)): str(node) for node, sha in rows if sha}
        return {per_sha[s] for s in shas}

    assert await in_bestand() == {weg["id"], oud["id"], van_bram["id"]}
    await nep.beeld_aan(client, bram, "uit")
    await nep.run_jobs(settings)
    assert await in_bestand() == {weg["id"], oud["id"]}


async def test_zonder_een_andere_versie_of_lengte(client, mensen, beeld):
    (eva, eva_id), _ = mensen
    fake, settings = beeld
    await nep.beeld_aan(client, eva)
    await nep.run_jobs(settings)
    rood = await foto(client, eva, settings, "rood.jpg", "rood")
    groen = await foto(client, eva, settings, "groen.jpg", "groen", seed=1)
    blauw = await foto(client, eva, settings, "blauw.jpg", "blauw", seed=2)
    await nep.embed_all(settings)
    async with nep.maker(settings)() as db:
        await db.execute(
            sa.update(BlobEmbed)
            .where(BlobEmbed.blob_sha == rood["sha"])
            .values(version=VERSIONS[Step.EMBED] + 1)
        )
        await db.execute(
            sa.update(BlobEmbed).where(BlobEmbed.blob_sha == groen["sha"]).values(vec=b"\0" * 10)
        )
        await db.commit()
    built = await _bouw(settings, eva_id)
    head, _, _, shas = lees(built.path)
    assert (head.count, shas) == (1, [vt.sha_prefix(blauw["sha"])])


async def test_os_replace_een_nieuwe_inode_en_geen_tijdelijk_bestand(client, mensen, beeld):
    """Een tweede bestand krijgt een nieuwe inode (de cache van de worker onthoudt de sha256 per
    inode, K2-23); een fd op het oude bestand leest nog het oude. Na een fout halverwege staat
    het oude bestand er nog, zonder tijdelijk bestand, en beeld_owner is niet veranderd."""
    (eva, eva_id), _ = mensen
    fake, settings = beeld
    await nep.beeld_aan(client, eva)
    await nep.run_jobs(settings)
    await foto(client, eva, settings, "rood.jpg", "rood")
    await nep.embed_all(settings)
    eerste = await _bouw(settings, eva_id)
    fd = os.open(eerste.path, os.O_RDONLY)
    try:
        oud = os.fstat(fd).st_ino
        await foto(client, eva, settings, "groen.jpg", "groen", seed=1)
        await nep.backfill(settings)
        await nep.pump(settings)
        tweede = await _bouw(settings, eva_id)
        assert tweede.path == eerste.path
        assert tweede.path.stat().st_ino != oud
        assert vecfile.read_head_of(fd).count == 1
        assert lees(tweede.path)[0].count == 2
    finally:
        os.close(fd)
    assert sorted(p.name for p in settings.beeld_dir.iterdir()) == [tweede.path.name]

    voor = lees(tweede.path)
    stand = await nep.owner(settings, eva_id)
    await foto(client, eva, settings, "blauw.jpg", "blauw", seed=2)
    await nep.backfill(settings)
    await nep.pump(settings)
    echt = vt.sha_prefix

    def stuk(sha: str) -> bytes:
        raise OSError("de schijf is vol")

    vt.sha_prefix = stuk  # type: ignore[assignment]
    try:
        with pytest.raises(OSError):
            await _bouw(settings, eva_id)
    finally:
        vt.sha_prefix = echt  # type: ignore[assignment]
    assert lees(tweede.path) == voor
    assert sorted(p.name for p in settings.beeld_dir.iterdir()) == [tweede.path.name]
    na = await nep.owner(settings, eva_id)
    assert stand is not None and na is not None
    assert (na.built_gen, na.built_rows) == (stand.built_gen, stand.built_rows)


async def test_een_symlink_als_map_wordt_geweigerd(client, mensen, beeld, tmp_path):
    (eva, eva_id), _ = mensen
    fake, settings = beeld
    await nep.beeld_aan(client, eva)
    await nep.run_jobs(settings)
    elders = tmp_path / "elders"
    elders.mkdir()
    settings.beeld_dir.parent.mkdir(parents=True, exist_ok=True)
    settings.beeld_dir.symlink_to(elders, target_is_directory=True)
    with pytest.raises(OSError):
        await _bouw(settings, eva_id)
    assert list(elders.iterdir()) == []
    # het opruimwerk raakt een symlink ook niet
    (elders / f"{uuid.uuid4().hex}.vec").write_bytes(b"niet van ons")
    await bjobs.sweep(settings)
    assert len(list(elders.iterdir())) == 1


async def test_het_opruimwerk_haalt_een_oud_tijdelijk_bestand_weg(client, mensen, beeld):
    """Een build die stopte (de app ging uit) liet .<hex>.<hex>.tmp staan: na TMP_STALE_S weg;
    een jong tijdelijk bestand, een ander bestand of een symlink blijft."""
    (eva, eva_id), _ = mensen
    fake, settings = beeld
    await nep.beeld_aan(client, eva)
    await nep.run_jobs(settings)
    directory = vecfile.ensure_dir(settings.beeld_dir)
    oud = directory / f".{uuid.UUID(eva_id).hex}.{'a' * 12}.tmp"
    jong = directory / f".{uuid.UUID(eva_id).hex}.{'b' * 12}.tmp"
    anders = directory / ".iets.tmp"
    link = directory / f".{uuid.UUID(eva_id).hex}.{'c' * 12}.tmp"
    for path in (oud, jong, anders):
        path.write_bytes(b"half")
    link.symlink_to(anders)
    lang_geleden = time.time() - vecfile.TMP_STALE_S - 60
    for path in (oud, anders):
        os.utime(path, (lang_geleden, lang_geleden))
    os.utime(link, (lang_geleden, lang_geleden), follow_symlinks=False)
    assert vecfile.stale_tmp(oud) and not vecfile.stale_tmp(jong)
    await bjobs.sweep(settings)
    assert sorted(p.name for p in directory.iterdir()) == sorted(
        p.name for p in (jong, anders, link)
    )


async def test_is_current(client, mensen, beeld, monkeypatch):
    (eva, eva_id), _ = mensen
    fake, settings = beeld
    user = uuid.UUID(eva_id)
    assert not vecfile.is_current(settings.beeld_dir, user)
    await nep.beeld_aan(client, eva)
    await nep.run_jobs(settings)
    await _bouw(settings, eva_id)
    assert vecfile.is_current(settings.beeld_dir, user)
    monkeypatch.setitem(VERSIONS, Step.EMBED, VERSIONS[Step.EMBED] + 1)
    assert not vecfile.is_current(settings.beeld_dir, user)


async def test_een_andere_gen_in_de_kop_maakt_het_bestand_opnieuw(client, mensen, beeld):
    (eva, eva_id), _ = mensen
    fake, settings = beeld
    user = uuid.UUID(eva_id)
    await nep.beeld_aan(client, eva)
    await nep.run_jobs(settings)
    await foto(client, eva, settings, "rood.jpg", "rood")
    await nep.embed_all(settings)
    stand = await nep.owner(settings, eva_id)
    assert stand is not None and stand.built_gen == stand.gen and stand.built_at is not None
    path = vecfile.path_for(settings.beeld_dir, user)
    assert lees(path)[0].gen == stand.built_gen
    assert vecfile.is_current(settings.beeld_dir, user, stand.built_gen)
    async with nep.maker(settings)() as db:
        assert not await bjobs._behind(db, user, settings)

    # De database noemt een later bestand dan dit (gen en built_gen gelijk, dus zonder de kop
    # zou niets het opnieuw maken)
    later = stand.gen + 3
    async with nep.maker(settings)() as db:
        await db.execute(
            sa.update(BeeldOwner)
            .where(BeeldOwner.user_id == user)
            .values(gen=later, built_gen=later)
        )
        await db.execute(sa.delete(Job).where(Job.type == bjobs.VEC))
        await db.commit()
    assert vecfile.is_current(settings.beeld_dir, user)  # model en versie kloppen wel
    assert not vecfile.is_current(settings.beeld_dir, user, later)
    async with nep.maker(settings)() as db:
        assert await bjobs._behind(db, user, settings)

    # Een vraag zet de job klaar (en zoekt intussen in het bestand dat er is)
    r = await nep.zoek(client, eva, "rood")
    assert r.status_code == 200, r.text
    async with nep.maker(settings)() as db:
        assert await db.scalar(sa.select(Job.id).where(Job.type == bjobs.VEC)) is not None

    # De job maakt het opnieuw, met de gen van nu in de kop
    await bjobs.vec_job(settings, {"user": user.hex})
    na = await nep.owner(settings, eva_id)
    assert na is not None and na.built_gen == later
    assert lees(path)[0].gen == later
    assert vecfile.is_current(settings.beeld_dir, user, later)
    async with nep.maker(settings)() as db:
        assert not await bjobs._behind(db, user, settings)


@pytest.mark.pg
async def test_een_vector_tijdens_de_bouw_maakt_het_bestand_niet_bij(
    client, mensen, beeld, monkeypatch
):
    """Op PostgreSQL leest build aantal, gen en rijen uit één momentopname (REPEATABLE READ).
    Komt er tijdens de bouw een vector bij (in een andere transactie, met een hogere gen), dan
    staat die niet in het bestand en ook niet in de gen van de kop: het bestand loopt dan achter
    en de job maakt het opnieuw. Eerder las build de nieuwe gen na het aantal, en heette een
    bestand zonder de nieuwe foto bij (review van golf 2, pg-analyse F5). Alleen PostgreSQL: op
    SQLite leest build zonder transactie (pysqlite), en daar staat dit nog open (w2.md)."""
    (eva, eva_id), _ = mensen
    fake, settings = beeld
    user = uuid.UUID(eva_id)
    await nep.beeld_aan(client, eva)
    await nep.run_jobs(settings)
    for i, kleur in enumerate(("rood", "groen")):
        await foto(client, eva, settings, f"{kleur}.jpg", kleur, seed=i)
    await nep.embed_all(settings)
    blauw = await foto(client, eva, settings, "blauw.jpg", "blauw", seed=2)
    async with nep.maker(settings)() as db:
        num = await db.scalar(sa.select(Node.num).where(Node.id == uuid.UUID(blauw["id"])))

    echt = AsyncSession.scalar
    tussendoor: list[str] = []

    async def scalar(self, statement, *args, **kwargs):
        found = await echt(self, statement, *args, **kwargs)
        if not tussendoor and str(statement).lstrip().startswith("SELECT count(*)"):
            tussendoor.append("embed")  # één keer: direct na het aantal van build
            async with nep.maker(settings)() as other:
                vec = nep.pack(nep.vector(nep.KLEUREN["blauw"]))
                await beeld_rows.write_embed(other, blauw["sha"], vec)
                await other.commit()
        return found

    monkeypatch.setattr(AsyncSession, "scalar", scalar)
    built = await _bouw(settings, eva_id)
    monkeypatch.setattr(AsyncSession, "scalar", echt)
    assert tussendoor == ["embed"]
    head, nums, _vecs, _shas = lees(built.path)
    stand = await nep.owner(settings, eva_id)
    assert stand is not None and stand.built_gen == head.gen
    async with nep.maker(settings)() as db:
        behind = await bjobs._behind(db, user, settings)
    # Wat het bestand ook mist, het mag niet bij heten: de nieuwe foto staat erin, of het loopt
    # achter
    assert num in nums or behind, (head, nums, num, stand.gen, stand.built_gen)
    assert num not in nums and head.count == 2 and stand.gen > stand.built_gen
    # De job maakt het opnieuw, nu met de nieuwe foto
    await bjobs.vec_job(settings, {"user": user.hex})
    head, nums, _vecs, _shas = lees(built.path)
    assert num in nums and head.count == 3
    async with nep.maker(settings)() as db:
        assert not await bjobs._behind(db, user, settings)


# --- hoe vaak (Keuze 76) -----------------------------------------------------------------------


async def test_hooguit_eens_per_5_minuten_en_na_30_s_na_een_vraag(beeld):
    fake, settings = beeld
    vijf = settings.model_copy(update={"beeld_rebuild_s": 300.0})
    user = uuid.uuid4()
    t = time.time()
    row = BeeldOwner(user_id=user, gen=5, built_gen=4, built_rows=1, built_at=None)
    assert bjobs.wait_s(row, vijf, at=t) == 0.0  # nog nooit gemaakt: meteen
    row.built_at = datetime.fromtimestamp(t, UTC)
    assert bjobs.wait_s(row, vijf, at=t + 10) == pytest.approx(290)
    assert bjobs.wait_s(row, vijf, at=t + 301) == 0.0
    bjobs.searched(user)
    assert bjobs.wait_s(row, vijf, at=t + 10) == pytest.approx(20)
    assert bjobs.wait_s(row, vijf, at=t + 31) == 0.0


async def test_een_vraag_zet_het_bestand_na_10_s_klaar(client, mensen, beeld):
    """Na een nieuwe foto zet een zoekvraag de job klaar: niet eerder dan SOON_S (10 s), en niet
    eerder dan FRESH_S (30 s) na het vorige bestand. vec_job die te vroeg draait, schuift door
    in plaats van te bouwen."""
    (eva, eva_id), _ = mensen
    fake, settings = beeld
    vijf = settings.model_copy(update={"beeld_rebuild_s": 300.0})
    from app.config import get_settings
    from app.main import app

    await nep.beeld_aan(client, eva)
    await nep.run_jobs(settings)
    await foto(client, eva, settings, "rood.jpg", "rood")
    await nep.embed_all(settings)
    stand = await nep.owner(settings, eva_id)
    assert stand is not None and stand.built_gen == stand.gen
    await foto(client, eva, settings, "groen.jpg", "groen", seed=1)
    await nep.backfill(settings)
    await nep.pump(settings)
    async with nep.maker(settings)() as db:
        await db.execute(sa.delete(Job).where(Job.type == bjobs.VEC))
        await db.commit()
    app.dependency_overrides[get_settings] = lambda: vijf
    try:
        voor = datetime.now(UTC)
        r = await nep.zoek(client, eva, "rood")
        assert r.status_code == 200, r.text
    finally:
        app.dependency_overrides[get_settings] = lambda: settings
    async with nep.maker(settings)() as db:
        job = await db.scalar(sa.select(Job).where(Job.type == bjobs.VEC))
    assert job is not None and job.run_after is not None
    wacht = job.run_after.replace(tzinfo=UTC) - voor
    # het bestand is net gemaakt: FRESH_S na built_at (≥ SOON_S), nooit pas na 5 minuten
    assert timedelta(seconds=bjobs.SOON_S) <= wacht <= timedelta(seconds=bjobs.FRESH_S + 1)

    # te vroeg: geen nieuw bestand, wel een job voor later
    await bjobs.vec_job(vijf, {"user": uuid.UUID(eva_id).hex})
    na = await nep.owner(settings, eva_id)
    assert na is not None and na.built_gen == stand.built_gen < na.gen
    async with nep.maker(settings)() as db:
        later = await db.scalar(
            sa.select(Job.run_after).where(Job.type == bjobs.VEC, Job.status == "queued")
        )
    assert later is not None and later.replace(tzinfo=UTC) > datetime.now(UTC)
