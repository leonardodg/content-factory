# TRD — CLI `cf` (contrato com agentes de AI)

> **Status:** contrato fechado ([ADR-0004](../adr/0004-cli-first.md) +
> spec §4.2). Comandos `ping` e `segment load` já existem (task 6);
> `collect`/`ingest`/`score`/`status` chegam nas tasks 11/15/16/17 —
> **comandos que ainda não existem não são anunciados** no `--help`.
> Estado real: [HANDOFF](../HANDOFF.md).

## Fronteira técnica

**Dentro:** superfície da CLI (`cli/main.py` Typer + `cli/output.py`) —
formato do envelope, exit codes, mapeamento de erros de negócio para
`error.code`.

**Não muda:** a CLI **não devolve dados** (nada de dump de resultados);
resultados ficam no banco e os agentes leem o banco (ADR-0004). Sem
FastAPI, sem output humano decorado — stdout é **uma linha JSON**.

## Contrato

### Envelope (único formato de saída)

```json
{"ok": true, "command": "segment.load", "slug": "candles", "id": 1}
{"ok": false, "command": "segment.load", "error": {"code": "invalid_segment", "message": "..."}}
```

- `envelope_ok(command, **contagens)` → `{"ok": true, "command": ...}` +
  contagens livres (`counts`, `scored`, `id`, `duration_s`…).
- `envelope_err(command, code, message)` → `{"ok": false, "command": ...,
  "error": {"code", "message"}}`.
- Serialização `json.dumps(..., ensure_ascii=False, default=str)` — UTF-8
  direto, sem escape de acento.

### Exit codes

| Code | Significado |
|---|---|
| `0` | sucesso |
| `2` | falha parcial (alguma plataforma falhou; lote seguiu) |
| `1` | falha total / erro de uso |

### Comandos

| Comando | Função | Status |
|---|---|---|
| `cf ping` | healthcheck sem banco | ✅ existe |
| `cf segment load <arquivo.yaml>` | valida (pydantic) e grava/atualiza o segmento | ✅ existe |
| `cf collect --segment <slug> [--platform a,b] [--limit N]` | coleta → upsert idempotente | task 11 |
| `cf ingest --url <url> [--segment <slug>]` | enriquece URL fornecida e grava candidato | task 15 |
| `cf score --segment <slug>` | recalcula viral score + trend decay | task 16 |
| `cf status --segment <slug>` | resumo operacional | task 17 |

### Códigos de erro (assinatura `error.code`)

| Código | Quando |
|---|---|
| `usage_error` | Typer/uso inválido (arg faltando) — exit 1 |
| `invalid_segment` | YAML de segmento rejeitado na validação (antes do DB) |
| `segment_not_found` | slug inexistente em `load`/`collect`/`score`/`status` |
| `invalid_url` | guarda SSRF rejeitou a URL do `ingest` |
| `internal_error` | exceção não mapeada (mensagem redigida) |
| `aborted` | operação abortada (Ctrl-C) |
| `blocked`, `rate_limited`, `auth_expired`, `network`, `upstream_changed`, `invalid_payload` | erros de collector propagados do use-case (TRD [coletores](trd-collectors.md)) |

## Requisitos e critérios de aceitação

- [ ] R1 Toda saída (sucesso, erro de domínio, erro de uso, abort) é
      envelope JSON em uma linha no stdout — *testes
      `tests/cli/test_output.py` + `tests/cli/test_cli.py`*
- [ ] R2 Exit codes `0|2|1` conforme tabela — *teste `test_exit_codes`*
- [ ] R3 `cf segment load <inexistente>` → `ok:false`, exit 1, **sem**
      exceção de conexão (validação antes do DB) — *teste
      `test_segment_load_missing_file_exit_1`*
- [ ] R4 `cf segment load <yaml válido>` grava de verdade (`id > 0` no
      banco) — *E2E `01`* (spec §12.1)
- [ ] R5 Comando inexistente/arg faltando → `usage_error` + exit 1 —
      *teste de uso (`main()` com `standalone_mode=False`)*
- [ ] R6 Nenhum dump de dados no envelope — *revisão de código +
      contrato dos testes* (só status/contagens)

## Interfaces e dependências

| Item | Detalhe |
|---|---|
| Módulos | `cli/output.py` (`envelope_ok`, `envelope_err`, `dumps`, `exit_code_for`), `cli/main.py` (Typer) |
| Consumidores | agentes de AI/automação: leem exit code + envelope, dados do banco (Postgres) |
| Erros vindo de | `domain.errors.DomainError` e subclasses `CollectorError` |
| Wire | factory de sessão + repositórios de `infra/db` injetados por command (DI manual) |

## Pré-condições antes de código

- [ADR-0004](../adr/0004-cli-first.md) aceito; spec §4.2 aprovada.
- Banco e repositórios (task 4), segmento YAML (task 5) — já em pé.
- Comandos novos só entram com seus use-cases (tasks 11/15/16/17).

## Riscos e limites técnicos

| Risco | Mitigação |
|---|---|
| Quebrar o envelope quebra agentes | contrato versionado aqui; qualquer mudança = TRD revisado + aviso |
| Anunciar comando inexistente | help só lista comandos reais (regra da task 6) |
| Erro vaza segredo em `message` | redação de logs/tokens (spec §7) + teste de privacidade no CI |
| Exit 2 confunde integrador | tabela acima é normativa; E2E cobre o caminho parcial |
