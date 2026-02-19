from pydantic_settings import BaseSettings
from typing import Optional

class Settings(BaseSettings):
    # Database
    DATABASE_URL: str

    # Auth
    ACCESS_TOKEN_SECRET_KEY: str
    REFRESH_TOKEN_SECRET_KEY: str
    ALGORITHM: str
    SIGN_UP_TEMPORARY_TOKEN_EXP_MIN: int
    ACCESS_TOKEN_EXP_MIN: int
    REFRESH_TOKEN_EXP_MIN: int

    # Telegram
    TELEGRAM_TOKEN: str
    TELEGRAM_CHAT_ID: int
    ERR_THREAD_ID: int
    NEW_USER_THREAD_ID: int
    INFO_THREAD_ID: int
    BAKERY_TICKET_THREAD_ID: int
    RATE_THREAD_ID: int
    HARDWARE_CLIENT_ERROR_THREAD_ID: int
    TELEGRAM_PROXY_URL: Optional[str] = None

    # SMS
    SMS_KEY: str

    # MQTT
    MQTT_BROKER_HOST: str
    MQTT_BROKER_PORT: int
    MQTT_PUBLISH_TIMEOUT_S: float = 2.0

    # Redis
    REDIS_URL: str

    # Celery
    CELERY_BROKER_URL: str
    ENABLE_AUTO_DISPATCH_READY_TICKETS: bool = True

    class Config:
        env_file = "../.env"  # only needed for local/dev; ignored in Docker if env vars already set
        case_sensitive = True   # match variable names exactly

settings = Settings()
