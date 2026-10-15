from functools import lru_cache
from zoneinfo import ZoneInfo

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    database_url: str = "sqlite:///./gamepulse.db"
    timezone: str = "Europe/Moscow"
    interval_seconds: int = 3600
    lease_seconds: int = 21600
    request_delay_seconds: float = 1.5
    max_pages_per_run: int = 30
    review_pages: int = 2
    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: str = ""
    llm_model: str = "gpt-5-nano"
    youtube_api_key: str = ""
    youtube_refresh_hours: int = 24
    admin_token: str = ""
    web_username: str = "admin"
    web_password_hash: str = ""
    session_secret: str = ""
    session_hours: int = 12
    cookie_secure: bool = False
    cover_directory: str = "data/covers"
    demo_mode: bool = False
    transcribe_audio: bool = False
    whisper_model: str = "small"

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        ZoneInfo(value)
        return value

    @field_validator(
        "interval_seconds",
        "lease_seconds",
        "max_pages_per_run",
        "review_pages",
        "session_hours",
        "youtube_refresh_hours",
    )
    @classmethod
    def positive(cls, value: int) -> int:
        if value < 1:
            raise ValueError("Must be positive")
        return value


@lru_cache
def settings() -> Settings:
    return Settings()
