import os

import pytest

requires_infra = pytest.mark.skipif(
    os.getenv("TEST_DATABASE_URL") is None,
    reason="TEST_DATABASE_URL ausente — suba o compose (ver docs/dev/dev-environment.md)",
)
