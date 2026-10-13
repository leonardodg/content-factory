# Roadmap de melhorias — content-factory → motor de inteligência de conteúdo

> Documento-guarda-chuva. Não é um plano de execução linha-a-linha — é o
> mapa de prioridades que organiza os planos detalhados (um arquivo por
> fase, no padrão já usado em `2026-10-12-pipeline-pesquisa-fase-{a..e}.md`).
> Nasceu da revisão técnica externa ao `content-factory` (ver contexto da
> sessão) cruzada com auditoria direta do código nesta sessão.

## Contexto

A revisão externa aponta, corretamente, que o risco atual é confundir
"coleta funcionando" com "inteligência de conteúdo funcionando". A
auditoria de código desta sessão confirmou os problemas apontados e achou
**um problema adicional, mais severo, que a revisão externa não cobriu**:
hoje **nenhum fluxo grava métricas em `video_metrics`** — nem `collect()`
nem `research_pipeline()`. `VideoRepositoryImpl.upsert_candidates`
(`src/content_factory/infra/db/repositories.py:69-124`) grava só
`videos`/`video_segments`; não existe nenhum INSERT em `video_metrics` em
lugar nenhum do código (`grep -rn "VideoMetricsRow" src/` só aparece em
`orm.py` e na leitura via LEFT JOIN de `latest_for_segment`). Isso significa
que **todo score calculado hoje usa métricas zeradas** — `views=0,
likes=0, ...` sempre, porque nunca existe linha em `video_metrics` para
nenhum vídeo. O scoring nunca teve dados reais para pontuar; só parece
funcionar porque `relevance` (texto) ainda produz um número não-zero.

Isso eleva a prioridade: antes de redesenhar pesos de scoring (Fase 02),
é preciso primeiro **fazer a métrica chegar ao banco**. Sem isso, qualquer
ajuste de fórmula é ajuste no vácuo.

## Classificação (o que existe, o que é limitação, o que é futuro)

| Item | Status real | Evidência |
|---|---|---|
| Coleta YT/TikTok/IG/web, descoberta por keyword | **Implementado** | `infra/collectors/*.py`, 186 testes reais passando |
| Scoring determinístico (fórmula única) | **Implementado, com dados reais (Fase F)** | `domain/scoring.py` + `video_metrics` agora é escrito |
| Persistência de métricas (`video_metrics`) | **Implementado (Fase F)** | `upsert_candidates` grava snapshot |
| Persistência de candidatos descobertos via pesquisa | **Implementado (Fase F)** | `research_pipeline` usa `upsert_candidates` |
| Série temporal do Google Trends | **Implementado (Fase F)** | `trend_observations` append-only |
| Contadores operacionais padronizados na pesquisa | **Implementado (Fase F)** | vocabulário collect() (found/inserted/updated/skipped) |
| Diversidade de modos de descoberta (recentes/emergentes/semântica) | **Futuro** | só existe "top por views + janela" |
| Expansão semântica de segmento (sinônimos, subnichos, exclusões) | **Futuro** | hoje só `segment.keywords` literal + Google Suggest |
| Contrato de integração com `insta_kb` | **Futuro — decisão arquitetural não tomada** | nenhuma referência cruzada no código |
| Análise criativa estruturada (hook/payoff/CTA/evidências) | **Futuro — schema já existe, use-case não** | `CreativePatternRow` existe em `orm.py`, sem nenhum caso de uso/repositório/porta |
| Geração de ideias/roteiros | **Futuro — schema já existe, use-case não** | `IdeaRow`, `ScriptRow`, `ProductionJobRow`, `PublishedVideoRow`, `PerformanceMetricRow` todos existem em `orm.py`, nenhum código os usa |
| Loop de feedback publicação→resultado | **Futuro** | `PerformanceMetricRow` existe, mas nada escreve nela |

