"""环境变量与应用全局配置（pydantic-settings）。

变量清单与口径见《代码结构与核心工程详细设计规范》第 6 章，模板见 backend/.env.example。
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from urllib.parse import quote_plus

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]
STATIC_DIR = BACKEND_DIR / "static"
ENV_FILE = BACKEND_DIR / ".env"


class Settings(BaseSettings):
    """应用配置。字段名与环境变量名一一对应（大小写不敏感）。"""

    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---- 数据库 ----
    db_host: str = "127.0.0.1"
    db_port: int = 3306
    db_user: str = "root"
    db_password: str = ""
    db_name: str = "dawn_meow"
    database_url: str | None = None
    db_echo: bool = False

    # ---- LLM（星区推演导演）----
    llm_provider: str = "deepseek"
    llm_base_url: str = "https://api.deepseek.com/v1"
    llm_api_key: str = ""
    llm_model: str = "deepseek-chat"
    llm_timeout_seconds: int = 30
    llm_max_retry: int = 2
    llm_batch_size_max: int = 200
    llm_daily_call_budget: int = 200
    llm_daily_token_budget: int = 200000

    # ---- 游戏 ----
    game_tick_ms: int = 100
    autosave_interval_ms: int = 15000
    log_level: str = "INFO"

    # ---- 对账协议（数据库设计定稿 §8.2：偏差 > 0.5% 记 SNAPSHOT_DRIFT）----
    snapshot_drift_tolerance: float = 0.005

    @property
    def sqlalchemy_url(self) -> str:
        """SQLAlchemy 连接串。显式 DATABASE_URL 优先（测试可指向 SQLite）。"""
        if self.database_url:
            return self.database_url
        password = quote_plus(self.db_password) if self.db_password else ""
        credentials = f"{quote_plus(self.db_user)}:{password}@" if password else f"{quote_plus(self.db_user)}@"
        return (
            f"mysql+aiomysql://{credentials}{self.db_host}:{self.db_port}/"
            f"{self.db_name}?charset=utf8mb4"
        )

    @property
    def is_sqlite(self) -> bool:
        return self.sqlalchemy_url.startswith("sqlite")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """进程内单例配置。"""
    return Settings()


def reload_settings() -> Settings:
    """测试/脚本用：清空缓存后重新读取环境变量。"""
    get_settings.cache_clear()
    return get_settings()
