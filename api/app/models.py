import uuid
from datetime import UTC, datetime

from sqlalchemy import JSON, BigInteger, DateTime, ForeignKey, Index, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


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


class Blob(Base):
    """Inhoud, content-addressed op SHA-256: dezelfde inhoud staat maar één keer op disk."""

    __tablename__ = "blob"

    sha256: Mapped[str] = mapped_column(String(64), primary_key=True)
    size: Mapped[int] = mapped_column(BigInteger)
    ref_count: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Node(Base):
    """Een map of bestand in de boom. Prullenbak = deleted_at gezet."""

    __tablename__ = "node"
    __table_args__ = (Index("ix_node_parent_name", "parent_id", "name"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("node.id", ondelete="CASCADE"), nullable=True
    )
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("user.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(255))
    kind: Mapped[str] = mapped_column(String(8))  # file, dir
    mime: Mapped[str | None] = mapped_column(String(255), nullable=True)
    size: Mapped[int] = mapped_column(BigInteger, default=0)
    blob_id: Mapped[str | None] = mapped_column(ForeignKey("blob.sha256"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    modified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

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