Achado relevante para o planejamento: as tabelas de análise criativa,
ideias, roteiros, produção e performance publicada **já foram modeladas**
na migração inicial (`alembic/versions/0001_schema_inicial_content_factory.py`
→ `orm.py`). As Fases 03-05 não vão precisar desenhar schema do zero —
precisam desenhar os *use-cases* e *portas* que população essas tabelas,
e validar se o schema existente ainda serve depois de usado de verdade.

## Ordem de execução

Mantida a lógica da revisão externa (corrigir dados antes de empilhar IA),
com o achado novo elevado ao topo da Fase 01.

### Fase F — Integridade do pipeline (P0, bloqueia tudo abaixo) ✅ CONCLUÍDA

Plano detalhado: `docs/ai-plans/2026-10-12-content-factory-fase-f-integridade-pipeline.md`.

- [x] Gravar snapshot de métricas no upsert de candidatos (bug novo, mais
  crítico que os da revisão externa — sem isso não existe dado para
  pontuar).
- [x] `research_pipeline` persiste candidatos sociais pelo mesmo contrato do
  `collect()` — sem duplicar código.
- [x] Google Trends passa a ter observação histórica própria, separada do
  sinal de descoberta (`keyword_signals`).
- [x] Contadores da pesquisa alinhados ao vocabulário já usado por
  `CollectRun` (found/inserted/updated/skipped/failed), eliminando
  `social_found` ambíguo.

**Critério de conclusão:** todo candidato contado como persistido existe
em `videos` e tem pelo menos uma linha em `video_metrics`; toda execução
de pesquisa por plataforma tem um `CollectRun` auditável; uma observação
de Trends pode ser consultada por keyword e período.

### Fase G — Modelo de métricas e scoring v2 (P0, depende da Fase F) ✅ PARCIAL

Plano: `docs/ai-plans/2026-10-12-content-factory-fase-g-scoring-v2.md`.

- [x] `VideoMetrics` distingue métrica ausente de métrica zero (`int | None`),
  propagado até `VideoSnapshot` (hoje `_snapshot()` colapsa `None` em `0`).
- [x] Registrar cobertura de métricas por plataforma no resultado do score
  (`ScoreResult.metrics_available`).
- [x] Velocidade calculada por diferença entre snapshots reais, não por
  idade — **ENTREGUE pela Fase H Task 3** (`momentum_score` +
  `previous_metrics` em `VideoSnapshot`; matou o fake `age_days=999`).
- [x] Separar performance (alcance acumulado) de momentum (crescimento) de
  relevância, como dimensões auditáveis independentes — **parcialmente
  entregue pela Fase H**: `ScoreResult` expõe `performance`/`velocity`/
  `engagement`/`relevance`/`recency` como campos separados e
  `metrics_available`+`formula_version` são persistidos (Task 2). A
  recalibração dos pesos continua pendente de dados reais.

**Critério de conclusão:** o ranking pode ser explicado componente a
componente, e vídeos sem uma métrica (ex.: Instagram sem views) não são
penalizados como se tivessem desempenho zero.

**Code review pós-G (2026-10-12):** achou e corrigiu 1 bug crítico
(`video_scores.performance`/`engagement` NOT NULL vs `ScoreResult` com
`None` — `cf score` falhava pra todo vídeo sem métrica capturada,
reproduzido contra Postgres real) + 1 gap real de menor severidade
(`engagement_rate` não tratava "nenhuma submétrica" como ausente). Ver
`docs/ai-plans/2026-10-12-content-factory-fase-g-scoring-v2.md` Task G4.
As duas pendências marcadas acima (velocidade por diff, separar
performance/momentum) foram tratadas pela Fase H.

### Fase H — Integridade operacional + auditabilidade do score ✅ (2026-10-12)

**CONCLUÍDA** (branch `feat/fase-h-integridade`, 8 commits, 226 passed).
Plano: `2026-10-12-content-factory-fase-h-integridade-operacional-scoring.md`.

