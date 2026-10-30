"""De tabellen.

Opnamedatum (fase 3): een "zwevende" lokale tijd. Een foto zegt hoe laat de klok van de
camera stond, niet in welke tijdzone; die kloktijd bewaren we als UTC-tijd met dezelfde
cijfers (12:30 op de camera = 12:30 UTC in de kolom) en de web-app toont hem met
timeZone "UTC". Een tijd die echt UTC is (zoals creation_time van een video) zet de API eerst
om naar settings.timezone (zie app/photodate.py). Node.taken_day is dezelfde dag als
JJJJMMDD-getal, om zonder SQL-dialect per dag of maand te groeperen. GPS en locatie slaan we
in fase 3 nergens op; dat komt in fase 6.
"""

import uuid
from datetime import UTC, date, datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    event,
    false,
    text,
    true,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.text import fold


def now() -> datetime:
    return datetime.now(UTC)


class User(Base):
    __tablename__ = "user"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(320), unique=True)
    display_name: Mapped[str] = mapped_column(String(100))
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(16), default="user")  # admin, user, guest
    quota_bytes: Mapped[int] = mapped_column(BigInteger, default=0)  # 0 = onbeperkt
    root_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    # 2FA (TOTP): totp_secret is gezet zodra 2FA aan staat; totp_pending tijdens het instellen.
    # totp_last_step voorkomt dat dezelfde code twee keer werkt.
    totp_secret: Mapped[str | None] = mapped_column(String(64), nullable=True)
    totp_pending: Mapped[str | None] = mapped_column(String(64), nullable=True)
    totp_last_step: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    # Foute pogingen voor de tweede factor op rij, en tot wanneer de gebruiker daardoor
    # geblokkeerd is. Staat in de database, zodat een herstart de teller niet wist.
    totp_failures: Mapped[int] = mapped_column(default=0, server_default="0")
    totp_locked_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Taal van de web-app voor dit account ("nl" of "en"); None = wat het apparaat kiest
    locale: Mapped[str | None] = mapped_column(String(5), nullable=True)
    # Geblokkeerd door een beheerder: kan niets meer, ook niet met een uitgegeven token
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class RecoveryCode(Base):
    """Eenmalige herstelcodes voor als de telefoon met de authenticator-app kwijt is."""

    __tablename__ = "recovery_code"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user.id", ondelete="CASCADE"), index=True
    )
    code_hash: Mapped[str] = mapped_column(String(64))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Setting(Base):
    """Instellingen die je in /beheer aanpast, zoals het domein voor https."""

    __tablename__ = "setting"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class RefreshToken(Base):
    """Eén rij per uitgegeven refresh-token. Tokens van één login delen een family:
    wordt een al gebruikt token opnieuw aangeboden, dan wordt de hele family ingetrokken."""

    __tablename__ = "refresh_token"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("user.id", ondelete="CASCADE"))
    family: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    device: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Vanaf welk IP-adres dit token werd uitgegeven (voor het sessie-overzicht)
    ip: Mapped[str | None] = mapped_column(String(45), nullable=True)


class Blob(Base):
    """Inhoud, content-addressed op SHA-256: dezelfde inhoud staat maar één keer op disk."""

    __tablename__ = "blob"
    __table_args__ = (Index("ix_blob_pdf", "pdf_version"),)

    sha256: Mapped[str] = mapped_column(String(64), primary_key=True)
    size: Mapped[int] = mapped_column(BigInteger)
    ref_count: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    # Soort volgens de inhoud zelf (niet de naam); None = nog niet bepaald
    mime: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # None = nog niet aan begonnen, pending, ready, none (geen preview mogelijk), failed
    preview: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # Wat de preview opleverde: afmetingen, eerste regels tekst, inhoud van een archief, ...
    # (en vanaf fase 3 onder "exif" camera, objectief en belichting; nooit GPS)
    preview_meta: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Opnamedatum uit de inhoud zelf (EXIF, metadata van een video), als zwevende lokale tijd
    taken_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Met welke versie van app/render_meta.py de metadata gelezen is (0 = nog niet)
    meta_version: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # Met welke versie van de previews (previews.RENDER_VERSION) die gemaakt zijn
    render_version: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # De PDF van een kantoordocument via de document server (fase 4, Keuze 25):
    # None = nog niet aan begonnen, pending, ready, failed, none (DS kan het niet omzetten),
    # unsafe (de invoercontrole weigerde het, Keuze 23), ask (alleen als een ingelogde
    # gebruiker erom vraagt: een upload via een link, of groter dan office_auto_convert_bytes).
    pdf_state: Mapped[str | None] = mapped_column(String(8), nullable=True)
    # Met welke office.keys.PDF_VERSION de PDF gemaakt is (0 = geen); het adres van een
    # preview draagt hem mee (previews.url, deel v)
    pdf_version: Mapped[int] = mapped_column(Integer, default=0, server_default="0")


