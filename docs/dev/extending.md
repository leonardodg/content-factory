# Estender o sistema

Duas extensões são requisitos de aceitação do [PRD](../produto/prd.md) —
**nenhuma delas pode tocar em `domain/` ou `app/`**.

## Novo segmento = 1 arquivo YAML (zero código)

1. Copie `segments/candles.yaml` para `segments/<slug>.yaml` e ajuste:

   | Campo | O que é |
   |---|---|
   | `slug` | identificador único (vira UNIQUE em `segments.slug`) |
   | `name` | nome legível |
   | `languages`, `platforms`, `content_types` | recorte de coleta |
   | `keywords` | alimenta busca dos coletores **e** o score de relevância |
   | `subnichos`, `audience` | agrupamentos livres (JSONB `config`) |

2. Valide e grave:

   ```bash
   uv run cf segment load segments/<slug>.yaml
   # → {"ok": true, "command": "segment.load", "slug": "<slug>", "id": N}
   ```

A validação é pydantic (`domain.models.SegmentConfig`) — arquivo inválido
dá `{"ok": false, "error": {"code": "invalid_segment", …}}` **antes** de
tocar o banco. Nenhum código novo; testes em `tests/app/test_segment.py`.

## Novo collector = 1 classe + registro

Plataformas **novas** (fora de youtube/tiktok/instagram/web) exigem também
um **ADR novo** (contexto/alternativas/consequências — ver
[ADR-0003](../adr/0003-coletores-fontes.md) como modelo); plataformas já
previstas só adicionam a classe.

1. **Implemente o Protocolo** `app/ports.py::Collector`:

   ```python
   class MeuCollector:
       platform = "meu"  # ClassVar — identificador estável

       async def discover(self, segment: SegmentConfig, limit: int) -> list[CandidateVideo]: ...
   ```

2. **Devolva `CandidateVideo`** (DTO pydantic) — upsert idempotente vem
   grátis do `UNIQUE(platform, platform_video_id)`.
3. **Erros tipados**, nunca exceção crua: `AuthExpired`, `RateLimited`,
   `UpstreamChanged`, `NetworkError`, `InvalidPayload` ou
   `CollectorError(code, platform)` (`domain/errors.py`) — o use-case
   grava o `code` em `collect_runs.error_code`.
4. **Registre** em `infra/collectors/__init__.py::register_defaults`
   (ponto único de wire — CLI e worker chamam uma vez no entrypoint).
5. **Teste com fixture** na fronteira (`httpx.MockTransport` ou stub do
   cliente), espelhando `tests/infra/collectors/`.

O que **não** muda: `domain/`, `app/`, CLI, schema. Retry/backoff/circuit
breaker ficam **só** na fronteira do collector (spec §8); o domínio nunca
retrya.

## Não é para estender por aqui

| Quer | Não faça |
|---|---|
| Novo comportamento de score | editar `viral_score()` ad-hoc — pesos mudam via TRD scoring + renormalização |
| Interface HTTP | é CLI-first ([ADR-0004](../adr/0004-cli-first.md)) — API exige ADR novo |
| Scraping de plataforma | princípio de APIs oficiais (spec §6/ADR-0003) |
