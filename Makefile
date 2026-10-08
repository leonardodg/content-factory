.PHONY: test test-int lint typecheck security docs gates
test:        ; uv run pytest -q -m "not integration and not e2e"
test-int:    ; uv run pytest -q -m integration
lint:        ; uv run ruff check . && uv run ruff format --check .
typecheck:   ; uv run pyright
security:    ; uv run bandit -q -c pyproject.toml && uv run pip-audit
docs:        ; uv run --only-group docs mkdocs build --strict
gates: test lint typecheck security docs