class Node(Base):
    """Een map of bestand in de boom. Prullenbak = deleted_at gezet."""

    __tablename__ = "node"
    __table_args__ = (
        Index("ix_node_parent_name", "parent_id", "name"),
        # De tijdlijn: per eigenaar op opnamedag
        Index("ix_node_owner_taken", "owner_id", "taken_day", "taken_at", "id"),
        Index("ix_node_blob", "blob_id"),
        # Een map in de volgorde van de lijst (in pagina's)
        Index("ix_node_parent_order", "parent_id", "kind", "search_name", "id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("node.id", ondelete="CASCADE"), nullable=True
    )
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("user.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(255))
    # file, dir of album. Een album staat buiten de boom (parent_id NULL, geen hoofdmap: die
    # vind je via User.root_id); wat erin zit, staat in album_item.
    kind: Mapped[str] = mapped_column(String(8))
    mime: Mapped[str | None] = mapped_column(String(255), nullable=True)
    size: Mapped[int] = mapped_column(BigInteger, default=0)
    blob_id: Mapped[str | None] = mapped_column(ForeignKey("blob.sha256"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    modified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # De naam zonder hoofdletters en accenten, om op te zoeken; volgt name vanzelf
    search_name: Mapped[str] = mapped_column(Text, default="", server_default="")
    # Alleen voor afbeeldingen en video's: de opnamedatum (zwevende lokale tijd, zie boven),
    # en anders de lastModified van de client of het uploadmoment. taken_day = JJJJMMDD.
    taken_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    taken_day: Mapped[int | None] = mapped_column(Integer, nullable=True)

    @property
    def etag(self) -> str:
        return f'"{self.blob_id or self.id.hex}"'


class Upload(Base):
    """Een hervatbare upload (tus). Zolang hij loopt staan de bytes in uploads/<id> en is
    de grootte van dat bestand de offset."""

    __tablename__ = "upload"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("user.id", ondelete="CASCADE"))
    parent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("node.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(255))
    conflict: Mapped[str] = mapped_column(String(8), default="rename")
    size: Mapped[int] = mapped_column(BigInteger)
    # Gezet zodra het bestand gemaakt is. De rij blijft tot hij verloopt, zodat een client
    # die het laatste antwoord miste nog kan opvragen welk bestand het werd.
    node_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    # owner_id is van wie het bestand wordt (en wiens quotum het kost): bij uploaden in een
    # gedeelde map de eigenaar van die map. created_by is wie uploadt; via een publieke
    # uploadlink is dat niemand, en dan is share_id die link.
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("user.id", ondelete="CASCADE"), nullable=True
    )
    share_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("share.id", ondelete="CASCADE"), nullable=True, index=True
    )
    # lastModified van het bestand bij de client (tus-metadata lastmodified), als die er was
    client_mtime: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


@event.listens_for(Node.name, "set")
def _search_name(target: Node, value: str | None, _old, _initiator) -> None:
    target.search_name = fold(value or "")


class Version(Base):
    """Een eerdere inhoud van een bestand. Telt als verwijzing naar de blob (ref_count)."""

    __tablename__ = "version"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    node_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("node.id", ondelete="CASCADE"), index=True
    )
    blob_id: Mapped[str] = mapped_column(ForeignKey("blob.sha256"))
    size: Mapped[int] = mapped_column(BigInteger)
    # Wanneer deze inhoud gemaakt werd (modified_at van toen) en wanneer hij vervangen werd
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    replaced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    replaced_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL"), nullable=True
    )


class Group(Base):
    """Een groep gebruikers (beheerd op /beheer), om in één keer met iedereen erin te delen."""

    __tablename__ = "user_group"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class GroupMember(Base):
    __tablename__ = "group_member"

    group_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user_group.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user.id", ondelete="CASCADE"), primary_key=True, index=True
    )


