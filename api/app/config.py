import secrets
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field
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

    # Poorten in de container. https start pas zodra er een certificaat is; het domein
    # stel je in op /beheer.
    host: str = "0.0.0.0"
    http_port: int = 8080
    https_port: int = 8443
    # Leeg = Let's Encrypt (of de staging-omgeving als testmodus aan staat). Voor tests
    # met Pebble: eigen directory-URL en het CA-bestand om die te vertrouwen.
    acme_directory: str = ""
    acme_ca_file: str = ""

    # Tokens
    jwt_secret: str = ""
    access_token_minutes: int = 15
    refresh_token_days: int = 30
    download_token_minutes: int = 10
    # Cookies altijd met Secure sturen; over https gebeurt dat sowieso
    secure_cookies: bool = False

    # Standaardquotum voor nieuwe gebruikers (0 = onbeperkt)
    default_quota_bytes: int = 0
    max_upload_bytes: int = 50 * 1024**3
    # Onafgemaakte hervatbare uploads worden na zoveel uur zonder activiteit opgeruimd
    upload_expiry_hours: int = 24

    # Achtergrondtaken (zoals previews): zoveel tegelijk, en zo lang (seconden) mag één taak
    # duren voordat hij als mislukt telt en later opnieuw gaat
    job_workers: int = 2
    job_timeout: float = 300
    # Een preview wordt in een eigen proces gemaakt, met hooguit zoveel geheugen (MB, 0 = geen
    # grens); afbeeldingen met meer pixels krijgen geen preview (bescherming tegen "bommen")
    preview_memory_mb: int = 1536
    preview_max_pixels: int = 100_000_000
    # PDF-pagina's voor de viewer: zoveel renderprocessen tegelijk, en hooguit zoveel MB aan
    # gerenderde pagina's op schijf (de oudste gaan eerst)
    page_render_workers: int = 2
    page_cache_mb: int = 2048

    # Tijdzone van de server, voor tijden die echt UTC zijn (zoals de opnametijd van een
    # video) en voor de terugvaldatum van een foto zonder EXIF
    timezone: str = "Europe/Amsterdam"

    # Limieten op inloggen, links openen en dergelijke. Alleen uitzetten voor een testserver
    # (PLATFORM_RATE_LIMITS=false); bij het starten staat dan een waarschuwing in het log.
    rate_limits: bool = True

    # Cluster (later): "node1=192.168.50.11,node2=..."
    cluster_nodes: str = ""

    # Office (fase 4): de document server van OnlyOffice in een eigen container op het
    # interne netwerk. Leeg = geen office; de app is dan precies fase 3.
    office_url: str = ""  # PLATFORM_OFFICE_URL, bijv. http://office
    # PLATFORM_OFFICE_SECRET = JWT_SECRET van DS: minstens 32 tekens en nooit het app-geheim
    office_secret: str = ""
    # Hoe DS de app bereikt (de interne luisteraar), bijv. http://platform:8081
    office_app_url: str = ""
    office_internal_host: str = ""  # leeg = host
    office_internal_port: int = 8081
    office_convert_timeout: float = 120
    office_max_pdf_bytes: int = 512 * 1024**2
    # Grens op een download in de callback (Keuze 16)
    office_max_save_bytes: int = 512 * 1024**2
    # Groter: alleen omzetten op verzoek (Keuze 25)
    office_auto_convert_bytes: int = 20 * 1024**2
    # doc, xls, ppt en rtf (Keuze 23)
    office_ole_max_bytes: int = 20 * 1024**2
    office_max_tunnels: int = 1000
    office_max_tunnels_user: int = 8
    office_gate_cache_seconds: float = 15
    office_health_seconds: float = 30
    office_sweep_seconds: float = 30  # de e2e zet 1
    office_recovery_seconds: float = 600  # de e2e zet 2

    # Media (fase 5, bouwontwerp §3.2). Uit tenzij PLATFORM_MEDIA_ENABLED=true: zonder die
    # vlag start er geen worker, geeft elk media-eindpunt 503 media_off, en is de app precies
    # 0.6.0 (plus F1–F4). Aan, dan nog uit te zetten op /beheer (MediaConfig.enabled).
    media_enabled: bool = False
    # De socket van de worker-container; leeg = de app start zelf een lokale worker (Keuze 13)
    media_worker: str = ""
    media_max_transcodes: int = 3  # plafond; /beheer kan alleen lager (Keuze 26)
    media_job_memory_mb: int = 1200  # RLIMIT_DATA per ffmpeg voor live en sprites (Keuze 12)
    media_job_workers: int = 1  # workers van de rij media (Keuze 14)
    # ffmpeg en ffprobe samen in de worker; het harnas en de belastingtest zetten 3 (Keuze 12)
    media_max_procs: int = 10
    media_loadtest: bool = False  # Op.SYNTH en LoadtestFile toestaan (§4.6)
    media_scan_tick: float = 30  # de lus van de scanner (tests: 0.5)
    media_access_cache: float = 15  # seconden dat een toegangscontrole meegaat (Keuze 21)
    media_segment_wait: float = 20  # seconden wachten op een segment (Keuze 25)
    media_bench_fixed: bool = False  # tests: de kostentabel van de proef, geen meting
    media_tmdb_url: str = "https://api.themoviedb.org"
    media_tmdb_image_url: str = "https://image.tmdb.org"
    media_musicbrainz_url: str = "https://musicbrainz.org"
    media_caa_url: str = "https://coverartarchive.org"
    # Alleen tests: de nep-providers op 127.0.0.1 (Keuze 39)
    media_provider_private_ok: bool = False

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

    @property
    def upload_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def tls_dir(self) -> Path:
        return self.data_dir / "tls"

    @property
    def media_dir(self) -> Path:
        """De cache van media (sessies, audio, sprites, art) en de socket van de lokale
        worker (Keuze 13, 31). Systeemruimte, geen quotum van een gebruiker."""
        return self.data_dir / "media"

    @property
    def media_socket(self) -> Path:
        """Waar de app de worker bereikt: de socket van de container, of die van de lokale
        worker in data_dir/media/run."""
        if self.media_worker:
            return Path(self.media_worker)
        return self.media_dir / "run" / "worker.sock"

    def office_problem(self) -> str | None:
        """Waarom office niet ingesteld is, of None. Zonder office_url is er geen probleem:
        dan staat office gewoon uit."""
        if not self.office_url:
            return None
        if not self.office_url.startswith(("http://", "https://")):
            return "PLATFORM_OFFICE_URL moet met http:// of https:// beginnen"
        if not self.office_app_url.startswith(("http://", "https://")):
            return "PLATFORM_OFFICE_APP_URL ontbreekt of begint niet met http:// of https://"
        if len(self.office_secret) < 32:
            return "PLATFORM_OFFICE_SECRET ontbreekt of is korter dan 32 tekens"
        if self.office_secret == self.secret():
            return "PLATFORM_OFFICE_SECRET mag niet gelijk zijn aan het geheim van de app"
        return None

    @property
    def office_configured(self) -> bool:
        """Office staat aan: een adres voor DS, een adres waarop DS de app bereikt en een
        eigen, geldig geheim."""
        return bool(self.office_url) and self.office_problem() is None

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


