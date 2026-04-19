from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
import os

from dotenv import load_dotenv

load_dotenv()


def _int_env(key: str, default: int) -> int:
    value = os.getenv(key)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    app_name: str = os.getenv("APP_NAME", "Date Agent MVP")
    app_env: str = os.getenv("APP_ENV", "dev")
    db_path: str = os.getenv("DB_PATH", "data/date_agent.db")
    glm_api_key: str = os.getenv("GLM_API_KEY", "")
    glm_base_url: str = os.getenv("GLM_BASE_URL", "https://open.bigmodel.cn/api/paas/v4/")
    chat_model: str = os.getenv("GLM_CHAT_MODEL", "glm-4-flash")
    chat_fallback_model: str = os.getenv("GLM_CHAT_FALLBACK_MODEL", "glm-4-plus")
    embedding_model: str = os.getenv("GLM_EMBEDDING_MODEL", "")
    tavily_api_key: str = os.getenv("TAVILY_API_KEY", "")
    web_search_timeout_sec: float = float(os.getenv("WEB_SEARCH_TIMEOUT_SEC", "10"))
    web_search_max_results: int = int(os.getenv("WEB_SEARCH_MAX_RESULTS", "5"))
    local_address: str = os.getenv("LOCAL_ADDRESS", "")
    default_weather_location: str = os.getenv("DEFAULT_WEATHER_LOCATION", "")
    local_timezone: str = os.getenv("LOCAL_TIMEZONE", "Asia/Shanghai")
    local_context_hint: str = os.getenv("LOCAL_CONTEXT_HINT", "")
    timeline_start_hour: int = _int_env("TIMELINE_START_HOUR", 8)
    fixed_schedule_file: str = os.getenv("FIXED_SCHEDULE_FILE", "data/fixed_schedules.json")

    @property
    def db_file(self) -> Path:
        return Path(self.db_path).resolve()

    @property
    def fixed_schedule_config_file(self) -> Path:
        return Path(self.fixed_schedule_file).resolve()


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def build_local_context_block(settings: Settings) -> str:
    address = settings.local_address.strip() or "N/A"
    weather_location = settings.default_weather_location.strip() or "N/A"
    timezone = settings.local_timezone.strip() or "Asia/Shanghai"
    hint = settings.local_context_hint.strip() or "N/A"

    return (
        f"Local address: {address}\n"
        f"Default weather location: {weather_location}\n"
        f"Local timezone: {timezone}\n"
        f"Local context hint: {hint}"
    )
