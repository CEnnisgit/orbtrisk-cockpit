from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

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
    llm_enabled: bool = True
    llm_base_url: str = "http://localhost:11434"
    llm_model: str = "llama3.1:8b"
    llm_timeout_seconds: float = 20.0
    llm_temperature: float = 0.2
    llm_max_tokens: int = 500
    horizons_base_url: str = "https://ssd.jpl.nasa.gov/api/horizons.api"
    solar_small_body_cache_hours: int = 6
    default_hbr_m: float = 10.0
    poc_alert_threshold: float = 1e-4
    poc_num_angle_steps: int = 180


settings = Settings()
