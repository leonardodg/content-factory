# Evidência — sondas do Suggest e QA dos commits `db3ad2e`/`4f56a0a` (16/10/2026)

Registro do que foi **medido e verificado** em 16/10/2026, para a F1/F4 do
plano [Relevância web e potencial](../ai-plans/2026-10-16-web-relevancia-potencial.md).
Complementa o pré-registro ([validação web](validacao-web.md)): aqui ficam os
números brutos e as análises; lá ficam os critérios congelados.

Nada aqui altera critério pré-registrado. As medições do autocompletar são
**read-only** (nenhuma escrita no banco, nenhum restart do worker).

## 1. Revisão do commit `db3ad2e` — achados e correção

Revisão externa em modo só leitura. Todos os achados foram reproduzidos antes de
corrigir.

| # | Achado | Verificação | Correção |
| --- | --- | --- | --- |
| 1 | O smoke test da imagem **sempre passava** | `docker run` sem `-i` não entrega o stdin: um probe com `sys.exit(3)` saía **0**. Mesmo com `-i`, `--entrypoint python` explode em `ModuleNotFoundError: content_factory` | probe extraído para `scripts/check_worker_segments.py`; o script usa `docker run --rm -i --entrypoint uv "$IMAGE" run python -` |
| 2 | Falta de protocolo caía no legado **em silêncio** | o teste novo fixava o fallback como aceitável | `WEB_QUERIES_REQUIRED=1` (`Settings` → `WebCollector(required=...)`): ausente/inválido → `CollectorError("missing_protocol")`, collect `failed`, legado **não** roda |
| 3 | `COPY segments` + teste de política | corretos | registrado: os YAMLs ficam fixos no build → aprovar protocolo exige **novo build** |
| 4 | `test_queries_for_reloads_after_mtime_change` instável | dependia da resolução do mtime | força o mtime com `os.utime` |
| 5 | commit numa branch cujo PR já foi mergeado | procedente | sem push; branch/PR novo fica com quem está na branch |

### Prova do defeito 1 (executado contra a imagem `content-factory-worker`)

| Comando | Exit | Significado |
| --- | --- | --- |
| probe `sys.exit(3)`, sem `-i` | **0** | nada roda — falso verde |
| probe `sys.exit(3)`, com `-i` | 3 | o probe roda de verdade |
| `--entrypoint python` | 1 | `ModuleNotFoundError: No module named 'content_factory'` |
| `scripts/smoke_worker_segments.sh` (imagem atual) | **1** | falha como esperado: a imagem em produção é anterior até ao módulo web |

O smoke test agora **falha** quando `segments/web-queries/` está ausente — é o
gate que impede subir `WEB_QUERIES_REQUIRED=1` sem protocolo.

## 2. Método da medição das sondas

O Suggest é semeado com as keywords do segmento e devolve variações de produto.
A opção (a) da revisão acrescenta **sondas de intenção**. Medido contra o
autocompletar real, comparando a cobertura **SEM sondas** (só keywords) e **COM
sondas** (keywords + sondas `probe-v2` sobre os termos-cabeça).

Reprodução: `uv run python scripts/measure_suggest_probes.py`.

### Experimento A — a sonda rende na cabeça do termo, não na cauda longa

Consultas de sonda diretas ao autocompletar (`client=firefox`):

| Sonda | Sugestões | Uteis (resolver/tendencia) |
| --- | --- | --- |
| `vela artesanal nao` | 0 | 0 |
| `vela artesanal tendencia` | 0 | 0 |
| `sabonete artesanal nao` | 0 | 0 |
| `vela artesanal vale a pena` | 0 | 0 |
| `vela artesanal derrete` | 0 | 0 |
| `vela nao` | 10 | 10 |
| `velas nao` | 10 | 10 |
| `pavio nao` | 8 | 8 |
| `vela tendencia` | 10 | 10 |
| `sabonete mais vendido` | 10 | 10 |
| `artesanato mais vendido` | 10 | 10 |
| `resina epoxi nao` | 2 | 2 |
| `bolo de cenoura nao` (controle, nicho popular) | 7 | 7 |

**Análise:** o mecanismo da sonda está correto (o controle em nicho popular
rende 7 frases de `resolver`). O limite é o **dado** do nicho: a cauda longa
("vela artesanal") praticamente não tem autocompletar. Por isso os termos
sondados passam a ser `SegmentConfig.probe_terms` (cabeça do termo), não as
keywords do segmento.

