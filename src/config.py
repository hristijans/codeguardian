from functools import lru_cache
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    APP_NAME: str = "CodeGuardian AI"
    APP_ENV: str = "development"
    DEBUG: bool = True

    # Postgres
    POSTGRES_SERVER: str = "postgres"
    POSTGRES_PORT: int = 5432
    POSTGRES_USER: str
    POSTGRES_PASSWORD: str
    POSTGRES_DB: str = "codeguardian_db"

    # Redis
    REDIS_HOST: str = "redis"
    REDIS_PORT: int = 6379

    # RabbitMQ
    RABBITMQ_HOST: str = "rabbitmq"
    RABBITMQ_PORT: int = 5672
    RABBITMQ_USER: str
    RABBITMQ_PASSWORD: str

    # GitHub
    GITHUB_TOKEN: SecretStr
    GITHUB_WEBHOOK_SECRET: SecretStr
    # Comma-separated owner/name values. Empty means nothing is reviewed.
    REVIEW_REPOSITORIES: str = ""

    # LLM
    LLM_PROVIDER: str = "ollama"
    OLLAMA_BASE_URL: str = "http://host.docker.internal:11434"
    LLM_MODEL: str = "llama3.2"
    # How long one LLM call may take. A CPU-only review takes about a minute.
    LLM_TIMEOUT_SECONDS: float = 300.0

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    @property
    def review_repositories(self) -> set[str]:
        # Lowercased because GitHub treats repository names as case-insensitive.
        return {
            name.strip().lower()
            for name in self.REVIEW_REPOSITORIES.split(",")
            if name.strip()
        }

    @property
    def postgres_dsn(self) -> str:
        return (
            f"postgresql+asyncpg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_SERVER}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

    @property
    def redis_url(self) -> str:
        return f"redis://{self.REDIS_HOST}:{self.REDIS_PORT}/0"

    @property
    def rabbitmq_url(self) -> str:
        return (
            f"amqp://{self.RABBITMQ_USER}:{self.RABBITMQ_PASSWORD}"
            f"@{self.RABBITMQ_HOST}:{self.RABBITMQ_PORT}/"
        )


@lru_cache()
def get_settings() -> Settings:
    """Memoized dependency injection helper for app settings."""
    return Settings()
