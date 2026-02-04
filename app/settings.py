from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    database_url: str = "sqlite:///./spaceops.db"
    raw_data_dir: str = "./data/raw"
    webhook_timeout_seconds: float = 3.0
    celestrak_group: str = "active"
    celestrak_gp_url: str = "https://celestrak.org/NORAD/elements/gp.php"
    celestrak_satcat_url: str = "https://celestrak.org/satcat/records.php"
    catalog_sync_hours: int = 24
    catalog_max_objects: Optional[int] = None


settings = Settings()
