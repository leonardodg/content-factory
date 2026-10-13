from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    database_url: str = "postgresql+psycopg://contentfactory@localhost/contentfactory"
    redis_url: str = "redis://localhost:6379/0"
    # Cota de API e circuit breaker são globais por chave de API, não por fila:
    # operacionalmente aponta sempre para /0, independente do banco de fila.
    # None = usa redis_url (comportamento pré-Task 3, worker único).
    rate_limit_redis_url: str | None = None
    youtube_api_key: str = ""
    instagram_access_token: str = ""
    ig_user_id: str = ""
    tiktok_ms_token: str = ""
    tiktok_proxy: str | None = None


def get_settings() -> Settings:
    return Settings()
