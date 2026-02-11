from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    app_env: str = "development"
    database_url: str = "sqlite:///./spaceops.db"
    raw_data_dir: str = "./data/raw"
    spice_kernel_dir: str = "./data/spice"
    webhook_timeout_seconds: float = 3.0
    celestrak_group: str = "active"
    celestrak_gp_url: str = "https://celestrak.org/NORAD/elements/gp.php"
    celestrak_satcat_url: str = "https://celestrak.org/satcat/records.php"
    catalog_sync_hours: int = 24
    catalog_max_objects: Optional[int] = None
    space_track_base_url: str = "https://www.space-track.org"
    space_track_user: Optional[str] = None
    space_track_password: Optional[str] = None
    space_track_sync_hours: int = 1
    cesium_ion_token: Optional[str] = None
    cesium_night_asset_id: Optional[int] = None
    session_secret: str = "dev-session-secret-change-me"
    session_https_only: Optional[bool] = None
    session_same_site: str = "lax"
    session_max_age_seconds: int = 28800
    business_access_code: Optional[str] = None
    trusted_hosts: str = "localhost,127.0.0.1,testserver"
    trusted_hosts_allow_all: bool = False
    trust_proxy_headers: bool = False
    enforce_origin_check: bool = True
    allowed_origins: Optional[str] = None
    login_rate_limit_attempts: int = 8
    login_rate_limit_window_seconds: int = 300
    webhook_allow_private_targets: bool = False
    webhook_allowed_schemes: str = "https"
    webhook_allow_http_localhost: bool = True
    llm_enabled: bool = True
    llm_base_url: str = "http://localhost:11434"
    llm_model: str = "llama3.1:8b"
    llm_timeout_seconds: float = 20.0
    llm_temperature: float = 0.2
    llm_max_tokens: int = 500
    horizons_base_url: str = "https://ssd.jpl.nasa.gov/api/horizons.api"
    solar_small_body_cache_hours: int = 6

    screening_horizon_days: int = 14
    screening_volume_km: float = 10.0
    time_critical_hours: float = 72.0
    risk_high_score: float = 0.7
    risk_watch_score: float = 0.4
    risk_high_miss_km: float = 1.0
    risk_watch_miss_km: float = 5.0
    tle_max_age_hours_for_confidence: float = 72.0

    orbit_state_retention_days: int = 30
    tle_record_retention_days: int = 90

    series_window_hours: float = 6.0
    series_step_seconds: int = 120

    @property
    def env_name(self) -> str:
        return (self.app_env or "development").strip().lower()

    @property
    def is_production(self) -> bool:
        return self.env_name in {"production", "prod"}

    @property
    def resolved_session_https_only(self) -> bool:
        if self.session_https_only is not None:
            return bool(self.session_https_only)
        return self.is_production

    @property
    def trusted_hosts_list(self) -> list[str]:
        if self.trusted_hosts_allow_all:
            return ["*"]
        hosts = [h.strip() for h in (self.trusted_hosts or "").split(",") if h.strip()]
        return hosts or ["localhost", "127.0.0.1", "testserver"]

    @property
    def allowed_origins_list(self) -> list[str]:
        if not self.allowed_origins:
            return []
        return [o.strip().rstrip("/") for o in self.allowed_origins.split(",") if o.strip()]

    @property
    def webhook_allowed_schemes_set(self) -> set[str]:
        schemes = {s.strip().lower() for s in (self.webhook_allowed_schemes or "").split(",") if s.strip()}
        return schemes or {"https"}


settings = Settings()
