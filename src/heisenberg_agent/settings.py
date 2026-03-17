"""Application settings: pydantic-settings + YAML merge."""

from pathlib import Path
from typing import Any

import yaml
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _load_yaml(path: Path) -> dict[str, Any]:
    """Load a YAML file, return empty dict if missing."""
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


class DatabaseSettings(BaseSettings):
    url: str = "sqlite:///data/db/heisenberg.db"


class CollectorSettings(BaseSettings):
    base_url: str = "https://heisenberg.kr"
    login_url: str = "https://heisenberg.kr/login/"
    latest_url: str = "https://heisenberg.kr/latest/"
    auth_mode: str = "playwright_storage_state"
    html_source_of_truth: str = "rendered_dom"
    max_pages_to_scan: int = 3
    max_articles_per_cycle: int = 20
    duplicate_safety_window_days: int = 7


class AnalysisSettings(BaseSettings):
    analysis_version: str = "analysis.v1"
    prompt_bundle_version: str = "prompt-bundle.v1"


class VectorDBSettings(BaseSettings):
    enabled: bool = True
    provider: str = "chromadb"
    persist_dir: str = "./data/vectordb"
    collection_name: str = "heisenberg_articles"
    embedding_provider: str = "openai"
    embedding_model: str = "text-embedding-3-small"
    embedding_version: str = "embed.v1"


class NotionSettings(BaseSettings):
    enabled: bool = True
    api_version: str = "2022-06-28"
    sync_mode: str = "one_way"
    dry_run: bool = False


class LoggingSettings(BaseSettings):
    level: str = "INFO"
    file: str = "logs/heisenberg.log"


class SchedulerSettings(BaseSettings):
    cron_hours: list[int] = [8, 13, 19]
    max_instances: int = 1
    coalesce: bool = True
    misfire_grace_time_seconds: int = 3600


class AppSettings(BaseSettings):
    """Root settings. Loads .env for secrets, settings.yaml for structure."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        extra="ignore",
    )

    timezone: str = "Asia/Seoul"
    environment: str = "local"
    data_dir: str = "./data"
    log_dir: str = "./logs"

    # Secrets from .env
    heisenberg_username_or_email: str = ""
    heisenberg_password: str = ""
    notion_api_key: str = ""
    notion_parent_page_id: str = ""
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    log_level: str = "INFO"
    manual_trigger_token: str = ""
    manual_trigger_bind: str = "127.0.0.1"
    manual_trigger_port: int = 8321

    # Sub-settings (populated from YAML)
    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    collector: CollectorSettings = Field(default_factory=CollectorSettings)
    analysis: AnalysisSettings = Field(default_factory=AnalysisSettings)
    vectordb: VectorDBSettings = Field(default_factory=VectorDBSettings)
    notion: NotionSettings = Field(default_factory=NotionSettings)
    logging: LoggingSettings = Field(default_factory=LoggingSettings)
    scheduler: SchedulerSettings = Field(default_factory=SchedulerSettings)


def load_settings(
    config_path: str = "config/settings.yaml",
) -> AppSettings:
    """Load settings by merging YAML config with .env overrides."""
    yaml_data = _load_yaml(Path(config_path))

    # Flatten 'app' level into root if present
    app_data = yaml_data.pop("app", {})
    merged = {**app_data, **yaml_data}

    return AppSettings(**merged)
