# Ideia inicial do produto (verbatim)

> **Origem:** primeira mensagem da sessão de criação do projeto
> (2026-10-07, opencode — recuperada do histórico de sessões).
> Este é o texto bruto enviado pelo Leo, preservado como fonte primária.
>
> **Nota:** parte das escolhas técnicas deste texto (MongoDB, FastAPI)
> foi deliberadamente substituída depois — ver PRD (`prd.md`) e
> ADRs 0001–0004 (`docs/adr/`). Onde houver divergência, mandam PRD/ADRs;
> a intenção de produto (funil segmento → descoberta → score → padrões →
> ideias → roteiros → produção) permanece a mesma.

---

Vamos contruir um motor de inteligência de conteúdo parametrizado por segmento. 

seguir esta idea: Sim. E eu faria isso como um **sistema de Creative Intelligence + Content Factory**, não como um simples agente que “procura vídeos virais”.

A ideia é você conseguir dizer:

> **Segmento = Velas artesanais**

e o sistema, diariamente, entregar algo como:

* 50 vídeos candidatos encontrados
* 15 considerados relevantes
* 5 realmente fortes
* 3 padrões criativos identificados
* 10 ideias originais derivadas
* 3 roteiros prontos para produção
* 1 vídeo recomendado para produzir hoje

O TikTok já oferece uma peça importante desse quebra-cabeça: o Creative Center permite explorar tendências por indústria, período, hashtags, vídeos, creators e outros sinais. ([TikTok For Business][1]) O YouTube também permite pesquisa programática de vídeos e recuperação de estatísticas como visualizações e likes via API. ([Google for Developers][2])

## 1. A arquitetura que eu recomendo

```text
                    ┌─────────────────────┐
                    │     SEGMENTO         │
                    │  "Velas artesanais"  │
                    └──────────┬──────────┘
                               │
                               ▼
                  ┌─────────────────────────┐
                  │  TREND DISCOVERY AGENT  │
                  │                         │
                  │ TikTok                  │
                  │ Instagram               │
                  │ YouTube                 │
                  │ Google/Web               │
                  └────────────┬────────────┘
                               │
                               ▼
                  ┌─────────────────────────┐
                  │   VIDEO COLLECTOR       │
                  │                         │
                  │ URL                     │
                  │ creator                 │
                  │ views                   │
                  │ likes                   │
                  │ comments                │
                  │ date                    │
                  │ duration                │
                  │ transcript              │
                  └────────────┬────────────┘
                               │
                               ▼
                  ┌─────────────────────────┐
                  │ VIRALITY SCORER         │
                  │                         │
                  │ Views velocity          │
                  │ Engagement              │
                  │ Recency                 │
                  │ Niche relevance         │
                  │ Replication potential   │
                  └────────────┬────────────┘
                               │
                               ▼
                  ┌─────────────────────────┐
                  │ CONTENT ANALYST AI      │
                  │                         │
                  │ Hook                   │
                  │ Structure              │
                  │ Story                  │
                  │ Visual pattern         │
                  │ Payoff                 │
                  │ CTA                    │
                  └────────────┬────────────┘
                               │
                               ▼
                  ┌─────────────────────────┐
                  │ IDEA GENERATOR          │
                  │                         │
                  │ abstrai → transforma    │
                  │ → combina → originaliza │
                  └────────────┬────────────┘
                               │
                               ▼
                  ┌─────────────────────────┐
                  │ SCRIPT GENERATOR        │
                  │                         │
                  │ Hook                   │
                  │ Scenes                 │
                  │ Narration              │
                  │ On-screen text         │
                  │ B-roll                 │
                  │ CTA                    │
                  └────────────┬────────────┘
                               │
                               ▼
                  ┌─────────────────────────┐
                  │ PRODUCTION QUEUE        │
                  │                         │
                  │ HOJE                    │
                  │ AMANHÃ                  │
                  │ +7 dias                 │
                  └─────────────────────────┘
```

### O detalhe mais importante

O agente **não deve copiar o vídeo**.

Ele deve identificar:

> **“Por que esse vídeo funciona?”**

e não:

> “Como faço o mesmo vídeo?”

