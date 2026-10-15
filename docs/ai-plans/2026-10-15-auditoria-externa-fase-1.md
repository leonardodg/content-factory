# Auditoria externa 2026-10-15 — fase 1 do roadmap (integridade operacional)

Origem: auditoria sênior externa colada pelo usuário em 2026-10-15
(registro em `REVISAO_2026-10-14.md` § "Auditoria externa"). Os 4 pontos
novos foram conferidos no código antes deste plano. Decisão mantida:
**corrigir, não refatorar**; nada de arquitetura nova durante o experimento.

## Tasks (TDD, 1 commit por task, 1 PR)

### T1 — Ciclo de vida dos clientes HTTP (4.1, parte do C1)

Confirmado: `YoutubeCollector` e `InstagramCollector` criam
`httpx.AsyncClient` no `__init__` e nada os fecha; `compose_app_deps` só
registra engine e Redis no `AsyncExitStack`.

- `aclose()` idempotente nos dois coletores.
- `compose_app_deps` registra o fechamento de **todo** coletor resolvido que
  tenha `aclose`, cada um isolado (falha ao fechar um é logada e não impede
  os demais nem o `engine.dispose`), também quando o startup falha depois do
  registro.
- Testes: fecha uma vez; falha de um não impede os outros; startup que falha
  depois do registro fecha os coletores.

### T2 — Web vazio (4.3, A-2(b))

Confirmado na fonte do `ddgs` (`base.py`: `request` devolve `None` para
status ≠ 200 sem exceção; `ddgs.py`: sem resultado e sem exceção ⇒
`"No results found."`): **busca vazia legítima e engine bloqueada (403/429)
são indistinguíveis pela biblioteca.** Não inventar classificação:

- WARNING `web: busca vazia (ambígua: sem resultado ou engines bloqueadas)`
  ao devolver `[]`, para o operador ver no log;
- relatório diário passa a ter a coluna `web_empty` = runs `web` `ok` com
  `found = 0`, marcada como **ambígua**; a limitação fica registrada na
  REVISAO e no manual do experimento.

### T3 — Contrato de estado dos singletons (4.2, C1)

Documento `docs/architecture/contrato-singletons.md` respondendo, para cada
componente compartilhado (coletores, `CooldownBreaker`, `PlatformSlots`,
`RedisRateLimiter`, registry), as 5 perguntas da auditoria: escopo do
estado, mutação concorrente, ordem de término, reset após erro/cancelamento,
efeito de mais de um processo. Restrição explícita: breaker em memória
pressupõe **1 processo worker**.

### T4 — README (4.4)

Trocar contagens de testes escritas à mão pelo badge do CI (fonte única);
sem números que envelhecem.

## Deploy

Rebuild do worker entre o score das 21:30 e o research das 07:00 BRT.

## Fora desta fase

C2 (`cf ops-report`), C3 (envelopes), D1/D2 e ADR-0008 seguem no plano de
revisão sênior, depois do B3.
