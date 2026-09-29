"""Configuration settings for sd-notif service."""
import json
import logging
from typing import List, Optional, Union
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field, field_validator


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    # ELMA365 Settings
    ELMA_BASE_URL: str = Field(default="https://example.elma365.ru", description="ELMA365 base URL")
    ELMA_API_TOKEN: str = Field(default="", description="ELMA365 Bearer token")
    ELMA_PAGE_SIZE: int = Field(default=100, description="Page size for ELMA list requests")
    ELMA_MAX_ITEMS_FETCH: int = Field(default=500, description="Max items to fetch in pagination")

    # Telegram Bot Settings
    TELEGRAM_BOT_TOKEN: str = Field(default="", description="Telegram Bot token")
    TELEGRAM_GROUP_CHAT_ID: int = Field(default=0, description="Telegram Group Chat ID")
    TELEGRAM_TOPIC_ID: Optional[int] = Field(default=None, description="Telegram Topic / Message Thread ID")
    BOT_USERNAME: str = Field(default="sd_notif_bot", description="Bot username without @")
    TELEGRAM_PROXY: Optional[str] = Field(default=None, description="Proxy for Telegram requests")
    TELEGRAM_POLL_TIMEOUT: int = Field(default=25, description="Telegram getUpdates timeout in seconds")

    # Network / SSL
    VERIFY_SSL: Union[bool, str] = Field(
        default=True,
        description="Verify SSL certificates. Can be boolean or path to ca-bundle file"
    )

    # Watchdog Timing Settings
    CHECK_INTERVAL_SECONDS: int = Field(default=20, description="Watchdog cycle interval in seconds")
    GRACE_PERIOD_MINUTES: int = Field(default=10, description="Grace period before alerting on closed ticket with open session")
    ALERT_COOLDOWN_MINUTES: int = Field(default=60, description="Cooldown between alerts for same session")

    # Shift Handover Settings
    SHIFT_TIMES: List[str] = Field(default_factory=lambda: ["08:00", "20:00"])
    TIMEZONE_OFFSET_HOURS: int = Field(default=5, description="UTC timezone offset")

    # Supervisors Whitelist (RBAC)
    SUPERVISOR_USER_IDS: List[int] = Field(default_factory=list)
    SUPERVISOR_USERNAMES: List[str] = Field(default_factory=list)

    @field_validator("SUPERVISOR_USER_IDS", mode="before")
    @classmethod
    def parse_supervisor_user_ids(cls, v):
        if isinstance(v, str):
            v = v.strip()
            if not v:
                return []
            if v.startswith("["):
                try:
                    return json.loads(v)
                except Exception:
                    pass
            return [int(x.strip()) for x in v.split(",") if x.strip()]
        return v or []

    @field_validator("SUPERVISOR_USERNAMES", mode="before")
    @classmethod
    def parse_supervisor_usernames(cls, v):
        if isinstance(v, str):
            v = v.strip()
            if not v:
                return []
            if v.startswith("["):
                try:
                    return json.loads(v)
                except Exception:
                    pass
            return [x.strip().lstrip("@") for x in v.split(",") if x.strip()]
        if isinstance(v, list):
            return [str(u).strip().lstrip("@") for u in v if str(u).strip()]
        return []

    @field_validator("SHIFT_TIMES", mode="before")
    @classmethod
    def parse_shift_times(cls, v):
        if isinstance(v, str):
            v = v.strip()
            if not v:
                return ["08:00", "20:00"]
            if v.startswith("["):
                try:
                    return json.loads(v)
                except Exception:
                    pass
            return [x.strip() for x in v.split(",") if x.strip()]
        return v or ["08:00", "20:00"]

    @field_validator("VERIFY_SSL", mode="before")
    @classmethod
    def parse_verify_ssl(cls, v):
        if isinstance(v, str):
            v_lower = v.strip().lower()
            if v_lower in ("true", "1", "yes"):
                return True
            if v_lower in ("false", "0", "no"):
                return False
            return v.strip()
        return v


settings = Settings()
