# Contrato dos envelopes (C3)

_Plano de revisão sênior §C3; segunda auditoria externa (2026-10-15)._

Um único conjunto de funções (`app/results.py`) transforma o resultado de
cada caso de uso em dict. A **CLI** acrescenta só `command` e o exit code; o
**worker** usa o mesmo dict por segmento e o agrega por job. Testes de
paridade (`tests/cli/test_cli.py::test_*_envelope_parity_cli_and_worker`)
garantem que o mesmo resultado sai igual nos dois lados.

## Semântica

| `ok` | `partial` | Significado | CLI exit | Campos |
| --- | --- | --- | --- | --- |
| `true` | `false` | tudo certo | `0` | contagens |
| `true` | `true` | algo gravou e algo falhou | `2` | contagens (`failed` > 0 diz o quê) |
| `false` | `false` | nada útil | `1` | `error: {code, message}` **obrigatório**, contagens quando houver |

`partial` está **sempre presente** (antes só aparecia quando `true`).

## Códigos de erro (`error.code`)

| Origem | Código |
| --- | --- |
| collect com todas as plataformas falhando | `error_code` da 1ª run falha (`auth_expired`, `rate_limited`, `blocked`…) |
| score sem nenhuma linha gravada | `internal_error` |
| research sem nenhuma fonte nem rede social | `research_failed` |
| lock do segmento ocupado | `ja_em_execucao` |
| exceção que escapou do caso de uso (lock, banco) | `internal_error`, com `error.type` = classe da exceção |
| CLI antes do caso de uso | `segment_not_found`, `usage_error` |

Mensagens nunca levam dado bruto (R5): a causa detalhada fica no log,
redigida.

## Envelope do job (worker)

```json
{"ok": false, "partial": true, "segments": {
  "velas-artesanais": {"ok": true, "partial": true, "found": 13, "failed": 1},
  "resina-artesanal": {"ok": false, "partial": false,
                       "error": {"code": "internal_error", "message": "…", "type": "DataError"}}
}}
```

- `ok` do job = todos os segmentos `ok`;
- `partial` do job = algum segmento parcial, ou algum `ok` com outro falhando.

## Mudança em relação ao formato anterior (2026-10-15)

- worker: execução parcial era `ok: false, partial: true`; agora é
  `ok: true, partial: true`, igual à CLI. O `score_job` ganhou `partial`.
- worker: erro de segmento era `{"error": "<texto>", "error_type": "X"}`;
  agora é `{"error": {"code", "message", "type"}}`.
- CLI: `partial: false` aparece também no sucesso; falha do collect passa a
  trazer as contagens.