class Share(Base):
    """Een map of bestand gedeeld met precies één van: een gebruiker (grantee_id), een groep
    (group_id) of een publieke link (token)."""

    __tablename__ = "share"
    __table_args__ = (
        UniqueConstraint("node_id", "grantee_id"),
        Index("uq_share_node_group", "node_id", "group_id", unique=True),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    node_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("node.id", ondelete="CASCADE"), index=True
    )
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("user.id", ondelete="CASCADE"))
    grantee_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("user.id", ondelete="CASCADE"), nullable=True, index=True
    )
    group_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("user_group.id", ondelete="CASCADE"), nullable=True, index=True
    )
    token: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    # Gebruiker of groep: read of write. Link: read of upload (alleen uploaden, niets zien).
    permission: Mapped[str] = mapped_column(String(8), default="read")
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    # Alleen voor links: een naam voor de eigenaar ("voor de fotograaf")
    label: Mapped[str | None] = mapped_column(String(100), nullable=True)


class Tag(Base):
    __tablename__ = "tag"
    __table_args__ = (UniqueConstraint("owner_id", "name"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("user.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(50))
    color: Mapped[str] = mapped_column(String(16), default="")


class NodeTag(Base):
    __tablename__ = "node_tag"

    node_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("node.id", ondelete="CASCADE"), primary_key=True
    )
    tag_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tag.id", ondelete="CASCADE"), primary_key=True, index=True
    )


class Job(Base):
    """Werk op de achtergrond (previews, later meer). Mislukt na max_attempts = failed:
    zichtbaar op /beheer en daar opnieuw te starten."""

    __tablename__ = "job"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    type: Mapped[str] = mapped_column(String(32), index=True)
    # Om dubbel werk te voorkomen, bijv. "preview:<sha256>"
    key: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    attempts: Mapped[int] = mapped_column(default=0)
    max_attempts: Mapped[int] = mapped_column(default=3)
    run_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Album(Base):
    """Wat een album meer heeft dan zijn Node (kind "album", buiten de boom): de beschrijving en
    de omslag. Delen gaat zoals bij een map, via een Share op de node."""

    __tablename__ = "album"

    id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("node.id", ondelete="CASCADE"), primary_key=True
    )
    description: Mapped[str] = mapped_column(Text, default="", server_default="")
    cover_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("node.id", ondelete="SET NULL"), nullable=True
    )


