from typing import get_type_hints

from content_factory.app import ports
from content_factory.domain.errors import CollectorError


def test_collector_protocol_declares_discover() -> None:
    hints = get_type_hints(ports.Collector.discover)
    assert set(hints) >= {"segment", "limit", "return"}


def test_collector_error_carries_code() -> None:
    err = CollectorError("blocked", "tiktok")
    assert err.code == "blocked"
    assert err.platform == "tiktok"