Essa diferença é fundamental.

---

# 2. Começaria definindo o "DNA" do segmento

Para cada nicho você cria um arquivo de configuração.

Por exemplo:

```yaml
segment:
  name: "Velas artesanais"

audience:
  - artesanato
  - decoração
  - presentes
  - empreendedorismo
  - DIY

languages:
  primary:
    - pt-BR
  secondary:
    - en
    - es

platforms:
  - tiktok
  - instagram
  - youtube

content_types:
  - tutorial
  - transformation
  - satisfying
  - tips
  - mistakes
  - behind_the_scenes
  - product
  - business
  - humor
  - storytelling

keywords:
  - candle making
  - soy candle
  - scented candle
  - candle DIY
  - candle business
  - velas artesanais
  - vela aromática
  - vela de soja
```

Mas eu iria além.

O sistema deveria aprender **subnichos**.

Exemplo:

```text
VELAS
│
├── fabricação
│   ├── cera
│   ├── pavio
│   ├── temperatura
│   └── moldes
│
├── decoração
│   ├── minimalista
│   ├── luxo
│   └── temática
│
├── fragrâncias
│
├── erros
│
├── ASMR / satisfying
│
├── empreendedorismo
│
├── embalagem
│
├── presentes
│
└── decoração de ambientes
```

Isso aumenta brutalmente a variedade.

---

# 3. O primeiro agente: Trend Hunter

Ele roda automaticamente, por exemplo, **2 vezes por dia**.

### Fontes

**TikTok**

O Creative Center é particularmente interessante porque já oferece tendências por indústria e período, além de analytics de hashtags e vídeos relacionados. ([TikTok For Business][1])

**YouTube**

A API permite pesquisar vídeos por query, idioma, região etc., e depois recuperar estatísticas do vídeo. ([Google for Developers][2])

**Instagram**

Aqui eu evitaria projetar o sistema dependendo de scraping agressivo da plataforma. É melhor tratar Instagram como uma fonte de descoberta/referência e usar APIs oficiais quando o caso de uso e as permissões permitirem.

**Web**

Google/Bing/Google Trends/portais especializados podem encontrar conteúdo que as plataformas não expõem facilmente.

---

# 4. Não procure simplesmente "vídeos virais"

Esse é um erro de arquitetura.

Procure:

### A. Vídeos populares

Exemplo:

> candle making

### B. Vídeos com crescimento anormal

Muito mais interessante:

> 30k seguidores → vídeo com 2 milhões de views

Esse criador pode ter encontrado um formato excepcional.

### C. Vídeos recentes

Um vídeo com:

> 1.2M views em 3 dias

pode ser mais interessante que:

> 20M views em 2 anos.

### D. Vídeos com alta taxa de interação

Por exemplo:

```text
engagement_rate =
(likes + comments + shares) / views
```

quando esses dados estiverem disponíveis.

### E. Formatos replicáveis

Essa é uma métrica que eu criaria:

**Replication Potential**

Pergunta:

> "É possível produzir uma versão original desse formato sem precisar copiar o vídeo?"

Exemplo:

**Pessoa mostra 5 erros de fabricação de vela**

Replication Potential: **9/10**

Já:

**Influencer famoso fazendo uma pegadinha cara**

Replication Potential: **2/10**

---

# 5. O Viral Score

Eu criaria um score próprio.

Por exemplo:

```text
VIRAL SCORE

25% = performance
20% = velocidade
15% = engagement
15% = relevância do nicho
10% = novidade
10% = potencial de replicação
 5% = facilidade de produção
```

Resultado:

```text
Video A
──────────────
Performance       92
Velocity          88
Engagement        79
Niche relevance   96
Novelty           81
Replication       94
Production        90

VIRAL SCORE       89.7
```

Isso permite ao sistema parar de simplesmente dizer:

> "Este vídeo tem muitas views."

e começar a dizer:

> **"Este vídeo é uma excelente oportunidade de conteúdo para este segmento."**

---

# 6. Depois vem a parte realmente interessante: abstração

Imagine que encontramos:

> "I made a giant candle using 10 kg of wax."

A IA não deveria produzir:

> "Faça uma vela gigante usando 10 kg."