class AlbumItem(Base):
    """Een eigen foto of video in een album; er wordt niets gekopieerd."""

    __tablename__ = "album_item"
    __table_args__ = (Index("ix_album_item_node", "node_id"),)

    album_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("node.id", ondelete="CASCADE"), primary_key=True
    )
    node_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("node.id", ondelete="CASCADE"), primary_key=True
    )
    added_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL"), nullable=True
    )
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class OfficeSession(Base):
    """Eén bewerksessie van een kantoordocument in de document server (fase 4, Keuze 14).
    De key is document.key van OnlyOffice: iedereen die tegelijk bewerkt, krijgt dezelfde.

    state: pending (een config uitgegeven, nog geen status 1), open (iemand verbonden),
    saving (de laatste is weg, wacht op de opslag), closed. Er is hooguit één niet-dichte
    sessie per bestand (ux_office_session_open). node_id gaat bij definitief verwijderen
    op NULL; de rij blijft, zodat een late callback en het herstel weten wat het was en
    niets schrijven (Keuze 19).

    Versies (Keuze 17): een opslag maakt een nieuwe versie als er sinds versioned_at
    version_minutes voorbij zijn, met hooguit 10 versies per sessie (versions_made); anders
    vervangt hij de vorige tussenstand van deze sessie (checkpoint, squash in place_file).
    blob_id en checkpoint houden geen verwijzing vast: ref_count blijft van bestanden en
    versies."""

    __tablename__ = "office_session"
    __table_args__ = (
        Index("ix_office_session_node", "node_id"),
        Index("ix_office_session_state", "state"),
        Index(
            "ux_office_session_open",
            "node_id",
            unique=True,
            sqlite_where=text("state <> 'closed' AND node_id IS NOT NULL"),
            postgresql_where=text("state <> 'closed' AND node_id IS NOT NULL"),
        ),
    )

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    node_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("node.id", ondelete="SET NULL"), nullable=True
    )
    owner_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL"), nullable=True
    )
    # De naam bij het openen (voor een herstelkopie en "Niet opgeslagen")
    name: Mapped[str] = mapped_column(String(255))
    file_type: Mapped[str] = mapped_column(String(8))  # docx | xlsx | pptx
    # De inhoud waar de key voor staat (na een tussentijdse opslag bijgewerkt)
    blob_id: Mapped[str] = mapped_column(String(64))
    # Wat deze sessie het laatst zelf opsloeg (squash)
    checkpoint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Laatste opslag van deze sessie die een versie maakte
    versioned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    versions_made: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # Laatste opslag (voor het tussentijds opslaan, Keuze 18)
    saved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Hoogste merk dat is toegepast (Keuze 15; OnlyOffice: lastsave)
    applied_mark: Mapped[str | None] = mapped_column(String(40), nullable=True)
    state: Mapped[str] = mapped_column(String(8), default="pending", server_default="pending")
    # {ds_uid: user_id}: wie een config kreeg en nog recht heeft
    members: Mapped[dict] = mapped_column(JSON, default=dict, server_default="{}")
    # [ds_uid]: eruit gezet (Keuze 20)
    banned: Mapped[list] = mapped_column(JSON, default=list, server_default="[]")
    # [ds_uid] volgens de laatste status 1 of info
    connected: Mapped[list] = mapped_column(JSON, default=list, server_default="[]")
    # no_writer | gone | no_room | too_large | owner_gone | error_status
    last_error: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # saved | no_changes | lost | copy | refused | gone
    closed_reason: Mapped[str | None] = mapped_column(String(16), nullable=True)
    node_name: Mapped[str] = mapped_column(String(64))  # settings.node_name
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    # Ook: sinds wanneer de sessie in 'saving' staat (Keuze 20)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# --- Media (fase 5, bouwontwerp §3.1) ------------------------------------------------------
#
# De analyse hangt aan de inhoud (media_probe, per sha): twee kopieën delen één probe, één
# keyframe-index, één set ondertitels en sprites. Het item en de voortgang hangen aan de node:
# een kopie in een andere map is een eigen item (Keuze 6). Waarden in het Engels, zoals de rest
# van het schema; de web-app vertaalt ze.


