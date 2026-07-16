from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """EVA application settings with environment variable support.
    
    Configuration is loaded from environment variables with EVA_ prefix.
    All paths are automatically resolved relative to the base directory.
    All timeout values are in seconds.
    """
    model_config = SettingsConfigDict(env_prefix="EVA_", extra="ignore")

    app_name: str = "EVA"
    env: str = "dev"
    host: str = "0.0.0.0"
    port: int = 8000

    base_dir: Path = Path(".")
    data_dir: Path = Path("data")
    log_dir: Path = Path("logs")
    log_file: Path = Path("eva.log")
    log_level: str = "INFO"
    db_path: Path = Path("data/eva.db")
    ui_dir: Path = Path("ui/web")
    template_dir: Path = Path("ui/web/templates")
    static_dir: Path = Path("ui/web/static")
    snapshot_dir: Path = Path("data/snapshots")
    latest_snapshot_path: Path = Path("data/snapshots/latest.json")
    profile_path: Path = Path("data/profile.json")
    persona_path: Path = Path("data/persona.json")
    self_model_path: Path = Path("data/self_model.json")

    tick_interval_sec: float = 0.5
    queue_poll_timeout_sec: float = 0.5
    max_recent_items: int = 20
    request_timeout_sec: float = 8.0
    result_ttl_sec: float = 60.0
    scheduler_tick_interval_sec: int = 10
    scheduler_maintenance_interval_sec: int = 60
    scheduler_snapshot_interval_sec: int = 120
    stagnation_threshold_sec: int = 86400
    reminder_cooldown_sec: int = 43200
    enable_v02_pipeline: bool = False
    storage_backend: str = "sqlite"     # sqlite | postgresql
    database_url: str = ""              # PostgreSQL connection URL (when backend=postgresql)

    @field_validator("port")
    @classmethod
    def validate_port(cls, v: int) -> int:
        if not (1 <= v <= 65535):
            raise ValueError(f"Port must be between 1 and 65535, got {v}")
        return v

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        valid_levels = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        if v.upper() not in valid_levels:
            raise ValueError(f"Log level must be one of {valid_levels}, got {v}")
        return v.upper()

    @field_validator("tick_interval_sec", "queue_poll_timeout_sec", "request_timeout_sec", "result_ttl_sec")
    @classmethod
    def validate_positive_float(cls, v: float) -> float:
        # Convert to float if string
        if isinstance(v, str):
            try:
                v = float(v)
            except ValueError:
                raise ValueError(f"Must be a valid float, got {v}")
        if v <= 0:
            raise ValueError(f"Timeout values must be positive, got {v}")
        return v


settings = Settings()
