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
    # padrão global da busca do YouTube; o `search` do segmento vence.
    # Vazio = sem restrição.
    youtube_relevance_language: str = ""
    youtube_region_code: str = ""
    youtube_search_order: str = ""  # viewCount|relevance|date|rating; vazio = viewCount
    instagram_access_token: str = ""
    ig_user_id: str = ""
    tiktok_ms_token: str = ""
    tiktok_proxy: str | None = None
    # F5: liga a gravação de `web_page_scores` no score_job. Kill switch,
    # reversível sem mudar schema (migração 0010 é aditiva). Default off — só
    # ligar com veredito "aprovado" na F4 em >= 2 dos 3 segmentos PT.
    web_score_enabled: bool = False
    # R9 (Bloco 5): `viral-v2` + hashtags no job de score, DEPOIS do fase-h e
    # em transação própria. Default off: liga no deploy da Fase B
    # (`VIRAL_V2_ENABLED=1`) sem rebuild; desligar é reversível (tabela própria).
    viral_v2_enabled: bool = False
    # F1 em produção: com isto ligado, segmento sem protocolo `q1` válido faz
    # o collect da web falhar (`missing_protocol`) em vez de cair em silêncio
    # no legado de 1 consulta. Default off (compatível com o comportamento
    # anterior); ligar no app.env de produção a partir do deploy do q1.
    web_queries_required: bool = False


def get_settings() -> Settings:
    return Settings()
