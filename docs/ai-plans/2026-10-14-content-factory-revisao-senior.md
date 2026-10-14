# Revisão sênior 2026-10-14 — plano de correção

> TDD sempre: RED antes de GREEN, 1 commit por Task. Gates do CLAUDE.md antes
> de "pronto". Integração **só** em Postgres/Redis descartáveis
> (`cf-test-pg`/`cf-test-redis`). Registro dos achados:
> [`REVISAO_2026-10-14.md`](../REVISAO_2026-10-14.md).

## Decisão: corrigir, não refatorar

Os achados são de **integridade** (atomicidade, redação, diagnóstico,
contrato) e de **validação** (ranking), não de estrutura. Nenhum exige mexer
nas camadas, na composição ou na fila. Os refactors permitidos são locais e
não mudam as portas usadas pelos casos de uso:

| Refactor local | Por quê | Limite |
| --- | --- | --- |
| Scripts Lua em `infra/ratelimit.py` | atomicidade não se obtém com chamadas separadas | `RateLimiter`/`PlatformHealth` inalterados |
| `UpsertReport` no lugar de `int` em `ScoreRepository.upsert_many` | diagnóstico exige categorias | só a porta do score muda; collect não |
| `app/gate_hits.py` → contador genérico por chamada | `quota_used` precisa do mesmo mecanismo | API de `gate_hits` preservada |

Tudo o mais é correção pontual, documentação ou configuração.

## Ordem e prazos

```mermaid
flowchart LR
    subgraph AGORA["até 19/10 (sem tocar nos workers)"]
        A1["A1 rate limiter atômico"] --> A2["A2 redação"]
        A2 --> A4["A4 ruleset main"]
        A4 --> A5["A5 ADR retry"]
        B1["B1 score diagnosticável"]
        B2["B2 quota_used por chamada"]
        A3["A3 checklist da troca"]
    end
    subgraph SWAP["19/10"]
        S["troca 3→1 com A3"]
    end
    subgraph DEPOIS["após a troca"]
        B3["B3 validação do ranking"]
        C1["C1 contrato dos singletons"]
        C2["C2 ops-report"]
        C3["C3 contrato de envelopes"]
        D1["D1 supply chain"]
        D2["D2 ADR SSRF"]
    end
    AGORA --> SWAP --> DEPOIS
    B3 --> GATE{"content-factory 100%:<br/>todos os critérios de saída?"}
    GATE -- sim --> IA["Bloco IA (insta_kb, ideias, roteiros)"]
```

A1, A2 e B1 entram **antes** da troca porque o worker único vai usar esse
código. B3 depende dos dados do experimento.

---

# Bloco A — Integridade operacional

## A1 — Rate limiter atômico (R2)

**Files:** `src/content_factory/infra/ratelimit.py`,
`tests/infra/test_ratelimit_redis.py` (novo, `requires_redis`),
`tests/infra/test_ratelimit.py`, `tests/stubs.py` (`FakeRedis.eval`).

1. **Caracterização primeiro** (Redis real), para fixar o comportamento
   atual que deve ser preservado:
   - janela cheia → backoff e depois `RateLimited`;
   - espera maior que 60 s → `RateLimited` imediato;
   - janela diária do YouTube (100/86 400 s);
   - breaker com 3 falhas → `blocked`.
2. **RED:** chave `cf:rl:<p>` criada **sem TTL** (simula crash entre `INCR`
   e `EXPIRE`) → `acquire` hoje levanta `RateLimited` para sempre; o
   esperado é recuperar. Idem `cf:cb:<p>:fails` sem TTL.
3. **RED de concorrência:** 20 `acquire` simultâneos de 3 clientes com
   janela 10 → exatamente 10 passam e a chave tem TTL.
4. **GREEN:** um script Lua por operação (`EVAL`), que é atômico no Redis:
   `INCR` e, se `count == 1` **ou** `PTTL == -1`, `PEXPIRE`; devolve
   `{count, pttl}`. O `DECR` da espera e o `record_failure` usam o mesmo
   padrão. O ramo `remaining < 0 → continue` passa a ser só o caso `-2`
   (expirou).
5. O `FakeRedis.eval` dos testes unitários reimplementa os scripts. A
   verdade fica no teste com Redis real; a lição da Etapa 0 Task 1 vale aqui.

**Aceite:** os 4 testes de caracterização seguem verdes, os 2 RED passam, e
o comportamento não muda para chaves saudáveis.

## A2 — Redação contra formatos reais (R3)

**Files:** `src/content_factory/infra/redact.py`, `tests/infra/test_redact.py`.

