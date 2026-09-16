"""Configuration settings for sd-notif service."""
from typing import List, Optional
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    # ELMA365 Settings (PROD)
    ELMA_BASE_URL: str = "https://ek7ixgofmtiik.elma365.ru"
    ELMA_API_TOKEN: str = "f62603fa-7794-444b-ad68-8816954f871b"

    # Telegram Bot Settings
    TELEGRAM_BOT_TOKEN: str = "8879706556:AAEEbR6XQvSdE-Dc5eAFPjWQLv0iARSnNxY"
    TELEGRAM_GROUP_CHAT_ID: int = -1003374943434
    TELEGRAM_TOPIC_ID: Optional[int] = 727
    BOT_USERNAME: str = "mrdnengnotif_bot"
    TELEGRAM_PROXY: Optional[str] = "http://10.4.5.170:8888"

    # Watchdog Timing Settings
    CHECK_INTERVAL_SECONDS: int = 20
    GRACE_PERIOD_MINUTES: int = 10
    ALERT_COOLDOWN_MINUTES: int = 60

    # Shift Handover (UTC+5)
    SHIFT_TIMES: List[str] = ["08:00", "20:00"]
    TIMEZONE_OFFSET_HOURS: int = 5

    # RBAC / Supervisors Whitelist
    SUPERVISOR_USER_IDS: List[int] = [278521888, 875221453, 420241740]
    SUPERVISOR_USERNAMES: List[str] = ["w1ngman5", "dobriyytro", "Osokin_A_N"]


settings = Settings()
