from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Instellingen uit omgevingsvariabelen met prefix PLATFORM_."""

    model_config = SettingsConfigDict(env_prefix="PLATFORM_", env_file=".env", extra="ignore")

    app_name: str = "platform-api"
    environment: str = "development"
    node_name: str = "local"
    log_level: str = "info"
    # single = één node zonder etcd/Patroni (eerste versie); cluster = 3 nodes
    mode: Literal["single", "cluster"] = "cluster"
    # "node1=192.168.50.11,node2=192.168.50.12,..."; leeg = alleen deze node lokaal
    cluster_nodes: str = ""
    # Tijdelijk beheertoken voor /api/v1/admin tot de echte login er is (fase 2)
    admin_token: str = ""

    def nodes(self) -> list[tuple[str, str]]:
        if not self.cluster_nodes:
            return [(self.node_name, "127.0.0.1")]
        pairs = (item.split("=", 1) for item in self.cluster_nodes.split(",") if item.strip())
        return [(name.strip(), ip.strip()) for name, ip in pairs]


@lru_cache
def get_settings() -> Settings:
    return Settings()
