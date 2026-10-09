.PHONY: test test-int lint typecheck security docs privacy gates
test:        ; uv run pytest -q -m "not integration and not e2e"
test-int:    ; uv run pytest -q -m integration
lint:        ; uv run ruff check . && uv run ruff format --check .
typecheck:   ; uv run pyright
security:    ; uv run bandit -q -c pyproject.toml -r src/ && uv run pip-audit
docs:        ; uv run --only-group docs mkdocs build --strict
privacy:     ; uv run python scripts/check_privacy.py
gates: test lint typecheck security docs privacy
