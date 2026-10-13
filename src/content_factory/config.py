from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    database_url: str = "postgresql+psycopg://contentfactory@localhost/contentfactory"
    redis_url: str = "redis://localhost:6379/0"
    # Cota de API e circuit breaker são globais por chave de API, não por fila:
    # operacionalmente aponta sempre para /0, independente do banco de fila.
    # Default = redis_url (worker único, comportamento pré-Task 3).
    rate_limit_redis_url: str = ""

    @model_validator(mode="after")
    def _default_rate_limit_redis_url(self) -> Settings:
        if not self.rate_limit_redis_url:
            self.rate_limit_redis_url = self.redis_url
        return self

    youtube_api_key: str = ""
    instagram_access_token: str = ""
    ig_user_id: str = ""
    tiktok_ms_token: str = ""
    tiktok_proxy: str | None = None


def get_settings() -> Settings:
    return Settings()