- **RED parametrizado** com mensagens reais, montadas pelas próprias
  bibliotecas sempre que possível:
  - `httpx.HTTPStatusError` de um `MockTransport` com `?key=` (YouTube) e
    `?access_token=` (Instagram);
  - `psycopg.OperationalError` com DSN;
  - `redis.ConnectionError` com URL;
  - `str()` de um `CollectorError` que embrulha cada uma.
- **GREEN:**
  - `key`, `api_key`, `token`, `password` e `passwd` como parâmetros de
    query/forma;
  - credencial em URL (`scheme://user:SENHA@host` → `scheme://user:***@host`,
    `scheme://:SENHA@`).
- **Aceite:** nenhum segredo sobrevive; os testes atuais de redação seguem
  verdes; o `RedactingFilter` herda as regras sem mudança.

## A3 — Checklist verificável da troca 3 → 1 (R1)

**Files:** `docs/manual/experimento-7-dias.md` (§ Pós-2026-10-19).

Cada passo com um comando de verificação **read-only** e o resultado
esperado:

1. Exportar os dados do experimento (comando + contagem conferida).
2. Parar os 3 `cf-worker-*` e confirmar com `docker ps` que 0 estão de pé.
3. Subir o compose de worker único. Conferir que o guard `COLLECT_SEGMENT`
   não dispara, que há 3 crons e que `active_segments()` devolve 3.
4. No 1º tick, conferir que há 1 run por (segmento, plataforma) e nenhuma
   duplicata. Query: `collect_runs` agrupado por minuto, segmento e coletor
   com `count > 1` deve voltar vazio.
5. Conferir que não há `ERROR` inesperado no log e que `collect-timings`
   está populado.

**Sem código.** O checklist é executado em 19/10 por você, com o meu apoio.

## A4 — `main` protegida por ruleset (R4)

Configuração no GitHub, **com sua confirmação**:

- ruleset em `main` exigindo os checks `quality` (CI) e `Build site`
  (Docs);
- branch atualizada antes do merge;
- bloqueio de force-push e de deleção;
- **sem** exigir review, porque o projeto tem uma pessoa só e exigir review
  travaria o próprio merge;
- bypass apenas para o admin, em emergência.

**Aceite:** um PR com check vermelho não pode ser mergeado.

## A5 — ADR-0007: política de retry dos jobs (R9)

**Files:** `docs/adr/0007-politica-retry-jobs.md`, `mkdocs.yml` (nav).

Decisão proposta, sem mudança de código:

- **Sem retry do arq** (`max_tries=1`). O próximo horário do cron é a nova
  tentativa: collect a cada 12 h, research e score diários.
- **Transitório dentro do job:**
  - backoff do limiter (até 5×);
  - backoff do TikTok (até 5×);
  - breakers decidem quando parar de bater.
- **Persistente:**
  - breaker por plataforma (15 min) e breaker de fonte (72 h);
  - `auth_expired` não abre breaker: precisa de humano.
- **Infra (banco/Redis):** o segmento falha inteiro, com rollback e log.
  Upsert idempotente significa que reexecutar não duplica nada.
- **Shutdown:** cancelamento propaga, o `finally` libera o lock e a UoW
  reverte. Isso já é testado
  (`test_collect_external_cancel_releases_lock`).
- **Reexecução manual:** `cf collect|score --segment` ou um
  `arq enqueue_job` documentado.

**Aceite:** ADR aceito e referenciado no mapa do ciclo e no `operacao.md`.

---

# Bloco B — Dados e scoring

## B1 — Score diagnosticável (R5)

**Files:**
- `app/ports.py`: `UpsertReport(persisted, failures: dict[str, int])`;
- `infra/db/repositories.py`: `upsert_many` devolve o relatório e loga
  WARNING por linha descartada, com `video_id` e classe da exceção, sem
  dado bruto;
- `app/score.py`: `ScoreOutcome` ganha `processed`/`persisted`/`failed`/
  `failures`;
- CLI e worker: envelope;
- testes.

- **RED:** lote com 1 linha inválida → `failures == {"data_error": 1}`,
  `persisted == n-1`, 1 WARNING com o `video_id`.
- **Semântica:**
  - `processed == 0` → ok;
  - `persisted == 0` com `processed > 0` → `ok: false, partial: false`
    (falha total);
  - parcial = `0 < persisted < processed`.
- **Erro de infraestrutura continua propagando** (T11). O teste
  `test_score_job_batch_failure_reaches_envelope_and_rolls_back` não muda.
- **Envelope:** só contagens e categorias. A mensagem de erro fica no log
  (redigida).

## B2 — `quota_used` por chamada (R6)

**Files:**
- `app/gate_hits.py` → `app/call_counters.py`, com `counting()` genérico
  por nome; `gate_hits` fica como wrapper compatível;
