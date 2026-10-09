from sqlalchemy.engine import make_url

from content_factory.config import Settings
from content_factory.infra.db.base import get_session_factory


def test_get_session_factory_binds_settings_url() -> None:
    settings = Settings(database_url="postgresql+psycopg://user:pass@localhost:5432/db")
    factory = get_session_factory(settings)
    engine = factory.kw["bind"]
    assert engine is not None
    assert engine.url == make_url(settings.database_url)
    assert factory.kw.get("expire_on_commit") is False