Isso é cópia.

Ela deveria identificar:

### Estrutura

```text
HOOK
↓
escala inesperada
↓
processo visual
↓
problema
↓
resultado
↓
reveal
```

### Trigger psicológico

```text
curiosidade
+
escala
+
satisfação visual
```

### Pattern

> "Transformar algo comum em uma versão exageradamente grande."

Então gerar:

```text
IDEIA 1
"Transformei uma vela comum em uma vela de 5 kg"

IDEIA 2
"Quanto custa fazer uma vela gigante?"

IDEIA 3
"Será que uma vela gigante realmente queima?"

IDEIA 4
"Fiz uma vela maior que minha mão"

IDEIA 5
"Qual é o limite de uma vela artesanal?"
```

A **ideia é derivada do mecanismo**, não do conteúdo específico.

---

# 7. Eu criaria um "Creative Pattern Library"

Esse provavelmente será um dos ativos mais valiosos do projeto.

Banco:

```text
creative_patterns
```

Exemplo:

```json
{
  "pattern": "Before / After",
  "hook": "Você não vai acreditar no resultado",
  "psychology": [
    "curiosity",
    "transformation"
  ],
  "visual_structure": [
    "show_before",
    "quick_process",
    "reveal_after"
  ],
  "best_for": [
    "craft",
    "beauty",
    "food",
    "DIY"
  ]
}
```

Outro:

```json
{
  "pattern": "3 mistakes",
  "hook": "3 erros que quase todo mundo comete",
  "psychology": [
    "fear",
    "authority",
    "curiosity"
  ]
}
```

Outro:

```json
{
  "pattern": "Impossible challenge",
  "hook": "Será que isso funciona?",
  "psychology": [
    "curiosity",
    "uncertainty"
  ]
}
```

Depois o sistema começa a perceber:

> **Patterns que funcionam em múltiplos nichos.**

Isso é muito mais poderoso do que simplesmente armazenar vídeos.

---

# 8. O agente de roteiro

Depois:

```text
REFERENCE VIDEO
        ↓
TRANSCRIPT
        ↓
SCENE DETECTION
        ↓
HOOK ANALYSIS
        ↓
PATTERN
        ↓
ORIGINAL IDEA
        ↓
SCRIPT
```

E o roteiro poderia sair estruturado:

```json
{
  "title": "O erro que destrói sua vela",
  "duration": 28,
  "hook": "Se sua vela faz isso, provavelmente você está errando a temperatura.",
  "scenes": [
    {
      "time": "0-3",
      "visual": "...",
      "voice": "...",
      "text": "..."
    },
    {
      "time": "3-8",
      "visual": "...",
      "voice": "...",
      "text": "..."
    }
  ],
  "cta": "...",
  "production_difficulty": 2
}
```

Isso já vira praticamente uma **ordem de produção**.

---

# 9. E aqui entra a geração dos seus vídeos brutos

Eu separaria isso completamente do sistema de pesquisa.

```text
CONTENT INTELLIGENCE
        │
        │
        ▼
CONTENT DATABASE
        │
        ▼
PRODUCTION QUEUE
        │
        ▼
VIDEO GENERATION
        │
        ▼
RAW VIDEO
        │
        ▼
EDITORIAL PIPELINE
        │
        ▼
FINAL VIDEO
```

Isso permite trocar o gerador de vídeo depois.

Hoje:

```text
Veo
```

amanhã:

```text
Runway
Kling
Sora
local model
```

sem alterar o sistema de inteligência.

---

# 10. Para publicar diariamente, eu NÃO faria um vídeo por dia

Eu faria **produção em lote**.

Por exemplo:

### Segunda

Sistema gera:

```text
50 referências
↓
15 oportunidades
↓
10 ideias
↓
7 roteiros
```

Você produz:

```text
SEG  → vídeo 1
TER  → vídeo 2
QUA  → vídeo 3
QUI  → vídeo 4
SEX  → vídeo 5
SÁB  → vídeo 6
DOM  → vídeo 7
```

E simultaneamente o agente continua pesquisando.

Você sempre mantém:

> **7–14 dias de conteúdo em estoque.**

Isso elimina a pressão diária.