- `infra/collectors/youtube.py`: `record("quota", 100|1)`;
- `app/collect.py` e `research_pipeline.py`: `run.quota_used`;
- testes.

**RED:** collect do YouTube com 1 search + 2 lists → `run.quota_used == 102`.
Com 2 segmentos concorrentes, cada um conta só o seu.

## B3 — Validação empírica do ranking (R7) — após 2026-10-19

**Files:**
- `docs/manual/validacao-ranking.md`: protocolo e resultado versionado;
- `cf metrics-coverage --segment`: novo comando, só leitura;
- queries em `analise.md`.

Protocolo, **com os limiares fixados antes de olhar os dados**:

1. **Cobertura:** % de vídeos por plataforma com cada métrica e com 2
   snapshots (`velocity` calculável).
2. **Estabilidade:** Spearman entre o ranking de scores consecutivos do
   mesmo segmento (08:30 × 21:30).
3. **Ablação:** retirar cada componente e medir quanto o top-20 muda; se
   um componente não move nada, não agrega.
4. **Sensibilidade:** perturbar os pesos em ±20% e ver se o top-20 muda de
   forma previsível.
5. **Concordância humana:** `cf score-sample` de 50 por segmento → sua
   anotação → `cf score-correlation`.
6. **Utilidade:** no top-20, a proporção de vídeos "úteis para o nicho" (não
   só populares), também por anotação.

Limiares propostos, **para você decidir**:

| Critério | Limiar proposto |
| --- | --- |
| Cobertura de `views` | ≥ 80% (YouTube/TikTok) |
| Cobertura de 2 snapshots | ≥ 60% após 3 dias |
| Estabilidade (Spearman) | ≥ 0,7 |
| Concordância humana (Spearman) | ≥ 0,5 |
| Utilidade no top-20 | ≥ 60% |

Se não passar: recalibrar com **bump de `FORMULA_VERSION`**, sem acrescentar
dimensões.

---

# Bloco C — Confiabilidade (após a troca)

## C1 — Contrato dos singletons (R8)

Uma tabela no mapa do ciclo com, para cada coletor ou provider: o estado,
se é seguro para tasks concorrentes, o ciclo de vida e o teste que cobre.
Correções que já se conhecem:

- `httpx.AsyncClient` do YouTube e do Instagram nunca é fechado: fechar no
  `close()` da composição;
- o contador de quota sai do singleton (B2).

## C2 — `cf ops-report` (R10)

Por segmento e janela:

- taxa ok/failed/blocked por coletor e por fonte (`research:*`);
- breakers abertos (runs `blocked`);
- cobertura de métricas;
- novos, atualizados, ignorados e pontuados;
- duração de parede do segmento (min `started_at` → max `finished_at`)
  **separada** da soma `discover_ms + persist_ms`.

## C3 — Contrato de envelopes (R11)

Uma tabela única com a semântica de `ok`/`partial`/`failed`/`skipped` em CLI,
worker e `collect_runs` (no `operacao.md`), mais um teste de paridade: o
mesmo cenário pela CLI e pelo worker produz os mesmos campos.

---

# Bloco D — Hardening (após a troca)

- **D1 (R12):**
  - pin de todas as actions por SHA completo, com a tag em comentário;
  - `permissions: contents: read` no `ci.yml`;
  - `.github/dependabot.yml` para `github-actions` e `uv`.
- **D2 (R13):** ADR-0008. O SSRF TOCTOU fica adiado enquanto `cf ingest`
  for CLI local; reabrir se a ingestão for exposta via API ou automação
  (resolver DNS uma vez, conectar no IP validado, revalidar redirects).

# Execução 2026-10-14 — pausa, correção e reativação

**Decisão do usuário (2026-10-14):** pausar o experimento agora, sem
esperar 19/10, salvar tudo, corrigir e reativar os 3 segmentos já no worker
único.

1. ✅ **Pausa:** 3 workers legados parados às 15:21:26 UTC e backup
   completo. Detalhes em
   [`manual/experimento-pausa-2026-10-14.md`](../manual/experimento-pausa-2026-10-14.md).
2. **Correções desta rodada**, em ordem, com TDD e 1 commit por task:
   A1 (rate limiter atômico) → A2 (redação) → B2 (`quota_used`) → B1
   (score diagnosticável) → A5 (ADR-0007).
   - A4 (ruleset) depende da confirmação do usuário.
   - C/D ficam para depois da reativação.
3. **Deploy (A3, adiantado para hoje):**
   - merge na `main` com CI verde;
   - `alembic upgrade head` no banco real (está em `0008`, a `main` pede
     `0009`), com backup antes, que já foi feito;
   - build da imagem e `docker compose … multi-segmento … --profile
     worker-multi up -d worker`;
   - verificações do checklist A3: sem `COLLECT_SEGMENT`, 3 crons,
     `active_segments()` = 3.
