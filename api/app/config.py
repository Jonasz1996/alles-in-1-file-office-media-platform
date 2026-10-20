import secrets
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Instellingen uit omgevingsvariabelen met prefix PLATFORM_."""

    model_config = SettingsConfigDict(env_prefix="PLATFORM_", env_file=".env", extra="ignore")

    app_name: str = "platform-api"
    environment: str = "development"
    node_name: str = "local"
    log_level: str = "info"

    # standalone = één container met alles erin; cluster = 3 nodes (later)
    mode: Literal["standalone", "cluster"] = "standalone"
    # Map voor database (standalone), bestanden en geheimen
    data_dir: Path = Path("data")
    # Leeg = SQLite in data_dir; anders bijv. postgresql+asyncpg://user:pw@host/db
    database_url: str = ""
    # Gebouwde web-app die de API zelf serveert (leeg of niet aanwezig = alleen API)
    web_dist: Path = Path("web")

    # Tokens
    jwt_secret: str = ""
    access_token_minutes: int = 15
    refresh_token_days: int = 30
    download_token_minutes: int = 10
    # Cookies alleen over https sturen; zet op true zodra er https is
    secure_cookies: bool = False

    # Standaardquotum voor nieuwe gebruikers (0 = onbeperkt)
    default_quota_bytes: int = 0
    max_upload_bytes: int = 50 * 1024**3

    # Cluster (later): "node1=192.168.50.11,node2=..."
    cluster_nodes: str = ""

    def nodes(self) -> list[tuple[str, str]]:
        if not self.cluster_nodes:
            return [(self.node_name, "127.0.0.1")]
        pairs = (item.split("=", 1) for item in self.cluster_nodes.split(",") if item.strip())
        return [(name.strip(), ip.strip()) for name, ip in pairs]

    @property
    def db_url(self) -> str:
        return self.database_url or f"sqlite+aiosqlite:///{self.data_dir / 'platform.db'}"

    @property
    def blob_dir(self) -> Path:
        return self.data_dir / "blobs"

    def secret(self) -> str:
        """JWT-geheim: uit de omgeving, of eenmalig gegenereerd en bewaard in data_dir."""
        if self.jwt_secret:
            return self.jwt_secret
        path = self.data_dir / "jwt.secret"
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(secrets.token_hex(32))
            path.chmod(0o600)
        return path.read_text().strip()


@lru_cache
def get_settings() -> Settings:
    return Settings()