---

# 11. Feedback loop

Aqui está o que transforma o sistema em algo realmente inteligente.

Depois que você publica:

```text
VIDEO 001
```

coleta:

```text
views
likes
comments
shares
saves
watch time
retention
completion rate
followers gained
```

Então:

```text
IDEA
 ↓
SCRIPT
 ↓
VIDEO
 ↓
PUBLISH
 ↓
PERFORMANCE
 ↓
LEARNING
 ↓
PATTERN SCORE
```

Depois de 100 vídeos, o sistema pode descobrir:

> "Neste nicho, vídeos de 20–30 segundos com transformação visual têm 2.7× mais retenção."

Isso vale muito mais que qualquer lista de "trends".

---

# 12. Banco de dados

Como você já trabalha com Docker/MongoDB nos seus outros projetos, eu usaria algo parecido aqui.

```text
MongoDB

segments
creators
videos
video_metrics
transcripts
creative_patterns
ideas
scripts
production_jobs
published_videos
performance_metrics
```

Relacionamento:

```text
Creator
   │
   └── Videos
          │
          └── Pattern
                 │
                 └── Ideas
                        │
                        └── Script
                               │
                               └── Published Video
                                      │
                                      └── Performance
```

---

# 13. Stack que eu usaria

Para o seu perfil técnico, eu faria:

```text
                    FastAPI
                       │
        ┌──────────────┼──────────────┐
        │              │              │
        ▼              ▼              ▼
   Trend Hunter    Analyzer       Script Agent
        │              │              │
        └──────────────┼──────────────┘
                       │
                    MongoDB
                       │
                    Redis
                       │
                  Worker Queue
                       │
        ┌──────────────┼──────────────┐
        ▼              ▼              ▼
     TikTok        YouTube         Web
```

### Infra

```text
Docker Compose
FastAPI
Python
MongoDB
Redis
Celery/RQ/Arq
LLM
Whisper
FFmpeg
S3/MinIO
```

E para automação:

```text
n8n
```

pode ser usado inicialmente para orquestração externa, enquanto o núcleo permanece em Python.

---

# 14. Eu dividiria os agentes assim

### Agent 01 — Segment Manager

Define:

```text
nicho
público
keywords
subnichos
idiomas
competidores
```

---

### Agent 02 — Trend Hunter

Busca:

```text
TikTok
Instagram
YouTube
Web
```

---

### Agent 03 — Video Analyzer

Analisa:

```text
hook
estrutura
duração
edição
narração
texto
visual
CTA
```

---

### Agent 04 — Viral Scorer

Calcula:

```text
viral score
replication score
novelty score
production score
```

---

### Agent 05 — Creative Strategist

Transforma:

```text
VIDEO
```

em:

```text
CREATIVE PATTERN
```

---

### Agent 06 — Idea Generator

Transforma:

```text
PATTERN
+
NICHO
+
AUDIÊNCIA
```

em:

```text
10 ideias
```

---

### Agent 07 — Script Writer

Transforma:

```text
IDEA
```

em:

```text
SCRIPT
```

---

### Agent 08 — Production Director

Transforma:

```text
SCRIPT
```

em:

```text
shot list
prompts
assets
voice
B-roll
```

---

### Agent 09 — Performance Analyst

Depois da publicação:

```text
performance
↓
learning
↓
atualiza scores
```

---

# 15. O resultado final poderia ser um painel assim

```text
╔══════════════════════════════════════════╗
║       CONTENT INTELLIGENCE               ║
╠══════════════════════════════════════════╣
║ NICHO: VELAS ARTESANAIS                  ║
║                                          ║
║ 🔥 Opportunities today: 17               ║
║                                          ║
║ ┌──────────────────────────────────────┐ ║
║ │ #1  "Candle frosting mistake"        │ ║
║ │                                     │ ║
║ │ Viral Score:       94               │ ║
║ │ Replication:       91               │ ║
║ │ Production:        87               │ ║
║ │                                     │ ║
║ │ Pattern: Problem → Solution          │ ║
║ │                                     │ ║
║ │ [Generate Idea] [Generate Script]   │ ║
║ └──────────────────────────────────────┘ ║
║                                          ║
║ TODAY                                    ║
║ ├── Video 01  █████████  READY          ║
║ ├── Video 02  ████████   SCRIPT         ║
║ ├── Video 03  ██████     IDEA           ║
║                                          ║
║ CONTENT STOCK: 9 DAYS                    ║
╚══════════════════════════════════════════╝
```