4. **Reativação:**
   - `cf research stop` + `cf research start --weeks 1` nos 3 segmentos,
     para a janela nova começar no deploy;
   - disparar 1 `collect_job` manual de fumaça (fora do cron) e conferir 1
     run por (segmento, plataforma), sem duplicata e com `discover_ms`
     populado.

# Plano de análise dos relatórios

O objetivo é transformar o teste reativado em **evidência**: saber se o
pipeline está saudável e se o ranking presta, com critérios definidos antes
de olhar os dados.

## Fontes

| Fonte | Como obter | Para quê |
| --- | --- | --- |
| Relatório da pausa | `manual/experimento-pausa-2026-10-14.md` + backup | linha de base pré-correção |
| `collect_runs` | `cf status`, `cf collect-timings`, SQL em `analise.md` | saúde por fonte, plataforma, segmento e dia |
| Envelopes dos jobs | log do worker (`← collect_job ● {…}`) | `ok`/`partial`, `source_errors`, `failures` |
| Log `ERROR`/`WARNING` | `docker logs cf-worker \| grep ' ERROR content_factory'` | bug fora do contrato, linha de score descartada |
| `video_metrics` / `video_scores` | SQL + `cf score-sample` / `cf score-correlation` | cobertura, estabilidade, ranking |
| Redis `cf:cb:*`, `cf:rl:*` | `redis-cli --scan` | breaker aberto, janela de cota |

## Cadência

| Quando | Análise | Saída |
| --- | --- | --- |
| **D0 (deploy)** | fumaça: 1 run por (segmento, plataforma), sem duplicata, `discover_ms`/`persist_ms`/`quota_used` preenchidos, nenhum `ERROR` inesperado | ok/não ok no HANDOFF |
| **Diário (após o collect das 20:00)** | ok/failed/blocked por coletor e fonte; `source_errors` do research; breakers abertos; `ERROR` no log | 1 linha por dia em `manual/experimento-reativado.md` |
| **D+3** | cobertura de métricas (views, 2 snapshots); duração por plataforma comparada à linha de base; comparar o research que gravou com o que falhou | tabela de cobertura + nota |
| **D+7 (fim da janela)** | validação do ranking B3 completa: estabilidade, ablação, sensibilidade, concordância humana, utilidade; anotação manual via `score-sample` | `manual/validacao-ranking.md` com veredito contra os limiares |

## Perguntas que cada relatório responde

1. **Coleta saudável?**
   - por plataforma: % `ok` e erros por código;
   - `rate_limited`/`blocked` sem causa externa é bug ou limite mal
     calibrado.
2. **Research útil?**
   - `web_found`/`trends_found` por dia;
   - quantos vídeos vieram **só** da busca social por keyword
     (`video_segments.discovery_source`);
   - a fase social roda mesmo com o Trends caindo?
3. **Re-medição funciona?** % de vídeos com 2 ou mais snapshots e
   intervalo médio entre eles.
4. **Score diagnosticável?** `failures` do score com zeros esperados;
   qualquer `data_error` vira investigação.
5. **Ranking presta?** Os critérios do B3, com os limiares abaixo, fixados
   **antes** de D+7.

## Limiares (propostos; o usuário aprova antes de D+7)

| Critério | Limiar proposto |
| --- | --- |
| Runs `ok` por plataforma ativa (YouTube, web, Instagram) | ≥ 90% |
| Research com pelo menos 1 fonte ok por dia | 100% dos dias |
| Cobertura de `views` (YouTube) | ≥ 80% |
| Vídeos com 2 snapshots após D+3 | ≥ 60% |
| Estabilidade do ranking (Spearman entre scores consecutivos) | ≥ 0,7 |
| Concordância humana (Spearman) | ≥ 0,5 |
| Utilidade no top-20 (anotação) | ≥ 60% |

Fora do limiar: abrir achado no `REVISAO_*` com evidência. Não ajustar
pesos sem passar pelo B3.

# Restrição de escopo (decisão do usuário, 2026-10-14)

**Nada será feito no projeto `insta_kb` até o content-factory fechar 100%.**
Isso inclui código, contrato de integração e design da Fase I. Fechar 100%
significa cumprir **todos** os critérios de saída abaixo. O Bloco IA só
começa depois disso.

# Critérios de saída da fase (do review, aceitos)

- R2, R3 e R5 verdes, com testes em infra real.
- Ruleset ativo (R4).
- Troca feita pelo checklist A3, sem duplicação.
- B3 com o relatório versionado e os limiares decididos.
- ADRs 0007/0008 aceitos.
- Contrato de envelopes único (C3).