class MediaLibrary(Base):
    """Een map van de boom die een bibliotheek is (Keuze 1, 4). Wie de map mag lezen, ziet de
    bibliotheek. Weg met de eigenaar of de map (CASCADE); de bestanden blijven."""

    __tablename__ = "media_library"
    __table_args__ = (
        UniqueConstraint("root_id", name="uq_media_library_root"),
        Index("ix_media_library_owner", "owner_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("user.id", ondelete="CASCADE"))
    root_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("node.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(8))  # movies | shows | music | videos
    name: Mapped[str] = mapped_column(String(255))
    language: Mapped[str] = mapped_column(String(16), default="nl-NL", server_default="nl-NL")
    # Online opzoeken bij TMDB en MusicBrainz (Keuze 39)
    online: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    # idle | queued | running | failed
    scan_state: Mapped[str] = mapped_column(String(8), default="idle", server_default="idle")
    scanned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    item_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class MediaProbe(Base):
    """De analyse van één inhoud (Keuze 15), gemaakt door de worker. version is
    analysis.PROBE_VERSION: een hogere versie mag opnieuw proben. state: ready | failed.

    Per stap een eigen staat; een mislukte stap houdt de rest niet tegen (Keuze 14):
    - keyframes_state: none | ready | sparse (Cues met gaten > 20 s) | failed (Keuze 17);
      keyframes is zlib(varint-delta's in ms), hooguit 48 KB;
    - subs_state, trick_state, audio_state: none | pending | ready | failed | skip.
    truncated zegt wat de grens van 64 wegliet: {streams, chapters, attachments} (Keuze 12)."""

    __tablename__ = "media_probe"

    blob_sha: Mapped[str] = mapped_column(
        ForeignKey("blob.sha256", ondelete="CASCADE"), primary_key=True
    )
    version: Mapped[int] = mapped_column(Integer)
    state: Mapped[str] = mapped_column(String(8))
    # mp4 | mov | m4v | matroska | webm | avi | mpegts | m2ts | mp3 | flac | ogg | …
    container: Mapped[str | None] = mapped_column(String(32), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    bit_rate: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # start_time van de bron (S, Keuze 22)
    start_ms: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    streams: Mapped[list] = mapped_column(JSON, default=list, server_default="[]")
    chapters: Mapped[list] = mapped_column(JSON, default=list, server_default="[]")
    tags: Mapped[dict] = mapped_column(JSON, default=dict, server_default="{}")
    keyframes: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    keyframes_state: Mapped[str] = mapped_column(String(8), default="none", server_default="none")
    max_gop_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    subs_state: Mapped[str] = mapped_column(String(8), default="none", server_default="none")
    trick_state: Mapped[str] = mapped_column(String(8), default="none", server_default="none")
    # {interval, w, h, cols, rows, count} (Keuze 30)
    trick: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    audio_state: Mapped[str] = mapped_column(String(8), default="none", server_default="none")
    # {rate, samples, delay, padding, source} (Keuze 18)
    audio: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    truncated: Mapped[dict] = mapped_column(JSON, default=dict, server_default="{}")
    error: Mapped[str | None] = mapped_column(String(200), nullable=True)
    probed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class MediaShow(Base):
    """Een serie in een bibliotheek: de map van de serie (Keuze 8). match_state: local |
    matched | review | manual; candidates zijn de 5 beste bij review (Keuze 42)."""

    __tablename__ = "media_show"
    __table_args__ = (UniqueConstraint("library_id", "folder_id", name="uq_media_show_folder"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    library_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("media_library.id", ondelete="CASCADE")
    )
    folder_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("node.id", ondelete="SET NULL"), nullable=True
    )
    title: Mapped[str] = mapped_column(String(255))
    sort_title: Mapped[str] = mapped_column(String(255))
    year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    overview: Mapped[str | None] = mapped_column(Text, nullable=True)
    ids: Mapped[dict] = mapped_column(JSON, default=dict, server_default="{}")  # {tmdb, imdb, tvdb}
    art: Mapped[dict] = mapped_column(JSON, default=dict, server_default="{}")
    locked: Mapped[list] = mapped_column(JSON, default=list, server_default="[]")
    match_state: Mapped[str] = mapped_column(String(8), default="local", server_default="local")
    candidates: Mapped[list] = mapped_column(JSON, default=list, server_default="[]")


class MediaAlbum(Base):
    """Een album in een muziekbibliotheek. key is de genormaliseerde albumartiest|titel
    (Keuze 37)."""

    __tablename__ = "media_album"
    __table_args__ = (UniqueConstraint("library_id", "key", name="uq_media_album_key"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    library_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("media_library.id", ondelete="CASCADE")
    )
    key: Mapped[str] = mapped_column(String(255))
    title: Mapped[str] = mapped_column(String(255))
    sort_title: Mapped[str] = mapped_column(String(255))
    album_artist: Mapped[str] = mapped_column(String(255))
    year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    compilation: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    mbid: Mapped[str | None] = mapped_column(String(36), nullable=True)
    art: Mapped[dict] = mapped_column(JSON, default=dict, server_default="{}")
    match_state: Mapped[str] = mapped_column(String(8), default="local", server_default="local")
    track_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    duration_ms: Mapped[int] = mapped_column(Integer, default=0, server_default="0")


class MediaItem(Base):
    """Eén bestand in een bibliotheek (per node). kind: movie | episode | track | video |
    extra; extra: trailer | featurette | deleted | behind | sample | other, met parent_id de
    film. present 0 = in de prullenbak: zo blijft een lijst één index-scan (Keuze 5).
    season is NULL bij absolute nummering (Keuze 7, 8). source: name | nfo | tags | provider.
    locked: velden die een gebruiker zelf zette en die niets meer overschrijft."""

    __tablename__ = "media_item"
    __table_args__ = (
        Index("ix_media_item_lib", "library_id", "present", "kind", "sort_title"),
        Index("ix_media_item_added", "library_id", "present", "added_at"),
        Index("ix_media_item_show", "show_id", "season", "episode"),
        Index("ix_media_item_album", "album_id", "disc", "track"),
        Index("ix_media_item_parent", "parent_id"),
    )

    node_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("node.id", ondelete="CASCADE"), primary_key=True
    )
    library_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("media_library.id", ondelete="CASCADE")
    )
    kind: Mapped[str] = mapped_column(String(8))
    extra: Mapped[str | None] = mapped_column(String(12), nullable=True)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("media_item.node_id", ondelete="SET NULL"), nullable=True
    )
    present: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())
    title: Mapped[str] = mapped_column(String(255))
    sort_title: Mapped[str] = mapped_column(String(255))
    year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    show_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("media_show.id", ondelete="SET NULL"), nullable=True
    )
    season: Mapped[int | None] = mapped_column(Integer, nullable=True)
    episode: Mapped[int | None] = mapped_column(Integer, nullable=True)
    episode_end: Mapped[int | None] = mapped_column(Integer, nullable=True)
    absolute: Mapped[int | None] = mapped_column(Integer, nullable=True)
    aired: Mapped[date | None] = mapped_column(Date, nullable=True)
    album_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("media_album.id", ondelete="SET NULL"), nullable=True
    )
    disc: Mapped[int | None] = mapped_column(Integer, nullable=True)
    track: Mapped[int | None] = mapped_column(Integer, nullable=True)
    artist: Mapped[str | None] = mapped_column(String(255), nullable=True)
    part: Mapped[int | None] = mapped_column(Integer, nullable=True)
    edition: Mapped[str | None] = mapped_column(String(64), nullable=True)
    overview: Mapped[str | None] = mapped_column(Text, nullable=True)
    ids: Mapped[dict] = mapped_column(JSON, default=dict, server_default="{}")
    # {poster: {node} | {art}, backdrop: …}
    art: Mapped[dict] = mapped_column(JSON, default=dict, server_default="{}")
    # Externe ondertitels: [{node, lang, forced, sdh, default}]
    subs: Mapped[list] = mapped_column(JSON, default=list, server_default="[]")
    source: Mapped[str] = mapped_column(String(8), default="name", server_default="name")
    confidence: Mapped[float] = mapped_column(Float, default=0.0, server_default="0")
    # NAMES_VERSION bij het laatste ontleden (Keuze 7)
    names_version: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    locked: Mapped[list] = mapped_column(JSON, default=list, server_default="[]")
    match_state: Mapped[str] = mapped_column(String(8), default="local", server_default="local")
    candidates: Mapped[list] = mapped_column(JSON, default=list, server_default="[]")
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class PlaybackState(Base):
    """De voortgang per gebruiker, profiel en bestand (Keuze 36). profile is in fase 5 altijd
    '' (Vraag 6). blob_sha zonder FK: alleen om een vervangen bestand te zien. origin en seq:
    het tabblad of apparaat van de laatste stand en zijn laatste volgnummer, zodat een late tik
    niets terugzet."""

    __tablename__ = "playback_state"
    __table_args__ = (Index("ix_playback_recent", "user_id", "profile", "watched", "updated_at"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user.id", ondelete="CASCADE"), primary_key=True
    )
    profile: Mapped[str] = mapped_column(
        String(36), primary_key=True, default="", server_default=""
    )
    node_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("node.id", ondelete="CASCADE"), primary_key=True
    )
    position_ms: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    duration_ms: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    watched: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    play_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    audio: Mapped[int | None] = mapped_column(Integer, nullable=True)
    subtitle: Mapped[str | None] = mapped_column(String(48), nullable=True)
    blob_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    origin: Mapped[str | None] = mapped_column(String(36), nullable=True)
    seq: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # started_at van de sessie van de laatste stand
    play_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # De laatste overgang naar gezien of end
    watched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class MediaPlaylist(Base):
    """Een afspeellijst, alleen van de eigenaar (M9)."""

    __tablename__ = "media_playlist"
    __table_args__ = (Index("ix_media_playlist_owner", "owner_id"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("user.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class MediaPlaylistItem(Base):
    """Een regel van een afspeellijst; weg met het bestand (CASCADE)."""

    __tablename__ = "media_playlist_item"
    __table_args__ = (Index("ix_media_playlist_item", "playlist_id", "position"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    playlist_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("media_playlist.id", ondelete="CASCADE")
    )
    node_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("node.id", ondelete="CASCADE"))
    position: Mapped[int] = mapped_column(Integer)
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
