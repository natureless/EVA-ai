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
    storage_backend: str = "sqlite"     # sqlite | postgresql
    database_url: str = ""              # PostgreSQL connection URL (when backend=postgresql)

    # LLM settings
    llm_timeout_sec: float = 60.0       # timeout for individual LLM API calls
    llm_max_retries: int = 2            # max retries for transient LLM errors

    # GitHub connector
    github_webhook_secret: str = ""
    github_api_token: str = ""
    github_poll_interval_sec: int = 300
    github_poll_repos: str = ""         # comma-separated "owner/repo,..."

    # Parallelism
    cognition_worker_count: int = 1     # number of parallel event-processing workers (1-8)

    # MVSC feature flag — set to "true" to enable the new pipeline
    enable_mvsc_pipeline: bool = False
    mvsc_subject_id: str = "eva-001"

    @field_validator("github_poll_interval_sec")
    @classmethod
    def validate_poll_interval(cls, v: int) -> int:
        if v < 30:
            raise ValueError(f"github_poll_interval_sec must be >= 30, got {v}")
        return v

    # Vector search
    embedding_provider: str = "local"       # "local" | "none"
    embedding_model_name: str = "all-MiniLM-L6-v2"
    embedding_alpha: float = 0.3           # BM25 weight in hybrid search (0.0-1.0)
    embedding_rerank_k: int = 0            # 0 = no re-rank
    vector_index_path: Path = Path("data/vector_index.faiss")

    @field_validator("embedding_alpha")
    @classmethod
    def validate_alpha(cls, v: float) -> float:
        if not (0.0 <= v <= 1.0):
            raise ValueError(f"embedding_alpha must be 0.0-1.0, got {v}")
        return v

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