Fechou as pendências estruturais deixadas pela Fase G: estado
`ok`/`partial` real em `run_research_cycle` (Task 1); `metrics_available`/
`formula_version` persistidos em `video_scores` (Task 2, mig `0006`);
`velocity` por diff real de snapshots via `momentum_score` — matou o fake
`age_days=999` (Task 3); `latest_for_segment` traz o snapshot anterior
com `row_number()` (Task 4); `upsert_candidates` atualiza metadados no
conflito via `COALESCE`/`NULLIF` (Task 5); `trend_observations` faz
upsert por período em vez de acumular duplicata (Task 6, mig `0007`);
`CollectRun.skipped` separado de `failed` com backfill (Task 7, mig
`0008`); e as ferramentas `cf score-sample`/`cf score-correlation`
(Spearman em stdlib, sem scipy) para validação empírica do ranking —
o veredito sobre os pesos continua sendo humano, com dados reais
(Task 8).

_O conteúdo original desta fase no roadmap anterior ("Diversidade de
descoberta" — modos complementares, sinônimos/subnichos/exclusões,
registro de cobertura por plataforma) NÃO foi implementado aqui. Ele
depende de momentum calculável (agora pronto) e foi adiado para uma
futura fase — renumerar quando for planejado._

### Fase I — Contrato com `insta_kb` (P1, decisão arquitetural) — ⏳ PRÓXIMO PASSO SUGERIDO, NÃO INICIADA

Não é uma tarefa de código isolada — precisa de uma sessão de
brainstorming/design cruzando os dois repositórios antes de virar plano
de execução (ids compartilhados, quem é dono de qual dado, como o
content-factory consulta estado de processamento sem duplicar fila/LLM).

**Perguntas que essa sessão precisa responder antes de qualquer código:**
- Quem é dono do quê: `content-factory` descobre/rankeia, `insta_kb`
  transcreve/resume — mas quem decide QUAL vídeo entra na fila do
  `insta_kb`? É push (content-factory empurra) ou pull (insta_kb consulta
  os top-ranked)?
- Identificador compartilhado: `content-factory` usa `(platform,
  platform_video_id)` como chave natural (`videos` table) — o `insta_kb`
  usa o mesmo par ou tem seu próprio id? Precisa de uma tabela de mapa ou
  o id natural basta?
- Onde mora o estado "já processado por X"? Hoje não existe nenhuma
  coluna/tabela em `content-factory` pra isso — precisa decidir se é uma
  FK pra uma tabela do `insta_kb` consultada via API, ou um campo local
  tipo `processing_status` espelhado por sync/webhook.
- O `content-factory` lê resultado do `insta_kb` (transcrição/resumo) via
  API síncrona, fila assíncrona, ou replica/copia os campos relevantes?
  Cada opção tem trade-off de acoplamento vs. latência vs. duplicação.
- `CreativePatternRow`/`IdeaRow` (Fase J/K) dependem do resultado do
  `insta_kb` — a sessão de design precisa decidir o formato desse
  contrato antes de qualquer schema novo nessas tabelas.

Essa sessão **não é esta conversa** — fica marcada aqui como ponto de
entrada para quando você quiser retomá-la com contexto fresco.

### Fase J — Análise criativa estruturada (P1, maior lacuna funcional)

Usa o schema já existente (`CreativePatternRow`) mas precisa de desenho
de output schema validável (hook/promessa/estrutura/payoff/evidências/
confiança, observação separada de hipótese) antes de virar plano
bite-sized. Depende da Fase I resolvida (a transcrição vem do `insta_kb`).

### Fase K — Geração de ideias + loop de feedback (P1)

Usa `IdeaRow`/`ScriptRow`/`ProductionJobRow`/`PublishedVideoRow`/
`PerformanceMetricRow`, todos já no schema. Depende da Fase J (padrões
estruturados são a entrada da geração de ideias).

## O que não fazer agora

Mesma lista da revisão externa, ratificada pela auditoria: não migrar
para microserviços, não duplicar transcrição/multimodal do `insta_kb`,
não adicionar mais IA antes de a Fase F/G corrigirem os dados, não
recalibrar pesos de scoring sem base de dados real, não apresentar
cobertura parcial de plataforma como cobertura geral, não automatizar
publicação antes de validar geração de ideias com revisão humana.
