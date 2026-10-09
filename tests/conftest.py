import os
from collections.abc import Generator

import pytest

requires_infra = pytest.mark.skipif(
    os.getenv("TEST_DATABASE_URL") is None,
    reason="TEST_DATABASE_URL ausente — suba o compose (ver docs/dev/dev-environment.md)",
)


@pytest.fixture(autouse=True)
def _isolate_collector_registry() -> Generator[None]:
    """Registry é global no processo — isola registros entre testes (deferred T10)."""
    from content_factory.infra.collectors.registry import restore, snapshot

    state = snapshot()
    restore({})
    yield
    restore(state)
