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

### Fase G — Modelo de métricas e scoring v2 (P0, depende da Fase F)

Plano a escrever **depois** da Fase F estar em produção por tempo
suficiente para existirem snapshots reais — não calibrar pesos no escuro
(mesma cautela da revisão externa).

- `VideoMetrics` distingue métrica ausente de métrica zero (`int | None`),
  propagado até `VideoSnapshot` (hoje `_snapshot()` colapsa `None` em `0`).
- Velocidade calculada por diferença entre snapshots reais, não por idade.
- Separar performance (alcance acumulado) de momentum (crescimento) de
  relevância, como dimensões auditáveis independentes — sem recalibrar
  pesos sem dados de base.
- Registrar cobertura de métricas por plataforma no resultado do score.

**Critério de conclusão:** o ranking pode ser explicado componente a
componente, e vídeos sem uma métrica (ex.: Instagram sem views) não são
penalizados como se tivessem desempenho zero.

### Fase H — Diversidade de descoberta (P1)

Modos de descoberta complementares (recentes/emergentes), estrutura de
segmento com sinônimos/subnichos/exclusões, registro explícito de
cobertura/limitação por plataforma no envelope operacional. Plano a
escrever quando a Fase G já estiver medindo momentum (a descoberta
"emergente" depende de ter crescimento calculável).

### Fase I — Contrato com `insta_kb` (P1, decisão arquitetural)

Não é uma tarefa de código isolada — precisa de uma sessão de
brainstorming/design cruzando os dois repositórios antes de virar plano
de execução (ids compartilhados, quem é dono de qual dado, como o
content-factory consulta estado de processamento sem duplicar fila/LLM).

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
