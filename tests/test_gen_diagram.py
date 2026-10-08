import importlib
import sys
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Column
from sqlalchemy.types import Double, Numeric

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def _gen_diagram() -> Any:
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    return importlib.import_module("gen_diagram")


def test_type_map_covers_sa_double() -> None:
    mod = _gen_diagram()
    assert mod._type(Column("retention_pct", Double[float]())) == "DOUBLE"


def test_unknown_type_fails_loudly_with_column_info() -> None:
    mod = _gen_diagram()
    with pytest.raises(SystemExit) as excinfo:
        mod._type(Column("mystery_col", Numeric()))
    message = str(excinfo.value)
    assert "mystery_col" in message
    assert "NUMERIC" in message