class BenchResult(BaseModel):
    """De uitkomst van de meting (Keuze 26): f = cores voor 1,0× hier / de referentie, en
    f_dec voor het decoderen van de bron. Alleen de worker meet; /beheer leest."""

    f: float = Field(gt=0, le=20)
    f_dec: float = Field(gt=0, le=20)
    at: datetime


class MediaConfig(BaseModel):
    """De instellingen van media op /beheer (Keuze 45), in de tabel setting onder de sleutel
    "media" en in het geheugen (media.state.MediaPolicy), zoals SecurityPolicy en
    OfficeConfig. max_transcodes kan niet hoger dan PLATFORM_MEDIA_MAX_TRANSCODES
    (MediaPolicy.effective houdt dat aan)."""

    enabled: bool = True
    cpu_budget: float = Field(default=3.0, ge=0.5, le=16)
    max_transcodes: int = Field(default=3, ge=0, le=16)
    max_user_transcodes: int = Field(default=2, ge=1, le=16)
    # Mag een publieke link transcoderen (Vraag 5); standaard nee
    public_transcode: bool = False
    # HDR live op 1080 als iemand het kiest; anders alleen 720 (Keuze 27)
    hdr_1080: bool = False
    cache_gb: int = Field(default=20, ge=1, le=10_000)
    musicbrainz: bool = False
    # Alleen lezen: de laatste meting (Keuze 26); None = nog niet gemeten (f = 1,0)
    bench: BenchResult | None = None


@lru_cache
def get_settings() -> Settings:
    return Settings()
