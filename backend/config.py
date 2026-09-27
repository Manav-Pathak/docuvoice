from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_DIR = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    app_name: str = "DocuVoice"
    host: str = "127.0.0.1"
    port: int = 8000
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    upload_limit_mb: int = 15
    data_dir: Path = ROOT_DIR / ".data"
    schema_dir: Path = ROOT_DIR / "backend" / "schemas"
    paddle_model_dir: Path = ROOT_DIR / ".models" / "paddleocr"
    whisper_model_path: Path = ROOT_DIR / ".models" / "faster-whisper-small"
    gemini_api_key: str | None = None
    gemini_model: str = "gemini-2.5-flash"

    model_config = SettingsConfigDict(
        env_file=ROOT_DIR / ".env",
        env_prefix="DOCUVOICE_",
        extra="ignore",
    )

    @property
    def allowed_origins(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    return settings
