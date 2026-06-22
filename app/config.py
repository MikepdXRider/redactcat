"""Application settings resolved from environment variables and .env via pydantic-settings.

Imported as the `settings` singleton throughout the app. JWT_SECRET is required —
the app will not start without it.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    DATABASE_URL: str = "sqlite:///./redactcat.db"
    APP_ENV: str = "development"

    JWT_SECRET: str
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 30

    S3_BUCKET: str

    # EventBridge Scheduler — injected by Terraform; empty in local dev (scheduling is best-effort)
    EXPIRE_JOB_LAMBDA_ARN: str = ""
    SCHEDULER_EXECUTION_ROLE_ARN: str = ""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
