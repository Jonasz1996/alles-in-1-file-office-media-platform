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
from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    event,
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
