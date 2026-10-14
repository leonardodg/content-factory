import logging
import os
from collections.abc import Generator

import pytest

requires_infra = pytest.mark.skipif(
    os.getenv("TEST_DATABASE_URL") is None,
    reason="TEST_DATABASE_URL ausente — suba o compose (ver docs/dev/dev-environment.md)",
)

requires_redis = pytest.mark.skipif(
    os.getenv("TEST_REDIS_URL") is None,
    reason="TEST_REDIS_URL ausente — suba um Redis descartável (ver docs/dev/dev-environment.md)",
)


@pytest.fixture(autouse=True)
def _isolate_collector_registry() -> Generator[None]:
    """Registry é global no processo — isola registros entre testes (deferred T10)."""
    from content_factory.infra.collectors.registry import restore, snapshot

    state = snapshot()
    restore({})
    yield
    restore(state)


@pytest.fixture(autouse=True)
def _remove_worker_log_handler() -> Generator[None]:
    """`configure_logging` muta o logger global `content_factory`: limpa entre testes."""
    yield
    logger = logging.getLogger("content_factory")
    for handler in list(logger.handlers):
        if handler.get_name() == "cf-worker":
            logger.removeHandler(handler)
    logger.setLevel(logging.NOTSET)
