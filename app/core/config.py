"""
app/core/config.py
──────────────────
Centralised application settings loaded from environment variables via Pydantic-Settings.
All secrets are read from .env; no value is ever hard-coded here.
"""

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Application ──────────────────────────────────────────────────────────
    app_name: str = "Employee Performance Evaluation"
    app_version: str = "1.0.0"
    debug: bool = False
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"

    # ── API Security ─────────────────────────────────────────────────────────
    api_secret_key: str = Field(..., min_length=32)
    api_token_expire_minutes: int = 1440

    # ── Admin credentials (used by /api/v1/auth/token for Swagger testing) ───
    admin_username: str = "admin"
    admin_password: str = "admin123"

    # ── Internal Database (SQLite → future PostgreSQL) ────────────────────────
    database_url: str = "sqlite+aiosqlite:///./data/evaluation.db"
    database_echo: bool = False

    # ── MySQL Evaluation Results Database (WRITE) ─────────────────────────────
    mysql_summary_url: str = ""

    # ── MySQL Source Databases (READ-ONLY) ────────────────────────────────────
    mysql_crm_host: str = "localhost"
    mysql_crm_port: int = 3306
    mysql_crm_user: str = ""
    mysql_crm_password: str = ""
    mysql_crm_database: str = ""

    mysql_hr_host: str = "localhost"
    mysql_hr_port: int = 3306
    mysql_hr_user: str = ""
    mysql_hr_password: str = ""
    mysql_hr_database: str = ""

    # ── GitLab REST API ────────────────────────────────────────────────────────────
    gitlab_url: str = "https://gitlab.com"
    gitlab_token: str = ""
    gitlab_group_id: str = ""  # only needed for REST API fallback

    # ── GitLab PostgreSQL (direct DB — preferred, no group_id needed) ─────────────
    gitlab_db_host: str = ""
    gitlab_db_port: int = 5432
    gitlab_db_name: str = "gitlabhq_production"
    gitlab_db_user: str = ""
    gitlab_db_password: str = ""
    gitlab_db_namespace: str = ""  # optional: restrict to a group namespace path

    # ── Anthropic (Primary) ───────────────────────────────────────────────────
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-4-5"

    # ── Groq (Fallback) ───────────────────────────────────────────────────────
    groq_api_key: str = ""
    groq_model: str = "llama-3.3-70b-versatile"

    # ── Redis / Celery ────────────────────────────────────────────────────────
    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str = "redis://localhost:6379/0"
    celery_result_backend: str = "redis://localhost:6379/1"

    # ── Scheduling ────────────────────────────────────────────────────────────
    monthly_eval_cron_hour: int = 2
    monthly_eval_cron_day_of_month: int = 1

    # ── Uploads ───────────────────────────────────────────────────────────────
    max_upload_size_mb: int = 10
    allowed_excel_extensions: str = ".xlsx,.xls"

    @field_validator("database_url")
    @classmethod
    def validate_database_url(cls, v: str) -> str:
        allowed_prefixes = ("sqlite+aiosqlite://",)
        if not any(v.startswith(p) for p in allowed_prefixes):
            raise ValueError(
                "DATABASE_URL must use 'sqlite+aiosqlite://'"
            )
        return v

    @property
    def mysql_crm_dsn(self) -> str:
        """Build an aiomysql-compatible DSN for the CRM database."""
        return (
            f"mysql+aiomysql://{self.mysql_crm_user}:{self.mysql_crm_password}"
            f"@{self.mysql_crm_host}:{self.mysql_crm_port}/{self.mysql_crm_database}"
        )

    @property
    def mysql_hr_dsn(self) -> str:
        """Build an aiomysql-compatible DSN for the HR/Attendance database."""
        return (
            f"mysql+aiomysql://{self.mysql_hr_user}:{self.mysql_hr_password}"
            f"@{self.mysql_hr_host}:{self.mysql_hr_port}/{self.mysql_hr_database}"
        )

    @property
    def has_gitlab_db(self) -> bool:
        """True when direct PostgreSQL access to GitLab DB is configured."""
        return bool(
            self.gitlab_db_host and self.gitlab_db_user and self.gitlab_db_password
        )

    @property
    def allowed_excel_ext_list(self) -> list[str]:
        return [ext.strip() for ext in self.allowed_excel_extensions.split(",")]


@lru_cache
def get_settings() -> Settings:
    """
    Returns a cached singleton Settings instance.
    Using lru_cache ensures the .env file is read only once per process.
    """
    return Settings()  # type: ignore[call-arg]