### Experimento B — trocar o `client` do endpoint não muda nada

`vela artesanal tendencia`, `vela artesanal nao`, `sabonete artesanal nao`
devolvem **0** em `client=firefox` **e** em `client=chrome`. Confirma que não é
a consulta: é a ausência de dado.

## 3. Resultado — cobertura por segmento e por classe

Contagens por classe (`intent_coverage`) sobre as frases devolvidas pelo
autocompletar. "SEM" = só as keywords do segmento; "COM" = keywords + 15 sondas
(`probe-v2` × 3 termos-cabeça).

| Segmento | Estado | Frases | aprender | resolver | tendencia | comercial | sem_intencao |
| --- | --- | --- | --- | --- | --- | --- | --- |
| velas-artesanais | SEM | 149 | 26 | 7 | **0** | 7 | 109 |
| velas-artesanais | COM | 238 | 43 | **35** | **22** | 28 | 110 |
| sabonetes-artesanais | SEM | 144 | 29 | 5 | **1** | 9 | 100 |
| sabonetes-artesanais | COM | 218 | 49 | **25** | **20** | 24 | 100 |
| resina-artesanal | SEM | 145 | 29 | 2 | **0** | 11 | 103 |
| resina-artesanal | COM | 218 | 54 | **19** | **13** | 29 | 103 |

Com as sondas, **as 4 classes têm ≥ 3 frases em todos os 3 segmentos** — o
insumo do `q1` (3 por classe + 1 controle = 13 consultas) passa a existir.

Termos-cabeça usados (`segments/*.yaml` → `probe_terms`):

| Segmento | `probe_terms` |
| --- | --- |
| velas-artesanais | `vela`, `velas`, `pavio` |
| sabonetes-artesanais | `sabonete`, `sabonetes`, `saboaria` |
| resina-artesanal | `resina`, `resina epoxi`, `artesanato` |

Modelos de sonda (`probe-v2`): `como fazer <termo>` (aprender), `<termo> nao`
(resolver), `<termo> tendencia` + `<termo> mais vendido` (tendencia),
`<termo> preco` (comercial).

## 4. QA do commit `4f56a0a` — léxico `intent-v3` e filtro `filter-v1`

Sorteio cego com a seed `20261017` sobre as frases acima. As 4 classes fechavam
nos 3 segmentos, mas **de 4 a 7 das 13 consultas saíam fora do nicho ou em outro
idioma**. Causas no léxico `intent-v2`:

| Causa | Exemplos sorteados |
| --- | --- |
| marcadores em inglês no léxico (`tutorial`, `for beginners`, `sale`, `price`, `wholesale`) | `soap making for beginners without lye`, `cold process soap vs hot process soap`, `resin tray price in pakistan` |
| `nao` como marcador isolado | `pavio parafinado ou nao`; `velas que nao pingam` (atributo desejável, não problema) |
| `significado` / `vs` como problema | `flores encapsuladas em resina significado` |
| marcas industriais | `sabonete mais vendido da natura`, `preco sabonete phebo` |
| homonímia do termo-cabeça (sem filtro possível) | `resina nao clareia` (odontológica), `vela martillo en tendencia bajista` (gráfico) |

**Correção:** léxico `intent-v3` (só PT; `nao` exige desfecho ruim; saem
`significado`, `vs`, `derrete` e os marcadores em inglês) + filtro `filter-v1`
antes do sorteio (idioma e marcas industriais), gravado no protocolo como
`filter_version`. Detalhes em [validação web](validacao-web.md).

### Cobertura com o filtro (`filter-v1`)

| Segmento | Frases | Elegíveis | aprender | resolver | tendencia | comercial |
| --- | --- | --- | --- | --- | --- | --- |
| velas-artesanais | 238 | 164 | 34 | 15 | 15 | 21 |
| sabonetes-artesanais | 218 | 142 | 33 | 4 | 15 | 13 |
| resina-artesanal | 218 | 164 | 43 | 7 | 13 | 23 |

`missing` vazio nos 3 segmentos (3 por classe + 1 controle). Reprodução:
`uv run python scripts/measure_suggest_probes.py` (mostra cobertura crua e
elegível).

### As 13 consultas sorteadas por segmento (`intent-v3` + `filter-v1`)

