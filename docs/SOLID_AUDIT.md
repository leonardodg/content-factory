# SOLID_AUDIT — 2026-10-09

Auditoria documentada da fundação + coletores + banco (Tasks 1–18,
branch `update/fundacao-2026-10-07`). Padrão: `python-quality-standards`
(referência `solid.md`).

## 1. Métricas

### 1.1 Coleta (comandos exatos, 2026-10-09)

```bash
uv run ruff check --statistics .            # exit 0, saída vazia = 0 achados
uv run python -m radon cc -s src            # 116 funções/class(es) avaliadas
uv run bandit -q -c pyproject.toml -r src/  # exit 0
uv run pyright                              # 0 erros (strict)
```

### 1.2 Piores funções (cc ≥ 13)

| cc | Rank | Local | Papel |
|---|---|---|---|
| 14 | C | `src/content_factory/infra/ssrf.py:12` `assert_public_http` | parse + DNS + bloqueio de privado/redireciona |
| 13 | C | `src/content_factory/cli/main.py:119` `collect_cmd` | envelope + 4 ramos de erro + exit codes |

Nenhuma função em D/E/F. Limiar da casa (cc ≥ 13) só estes 2 — ambos são
fronteiras de I/O/CLI finais por desenho (thin shell), não lógica de domínio.

### 1.3 Distribuição (funções cc ≥ 6 por módulo)

| Módulo | Funções cc ≥ 6 | Detalhe |
|---|---|---|
| `infra/ssrf.py` | 1 | `assert_public_http` (14) |
| `cli/main.py` | 2 | `collect_cmd` (13), `score_cmd` (6) |
| `domain/scoring.py` | 1 | `viral_score` (8) |
| `app/collect.py` | 1 | `collect` (7) |
| `infra/db/repositories.py` | 1 | `_snapshot` (7) |

6 de 116 funções (5%) com cc ≥ 6; mediana do projeto é A (1–4). A
complexidade vive nas fronteiras (CLI/SSRF/repo), não no domínio.

### 1.4 Nota de tooling (não é achado de código)

- `ruff` sem regras desativadas além do `select` da casa (`E,F,I,UP,B,SIM,RUF`);
  `bandit` com `B101` aceito só fora de `src/` (tests fora do scan por config).
- `pyright` strict nunca rebaixado; testes incluídos no `include`.

## 2. Single Responsibility / Open-Closed

- **SR** — `infra/ssrf.py:12` junta 3 motivos de mudança (parse de URL,
  resolução DNS, política de bloqueio); mudança de política (ex.: permitir
  link-local em dev) puxa edição da mesma função que faz I/O de DNS. Baixo:
  função única de fronteira, testada; extrair política se a regra crescer.
- **SR** — `cli/main.py` é composition root + envelope em 1 arquivo (~350
  linhas); cada comando é uma costura fina. Aceitável para 6 comandos; se
  dobrar, separar módulos por comando.
- **O** — coletores estendem via `registry.register` (`infra/collectors/registry.py:11`);
  platform nova = classe nova + 1 linha no `register_defaults`
  (`infra/collectors/__init__.py:9`) — nenhum `if platform ==` no use-case.
- **O** — `app/collect`/`app.score` não conhecem infra: novos repositórios/locks
  entram por `app/ports.py` (Protocol), teste AST garante `app/` sem import de
  `infra/`.

## 3. Liskov / Interface Segregation / Dependency Inversion

- **L** — todos os coletores satisfazem `Collector` (`app/ports.py:16`)
  estruturalmente; `FakeCollector` do teste roda no mesmo use-case do coletor
  real (e2e smoke prova substituição).
- **I** — ports pequenos por papel: `VideoRepository` (2 métodos),
  `SegmentRepository` (2), `CollectRunRepository` (2), `RateLimiter` (1),
  `Lock` (2) — nenhum "god repository".
- **D** — dependência de `Protocol` de casa, não de tipo de lib: domínio puro
  (pydantic models + dataclasses); SQLAlchemy isolado em `infra/db`; redis e
  httpx atrás de adaptadores finos; `SsrfGuard` é Protocol injetado pela
  composition root (`cli/main.py:166`).

## 4. Backlog priorizado

| # | Prioridade | Esforço | Item (arquivo:linha) | Por quê |
|---|---|---|---|---|
| 1 | **alta** | M | `app/collect.py:71-74` — `runs.record` no `finally` com transação abortada mascara o erro raiz (`InFailedSqlTransaction` no playtest T18) | diagnóstico de produção quebrado; savepoint no record ou savepoint por candidato (precedente: score, T16) |
| 2 | alta | M | `infra/lock.py` — `release` = `DEL` sem token de holder (ledger T11) | holder antigo pode liberar o lock do novo após TTL |
| 3 | média | S | `infra/ratelimit.py` — `INCR`+`EXPIRE` não atômicos (ledger T11) | crash entre os 2 = contador sem TTL, lockout permanente |
| 4 | média | S | `app/score.py` — `ScoreOutcome` sem detalhe do erro (ledger T16) | envelope `partial` undiagnosável |
| 5 | baixa | S | `infra/ssrf.py:12` — extrair política do resolver (SR) | prepara hardening TOCTOU (spec de segurança futura) |
| 6 | baixa | S | `cli/main.py` — split por comando se >6 comandos | preventivo |

## 5. Conclusão

**GO** — pode seguir para merge. Não há violação SRP/OCP/LSP/ISP/DIP que
trave evolução nas frentes 1–2 do PRD; os 2 pontos de cc ≥ 13 são fronteiras
delimitadas e testadas; backlog item 1 (mascaramento de erro) é candidato a
issue imediata pós-merge, não trava do merge.