---

# 16. E eu adicionaria uma coisa que muita gente esquece

## "Trend decay"

Uma trend tem validade.

Então cada oportunidade recebe:

```text
trend_score
trend_velocity
trend_age
trend_decay
```

Exemplo:

```text
Trend A
Score: 92
Age: 2 days
Decay: LOW
→ PRODUZIR AGORA

Trend B
Score: 84
Age: 19 days
Decay: HIGH
→ IGNORAR

Evergreen A
Score: 78
Age: 180 days
Decay: NONE
→ PRODUZIR QUANDO CONVENIENTE
```

Você acaba tendo **dois motores**:

```text
TRENDING CONTENT
       +
EVERGREEN CONTENT
```

E isso é muito melhor para publicação diária.

---

# 17. O MVP que eu faria

Não tente construir tudo de uma vez.

### Fase 1 — 1 segmento

Escolher:

> **Velas artesanais**

### Fase 2

Construir:

```text
YouTube
+
TikTok Creative Center
+
Web
```

### Fase 3

Banco:

```text
videos
creators
metrics
transcripts
```

### Fase 4

AI:

```text
video → pattern
```

### Fase 5

```text
pattern → 10 ideas
```

### Fase 6

```text
idea → script
```

### Fase 7

```text
script → production package
```

### Fase 8

Feedback:

```text
published → performance → learning
```

Só depois eu colocaria geração automática de vídeo.

---

## A arquitetura que eu considero ideal

O projeto poderia acabar ficando assim:

```text
content-factory/
│
├── api/
│   └── fastapi/
│
├── agents/
│   ├── trend_hunter/
│   ├── video_analyzer/
│   ├── viral_scorer/
│   ├── creative_strategist/
│   ├── idea_generator/
│   ├── script_writer/
│   ├── production_director/
│   └── performance_analyst/
│
├── collectors/
│   ├── youtube/
│   ├── tiktok/
│   ├── instagram/
│   └── web/
│
├── analyzers/
│   ├── transcript/
│   ├── vision/
│   ├── audio/
│   └── metrics/
│
├── generators/
│   ├── ideas/
│   ├── scripts/
│   ├── prompts/
│   └── videos/
│
├── models/
│
├── workers/
│
├── database/
│
├── storage/
│
├── prompts/
│
├── segments/
│   └── candles.yaml
│
└── docker-compose.yml
```

**E eu faria o sistema genérico desde o primeiro dia.** Não construiria um "gerador de conteúdo para velas". Construiria um **motor de inteligência de conteúdo parametrizado por segmento**.

Assim:

```text
segment = candles
```

amanhã:

```text
segment = macrame
```

depois:

```text
segment = fitness
```

depois:

```text
segment = cooking
```

sem reescrever a plataforma.

O TikTok inclusive já estrutura parte da descoberta por indústria e período, o que encaixa muito bem nesse conceito de `segment + trend window`. ([TikTok For Business][1])

**Se eu fosse implementar isso com você, o próximo passo seria desenhar o MVP técnico completo** — MongoDB schema + entidades + APIs FastAPI + workers + agentes + pipeline de coleta/análise + estrutura Docker — e começar com **Velas Artesanais como primeiro segmento**, mas deixando a arquitetura pronta para qualquer nicho.

[1]: https://ads.tiktok.com/resources/help/article/how-to-use-trends?lang=en&redirected=2&utm_source=chatgpt.com "How to use Trends"
[2]: https://developers.google.com/youtube/v3/docs/search/list?utm_source=chatgpt.com "Search: list  |  YouTube Data API  |  Google for Developers" 


Vamos aproveitar este projeto inicial e atualizar usando a skill python-quality-standards
precisamos ter as tecnlogias separados em docker, usando devcontainers

acredito que o objetivo e criar scripts para ser consumidor por agentes de AI das automacao