| Segmento | Consultas (aprender / resolver / tendencia / comercial) | Candidatas a veto |
| --- | --- | --- |
| velas-artesanais | `como fazer vela aromatizada com perfume`; `como fazer pavio para lamparina`; `como fazer pavio para lamparina a óleo` / `vela não queimou toda umbanda`; `pavio da vela nao acende`; `pavio que nao queima` / `aroma de vela mais vendido`; `vela de aniversario tendencia 2025`; `velas aromaticas tendencia` / `vela preço`; `preco velas 7 dias`; `vela artesanal preço` | 3 (vela ritual/de aniversário) |
| sabonetes-artesanais | `como fazer sabonete de babosa`; `como fazer sabonete artesanal em casa`; `como fazer sabonete artesanal natural` / `saboaria artesanal anvisa`; `sabonete nao faz espuma`; `saboaria artesanal legislação` / `marcas de sabonetes mais vendidos`; `sabonete liquido mais vendido`; `sabonete mais vendido` / `base glicerinada nossa terra atacado`; `preco sabonete liquido`; `sabonete artesanal preço` | 0 |
| resina-artesanal | `como fazer artesanatos de papel`; `como trabalhar com resina`; `como fazer artesanato para vender` / `resina epóxi não endurece`; `resina nao seca`; `resina epoxi nao endurece o que fazer` / `produtos de resina mais vendidos`; `artesanato tendencia 2025`; `artesanato mais vendido no brasil` / `cristais para artesanato preco`; `preco resina epoxi`; `resina epoxi artesanato preco` | 1 (`artesanato de papel`) |

De **15 candidatas a veto** (4+7+4 no QA do `4f56a0a`) para **4** (3+0+1), todas
resíduo de homonímia. Como o veto troca pela próxima do sorteio e o conjunto
agora está limpo, a substituta também é do nicho.

## 5. Análise e limitações

**O que a sonda resolve:** `resolver` e `tendencia`, que eram 0–2 e 0–1 frases
por segmento, passam a 19–35 e 13–22. O diagnóstico da revisão (o Suggest é
semeado com o nome do segmento e devolve produto) estava correto; o que faltava
era sondar a cabeça do termo.

**Limitação 1 — `tendencia` é induzido pela sonda.** A sonda de tendência leva
o próprio marcador, então a frase cai na classe por construção. A consulta é
uma busca real de usuário, mas o estrato `tendencia` do `q1` é **induzido**, não
descoberto passivamente. Na leitura da F4, tratar `tendencia` como piso
exploratório.

**Limitação 2 — termos-cabeça são ambíguos em PT.** Exemplos observados:

| Frase | Sentido fora do segmento |
| --- | --- |
| `resina nao clareia` / `resina nao para no dente` | resina odontológica |
| `vela nao sai do motor` / `vela nao da faisca` | vela de ignição |
| `velas tendencia alcista` / `vela martillo en tendencia alcista` | vela de gráfico (candlestick) |
| `vela nao queimou toda umbanda` | vela ritualística |

O `q1` é revisado pelo usuário, que **veta** a frase inadequada
(`cf web-queries --veto/--reason`); o veto e o motivo ficam registrados no YAML.
Decisão de 16/10: **manter `probe-v2` completo** e vetar na revisão.

**Limitação 3 — o léxico não era o gargalo; a sonda era.** A cobertura do léxico
`intent-v2` sobre os sinais **já gravados** (banco real) era 40/154 (velas),
45/150 (sabonetes) e 42/151 (resina) — o resto é `sem_intencao` por não ter
marcador, não por erro de classificação. As frases úteis simplesmente não
estavam nos sinais. O `intent-v3` (§4) não muda esse diagnóstico: ele corrige
**qualidade** (idioma, `nao` solto, marcas), não volume.

**Limitação 4 — margem estreita em sabonetes/`resolver`.** Com o `filter-v1`,
sobram 4 frases elegíveis (3 entram no `q1`): um veto abriria `missing`.

## 6. O que ainda não foi feito (depende do deploy)

- O **novo ciclo de research** com as sondas roda no worker (cron diário), só
  depois do deploy de 21/10 após 19:20 UTC. Antes disso os sinais no banco
  ainda não têm as frases das sondas.
- O **`q1` só é regenerado** depois desse ciclo (`cf web-queries --propose`),
  seguido da revisão/veto do usuário. Os YAMLs provisórios (gerados sem as
  sondas) foram removidos.
- O deploy precisa de `cf segment load` para subir os `probe_terms` novos.
