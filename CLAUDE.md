# content-factory — regras imutáveis

- Estado: ver docs/HANDOFF.md (única fonte de estado)
- Plano: docs/ai-plans/ antes de qualquer código; TDD sempre
- pyright strict 0 erros — nunca rebaixar config
- domain/ é puro (sem I/O); infra implementa app/ports.py
- Nenhum segredo em arquivo rastreado; .env nunca commita
- Gates antes de "pronto": pytest, ruff, pyright, alembic check, bandit,
  pip-audit, mkdocs --strict, teste de privacidade (ver spec §10)
- CLI devolve só envelope {"ok": ...}; dados no banco
