from functools import lru_cache
from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _env_files() -> tuple[str, ...]:
    app_dir = Path(__file__).resolve().parent
    candidates = [app_dir.parents[1] / ".env", app_dir.parents[0] / ".env"]
    return tuple(str(path) for path in candidates if path.exists())


class Settings(BaseSettings):
    sheetflow_data_dir: str = ""
    sheetflow_static: str = ""
    database_url: str = ""
    storage_dir: str = ""
    max_upload_bytes: int = 20 * 1024 * 1024
    max_rows_per_sheet: int = 50_000
    max_columns: int = 200
    max_sheets: int = 30
    preview_rows: int = 50
    preview_cell_chars: int = 500
    worker_poll_seconds: float = 0.5
    stale_job_seconds: int = 1800
    default_locale: str = "en-US"
    transform_version: str = "1"
    cors_origins: str = "http://127.0.0.1:5173,http://localhost:5173"
    log_level: str = "INFO"
    worker_enabled: bool = True

    model_config = SettingsConfigDict(
        env_file=_env_files() or None,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @model_validator(mode="after")
    def fill_local_paths(self):
        if self.sheetflow_data_dir:
            root = Path(self.sheetflow_data_dir)
            if not self.database_url:
                self.database_url = "sqlite:///" + (root / "sheetflow.db").resolve().as_posix()
            if not self.storage_dir:
                self.storage_dir = str(root / "storage")
        if not self.database_url:
            self.database_url = "postgresql+psycopg://symphony:symphony@127.0.0.1:5432/symphony"
        if not self.storage_dir:
            self.storage_dir = str(Path(__file__).resolve().parents[2] / "storage")
        return self

    @property
    def origins(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
