"""Camadas (Etapa 2 Task 13): portas não dependem dos use-cases (DIP)."""

import ast
import inspect

import content_factory.app.ports as ports
import content_factory.app.segment_deps as segment_deps

_USE_CASES = {
    "content_factory.app.collect",
    "content_factory.app.score",
    "content_factory.app.research_pipeline",
}


def _imported_modules(module: object) -> set[str]:
    tree = ast.parse(inspect.getsource(module))  # type: ignore[arg-type]
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
        elif isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
    return found


def test_ports_do_not_import_use_cases() -> None:
    assert _imported_modules(ports) & _USE_CASES == set()


def test_segment_deps_bundle_lives_outside_ports() -> None:
    assert not hasattr(ports, "SegmentDepsBundle")
    assert not hasattr(ports, "SegmentDepsFactory")
    assert segment_deps.SegmentDepsBundle.__module__ == "content_factory.app.segment_deps"


def test_unit_of_work_is_commit_rollback_only() -> None:
    """ISP: ninguém usa a UoW como context manager — o escopo é `session_scope`."""
    from content_factory.infra.db.base import SessionUnitOfWork

    assert not hasattr(ports.UnitOfWork, "__aenter__")
    assert not hasattr(SessionUnitOfWork, "__aenter__")
    assert callable(SessionUnitOfWork.commit) and callable(SessionUnitOfWork.rollback)
